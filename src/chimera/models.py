"""Immutable validated package objects; TAIPAN dataclass conversion belongs to C4."""

import hashlib
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chimera.config import ChimeraConfig, Probability
from chimera.dedup_types import ContentDrift, DedupEvidence
from chimera.document_types import DocumentLayout, DocumentParseEvidence
from chimera.extraction_types import ExtractionEvidence
from chimera.graph_types import GraphSnapshot
from chimera.model_types import ModelCallEvidence
from chimera.refusals import RefusalCode
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

    @model_validator(mode="after")
    def text_binding(self) -> "Extracted":
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

    def validate_policy(self, config: ChimeraConfig) -> None:
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
        digest = hashlib.sha256(self.raw).hexdigest()
        if self.sha256 != digest:
            raise ValueError("document digest must bind retained source bytes")
        if self.extracted.extraction is not None and (
            self.extracted.extraction.source_sha256 != digest
            or self.extracted.extraction.source_url != self.url
        ):
            raise ValueError("extraction evidence must bind this source occurrence")
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

    @model_validator(mode="after")
    def identity_evidence(self) -> "LedgerRow":
        if self.dedup is not None and self.event != "duplicate":
            raise ValueError("dedup evidence belongs only to a duplicate observation")
        if (self.content_drift is not None) != (self.event == "content_drift"):
            raise ValueError("content drift events require their explicit revision evidence")
        return self


class Receipt(Record):
    fetches: NonNegative
    bytes_read: NonNegative
    judge_calls: NonNegative
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
        if any(doc.verdict.decision != "accept" for doc in self.documents):
            raise ValueError("only accepted documents belong in harvest")
        if len({doc.sha256 for doc in self.documents}) != len(self.documents):
            raise ValueError("cluster representatives must have distinct raw content identities")
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
