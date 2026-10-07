"""Neutral research graph wire and configuration; no platform vocabulary guessed."""

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.human_browser_types import HumanBrowserEvidence
from ghimera.local_input_types import LocalInputEvidence
from ghimera.transport_types import TransportEvidence

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

    @classmethod
    def from_toml(cls, path: Path) -> "GraphConfig":
        with path.open("rb") as stream:
            return cls.model_validate(tomllib.load(stream))

    @model_validator(mode="after")
    def coherent(self) -> "GraphConfig":
        roles = {role.name for role in self.roles}
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
        if not self.sink_path.is_absolute():
            raise ValueError("graph sink path must be explicit and absolute")
        if not self.identity_namespace.strip() or not self.profile_version.strip():
            raise ValueError("graph profile/identity namespace must be nonblank")
        return self


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
    human_browser: HumanBrowserEvidence | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def content_bound(self) -> "GraphNode":
        if self.human_browser is not None and (
            self.role != "document"
            or self.source_url != self.human_browser.final_url
            or self.content_sha256 != self.human_browser.dom_sha256
            or self.transport is not None
            or self.local_input is not None
        ):
            raise ValueError("browser graph evidence belongs to its captured DOM document")
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
                raise ValueError("document text digest must match retained native text")
        elif any(value is not None for value in (self.text, self.text_sha256, self.content_sha256)):
            raise ValueError("content version fields belong only to document nodes")
        return self


class GraphEvidence(GraphRecord):
    document_id: Text
    document_sha256: Digest
    text_sha256: Digest
    start: Count
    end: Positive
    quote: Text

    @model_validator(mode="after")
    def nonempty_span(self) -> "GraphEvidence":
        if self.end <= self.start:
            raise ValueError("evidence span must be nonempty")
        return self


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

    @model_validator(mode="after")
    def nonempty(self) -> "GraphBatch":
        if not self.nodes and not self.edges:
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
