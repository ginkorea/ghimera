"""Immutable validated package objects; TAIPAN dataclass conversion belongs to C4."""

import hashlib
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chimera.browser_types import RenderResult
from chimera.config import ChimeraConfig, Probability
from chimera.dedup_types import ContentDrift, DedupEvidence
from chimera.document_types import DocumentLayout, DocumentParseEvidence
from chimera.embedding_types import EncodingCall
from chimera.extraction_types import ExtractionEvidence
from chimera.graph_types import GraphSnapshot
from chimera.model_types import ModelCallEvidence
from chimera.reference_types import DocumentReference, ReferenceDecision, ReferenceQuery
from chimera.refusals import RefusalCode
from chimera.scoring_types import SimilarityEvidence
from chimera.source_session_types import SourceSessionUse
from chimera.transport_types import TransportEvidence

NonEmpty = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[int, Field(strict=True, ge=0)]
StopReason = Literal["frontier_empty", "budget_exhausted", "saturated", "goal_satisfied", "failed"]


class Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class Goal(Record):
    text: NonEmpty
    seeds: tuple[str, ...] = ()

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal text must not be blank")
        return value


class Scope(Record):
    allowed_hosts: Annotated[tuple[str, ...], Field(min_length=1)]
    max_depth: NonNegative
    content_types: Annotated[tuple[str, ...], Field(min_length=1)]
    allowed_ports: Annotated[
        tuple[Annotated[int, Field(strict=True, ge=1, le=65535)], ...], Field(min_length=1)
    ] = (80, 443)

    @field_validator("allowed_hosts")
    @classmethod
    def exact_public_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        for host in hosts:
            if host != host.lower() or host.endswith(".") or ":" in host or "/" in host:
                raise ValueError("allowed_hosts must be lowercase exact DNS names without ports")
            if "." not in host or any(
                not label or not label.replace("-", "").isalnum() for label in host.split(".")
            ):
                raise ValueError("allowed_hosts must be exact public DNS names")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("IP literals are not permitted as crawl hosts")
        return hosts

    def permits(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
            return (
                parts.scheme in {"http", "https"}
                and parts.username is None
                and parts.password is None
                and parts.hostname in self.allowed_hosts
                and (parts.port or (443 if parts.scheme == "https" else 80)) in self.allowed_ports
                and not any(ord(char) < 33 for char in url)
            )
        except ValueError:
            return False


class LinkCandidate(Record):
    url: NonEmpty
    anchor: str = ""
    score: Probability = 0.0


class FetchRequest(Record):
    url: NonEmpty
    max_bytes: Annotated[int, Field(strict=True, gt=0)]
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    headers: tuple[tuple[Literal["if-none-match", "if-modified-since"], str], ...] = ()

    @field_validator("headers")
    @classmethod
    def one_line(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        if any("\r" in text or "\n" in text for _, text in value):
            raise ValueError("request metadata must be single-line HTTP headers")
        return value


class Page(Record):
    url: NonEmpty
    final_url: NonEmpty
    status: Annotated[int, Field(strict=True, ge=100, le=599)]
    content_type: NonEmpty
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    revalidated: bool = False
    transport: TransportEvidence | None = None
    rendered: RenderResult | None = None
    source_session: SourceSessionUse | None = None

    @model_validator(mode="after")
    def rendering_binding(self) -> "Page":
        if self.source_session is not None and self.source_session.request_url != self.final_url:
            raise ValueError("source session selection must bind this response URL")
        if self.rendered is not None and (
            self.rendered.source_sha256 != hashlib.sha256(self.body).hexdigest()
            or self.rendered.source_url != self.final_url
        ):
            raise ValueError("rendering must bind original retained response bytes and URL")
        return self

    def header(self, name: str) -> str | None:
        return next((value for key, value in self.headers if key == name.lower()), None)


class Extracted(Record):
    title: NonEmpty
    text: NonEmpty
    language: NonEmpty
    links: tuple[LinkCandidate, ...] = ()
    byline: str | None = None
    date: str | None = None
    canonical_url: str | None = None
    extraction: ExtractionEvidence | None = None
    document_parse: DocumentParseEvidence | None = None
    document_layout: DocumentLayout | None = None
    references: tuple[DocumentReference, ...] = ()

    @model_validator(mode="after")
    def text_binding(self) -> "Extracted":
        eligible = {(link.url, link.anchor) for link in self.links}
        if any(
            (item.target_url, item.anchor) not in eligible
            or not item.matches(self.text, self.document_layout)
            for item in self.references
        ):
            raise ValueError("references must bind observed native text/layout and extracted links")
        if len({item.target_url for item in self.references}) != len(self.references):
            raise ValueError("reference URLs must be unique within a document")
        if (
            self.extraction is not None
            and self.extraction.text_sha256 != hashlib.sha256(self.text.encode()).hexdigest()
        ):
            raise ValueError("extraction evidence must bind its native text")
        if (self.document_parse is None) != (self.document_layout is None):
            raise ValueError("document conversion requires both provenance and retained layout")
        if self.document_parse is not None and self.document_layout is not None:
            if self.document_parse.text_sha256 != hashlib.sha256(self.text.encode()).hexdigest():
                raise ValueError("document conversion must bind native text")
            if self.document_parse.layout_sha256 != self.document_layout.sha256:
                raise ValueError("document conversion must bind retained layout")
        return self


class Verdict(Record):
    model_call: ModelCallEvidence | None = None
    decision: Literal["accept", "reject", "hold"]
    kind: NonEmpty
    publisher: NonEmpty
    language: NonEmpty
    reason: NonEmpty
    date: str | None = None

    @field_validator("reason", "kind", "publisher", "language")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("verdict fields must not be blank")
        return value


class Grade(Record):
    model_call: ModelCallEvidence | None = None
    satisfied: bool
    confidence: Probability
    reason: NonEmpty


class ModelIdentity(Record):
    model_id: NonEmpty
    revision: NonEmpty
    location: Literal["self_hosted", "external", "test_double"]


class DocumentSource(Record):
    url: NonEmpty
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    raw: bytes
    extracted: Extracted
    verdict: Verdict
    transport: TransportEvidence | None = None
    rendered: RenderResult | None = None
    source_session: SourceSessionUse | None = None

    def validate_policy(self, config: ChimeraConfig) -> None:
        if self.source_session is not None:
            self.source_session.validate_policy(config.source_sessions, self.url)
        if self.rendered is not None:
            if config.browser is None or len(self.raw) > config.browser.max_input_bytes:
                raise ValueError("browser rendering requires its input policy and limits")
            self.rendered.validate_policy(
                config.browser, max_redirects=config.http.max_redirects if config.http else 0
            )
            for resource in self.rendered.resources:
                if resource.source_session is not None:
                    resource.source_session.validate_policy(config.source_sessions, resource.url)
        evidence = self.extracted.extraction
        if evidence is not None and (
            config.extraction is None
            or evidence.config_digest != config.extraction.content_digest()
        ):
            raise ValueError("extraction must bind the effective run configuration")
        parsed = self.extracted.document_parse
        if parsed is not None and (
            config.document_extraction is None
            or parsed.config_digest != config.document_extraction.content_digest()
        ):
            raise ValueError("document conversion must bind the effective run configuration")

    @model_validator(mode="after")
    def source_binding(self) -> "DocumentSource":
        if self.source_session is not None and self.source_session.request_url != self.url:
            raise ValueError("source session selection must bind this source occurrence")
        digest = hashlib.sha256(self.raw).hexdigest()
        if self.sha256 != digest:
            raise ValueError("document digest must bind retained source bytes")
        if self.rendered is not None and (
            self.rendered.source_sha256 != digest or self.rendered.source_url != self.url
        ):
            raise ValueError("browser rendering must bind this source occurrence")
        if self.extracted.extraction is not None and (
            self.extracted.extraction.source_sha256 != digest
            or self.extracted.extraction.source_url != self.url
        ):
            raise ValueError("extraction evidence must bind this source occurrence")
        if self.extracted.extraction is not None and (
            self.extracted.extraction.rendered_sha256
            != (self.rendered.html_sha256 if self.rendered is not None else None)
        ):
            raise ValueError("HTML extraction must identify its rendered or original input")
        if self.extracted.document_parse is not None and (
            self.extracted.document_parse.source_sha256 != digest
            or self.extracted.document_parse.source_url != self.url
        ):
            raise ValueError("document conversion must bind this source occurrence")
        return self


class DuplicateOccurrence(DocumentSource):
    dedup: DedupEvidence

    @model_validator(mode="after")
    def dedup_binding(self) -> "DuplicateOccurrence":
        if self.dedup.current.source_sha256 != self.sha256:
            raise ValueError("duplicate evidence must bind this source occurrence")
        if self.verdict.decision != "accept":
            raise ValueError("duplicate occurrences must have their own acceptance verdict")
        return self


class Document(DocumentSource):
    duplicate_urls: tuple[str, ...] = ()
    occurrences: tuple[DuplicateOccurrence, ...] = ()

    def evidence_sources(self) -> tuple["Document", ...]:
        """Every retained occurrence remains independently citable by its raw digest."""
        return (self,) + tuple(
            Document.model_validate(item.model_dump(exclude={"dedup"})) for item in self.occurrences
        )

    @model_validator(mode="after")
    def occurrences_binding(self) -> "Document":
        keys = {(item.url, item.sha256) for item in self.occurrences}
        if len(keys) != len(self.occurrences):
            raise ValueError("source occurrences cannot repeat")
        if any(item.dedup.representative_sha256 != self.sha256 for item in self.occurrences):
            raise ValueError("duplicate occurrence must name this representative")
        if self.occurrences and self.duplicate_urls != tuple(
            dict.fromkeys(item.url for item in self.occurrences if item.url != self.url)
        ):
            raise ValueError("duplicate URLs must project the retained source occurrences")
        return self


class LedgerRow(Record):
    sequence: NonNegative
    event: Literal[
        "fetch",
        "fallback",
        "refusal",
        "verdict",
        "grade",
        "duplicate",
        "stop",
        "policy",
        "plan",
        "assessment",
        "answer",
        "review",
        "discovery",
        "extraction",
        "content_drift",
        "render",
        "encoding",
        "scoring",
        "reference",
        "reference_query",
    ]
    url: str | None = None
    route: str | None = None
    status: int | None = None
    bytes_read: NonNegative = 0
    latency_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    refusal: RefusalCode | None = None
    reason: str
    transport: TransportEvidence | None = None
    model: ModelIdentity | None = None
    query: str | None = None
    model_call: ModelCallEvidence | None = None
    extraction: ExtractionEvidence | None = None
    document_parse: DocumentParseEvidence | None = None
    dedup: DedupEvidence | None = None
    content_drift: ContentDrift | None = None
    rendered: RenderResult | None = None
    encoding_call: EncodingCall | None = None
    similarity: SimilarityEvidence | None = None
    reference: ReferenceDecision | None = None
    reference_query: ReferenceQuery | None = None
    source_session: SourceSessionUse | None = None

    @model_validator(mode="after")
    def identity_evidence(self) -> "LedgerRow":
        if (self.event == "reference_query") != (self.reference_query is not None):
            raise ValueError("reference query events require their explicit source binding")
        if self.reference_query is not None and (
            self.url != self.reference_query.source.url or self.query != self.reference_query.query
        ):
            raise ValueError("reference query must match its ledger source and query")
        if (self.event == "reference") != (self.reference is not None):
            raise ValueError("reference events require their explicit native source decision")
        if self.reference is not None and self.url != self.reference.reference.target_url:
            raise ValueError("reference event URL must match its target")
        if (self.event == "encoding") != (self.encoding_call is not None):
            raise ValueError("encoding events require their explicit call evidence")
        if (self.event == "scoring") != (self.similarity is not None):
            raise ValueError("scoring events require their native similarity evidence")
        if self.encoding_call is not None:
            service = self.encoding_call.service
            if self.model is None or (self.model.model_id, self.model.revision) != (
                service.model_id,
                service.revision,
            ):
                raise ValueError("encoding event identity must match its call evidence")
            if (self.encoding_call.outcome == "success") != (self.refusal is None):
                raise ValueError("encoding refusal must match its call outcome")
        if self.event == "render":
            if (self.rendered is None) == (self.refusal is None):
                raise ValueError("render event requires either its result or a refusal")
            if self.rendered is not None and self.url != self.rendered.source_url:
                raise ValueError("render event must identify its source occurrence")
        elif self.rendered is not None:
            raise ValueError("render evidence belongs to its render event")
        if self.dedup is not None and self.event != "duplicate":
            raise ValueError("dedup evidence belongs only to a duplicate observation")
        if (self.content_drift is not None) != (self.event == "content_drift"):
            raise ValueError("content drift events require their explicit revision evidence")
        return self


class Receipt(Record):
    fetches: NonNegative
    bytes_read: NonNegative
    judge_calls: NonNegative
    encoding_calls: NonNegative = 0
    encoding_chars: NonNegative = 0
    accepted_documents: NonNegative
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    stop_reason: StopReason
    effective_config: ChimeraConfig
    judge: ModelIdentity


class Harvest(Record):
    schema_version: Literal["chimera.harvest/1"] = Field(alias="schema")
    goal: Goal
    documents: tuple[Document, ...]
    ledger: tuple[LedgerRow, ...]
    receipt: Receipt
    graph: GraphSnapshot | None = None

    @property
    def source_documents(self) -> tuple[Document, ...]:
        return tuple(item for doc in self.documents for item in doc.evidence_sources())

    @model_validator(mode="after")
    def consistent(self) -> "Harvest":
        from chimera.references import validate_reference_ledger

        validate_reference_ledger(self)
        for row in self.ledger:
            if row.source_session is not None:
                if row.event != "fetch" or row.url is None:
                    raise ValueError("source session metadata belongs to a source fetch")
                row.source_session.validate_policy(
                    self.receipt.effective_config.source_sessions, row.url
                )
            if row.rendered is not None:
                for resource in row.rendered.resources:
                    if resource.source_session is not None:
                        resource.source_session.validate_policy(
                            self.receipt.effective_config.source_sessions, resource.url
                        )
        if tuple(row.sequence for row in self.ledger) != tuple(range(len(self.ledger))):
            raise ValueError("ledger sequence must be contiguous")
        if self.receipt.fetches != sum(row.event == "fetch" for row in self.ledger):
            raise ValueError("fetch count does not match ledger")
        if self.receipt.bytes_read != sum(row.bytes_read for row in self.ledger):
            raise ValueError("byte spend does not match ledger")
        if self.receipt.judge_calls != sum(
            row.event in {"verdict", "grade", "plan", "assessment", "answer", "review"}
            for row in self.ledger
        ):
            raise ValueError("judge spend does not match ledger")
        if self.receipt.accepted_documents != len(self.documents):
            raise ValueError("accepted count does not match harvest")
        encoding = tuple(row.encoding_call for row in self.ledger if row.encoding_call is not None)
        if self.receipt.encoding_calls != len(encoding) or self.receipt.encoding_chars != sum(
            call.input_chars for call in encoding
        ):
            raise ValueError("encoding spend does not match ledger")
        scoring_policy = self.receipt.effective_config.scoring
        if encoding and (
            scoring_policy is None
            or any(call.service != scoring_policy.encoder for call in encoding)
        ):
            raise ValueError("encoding ledger must bind the effective service configuration")
        if scoring_policy is not None and (
            self.receipt.encoding_calls > scoring_policy.encoding_call_budget
            or self.receipt.encoding_chars > scoring_policy.encoding_char_budget
        ):
            raise ValueError("encoding spend exceeds the shared run budget")
        native_sources = {
            hashlib.sha256(document.extracted.text.encode()).hexdigest(): document.extracted
            for document in self.source_documents
        }
        for row in self.ledger:
            if row.similarity is not None and (
                scoring_policy is None
                or row.similarity.references_sha256 != scoring_policy.references_sha256
            ):
                raise ValueError(
                    "similarity observations must bind their configured reference vectors"
                )
            if row.similarity is not None:
                similarity = row.similarity
                if similarity.goal_sha256 != hashlib.sha256(self.goal.text.encode()).hexdigest():
                    raise ValueError("similarity observations must bind this run's original intent")
                if scoring_policy is None:
                    raise ValueError("similarity requires its effective scoring policy")
                for link in similarity.links:
                    expected = scoring_policy.keyword_weight * link.keyword_score + (
                        1.0 - scoring_policy.keyword_weight
                    ) * max(0.0, link.cosine)
                    if link.score != expected:
                        raise ValueError(
                            "frontier scores must reconcile to cosine and keyword policy"
                        )
                native = native_sources.get(similarity.text_sha256)
                if native is not None:
                    if len(native.text) != similarity.total_chars or any(
                        hashlib.sha256(native.text[window.start : window.end].encode()).hexdigest()
                        != window.text_sha256
                        for window in similarity.windows
                    ):
                        raise ValueError("similarity observations must bind retained native spans")
        if any(doc.verdict.decision != "accept" for doc in self.documents):
            raise ValueError("only accepted documents belong in harvest")
        if len({doc.sha256 for doc in self.documents}) != len(self.documents):
            raise ValueError("cluster representatives must have distinct raw content identities")
        for row in self.ledger:
            if row.rendered is not None:
                render_config = self.receipt.effective_config
                if render_config.browser is None:
                    raise ValueError("render ledger requires its configured policy")
                row.rendered.validate_policy(
                    render_config.browser,
                    max_redirects=render_config.http.max_redirects if render_config.http else 0,
                )
        for doc in self.documents:
            doc.validate_policy(self.receipt.effective_config)
            if doc.occurrences:
                from chimera.content_dedup import ContentIndex

                policy = self.receipt.effective_config.dedup
                if policy is None:
                    raise ValueError("retained dedup evidence requires its effective policy")
                index = ContentIndex(policy)
                index.add(doc)
                for item in doc.occurrences:
                    if index.match(item) != item.dedup:
                        raise ValueError("duplicate similarity evidence does not match its content")
                    item.validate_policy(self.receipt.effective_config)
        if any(row.dedup is not None or row.content_drift is not None for row in self.ledger):
            from chimera.content_dedup import ContentIndex, fingerprint

            identity_policy = self.receipt.effective_config.dedup
            if identity_policy is None:
                raise ValueError("identity ledger requires its effective configuration")
            sources = {(doc.sha256, doc.url): doc for doc in self.source_documents}
            representatives = {doc.sha256: doc for doc in self.documents}
            revisions = {
                (doc.sha256, fingerprint(doc, identity_policy).canonical_url): doc
                for doc in self.source_documents
            }
            for row in self.ledger:
                if row.dedup is not None:
                    if row.url is None:
                        raise ValueError("duplicate ledger must identify its source URL")
                    current = sources.get((row.dedup.current.source_sha256, row.url))
                    representative = representatives.get(row.dedup.representative_sha256)
                    if current is None or representative is None:
                        raise ValueError("duplicate ledger must reference retained source evidence")
                    verification = ContentIndex(identity_policy)
                    verification.add(representative)
                    if verification.match(current) != row.dedup:
                        raise ValueError("duplicate ledger similarity evidence was altered")
                if row.content_drift is not None:
                    drift = row.content_drift
                    previous = revisions.get((drift.previous_sha256, drift.canonical_url))
                    now = revisions.get((drift.current_sha256, drift.canonical_url))
                    if previous is None or now is None or row.url != now.url:
                        raise ValueError("drift ledger requires both retained source revisions")
                    verification = ContentIndex(identity_policy)
                    verification.add(previous)
                    if verification.drift(now) != drift:
                        raise ValueError("content drift evidence was altered")
        graph_enabled = (
            self.receipt.effective_config.graph is not None
            and self.receipt.effective_config.graph.enabled
        )
        if graph_enabled != (self.graph is not None):
            raise ValueError("enabled graph cannot be silently absent from harvest")
        if self.graph is not None:
            config = self.receipt.effective_config.graph
            if config is None or self.graph.config_digest != config.content_digest():
                raise ValueError("graph must bind the effective configuration")
        return self
