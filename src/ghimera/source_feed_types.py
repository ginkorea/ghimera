"""Native feed declarations are discovery observations, not fetched article facts."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.reference_types import reference_url
from ghimera.source_api import Relationship
from ghimera.source_feed_config import FeedFormat, SourceFeedConfig


class FeedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)


class SourceFeedEntry(FeedRecord):
    role: Literal["document", "feed"]
    base_url: str
    observed_url: str
    title: str
    content: str
    declared_date: str
    declared_id: str
    api_relation: Relationship | None = Field(default=None, exclude_if=lambda v: v is None)
    api_locator: str | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def mapped_source(self) -> "SourceFeedEntry":
        if (self.api_relation is None) != (self.api_locator is None):
            raise ValueError("API relationship requires its retained JSON locator")
        if (
            self.api_locator is not None
            and self.api_locator
            and not self.api_locator.startswith("/")
        ):
            raise ValueError("API locator must be a JSON pointer")
        if self.api_relation is not None and (self.role == "feed") != (
            self.api_relation == "pagination"
        ):
            raise ValueError("API pagination and citation roles must remain distinct")
        return self

    @property
    def url(self) -> str | None:
        return reference_url(self.base_url, self.observed_url) if self.observed_url else None


class SourceFeedEvidence(FeedRecord):
    schema_version: Literal["ghimera.source-feed-evidence/1"] = Field(alias="schema")
    parser_revision: Literal["source-feed-parser/1"] = "source-feed-parser/1"
    policy: SourceFeedConfig
    source_url: Annotated[str, Field(min_length=1)]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content_type: str
    format: FeedFormat
    title: str
    declared_language: str
    entries: tuple[SourceFeedEntry, ...]

    @property
    def reading_title(self) -> str:
        return self.title or next(
            (
                entry.title or entry.observed_url
                for entry in self.entries
                if entry.title or entry.observed_url
            ),
            "",
        )

    @property
    def text(self) -> str:
        # Publication dates/ids remain declarations, not acquisition timestamps.
        lines = [self.title] if self.title else []
        for entry in self.entries:
            lines.extend(
                value
                for value in (entry.title, entry.content, entry.observed_url, entry.declared_date)
                if value
            )
        return "\n\n".join(lines)

    @property
    def links(self) -> tuple[tuple[str, str], ...]:
        selected: dict[str, str] = {}
        for entry in self.entries:
            url = entry.url
            if url is not None and url not in selected and len(selected) < self.policy.max_links:
                selected[url] = entry.title or entry.observed_url
        return tuple(selected.items())

    @property
    def omitted_links(self) -> int:
        return len({entry.url for entry in self.entries if entry.url is not None}) - len(self.links)

    @model_validator(mode="after")
    def bounds(self) -> "SourceFeedEvidence":
        policy = self.policy
        if (
            self.format not in policy.formats
            or self.content_type not in policy.content_types
            or len(self.entries) > policy.max_entries
            or not self.text.strip()
            or not self.reading_title.strip()
            or len(self.text) > policy.max_text_chars
            or len(self.title) > policy.max_field_chars
            or len(self.declared_language) > policy.max_field_chars
            or any(
                (entry.api_relation is not None) != (self.format == "json_api")
                for entry in self.entries
            )
            or any(
                len(value) > policy.max_field_chars
                for entry in self.entries
                for value in (
                    entry.base_url,
                    entry.observed_url,
                    entry.title,
                    entry.content,
                    entry.declared_date,
                    entry.declared_id,
                    entry.api_locator or "",
                )
            )
        ):
            raise ValueError("source feed evidence must fit its declared format and bounds")
        return self

    def validate_source(self, raw: bytes) -> None:
        from ghimera.source_feed_parse import parse_feed

        if (
            len(raw) > self.policy.max_input_bytes
            or hashlib.sha256(raw).hexdigest() != self.source_sha256
        ):
            raise ValueError("feed evidence requires its exact original source")
        if parse_feed(raw, self.source_url, self.content_type, self.policy) != self:
            raise ValueError("feed declarations must replay from retained source bytes")
