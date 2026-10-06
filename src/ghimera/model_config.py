"""Private model-control policy: no inference launch or external fallback."""

import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]
Text = Annotated[str, Field(min_length=1)]


class EvidenceContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.evidence-context/1"] = Field(alias="schema")
    max_documents: Positive
    max_chars: Positive
    window_chars: Positive
    max_windows_per_document: Positive
    overlap_chars: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def coherent(self) -> "EvidenceContextConfig":
        if self.overlap_chars >= self.window_chars or self.window_chars > self.max_chars:
            raise ValueError("context windows need bounded overlap within the total allowance")
        return self


class PrivateModelService(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    endpoint: Text
    approved_addresses: Annotated[tuple[Text, ...], Field(min_length=1)]
    allow_plaintext: bool
    allow_plaintext_credentials: bool
    authorization: Literal["none", "bearer"]
    model_id: Text
    revision: Text
    served_model: Text
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_request_bytes: Positive
    max_response_bytes: Positive
    max_header_bytes: Positive
    require_usage: bool
    max_input_chars: Positive

    @model_validator(mode="after")
    def private_service(self) -> "PrivateModelService":
        parsed = urlsplit(self.endpoint)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("invalid model-service port") from None
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and not self.allow_plaintext)
            or port == 0
            or any(ord(char) < 33 for char in self.endpoint)
        ):
            raise ValueError("configure one credential-free, approved private model endpoint")
        for address in self.approved_addresses:
            value = ipaddress.ip_address(address)
            if (
                str(value) != address
                or not value.is_private
                or value.is_link_local
                or value.is_multicast
                or value.is_unspecified
                or (value.is_reserved and not value.is_loopback)
            ):
                raise ValueError(
                    "model addresses must be exact private/loopback, never metadata/public"
                )
        try:
            literal = str(ipaddress.ip_address(parsed.hostname))
        except ValueError:
            pass
        else:
            if literal not in self.approved_addresses:
                raise ValueError("literal model endpoint is not approved")
        if not all(text.strip() for text in (self.model_id, self.revision, self.served_model)):
            raise ValueError("model identity and revision must be nonblank")
        return self


class ModelServiceConfig(PrivateModelService):
    schema_version: Literal["chimera.model-service/1"] = Field(alias="schema")
    max_output_tokens: Positive
    temperature: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)]
    top_p: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    response_format: Literal["json_object", "json_schema"]
    citation_format: Literal["full", "template_ids"] = Field(
        default="full", exclude_if=lambda value: value == "full"
    )
    context: EvidenceContextConfig

    @model_validator(mode="after")
    def completion_endpoint(self) -> "ModelServiceConfig":
        if not urlsplit(self.endpoint).path.endswith("/chat/completions"):
            raise ValueError("configure an exact completion endpoint")
        return self


class EmbeddingServiceConfig(PrivateModelService):
    schema_version: Literal["chimera.embedding-service/1"] = Field(alias="schema")
    dimensions: Positive
    request_dimensions: bool
    max_batch_texts: Positive
    max_text_chars: Positive
    text_prefix: str

    @model_validator(mode="after")
    def embedding_endpoint(self) -> "EmbeddingServiceConfig":
        if not urlsplit(self.endpoint).path.endswith("/embeddings"):
            raise ValueError("configure an exact embeddings endpoint")
        if len(self.text_prefix) >= self.max_text_chars:
            raise ValueError("embedding prefix must leave room for native input")
        return self


class ModelBindingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.model-bindings/1"] = Field(alias="schema")
    planner: ModelServiceConfig
    analyst: ModelServiceConfig
    reviewer: ModelServiceConfig
    judge: ModelServiceConfig

    def service(
        self, role: Literal["planner", "analyst", "reviewer", "judge"]
    ) -> ModelServiceConfig:
        return {
            "planner": self.planner,
            "analyst": self.analyst,
            "reviewer": self.reviewer,
            "judge": self.judge,
        }[role]
