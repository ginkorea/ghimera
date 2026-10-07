"""Explicit operator-owned Ahmia index binding, independent of any hosted service."""

import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.private_service_config import Positive, PrivateJsonConfig


class AhmiaQueryField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Literal["title", "h1", "meta", "content"]
    boost: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class AhmiaConfig(PrivateJsonConfig):
    schema_version: Literal["ghimera.ahmia/1"] = Field(alias="schema")
    index_name: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
    index_revision: Annotated[str, Field(min_length=1)]
    query_fields: Annotated[tuple[AhmiaQueryField, ...], Field(min_length=1)]
    query_operator: Literal["and", "or"]
    max_results: Positive
    snippet_chars: Positive
    age_policy: Literal["all_observed", "bounded_age"]
    max_observation_age_seconds: Positive | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def exact_search(self) -> "AhmiaConfig":
        if urlsplit(self.endpoint).path != f"/{self.index_name}/_search":
            raise ValueError("Ahmia requires the exact named index read-only search endpoint")
        if not self.index_revision.strip():
            raise ValueError("Ahmia requires an explicit index revision")
        if len({field.name for field in self.query_fields}) != len(self.query_fields):
            raise ValueError("Ahmia query fields must be unique")
        if (self.age_policy == "bounded_age") != (self.max_observation_age_seconds is not None):
            raise ValueError("bounded Ahmia age requires exactly its age limit")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        raw = json.dumps(
            self.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return "ahmia", "index-search/1:" + hashlib.sha256(raw.encode()).hexdigest()
