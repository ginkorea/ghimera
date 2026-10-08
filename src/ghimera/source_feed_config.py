"""Explicit passive source-manifest parsing policy, never a new network route."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.source_api import SiteApiConfig

FeedFormat = Literal["rss", "atom", "sitemap", "json_feed", "json_api"]
Positive = Annotated[int, Field(strict=True, gt=0)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
XML_TYPES = frozenset(
    {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
)
JSON_TYPES = frozenset({"application/feed+json", "application/json"})


class SourceFeedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-feeds/1"] = Field(alias="schema")
    formats: Annotated[tuple[FeedFormat, ...], Field(min_length=1)]
    content_types: Annotated[tuple[str, ...], Field(min_length=1)]
    include_attachments: bool
    worker_python: Path
    work_directory: Path
    max_workers: Positive
    timeout_seconds: Seconds
    cleanup_timeout_seconds: Seconds
    max_input_bytes: Positive
    max_output_bytes: Positive
    max_diagnostic_bytes: Positive
    max_entries: Positive
    max_xml_nodes: Positive
    max_xml_depth: Positive
    max_text_chars: Positive
    max_field_chars: Positive
    max_links: Positive
    site_api: SiteApiConfig | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def bounded(self) -> "SourceFeedConfig":
        if len(set(self.formats)) != len(self.formats) or len(set(self.content_types)) != len(
            self.content_types
        ):
            raise ValueError("feed formats and content types must be unique")
        if ("json_api" in self.formats) != (self.site_api is not None):
            raise ValueError("site JSON requires an explicit API mapping")
        if "json_api" in self.formats and "json_feed" in self.formats:
            raise ValueError("one JSON response dialect has one explicit parser owner")
        available = (
            XML_TYPES if set(self.formats) - {"json_feed", "json_api"} else frozenset()
        ) | (JSON_TYPES if set(self.formats) & {"json_feed", "json_api"} else frozenset())
        if not set(self.content_types) <= available:
            raise ValueError("feed MIME admission must match its explicit formats")
        if any(
            not path.is_absolute() or path == Path("/")
            for path in (self.worker_python, self.work_directory)
        ):
            raise ValueError("feed worker and owned work directory must be explicit absolute paths")
        if self.max_field_chars > self.max_text_chars or self.max_links > self.max_entries:
            raise ValueError("feed field/link limits must fit their parent limits")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
