"""Neutral research graph wire and configuration; no platform vocabulary guessed."""

import hashlib
import json
import tomllib
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.human_browser_types import BrowserSourceEvidence
from ghimera.local_input_types import LocalInputEvidence
from ghimera.model_types import IdentityCallEvidence
from ghimera.source_refresh_types import SourceRefreshUse
from ghimera.transport_types import TransportEvidence
from ghimera.visual_types import ImageRegion

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
Text = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]
Count = Annotated[int, Field(strict=True, ge=0)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class GraphRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    def content_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


class GraphRole(GraphRecord):
    name: Name
    kind: Name


class GraphRelation(GraphRecord):
    name: Name
    predicate: Name
    source_roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    target_roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    semantic: bool


class IdentityResolutionConfig(GraphRecord):
    schema_version: Literal["ghimera.identity-resolution/1"] = Field(alias="schema")
    roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    max_decisions: Positive
    max_members_per_decision: Annotated[int, Field(strict=True, ge=2)]
    max_evidence_per_decision: Positive
    max_reason_chars: Positive

    @model_validator(mode="after")
    def distinct(self) -> "IdentityResolutionConfig":
        if len(set(self.roles)) != len(self.roles) or set(self.roles) & {
            "intent",
            "source",
            "document",
        }:
            raise ValueError("resolution requires distinct source-local entity roles")
        return self


class VisualProjectionConfig(GraphRecord):
    schema_version: Literal["ghimera.visual-projection/1"] = Field(alias="schema")
    observation_role: Name
    evidence_rule: Name
    max_spans_per_image: Positive
    max_reading_chars: Positive


class GraphConfig(GraphRecord):
    schema_version: Literal["chimera.graph-config/1"] = Field(alias="schema")
    enabled: bool
    profile: Name
    profile_version: Text
    identity_namespace: Text
    # TAIPAN projection is compiled/validated by its adapter, not by this core.
    projection_mode: Literal["research"]
    sink_path: Path
    max_batch_bytes: Positive
    max_nodes: Positive
    max_edges: Positive
    capture_semantics: bool
    semantic_min_confidence: Confidence
    source_audiences: Annotated[tuple[Text, ...], Field(min_length=1)]
    handling_labels: tuple[Text, ...]
    roles: Annotated[tuple[GraphRole, ...], Field(min_length=1)]
    relations: tuple[GraphRelation, ...]
    identity_resolution: IdentityResolutionConfig | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    visual_projection: VisualProjectionConfig | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @classmethod
    def from_toml(cls, path: Path) -> "GraphConfig":
        with path.open("rb") as stream:
            return cls.model_validate(tomllib.load(stream))

    @model_validator(mode="after")
    def coherent(self) -> "GraphConfig":
        roles = {role.name for role in self.roles}
        if (
            self.identity_resolution is not None
            and not set(self.identity_resolution.roles) <= roles
        ):
            raise ValueError("identity resolution names an unknown graph role")
        if len(roles) != len(self.roles) or not {"intent", "source", "document"} <= roles:
            raise ValueError("graph requires unique intent/source/document roles")
        names = {relation.name for relation in self.relations}
        if len(names) != len(self.relations) or not {"discovered", "retrieved"} <= names:
            raise ValueError("graph requires unique discovered/retrieved rules")
        for relation in self.relations:
            if not set(relation.source_roles + relation.target_roles) <= roles:
                raise ValueError("relation names an unknown endpoint role")
            if relation.name in {"discovered", "retrieved"} and relation.semantic:
                raise ValueError("discovery/retrieval are trace, not semantic claims")
            if relation.name == "discovered" and (
                "source" not in relation.source_roles
                or not {"intent", "document"} <= set(relation.target_roles)
            ):
                raise ValueError("discovered rule must support source to intent/document")
            if relation.name == "retrieved" and (
                "document" not in relation.source_roles or "source" not in relation.target_roles
            ):
                raise ValueError("retrieved rule must support document to source")
        if self.visual_projection is not None:
            visual = self.visual_projection
            rule = next(
                (item for item in self.relations if item.name == visual.evidence_rule), None
            )
            if (
                visual.observation_role in {"intent", "source", "document"}
                or visual.observation_role not in roles
                or rule is None
                or rule.semantic
                or "document" not in rule.source_roles
                or visual.observation_role not in rule.target_roles
            ):
                raise ValueError(
                    "visual projection requires an explicit observation role and trace rule"
                )
        if not self.sink_path.is_absolute():
            raise ValueError("graph sink path must be explicit and absolute")
        if not self.identity_namespace.strip() or not self.profile_version.strip():
            raise ValueError("graph profile/identity namespace must be nonblank")
        return self


class GraphReadingPage(GraphRecord):
    page_index: Count
    start: Count
    end: Positive
    text_sha256: Digest
    image_sha256: Digest
    transcription_call_sha256: Digest
    review_call_sha256: Digest

    @model_validator(mode="after")
    def nonempty(self) -> "GraphReadingPage":
        if self.end <= self.start:
            raise ValueError("graph reading pages require nonempty selected text")
        return self


class GraphPdfReading(GraphRecord):
    """Compact references to retained page/model evidence, never duplicate pixels."""

    schema_version: Literal["ghimera.graph-pdf-reading/1"] = Field(alias="schema")
    source_sha256: Digest
    text_sha256: Digest
    config_sha256: Digest
    pages: Annotated[tuple[GraphReadingPage, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def ordered(self) -> "GraphPdfReading":
        offset = 0
        for index, page in enumerate(self.pages):
            if page.page_index != index or page.start != offset:
                raise ValueError("graph readings require complete ordered source pages")
            offset = page.end + 2
        return self

    def validate_text(self, text: str) -> None:
        if (
            self.text_sha256 != hashlib.sha256(text.encode()).hexdigest()
            or self.pages[-1].end != len(text)
            or any(
                page.text_sha256 != hashlib.sha256(text[page.start : page.end].encode()).hexdigest()
                or (page.page_index and text[page.start - 2 : page.start] != "\n\n")
                for page in self.pages
            )
        ):
            raise ValueError("graph reading must bind the exact selected text and page spans")

    def cited_pages(self, start: int, end: int) -> tuple[int, ...]:
        return tuple(p.page_index for p in self.pages if start < p.end and end > p.start)


class GraphRetainedOrigin(GraphRecord):
    """A current corpus query admitted an old representation, not a new fetch."""

    schema_version: Literal["ghimera.graph-retained-origin/1"] = Field(alias="schema")
    document_sha256: Digest
    corpus_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    config_sha256: Digest
    generation: Count
    bundle_sha256: Digest
    query_sha256: Digest
    encoding_call_sha256: Digest
    source_mode: Literal["retained_snapshot"] = "retained_snapshot"
    source_age: Literal["unknown"] = "unknown"


class GraphVisualSpan(GraphRecord):
    start: Count
    end: Positive
    quote: Text
    basis: Literal["image_ocr", "reviewed_visual_claim"]
    regions: Annotated[tuple[ImageRegion, ...], Field(min_length=1)]


class GraphVisualReading(GraphRecord):
    """Compact derived reading; geometry and model references stay source-bound."""

    schema_version: Literal["ghimera.graph-visual-reading/1"] = Field(alias="schema")
    image_sha256: Digest
    parent_sha256: Digest
    parent_url: Text
    source_url: Text
    config_sha256: Digest
    ocr_sha256: Digest
    interpretation_sha256: Digest | None
    text: Text
    spans: Annotated[tuple[GraphVisualSpan, ...], Field(min_length=1)]
    pdf_page_index: Count | None = Field(default=None, exclude_if=lambda v: v is None)
    pdf_region: ImageRegion | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def bound(self) -> "GraphVisualReading":
        cursor = 0
        for span in self.spans:
            if (
                span.start != cursor
                or span.end != span.start + len(span.quote)
                or self.text[span.start : span.end] != span.quote
                or (cursor and self.text[cursor - 2 : cursor] != "\n\n")
                or (span.basis == "reviewed_visual_claim" and self.interpretation_sha256 is None)
            ):
                raise ValueError("visual spans require exact ordered derived text and review")
            cursor = span.end + 2
        if cursor - 2 != len(self.text) or (self.pdf_page_index is None) != (
            self.pdf_region is None
        ):
            raise ValueError("visual reading requires complete text and paired PDF crop anchors")
        return self


class GraphVisualAnchor(GraphRecord):
    reading_sha256: Digest
    image_sha256: Digest
    span_index: Count
    regions: Annotated[tuple[ImageRegion, ...], Field(min_length=1)]


class GraphNode(GraphRecord):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*:[0-9a-f]{64}$")]
    role: Name
    kind: Name
    identity: Text
    label: Text
    revision: Text
    audiences: Annotated[tuple[Text, ...], Field(min_length=1)]
    handling_labels: tuple[Text, ...]
    source_url: str | None = None
    content_sha256: Digest | None = None
    text_sha256: Digest | None = None
    text: str | None = None
    # Absent evidence must not change the canonical bytes of existing /1 journals.
    transport: TransportEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    local_input: LocalInputEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    source_refresh: SourceRefreshUse | None = Field(default=None, exclude_if=lambda v: v is None)
    human_browser: BrowserSourceEvidence | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    pdf_reading: GraphPdfReading | None = Field(default=None, exclude_if=lambda v: v is None)
    retained_source: GraphRetainedOrigin | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    visual_readings: tuple[GraphVisualReading, ...] = Field(default=(), exclude_if=lambda v: not v)

    @staticmethod
    def document_identity(
        source_url: str,
        content_sha256: str,
        text_sha256: str,
        revision: str,
        *,
        human_browser: BrowserSourceEvidence | None = None,
        pdf_reading: GraphPdfReading | None = None,
        retained_source: GraphRetainedOrigin | None = None,
        source_refresh: SourceRefreshUse | None = None,
        visual_readings: tuple[GraphVisualReading, ...] = (),
    ) -> str:
        identity = f"{len(source_url)}:{source_url}:{content_sha256}:{text_sha256}:{revision}"
        if human_browser is not None:
            identity += f":{human_browser.acquisition}:{human_browser.capture_id}"
        if pdf_reading is not None:
            identity += f":{pdf_reading.content_digest()}"
        if retained_source is not None:
            identity += f":{retained_source.content_digest()}"
        if source_refresh is not None:
            identity += f":{hashlib.sha256(source_refresh.model_dump_json().encode()).hexdigest()}"
        if visual_readings:
            identity += ":visual:" + ":".join(item.content_digest() for item in visual_readings)
        return identity

    @model_validator(mode="after")
    def content_bound(self) -> "GraphNode":
        if self.visual_readings and (
            self.role != "document"
            or len({item.image_sha256 for item in self.visual_readings})
            != len(self.visual_readings)
            or any(
                item.parent_sha256 != self.content_sha256 or item.parent_url != self.source_url
                for item in self.visual_readings
            )
            or self.identity
            != self.document_identity(
                self.source_url or "",
                self.content_sha256 or "",
                self.text_sha256 or "",
                self.revision,
                human_browser=self.human_browser,
                pdf_reading=self.pdf_reading,
                retained_source=self.retained_source,
                source_refresh=self.source_refresh,
                visual_readings=self.visual_readings,
            )
        ):
            raise ValueError("visual graph readings require their exact parent representation")
        if self.source_refresh is not None and (
            self.role != "document"
            or self.source_url != self.source_refresh.source_url
            or self.content_sha256 != self.source_refresh.source_sha256
            or self.local_input is not None
            or self.human_browser is not None
            or self.identity
            != self.document_identity(
                self.source_url or "",
                self.content_sha256 or "",
                self.text_sha256 or "",
                self.revision,
                human_browser=self.human_browser,
                pdf_reading=self.pdf_reading,
                retained_source=self.retained_source,
                source_refresh=self.source_refresh,
                visual_readings=self.visual_readings,
            )
        ):
            raise ValueError("refreshed graph documents require their exact reuse representation")
        if self.human_browser is not None and (
            self.role != "document"
            or self.source_url != self.human_browser.final_url
            or self.content_sha256 != self.human_browser.captured_sha256
            or self.transport is not None
            or self.local_input is not None
        ):
            raise ValueError("browser graph evidence belongs to its captured document")
        if self.local_input is not None and (
            self.role != "document"
            or self.source_url != self.local_input.source_id
            or self.content_sha256 != self.local_input.sha256
            or self.transport is not None
        ):
            raise ValueError("local graph evidence belongs to its original document version")
        if self.role == "document":
            if self.text is None or self.content_sha256 is None or self.source_url is None:
                raise ValueError("document graph node needs version, URL and retained text")
            if self.text_sha256 != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
                raise ValueError("document text digest must match its retained reading")
            if self.pdf_reading is not None:
                if self.pdf_reading.source_sha256 != self.content_sha256:
                    raise ValueError("graph PDF reading must bind its original source")
                self.pdf_reading.validate_text(self.text)
                if self.identity != self.document_identity(
                    self.source_url,
                    self.content_sha256,
                    hashlib.sha256(self.text.encode()).hexdigest(),
                    self.revision,
                    human_browser=self.human_browser,
                    pdf_reading=self.pdf_reading,
                    retained_source=self.retained_source,
                    source_refresh=self.source_refresh,
                    visual_readings=self.visual_readings,
                ):
                    raise ValueError(
                        "graph PDF reading must bind its exact representation identity"
                    )
        elif any(value is not None for value in (self.text, self.text_sha256, self.content_sha256)):
            raise ValueError("content version fields belong only to document nodes")
        elif self.pdf_reading is not None:
            raise ValueError("PDF reading provenance belongs only to document nodes")
        if self.retained_source is not None and (
            self.role != "document"
            or self.identity
            != self.document_identity(
                self.source_url or "",
                self.content_sha256 or "",
                self.text_sha256 or "",
                self.revision,
                human_browser=self.human_browser,
                pdf_reading=self.pdf_reading,
                retained_source=self.retained_source,
                source_refresh=self.source_refresh,
                visual_readings=self.visual_readings,
            )
        ):
            raise ValueError("retained graph origin must bind its document representation")
        return self


class GraphEvidence(GraphRecord):
    document_id: Text
    document_sha256: Digest
    text_sha256: Digest
    start: Count
    end: Positive
    quote: Text
    basis: Literal["native", "reviewed_pdf_transcription", "image_ocr", "reviewed_visual_claim"] = (
        Field(default="native", exclude_if=lambda v: v == "native")
    )
    page_indices: tuple[Count, ...] = Field(default=(), exclude_if=lambda v: not v)
    reading_sha256: Digest | None = Field(default=None, exclude_if=lambda v: v is None)
    visual_anchor: GraphVisualAnchor | None = Field(default=None, exclude_if=lambda v: v is None)

    @classmethod
    def from_visual(
        cls, document_id: str, reading: GraphVisualReading, span_index: int
    ) -> "GraphEvidence":
        if not 0 <= span_index < len(reading.spans):
            raise ValueError("visual span is outside the retained reading")
        span = reading.spans[span_index]
        return cls(
            document_id=document_id,
            document_sha256=reading.parent_sha256,
            text_sha256=hashlib.sha256(reading.text.encode()).hexdigest(),
            start=span.start,
            end=span.end,
            quote=span.quote,
            basis=span.basis,
            page_indices=(reading.pdf_page_index,) if reading.pdf_page_index is not None else (),
            visual_anchor=GraphVisualAnchor(
                reading_sha256=reading.content_digest(),
                image_sha256=reading.image_sha256,
                span_index=span_index,
                regions=span.regions,
            ),
        )

    @classmethod
    def from_reading(
        cls,
        document_id: str,
        document_sha256: str,
        text: str,
        start: int,
        end: int,
        *,
        pdf_reading: GraphPdfReading | None = None,
    ) -> "GraphEvidence":
        if not 0 <= start < end <= len(text):
            raise ValueError("graph evidence requires a span within its selected reading")
        if pdf_reading is not None:
            if pdf_reading.source_sha256 != document_sha256:
                raise ValueError("graph evidence reading belongs to a different original")
            pdf_reading.validate_text(text)
        return cls(
            document_id=document_id,
            document_sha256=document_sha256,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            start=start,
            end=end,
            quote=text[start:end],
            basis="reviewed_pdf_transcription" if pdf_reading is not None else "native",
            page_indices=pdf_reading.cited_pages(start, end) if pdf_reading is not None else (),
            reading_sha256=pdf_reading.content_digest() if pdf_reading is not None else None,
        )

    def matches_reading(
        self,
        document_sha256: str,
        text: str,
        *,
        pdf_reading: GraphPdfReading | None = None,
        visual_readings: tuple[GraphVisualReading, ...] = (),
    ) -> bool:
        if self.visual_anchor is not None:
            reading = next(
                (
                    item
                    for item in visual_readings
                    if item.content_digest() == self.visual_anchor.reading_sha256
                ),
                None,
            )
            if reading is None or reading.parent_sha256 != document_sha256:
                return False
            try:
                return self == self.from_visual(
                    self.document_id, reading, self.visual_anchor.span_index
                )
            except ValueError:
                return False
        try:
            expected = self.from_reading(
                self.document_id,
                document_sha256,
                text,
                self.start,
                self.end,
                pdf_reading=pdf_reading,
            )
        except ValueError:
            return False
        return self == expected

    @model_validator(mode="after")
    def nonempty_span(self) -> "GraphEvidence":
        if self.end <= self.start:
            raise ValueError("evidence span must be nonempty")
        reviewed = self.basis == "reviewed_pdf_transcription"
        visual = self.basis in {"image_ocr", "reviewed_visual_claim"}
        if visual:
            if (
                self.visual_anchor is None
                or self.reading_sha256 is not None
                or len(self.page_indices) > 1
            ):
                raise ValueError(
                    "visual evidence requires geometry rather than native reading offsets"
                )
            return self
        if (
            reviewed != bool(self.page_indices)
            or reviewed != (self.reading_sha256 is not None)
            or tuple(sorted(set(self.page_indices))) != self.page_indices
            or self.visual_anchor is not None
        ):
            raise ValueError("graph evidence must distinguish native and reviewed page readings")
        return self


class GraphIdentityDecision(GraphRecord):
    """Append-only reviewed identity decision, never destructive source-node replacement."""

    schema_version: Literal["ghimera.identity-decision/1"] = Field(alias="schema")

    id: Annotated[str, Field(pattern=r"^resolution:[0-9a-f]{64}$")]
    operation: Literal["merge", "split", "retract"]
    members: tuple[Text, ...]
    retracts: tuple[Text, ...]
    evidence: Annotated[tuple[GraphEvidence, ...], Field(min_length=1)]
    authority: Text
    revision: Text
    reason: Text
    basis: Literal["human_reviewed"]
    valid_from: date | None
    valid_to: date | None

    @model_validator(mode="after")
    def coherent(self) -> "GraphIdentityDecision":
        if (
            len(set(self.members)) != len(self.members)
            or len(set(self.retracts)) != len(self.retracts)
            or len({e.content_digest() for e in self.evidence}) != len(self.evidence)
            or (self.operation != "retract" and len(self.members) < 2)
            or (self.operation == "retract" and (self.members or not self.retracts))
            or (
                self.operation == "retract"
                and (self.valid_from is not None or self.valid_to is not None)
            )
            or (
                self.valid_from is not None
                and self.valid_to is not None
                and self.valid_from > self.valid_to
            )
            or not all(value.strip() for value in (self.authority, self.revision, self.reason))
        ):
            raise ValueError(
                "identity decisions require distinct members, evidence and valid dates"
            )
        return self


class GraphModelIdentityDecision(GraphRecord):
    """A separately model-reviewed decision, never a human approval claim."""

    schema_version: Literal["ghimera.identity-decision/2"] = Field(alias="schema")
    id: Annotated[str, Field(pattern=r"^resolution:[0-9a-f]{64}$")]
    operation: Literal["merge", "split", "retract"]
    members: tuple[Text, ...]
    retracts: tuple[Text, ...]
    evidence: Annotated[tuple[GraphEvidence, ...], Field(min_length=1)]
    authority: Text
    revision: Text
    reason: Text
    basis: Literal["model_reviewed"]
    valid_from: date | None
    valid_to: date | None
    population_digest: Digest
    proposal_digest: Digest
    review_digest: Digest
    proposal_call: IdentityCallEvidence
    review_call: IdentityCallEvidence

    @model_validator(mode="after")
    def coherent(self) -> "GraphModelIdentityDecision":
        if (
            len(set(self.members)) != len(self.members)
            or len(set(self.retracts)) != len(self.retracts)
            or len({e.content_digest() for e in self.evidence}) != len(self.evidence)
            or (self.operation != "retract" and len(self.members) < 2)
            or (self.operation == "retract" and (self.members or not self.retracts))
            or self.operation == "retract"
            and (self.valid_from is not None or self.valid_to is not None)
            or self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from > self.valid_to
            or not all(value.strip() for value in (self.authority, self.revision, self.reason))
            or self.proposal_call.task != "identity_propose"
            or self.review_call.task != "identity_review"
            or self.proposal_call.outcome != "success"
            or self.review_call.outcome != "success"
            or self.authority != self.review_call.service.model_id
        ):
            raise ValueError(
                "model-reviewed identity requires its actual separate successful calls"
            )
        return self


IdentityDecision = GraphIdentityDecision | GraphModelIdentityDecision


class GraphEdge(GraphRecord):
    id: Annotated[str, Field(pattern=r"^edge:[0-9a-f]{64}$")]
    rule: Name
    predicate: Name
    source: Text
    target: Text
    revision: Text
    audiences: Annotated[tuple[Text, ...], Field(min_length=1)]
    handling_labels: tuple[Text, ...]
    confidence: Confidence | None = None
    evidence: tuple[GraphEvidence, ...] = ()
    claim_status: Literal["model_asserted"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_request_sha256: Digest | None = Field(default=None, exclude_if=lambda v: v is None)
    valid_from: str | None = Field(default=None, exclude_if=lambda v: v is None)
    valid_to: str | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def asserted(self) -> "GraphEdge":
        if (self.claim_status is None) != (self.model_request_sha256 is None):
            raise ValueError("model assertions require their producing request digest")
        if (self.valid_from is not None or self.valid_to is not None) and self.claim_status is None:
            raise ValueError("extraction dates require an explicit model assertion")
        return self


class GraphBatch(GraphRecord):
    schema_version: Literal["chimera.graph-batch/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    config_digest: Digest
    sequence: Count
    previous_digest: Digest | None
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    identity_decisions: tuple[IdentityDecision, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def nonempty(self) -> "GraphBatch":
        if not self.nodes and not self.edges and not self.identity_decisions:
            raise ValueError("empty graph batch")
        if (self.sequence == 0) != (self.previous_digest is None):
            raise ValueError("first graph batch alone has no predecessor")
        return self


class GraphCheckpoint(GraphRecord):
    sequence: Count
    digest: Digest


class GraphSnapshot(GraphRecord):
    schema_version: Literal["chimera.research-graph/1"] = Field(alias="schema")
    run_id: Text
    config_digest: Digest
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    checkpoint: GraphCheckpoint | None
    identity_decisions: tuple[IdentityDecision, ...] = Field(default=(), exclude_if=lambda v: not v)
