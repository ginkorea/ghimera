"""Replay browser provenance against observed capture work, without live I/O."""

from typing import TYPE_CHECKING

from ghimera.human_browser_types import HumanBrowserEvidence

if TYPE_CHECKING:
    from ghimera.models import Harvest


def validate_harvest(harvest: "Harvest") -> None:
    policy = harvest.receipt.effective_config.human_browser
    captures: dict[str, HumanBrowserEvidence] = {}
    observed_ids: set[str] = set()
    content: set[tuple[str, str]] = set()
    for row in harvest.ledger:
        evidence = row.human_browser
        if evidence is not None:
            evidence.validate_policy(policy)
            if evidence.capture_id in observed_ids:
                raise ValueError("a browser capture cannot be charged/replayed twice")
            observed_ids.add(evidence.capture_id)
            captures[evidence.capture_id] = evidence
            content.add((evidence.final_url, evidence.dom_sha256))
        if row.human_assistance:
            if policy is None or len(row.human_assistance) > policy.max_assistance_attempts:
                raise ValueError("failed assistance requires its configured attempt bound")
            capture_ids = {item.request.capture_id for item in row.human_assistance}
            if len(capture_ids) != 1:
                raise ValueError("failed assistance cannot mix separate captures")
            capture_id = next(iter(capture_ids))
            if capture_id in observed_ids:
                raise ValueError("failed capture cannot reuse another capture observation")
            observed_ids.add(capture_id)
            if (
                sum(item.request.observed_dom_bytes for item in row.human_assistance)
                > row.bytes_read
            ):
                raise ValueError("failed assistance cannot hide observed DOM spend")
            for attempt, item in enumerate(row.human_assistance, start=1):
                request = item.request
                if (
                    request.attempt != attempt
                    or request.policy_digest != policy.content_digest()
                    or request.session_id != policy.session_id
                    or request.target_id != policy.target_id
                    or request.request_url != row.url
                    or not policy.permits(request.request_url)
                    or not policy.permits(request.final_url)
                    or request.reason not in policy.assistance_reasons
                    or request.observed_dom_bytes > policy.max_dom_bytes
                    or (attempt > 1 and row.human_assistance[attempt - 2].action != "resume")
                ):
                    raise ValueError(
                        "failed assistance must retain its exact source-policy binding"
                    )
    for document in harvest.source_documents:
        evidence = document.human_browser
        if evidence is None:
            if (document.url, document.sha256) in content:
                raise ValueError("browser-observed document cannot discard capture provenance")
        elif captures.get(evidence.capture_id) != evidence:
            raise ValueError("browser document requires its successful capture observation")
    if harvest.graph is not None:
        for node in harvest.graph.nodes:
            evidence = node.human_browser
            if evidence is None:
                if (node.source_url, node.content_sha256) in content:
                    raise ValueError("browser document node cannot discard capture provenance")
            elif captures.get(evidence.capture_id) != evidence:
                raise ValueError("browser graph document requires its capture observation")
