"""Immutable validated package objects; TAIPAN dataclass conversion belongs to C4."""

import hashlib
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ghimera.browser_types import RenderResult
from ghimera.challenge_types import ChallengeEvidence
from ghimera.config import GhimeraConfig, Probability
from ghimera.dedup_types import ContentDrift, DedupEvidence
from ghimera.document_types import DocumentLayout, DocumentParseEvidence
from ghimera.embedding_types import EncodingCall, IntentReferenceEvidence
from ghimera.extraction_attempts import HtmlExtractionAttempt, validate_chain
from ghimera.extraction_types import ExtractionEvidence
from ghimera.graph_planning_types import PlanningGraph
from ghimera.graph_types import GraphSnapshot
from ghimera.human_browser_types import AssistanceObservation, BrowserCapture, HumanBrowserEvidence
from ghimera.local_input_types import LocalInputEvidence
from ghimera.model_types import ModelCallEvidence
from ghimera.reference_types import DocumentReference, ReferenceDecision, ReferenceQuery
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.scoring_types import SimilarityEvidence
from ghimera.semantic_types import (
    FactorizedSemanticReview,
    GroundedSemanticReview,
    ReviewSelection,
    SemanticRefusal,
    SemanticReview,
    SemanticWindow,
)
from ghimera.source_session_types import SourceSessionUse
from ghimera.transport_types import TransportEvidence

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
    scope: Scope | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("headers")
    @classmethod
    def one_line(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        if any("\r" in text or "\n" in text for _, text in value):
            raise ValueError("request metadata must be single-line HTTP headers")
        return value


class Page(Record):
    url: NonEmpty
    final_url: NonEmpty
    status: Annotated[int, Field(strict=True, ge=100, le=599)] | None
    content_type: NonEmpty
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    revalidated: bool = False
    transport: TransportEvidence | None = None
    rendered: RenderResult | None = None
    source_session: SourceSessionUse | None = None
    challenge_use: ChallengeEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    local_input: LocalInputEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    human_browser: HumanBrowserEvidence | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def rendering_binding(self) -> "Page":
        if (self.status is None) != (self.human_browser is not None):
            raise ValueError("only explicit browser DOM acquisition has no HTTP status")
        if self.human_browser is not None:
            BrowserCapture(dom=self.body, evidence=self.human_browser)
            if (
                self.url != self.human_browser.request_url
                or self.final_url != self.human_browser.final_url
                or self.content_type != self.human_browser.content_type
                or self.headers
                or self.revalidated
                or any(
                    value is not None
                    for value in (
                        self.transport,
                        self.rendered,
                        self.source_session,
                        self.challenge_use,
                        self.local_input,
                    )
                )
            ):
                raise ValueError(
                    "browser DOM cannot impersonate an HTTP response or isolated render"
                )
        if self.local_input is not None and (
            self.url != self.local_input.source_id
            or self.final_url != self.url
            or self.local_input.sha256 != hashlib.sha256(self.body).hexdigest()
            or self.local_input.size_bytes != len(self.body)
            or self.local_input.content_type != self.content_type
            or any(
                value is not None
                for value in (
                    self.transport,
                    self.rendered,
                    self.source_session,
                    self.challenge_use,
                )
            )
        ):
            raise ValueError("local parser input must bind its snapshot without network evidence")
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
    challenge_use: ChallengeEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    local_input: LocalInputEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    human_browser: HumanBrowserEvidence | None = Field(default=None, exclude_if=lambda v: v is None)

    def validate_policy(self, config: GhimeraConfig) -> None:
        if self.human_browser is not None:
            self.human_browser.validate_policy(config.human_browser)
        if self.local_input is not None:
            self.local_input.validate_policy(config.local_inputs)
        if self.challenge_use is not None:
            self.challenge_use.validate_policy(config.challenges, self.url)
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
        if evidence is not None and config.extraction is not None:
            evidence.validate_policy(config.extraction)
        parsed = self.extracted.document_parse
        if parsed is not None and (
            config.document_extraction is None
            or parsed.config_digest != config.document_extraction.content_digest()
        ):
            raise ValueError("document conversion must bind the effective run configuration")
        if parsed is not None and config.document_extraction is not None:
            media_policy = config.document_extraction.media
            if (parsed.media is None) != (media_policy is None):
                raise ValueError("document conversion must retain its configured media evidence")
            if parsed.media is not None and media_policy is not None:
                parsed.media.validate_policy(media_policy, self.raw)

    @model_validator(mode="after")
    def source_binding(self) -> "DocumentSource":
        if self.human_browser is not None:
            BrowserCapture(dom=self.raw, evidence=self.human_browser)
            if self.human_browser.final_url != self.url or any(
                value is not None
                for value in (
                    self.transport,
                    self.rendered,
                    self.source_session,
                    self.challenge_use,
                    self.local_input,
                )
            ):
                raise ValueError(
                    "browser-observed sources retain their distinct acquisition evidence"
                )
        if self.local_input is not None and (
            self.local_input.source_id != self.url
            or self.local_input.sha256 != self.sha256
            or self.local_input.size_bytes != len(self.raw)
            or any(
                value is not None
                for value in (
                    self.transport,
                    self.rendered,
                    self.source_session,
                    self.challenge_use,
                )
            )
        ):
            raise ValueError("local source provenance must bind its retained original bytes")
        if self.url.startswith("urn:ghimera:local:") and self.local_input is None:
            raise ValueError("local sources require explicit import provenance")
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
        if (
            self.extracted.document_parse is not None
            and self.extracted.document_parse.media is not None
        ):
            self.extracted.document_parse.media.validate_source(self.raw)
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
        "extraction_attempt",
        "content_drift",
        "render",
        "encoding",
        "scoring",
        "intent_reference",
        "reference",
        "reference_query",
        "challenge",
        "local_input",
        "semantic",
        "semantic_review",
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
    search_response_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    model_call: ModelCallEvidence | None = None
    extraction: ExtractionEvidence | None = None
    extraction_attempt: HtmlExtractionAttempt | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    document_parse: DocumentParseEvidence | None = None
    dedup: DedupEvidence | None = None
    content_drift: ContentDrift | None = None
    rendered: RenderResult | None = None
    encoding_call: EncodingCall | None = None
    similarity: SimilarityEvidence | None = None
    intent_reference: IntentReferenceEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    reference: ReferenceDecision | None = None
    reference_query: ReferenceQuery | None = None
    source_session: SourceSessionUse | None = None
    challenge: ChallengeEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    challenge_use: ChallengeEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    local_input: LocalInputEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    semantic_window: SemanticWindow | None = Field(default=None, exclude_if=lambda v: v is None)
    semantic_refusal: SemanticRefusal | None = Field(default=None, exclude_if=lambda v: v is None)
    semantic_review: GroundedSemanticReview | FactorizedSemanticReview | SemanticReview | None = (
        Field(default=None, exclude_if=lambda v: v is None)
    )
    semantic_review_selection: ReviewSelection | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    planning_graph: PlanningGraph | None = Field(default=None, exclude_if=lambda v: v is None)
    human_browser: HumanBrowserEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    human_assistance: tuple[AssistanceObservation, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )

    @model_validator(mode="after")
    def identity_evidence(self) -> "LedgerRow":
        if (
            self.route == "human_browser_dom"
            or self.human_browser is not None
            or self.human_assistance
        ):
            if (
                self.event != "fetch"
                or self.status is not None
                or any(
                    value is not None
                    for value in (
                        self.transport,
                        self.rendered,
                        self.source_session,
                        self.challenge_use,
                        self.local_input,
                    )
                )
            ):
                raise ValueError(
                    "browser capture observations are not HTTP/isolated-render evidence"
                )
            if self.human_browser is not None and (
                self.refusal is not None
                or self.human_assistance
                or self.url != self.human_browser.request_url
                or self.bytes_read != self.human_browser.collector_dom_bytes_read
            ):
                raise ValueError("successful browser capture must retain its exact DOM spend")
            if self.human_assistance and self.refusal is None:
                raise ValueError("failed browser assistance requires its terminal refusal")
            if self.refusal is None and self.human_browser is None:
                raise ValueError("successful browser route cannot discard capture evidence")
        if self.event == "semantic_review":
            if (self.semantic_review is None) == (self.refusal is None):
                raise ValueError("semantic review requires its assessment or refusal")
            if self.semantic_review is not None and (
                self.model_call is None
                or self.model_call != self.semantic_review.model_call
                or self.model_call.task != "semantic_review"
                or self.model_call.outcome != "success"
            ):
                raise ValueError("semantic review must bind its actual model call")
        elif self.semantic_review is not None:
            raise ValueError("semantic review belongs to its review call observation")
        if self.planning_graph is not None and self.event != "plan":
            raise ValueError("planning graph belongs to its observed planning call")
        if self.event == "semantic":
            if (self.semantic_window is None) == (self.refusal is None):
                raise ValueError("semantic calls require a source-bound projection or refusal")
            if self.semantic_window is not None and (
                self.url != self.semantic_window.source_url
                or self.model_call != self.semantic_window.proposal.model_call
            ):
                raise ValueError("semantic ledger metadata must match its window and call")
        elif self.semantic_window is not None:
            raise ValueError("semantic projections belong to their call observation")
        if self.semantic_refusal is not None and (
            self.event != "semantic"
            or self.refusal is None
            or self.semantic_window is not None
            or self.url != self.semantic_refusal.source_url
            or (
                self.semantic_refusal.proposal is not None
                and self.model_call != self.semantic_refusal.proposal.model_call
            )
        ):
            raise ValueError("semantic refusal must bind its failed window and original extractor")
        if self.event == "local_input":
            if (self.local_input is None) == (self.refusal is None):
                raise ValueError("local import needs snapshot evidence or a refusal")
            if self.status is not None or self.transport is not None:
                raise ValueError("local imports are not HTTP fetches")
            if self.local_input is not None and (
                self.url != self.local_input.source_id
                or self.bytes_read != self.local_input.size_bytes
            ):
                raise ValueError("import observations must bind the original byte snapshot")
        elif self.local_input is not None:
            raise ValueError("local import metadata belongs only to its input observation")
        if self.challenge_use is not None and self.event != "fetch":
            raise ValueError("clearance use belongs to its source fetch")
        if self.event == "challenge":
            if (self.challenge is None) == (self.refusal is None):
                raise ValueError("challenge attempt needs clearance evidence or refusal")
        elif self.challenge is not None:
            raise ValueError("challenge metadata belongs to its attempt")
        if self.search_response_sha256 is not None and (
            self.event != "fetch"
            or self.route is None
            or not self.route.startswith("search:")
            or self.refusal is not None
            or self.query is None
        ):
            raise ValueError("search response digest requires a successful search fetch")
        if (self.event == "intent_reference") != (self.intent_reference is not None):
            raise ValueError(
                "intent reference events require their prepared vectors and call binding"
            )
        if self.intent_reference is not None:
            references = self.intent_reference.references
            if (
                self.url is not None
                or self.refusal is not None
                or self.model is None
                or self.model.location != "self_hosted"
                or (self.model.model_id, self.model.revision)
                != (references.model_id, references.revision)
            ):
                raise ValueError("intent reference metadata must identify its self-hosted encoder")
        if (self.event == "extraction_attempt") != (self.extraction_attempt is not None):
            raise ValueError("parse attempt events require their bounded source observation")
        if self.extraction_attempt is not None and (
            self.url != self.extraction_attempt.source_url
            or self.refusal != self.extraction_attempt.refusal
            or self.latency_seconds != self.extraction_attempt.latency_seconds
        ):
            raise ValueError("parse attempt ledger metadata must match its observation")
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
    effective_config: GhimeraConfig
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
        from ghimera.human_browser_validation import validate_harvest as validate_browser_harvest
        from ghimera.references import validate_reference_ledger
        from ghimera.scoring_validation import validate_reference_rows

        validate_reference_ledger(self)
        validate_browser_harvest(self)
        inputs = tuple(row for row in self.ledger if row.event == "local_input")
        input_policy = self.receipt.effective_config.local_inputs
        if inputs and (
            input_policy is None
            or len(inputs) > input_policy.max_files_per_run
            or sum(row.bytes_read for row in inputs) > input_policy.max_total_bytes
        ):
            raise ValueError("local imports exceed the effective input policy")
        for row in inputs:
            if row.local_input is not None:
                row.local_input.validate_policy(input_policy)
        for document in self.source_documents:
            if document.local_input is not None and not any(
                row.local_input == document.local_input for row in inputs
            ):
                raise ValueError("local documents must retain their import observation")
        parsing: dict[tuple[str, str, str | None], list[HtmlExtractionAttempt]] = {}
        for row in self.ledger:
            for clearance in (row.challenge, row.challenge_use):
                if clearance is not None:
                    if row.url is None:
                        raise ValueError("clearance observation requires its source URL")
                    clearance.validate_policy(self.receipt.effective_config.challenges, row.url)
            if row.extraction_attempt is not None:
                attempt = row.extraction_attempt
                extraction_policy = self.receipt.effective_config.extraction
                if extraction_policy is None:
                    raise ValueError("parse attempts require their effective extraction policy")
                attempt.validate_policy(extraction_policy)
                parsing.setdefault(
                    (attempt.source_url, attempt.source_sha256, attempt.rendered_sha256), []
                ).append(attempt)
            if row.extraction is not None:
                extraction_policy = self.receipt.effective_config.extraction
                if extraction_policy is None:
                    raise ValueError("extraction ledger evidence requires its effective policy")
                row.extraction.validate_policy(extraction_policy)
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
        for chain in parsing.values():
            validate_chain(tuple(chain))
            if chain[-1].outcome == "success" and not any(
                row.extraction is not None and row.extraction.attempts == tuple(chain)
                for row in self.ledger
            ):
                raise ValueError("successful parsing must retain its extraction result")
        for row in self.ledger:
            if row.extraction is not None and row.extraction.attempts:
                evidence = row.extraction
                observed = parsing.get(
                    (evidence.source_url, evidence.source_sha256, evidence.rendered_sha256), []
                )
                if tuple(observed) != evidence.attempts:
                    raise ValueError(
                        "extraction results must preserve every prior attempt ledger row"
                    )
        for doc in self.source_documents:
            doc_evidence = doc.extracted.extraction
            if (
                doc_evidence is not None
                and doc_evidence.attempts
                and tuple(
                    parsing.get(
                        (
                            doc_evidence.source_url,
                            doc_evidence.source_sha256,
                            doc_evidence.rendered_sha256,
                        ),
                        [],
                    )
                )
                != doc_evidence.attempts
            ):
                raise ValueError("document parsing/recovery must reconcile with the run ledger")
        if tuple(row.sequence for row in self.ledger) != tuple(range(len(self.ledger))):
            raise ValueError("ledger sequence must be contiguous")
        validate_reference_rows(self.receipt.effective_config, self.goal.text, self.ledger)
        if self.receipt.fetches != sum(row.event in {"fetch", "challenge"} for row in self.ledger):
            raise ValueError("fetch count does not match ledger")
        challenge_rows = tuple(row for row in self.ledger if row.event == "challenge")
        challenge_policy = self.receipt.effective_config.challenges
        if challenge_rows and (
            challenge_policy is None or len(challenge_rows) > challenge_policy.max_attempts_per_run
        ):
            raise ValueError("challenge attempts exceed their configured run budget")
        if self.receipt.bytes_read != sum(row.bytes_read for row in self.ledger):
            raise ValueError("byte spend does not match ledger")
        if self.receipt.judge_calls != sum(
            row.event
            in {
                "verdict",
                "grade",
                "plan",
                "assessment",
                "answer",
                "review",
                "semantic",
                "semantic_review",
            }
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
                from ghimera.content_dedup import ContentIndex

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
            from ghimera.content_dedup import ContentIndex, fingerprint

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
            for node in self.graph.nodes:
                if node.local_input is not None:
                    node.local_input.validate_policy(input_policy)
                    if not any(row.local_input == node.local_input for row in inputs):
                        raise ValueError(
                            "local graph documents must retain their input observation"
                        )
        from ghimera.graph_planning import validate_rows as validate_planning_rows
        from ghimera.semantic_graph import validate_harvest

        try:
            validate_harvest(self)
            validate_planning_rows(self.receipt.effective_config, self.ledger)
        except GhimeraRefused:
            raise ValueError("semantic source projection cannot be revalidated") from None
        return self
