"""Run-owned reference frontier policy and serialized provenance verification."""

import hashlib
from urllib.parse import urlsplit

from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.ledger import Ledger
from ghimera.models import Document, Harvest, LedgerRow, LinkCandidate, Scope
from ghimera.reference_types import (
    DocumentReference,
    ReferenceDecision,
    ReferenceQuery,
    ReferenceSource,
    SearchReference,
)


class ReferenceBook:
    def __init__(self, config: GhimeraConfig) -> None:
        self._config = config
        self._parents: set[tuple[str, str]] = set()
        self._hosts: set[str] = set()
        self._queued: set[str] = set()
        self._queued_by_parent: dict[tuple[str, str], int] = {}
        self._queries: set[tuple[str, str]] = set()

    @property
    def extra_hosts(self) -> tuple[str, ...]:
        """Read-only observations for the research discovery scope owner."""
        return tuple(sorted(self._hosts))

    def restore(self, rows: tuple[LedgerRow, ...], hosts: tuple[str, ...]) -> None:
        """Reconstruct reserved reference work from validated observations, not fresh limits."""
        policy = self._config.references
        if hosts and (policy is None or len(set(hosts)) > policy.max_extra_hosts):
            raise ValueError("restored reference hosts exceed the original policy")
        observed_hosts: set[str] = set()
        for row in rows:
            if row.reference_query is not None:
                source = row.reference_query.source
                key = (source.url, source.sha256)
                self._queries.add(key)
                self._parents.add(key)
            if row.reference is not None and row.reference.outcome == "queued":
                decision = row.reference
                key = (decision.source.url, decision.source.sha256)
                self._parents.add(key)
                self._queued.add(decision.reference.target_url)
                self._queued_by_parent[key] = self._queued_by_parent.get(key, 0) + 1
                host = urlsplit(decision.reference.target_url).hostname
                if host is not None:
                    observed_hosts.add(host)
        if not set(hosts) <= observed_hosts:
            raise ValueError("restored reference hosts require admitted reference observations")
        self._hosts = set(hosts)

    def claim_query(self, source: Document) -> bool:
        """Share the parent cap with document links and reserve before query I/O."""
        policy = self._config.references
        key = (source.url, source.sha256)
        if (
            policy is None
            or not policy.discover_cited_by
            or key in self._queries
            or (
                len(self._queries) >= policy.cited_by_query_budget
                or (key not in self._parents and len(self._parents) >= policy.max_parents)
            )
        ):
            return False
        self._queries.add(key)
        self._parents.add(key)
        return True

    def consider(
        self,
        source: Document,
        reference: DocumentReference | SearchReference,
        link: LinkCandidate,
        scope: Scope,
        parent_hops: int,
        visited: set[str],
        ledger: Ledger,
        origin_url: str | None = None,
    ) -> Scope | None:
        policy = self._config.references
        if policy is None:
            return None
        key = (source.url, source.sha256)
        selected: Scope | None = None
        decision = ReferenceDecision(
            schema="chimera.reference-decision/1",
            source=ReferenceSource(
                url=source.url,
                sha256=source.sha256,
                text_sha256=hashlib.sha256(source.extracted.text.encode()).hexdigest(),
            ),
            reference=reference,
            parent_hops=parent_hops,
            origin_url=origin_url,
            score=link.score,
            outcome="queued",
        )
        enabled = (
            policy.follow_document_references
            if isinstance(reference, DocumentReference)
            else policy.discover_cited_by
        )
        if not enabled:
            decision = decision.model_copy(update={"outcome": "disabled"})
        elif parent_hops >= policy.max_hops:
            decision = decision.model_copy(update={"outcome": "hop_limit"})
        elif link.score < self._config.min_link_score:
            decision = decision.model_copy(update={"outcome": "below_score"})
        elif reference.target_url in visited or reference.target_url in self._queued:
            decision = decision.model_copy(update={"outcome": "already_seen"})
        elif (
            len(self._queued) >= policy.max_queued_per_run
            or self._queued_by_parent.get(key, 0) >= policy.max_candidates_per_parent
            or (key not in self._parents and len(self._parents) >= policy.max_parents)
        ):
            decision = decision.model_copy(update={"outcome": "budget_limit"})
        else:
            selected = self._scope(reference.target_url, scope)
            if selected is None:
                decision = decision.model_copy(update={"outcome": "out_of_scope"})
            else:
                self._parents.add(key)
                self._queued.add(reference.target_url)
                self._queued_by_parent[key] = self._queued_by_parent.get(key, 0) + 1
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="reference",
                url=reference.target_url,
                reason="observed_source_reference:" + decision.outcome,
                reference=decision,
            )
        )
        return selected

    def _scope(self, url: str, original: Scope) -> Scope | None:
        policy = self._config.references
        if policy is None:
            return None
        host = urlsplit(url).hostname
        research = self._config.research
        if (
            host is None
            or host in policy.denied_hosts
            or (
                research is not None
                and (
                    host in research.denied_hosts
                    or (
                        research.source_policy == "configured_only"
                        and host not in research.allowed_hosts
                    )
                )
            )
        ):
            return None
        if original.permits(url):
            return original
        if policy.outside_scope != "observed_public" or (
            host not in self._hosts and len(self._hosts) >= policy.max_extra_hosts
        ):
            return None
        hosts = set(original.allowed_hosts) | self._hosts | {host}
        if research is not None and len(hosts) > research.max_source_hosts:
            return None
        try:
            selected = Scope.model_validate(
                dict(original.model_dump(), allowed_hosts=tuple(sorted(hosts)))
            )
        except ValidationError:
            return None
        if not selected.permits(url):
            return None
        self._hosts.add(host)
        return selected


def _parent_hops(source_url: str, origin_url: str | None, queued: dict[str, int]) -> int:
    if origin_url is not None and origin_url not in queued:
        raise ValueError("reference ledger must bind its frontier hop ancestry")
    hops = queued.get(origin_url or source_url, 0)
    if source_url in queued and hops != queued[source_url]:
        raise ValueError("reference ledger cannot reset a queued source's hop ancestry")
    return hops


def validate_reference_ledger(harvest: Harvest) -> None:
    policy = harvest.receipt.effective_config.references
    sources = {(doc.url, doc.sha256): doc for doc in harvest.source_documents}
    queued: dict[str, int] = {}
    parents: set[tuple[str, str]] = set()
    queued_by_parent: dict[tuple[str, str], int] = {}
    queries: dict[int, ReferenceQuery] = {}
    for row in harvest.ledger:
        request = row.reference_query
        if request is not None:
            source = sources.get((request.source.url, request.source.sha256))
            if (
                policy is None
                or not policy.discover_cited_by
                or source is None
                or (
                    request.source.text_sha256
                    != hashlib.sha256(source.extracted.text.encode()).hexdigest()
                    or request.parent_hops >= policy.max_hops
                    or request.parent_hops != _parent_hops(source.url, request.origin_url, queued)
                    or request.query
                    != policy.cited_by_query_template.format(
                        title=source.extracted.title[: policy.max_query_title_chars], url=source.url
                    )
                    or any(item.source == request.source for item in queries.values())
                )
            ):
                raise ValueError(
                    "reference query must bind its accepted source and effective policy"
                )
            queries[row.sequence] = request
            parents.add((source.url, source.sha256))
        decision = row.reference
        if decision is None:
            continue
        source = sources.get((decision.source.url, decision.source.sha256))
        if (
            policy is None
            or source is None
            or (
                decision.source.text_sha256
                != hashlib.sha256(source.extracted.text.encode()).hexdigest()
            )
        ):
            raise ValueError("reference ledger must bind accepted retained native source evidence")
        reference = decision.reference
        if isinstance(reference, DocumentReference):
            if reference not in source.extracted.references or not reference.matches(
                source.extracted.text, source.extracted.document_layout
            ):
                raise ValueError("reference ledger must bind its native locator")
            enabled = policy.follow_document_references
        else:
            enabled = policy.discover_cited_by
            request = queries.get(reference.query_sequence)
            expected_query = policy.cited_by_query_template.format(
                title=source.extracted.title[: policy.max_query_title_chars], url=source.url
            )
            if (
                request is None
                or request.source != decision.source
                or (
                    request.provider != reference.provider
                    or request.provider_revision != reference.provider_revision
                    or request.query != reference.query
                )
                or reference.query != expected_query
                or not any(
                    reference.query_sequence < item.sequence < row.sequence
                    and item.event == "fetch"
                    and item.refusal is None
                    and item.query == reference.query
                    and item.route == f"search:{reference.provider}@{reference.provider_revision}"
                    and item.reason == "grounded_search:" + reference.response_sha256
                    for item in harvest.ledger
                )
            ):
                raise ValueError("search reference must bind its source-derived provider request")
        if decision.parent_hops != _parent_hops(source.url, decision.origin_url, queued):
            raise ValueError("reference ledger must bind its frontier hop ancestry")
        if (
            (decision.outcome == "disabled" and enabled)
            or (decision.outcome == "hop_limit" and decision.parent_hops < policy.max_hops)
            or (
                decision.outcome == "below_score"
                and decision.score >= harvest.receipt.effective_config.min_link_score
            )
        ):
            raise ValueError("reference outcome contradicts its effective policy")
        if decision.outcome == "queued":
            target = decision.reference.target_url
            research = harvest.receipt.effective_config.research
            host = urlsplit(target).hostname
            if (
                not enabled
                or decision.parent_hops >= policy.max_hops
                or (
                    decision.score < harvest.receipt.effective_config.min_link_score
                    or target in queued
                    or host in policy.denied_hosts
                    or (
                        research is not None
                        and (
                            host in research.denied_hosts
                            or (
                                research.source_policy == "configured_only"
                                and host not in research.allowed_hosts
                            )
                        )
                    )
                )
            ):
                raise ValueError("queued reference violates its effective policy")
            queued[target] = decision.parent_hops + 1
            parents.add((source.url, source.sha256))
            key = (source.url, source.sha256)
            queued_by_parent[key] = queued_by_parent.get(key, 0) + 1
    if policy is not None and (
        len(queued) > policy.max_queued_per_run
        or len(parents) > policy.max_parents
        or len(queries) > policy.cited_by_query_budget
        or any(count > policy.max_candidates_per_parent for count in queued_by_parent.values())
    ):
        raise ValueError("reference ledger exceeds its shared run budget")
