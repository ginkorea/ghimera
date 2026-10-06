"""Immutable validated package objects; TAIPAN dataclass conversion belongs to C4."""

import hashlib
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chimera.config import ChimeraConfig, Probability
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

    @model_validator(mode="after")
    def text_binding(self) -> "Extracted":
        if (
            self.extraction is not None
            and self.extraction.text_sha256 != hashlib.sha256(self.text.encode()).hexdigest()
        ):
            raise ValueError("extraction evidence must bind its native text")
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


class Document(Record):
    url: NonEmpty
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    raw: bytes
    extracted: Extracted
    verdict: Verdict
    duplicate_urls: tuple[str, ...] = ()
    transport: TransportEvidence | None = None

    @model_validator(mode="after")
    def source_binding(self) -> "Document":
        digest = hashlib.sha256(self.raw).hexdigest()
        if self.sha256 != digest:
            raise ValueError("document digest must bind retained source bytes")
        if self.extracted.extraction is not None and (
            self.extracted.extraction.source_sha256 != digest
            or self.extracted.extraction.source_url != self.url
        ):
            raise ValueError("extraction evidence must bind this source occurrence")
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
        for doc in self.documents:
            evidence = doc.extracted.extraction
            extraction_config = self.receipt.effective_config.extraction
            if evidence is not None and (
                extraction_config is None
                or evidence.config_digest != extraction_config.content_digest()
            ):
                raise ValueError("extraction must bind the effective run configuration")
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
