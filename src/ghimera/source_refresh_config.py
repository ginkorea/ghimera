"""Explicit persistent conditional-source policy; never a source access grant."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ghimera.source_session_types import origin_key

Positive = Annotated[int, Field(strict=True, gt=0)]


def exact_http_url(value: str) -> str:
    if origin_key(value) is None or urlsplit(value).fragment:
        raise ValueError("refresh targets require exact HTTP(S) URLs without userinfo or fragments")
    return value


class SourceRefreshConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-refresh/1"] = Field(alias="schema")
    directory: Path
    create_if_missing: Annotated[bool, Field(strict=True)]
    urls: Annotated[tuple[str, ...], Field(min_length=1)]
    max_versions: Positive
    max_record_bytes: Positive
    max_store_bytes: Positive
    max_validator_age_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    database_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @field_validator("directory")
    @classmethod
    def owned_directory(cls, value: Path) -> Path:
        if not value.is_absolute() or value == Path("/") or ".." in value.parts:
            raise ValueError("source refresh requires an explicit absolute non-root directory")
        return value

    @model_validator(mode="after")
    def coherent(self) -> "SourceRefreshConfig":
        if len(set(self.urls)) != len(self.urls):
            raise ValueError("source refresh targets must be unique")
        for url in self.urls:
            exact_http_url(url)
        if self.max_record_bytes > self.max_store_bytes:
            raise ValueError("source record cannot exceed the total store allowance")
        return self

    @property
    def identity(self) -> str:
        # Bootstrap instruction and relocation do not change stored source identity.
        data = self.model_dump_json(exclude={"directory", "create_if_missing"}).encode()
        return hashlib.sha256(data).hexdigest()
