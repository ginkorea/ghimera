"""Explicit, pinned browser execution policy; no inferred appliance paths."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.browser/1"] = Field(alias="schema")
    engine: Literal["patchright"]
    executable: Path
    executable_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    isolator: Path
    work_directory: Path
    sandbox_work_directory: Path
    max_workers: Positive
    timeout_seconds: Seconds
    cleanup_timeout_seconds: Seconds
    max_input_bytes: Positive
    max_rendered_bytes: Positive
    max_protocol_bytes: Positive
    max_diagnostic_bytes: Positive
    max_resources: Positive
    resource_types: tuple[
        Literal["script", "stylesheet", "image", "font", "fetch", "xhr", "media"], ...
    ]
    resource_content_types: Annotated[tuple[str, ...], Field(min_length=1)]
    ready_selector: Annotated[str, Field(min_length=1)] | None = None
    settle_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    viewport_width: Positive
    viewport_height: Positive

    @model_validator(mode="after")
    def coherent(self) -> "BrowserConfig":
        if not all(
            path.is_absolute()
            for path in (
                self.executable,
                self.isolator,
                self.work_directory,
                self.sandbox_work_directory,
            )
        ):
            raise ValueError("browser paths must be explicit absolute paths")
        if (
            self.sandbox_work_directory.parent != Path("/run")
            or len(str(self.sandbox_work_directory).encode()) > 32
        ):
            raise ValueError("sandbox scratch must be a short immediate child of /run")
        if len(set(self.resource_types)) != len(self.resource_types):
            raise ValueError("resource types cannot repeat")
        if any(not value or ";" in value for value in self.resource_content_types):
            raise ValueError("resource MIME types must be normalized types without parameters")
        if self.ready_selector is not None and not self.ready_selector.strip():
            raise ValueError("ready selector must be nonblank")
        if self.settle_seconds >= self.timeout_seconds:
            raise ValueError("settle time must fit within the render deadline")
        if self.max_protocol_bytes < 2 * max(self.max_input_bytes, self.max_rendered_bytes):
            raise ValueError("protocol budget must hold encoded input and rendered output")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()
