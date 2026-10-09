"""Immutable model-call evidence, independent of source transport and credentials."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue, SkipJsonSchema
from pydantic_core import CoreSchema

from ghimera.model_config import ModelServiceConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
LegacyModelTask = Literal[
    "plan",
    "assessment",
    "answer",
    "review",
    "verdict",
    "grade",
    "semantic_extract",
    "semantic_review",
]
ModelTask = Literal[
    "plan",
    "assessment",
    "answer",
    "review",
    "verdict",
    "grade",
    "semantic_extract",
    "semantic_review",
    "identity_propose",
    "identity_review",
]


class _LegacyTaskSchema:
    """Freeze historical client evidence; identity calls have their own full schema."""

    def __get_pydantic_json_schema__(
        self, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        schema["enum"] = [
            "plan",
            "assessment",
            "answer",
            "review",
            "verdict",
            "grade",
            "semantic_extract",
            "semantic_review",
        ]
        return schema


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)
    prompt_tokens: Count
    completion_tokens: Count
    total_tokens: Count

    @model_validator(mode="after")
    def reconciled(self) -> "TokenUsage":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("token spend must reconcile")
        return self


FinishReason = Literal["stop", "length", "content_filter", "tool_calls", "function_call", "other"]


class CompletionShape(BaseModel):
    """Non-secret protocol observations, never model output or reasoning text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.completion-shape/1"] = Field(alias="schema")
    choices: Count
    model_matches: bool
    finish_reason: FinishReason | None
    final_content: Literal["missing", "empty", "present"] | None
    refusal_present: bool | None
    reasoning_present: bool | None

    @model_validator(mode="after")
    def single_choice(self) -> "CompletionShape":
        observed = (
            self.finish_reason,
            self.final_content,
            self.refusal_present,
            self.reasoning_present,
        )
        if self.choices == 1:
            if any(item is None for item in observed):
                raise ValueError("one completion choice requires its observed shape")
        elif any(item is not None for item in observed):
            raise ValueError("multiple or absent choices have no single completion shape")
        return self


class ModelOutputContractFailure(BaseModel):
    """Client-observed validation branch, never refused output or provider prose."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-output-contract/1"] = Field(alias="schema")
    reason: Literal[
        "response_too_large",
        "model_claimed_telemetry",
        "unbound_graph_reference",
        "invalid_completion_envelope",
        "invalid_final_payload",
        "semantic_review_normalization_failed",
        "semantic_review_source_binding_failed",
    ]


class _ClientEvidenceServiceSchema:
    """Frozen model-facing projection, not operational validation or serialization.

    The separate reference retains the historical short definition name when
    unambiguous, but cannot rewrite full configuration in a mixed schema.
    Runtime values remain native ModelServiceConfig instances with every pin.
    """

    def __get_pydantic_core_schema__(
        self, source_type: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        schema = dict(handler.resolve_ref_schema(handler(source_type)))
        schema["ref"] = "ghimera.model_types.client_evidence.ModelServiceConfig"
        return schema

    def __get_pydantic_json_schema__(
        self, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        projection = handler.resolve_ref_schema(schema)
        properties = projection["properties"]
        properties.pop("gateway", None)
        schema_field = "schema" if "schema" in properties else "schema_version"
        properties[schema_field]["enum"] = [
            "chimera.model-service/1",
            "chimera.model-service/2",
        ]
        return schema


class ModelCallEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.model-call/1"] = Field(alias="schema")
    service: Annotated[ModelServiceConfig, _ClientEvidenceServiceSchema()]
    task: Annotated[ModelTask, _LegacyTaskSchema()]
    prompt_revision: str
    request_sha256: Digest
    response_sha256: Digest
    response_bytes: Count
    status: int | None
    latency_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    usage: TokenUsage | None
    input_chars: Count
    context_sha256: Digest
    selected_spans: tuple[tuple[str, int, int], ...]
    selected_visual_citation_ids: SkipJsonSchema[
        tuple[Annotated[str, Field(pattern=r"^cite:[0-9a-f]{64}$")], ...]
    ] = Field(default=(), exclude_if=lambda value: not value)
    omitted_document_ids: tuple[str, ...]
    omitted_chars: Count
    outcome: Literal["success", "refused", "cancelled"]
    # Client-owned telemetry is validated and replayed, but must not alter the
    # frozen model-facing JSON schemas of historical extraction/review recipes.
    completion: SkipJsonSchema[CompletionShape | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    output_contract_failure: SkipJsonSchema[ModelOutputContractFailure | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def failure_is_refused(self) -> "ModelCallEvidence":
        if self.output_contract_failure is not None and self.outcome != "refused":
            raise ValueError("output-contract failure belongs only to a refused call")
        return self

    @property
    def total_tokens(self) -> int | None:
        return self.usage.total_tokens if self.usage is not None else None


class IdentityCallEvidence(ModelCallEvidence):
    """Truthful isolated schema for opt-in identity tasks, not legacy prompts."""

    service: ModelServiceConfig
    task: Literal["identity_propose", "identity_review"]
