"""Source-bound rendering and resource evidence; DOM is not original source bytes."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chimera.browser_config import BrowserConfig
from chimera.refusals import RefusalCode
from chimera.transport_types import TransportEvidence

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1)]


class BrowserRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class ResourceRequest(BrowserRecord):
    event: Literal["resource"] = "resource"
    sequence: Annotated[int, Field(strict=True, ge=0)]
    url: Text
    method: Text
    resource_type: Text


class RenderResource(BrowserRecord):
    url: Text
    method: Text
    resource_type: Text
    final_url: str | None = None
    status: Annotated[int, Field(strict=True, ge=100, le=399)] | None = None
    content_type: str | None = None
    body: bytes = b""
    source_sha256: Digest | None = None
    transport: TransportEvidence | None = None
    headers: tuple[tuple[str, str], ...] = ()
    refusal: RefusalCode | None = None

    @model_validator(mode="after")
    def bound(self) -> "RenderResource":
        if self.refusal is not None:
            if (
                any(
                    value is not None
                    for value in (
                        self.status,
                        self.source_sha256,
                        self.content_type,
                        self.final_url,
                        self.transport,
                    )
                )
                or self.body
                or self.headers
            ):
                raise ValueError("refused resource cannot claim retained fetched content")
        elif (
            self.method != "GET"
            or self.status is None
            or not self.final_url
            or not self.content_type
            or self.source_sha256 != hashlib.sha256(self.body).hexdigest()
        ):
            raise ValueError("successful resource requires original content and digest")
        return self


class ResourceResponse(BrowserRecord):
    sequence: Annotated[int, Field(strict=True, ge=0)]
    result: RenderResource


class RenderResult(BrowserRecord):
    schema_version: Literal["chimera.browser-render/1"] = Field(alias="schema")
    source_url: Text
    source_sha256: Digest
    html: bytes
    html_sha256: Digest
    config_digest: Digest
    browser_sha256: Digest
    driver_revision: Literal["patchright@1.63.0"]
    network_isolation: Literal["linux_network_namespace"]
    parent_network_namespace: Annotated[str, Field(pattern=r"^net:\[\d+\]$")]
    worker_network_namespace: Annotated[str, Field(pattern=r"^net:\[\d+\]$")]
    resources: tuple[RenderResource, ...]

    def validate_policy(self, policy: BrowserConfig) -> None:
        if (
            self.config_digest != policy.content_digest()
            or self.browser_sha256 != policy.executable_sha256
            or len(self.html) > policy.max_rendered_bytes
            or len(self.resources) > policy.max_resources
        ):
            raise ValueError("rendering must bind its artifact, effective policy and limits")
        if any(
            item.refusal is None
            and (
                item.resource_type not in policy.resource_types
                or item.content_type not in policy.resource_content_types
                or len(item.body) > policy.max_input_bytes
                or item.url != item.final_url
            )
            for item in self.resources
        ):
            raise ValueError("successful rendering resources must obey the declared policy")

    @model_validator(mode="after")
    def bound(self) -> "RenderResult":
        if not self.html or self.html_sha256 != hashlib.sha256(self.html).hexdigest():
            raise ValueError("rendered DOM requires retained content and digest")
        if self.parent_network_namespace == self.worker_network_namespace:
            raise ValueError("browser worker must have its own network namespace")
        return self


class WorkerResult(BrowserRecord):
    event: Literal["result"] = "result"
    result: RenderResult | None = None
    refusal: RefusalCode | None = None

    @model_validator(mode="after")
    def outcome(self) -> "WorkerResult":
        if (self.result is None) == (self.refusal is None):
            raise ValueError("browser worker must report exactly one outcome")
        return self
