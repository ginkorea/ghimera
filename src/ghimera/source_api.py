"""Explicit site JSON/citation mappings over already guarded, retained bytes."""

import json
import math
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from ghimera.source_feed_types import SourceFeedEntry

Relationship = Literal["result", "references", "cited_by", "pagination"]
PathParts = tuple[str, ...]


class SiteApiMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    relation: Relationship
    entries_path: PathParts
    url_path: PathParts
    title_path: PathParts | None = None
    content_path: PathParts | None = None
    date_path: PathParts | None = None
    id_path: PathParts | None = None
    url_template: str | None = None

    @model_validator(mode="after")
    def explicit_paths(self) -> "SiteApiMapping":
        if any(
            not part
            for path in (
                self.entries_path,
                self.url_path,
                self.title_path or (),
                self.content_path or (),
                self.date_path or (),
                self.id_path or (),
            )
            for part in path
        ):
            raise ValueError("JSON mapping paths require exact nonempty keys or indices")
        if self.url_template is not None:
            if self.url_template.count("{value}") != 1 or any(
                bracket in self.url_template.replace("{value}", "") for bracket in "{}"
            ):
                raise ValueError("API URL template permits one escaped observed value")
            parsed = urlsplit(self.url_template.replace("{value}", "identifier"))
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError("API URL template requires an explicit safe HTTP(S) target")
        return self


class SiteApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.site-api/1"] = Field(alias="schema")
    source_origins: Annotated[tuple[str, ...], Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    language: str
    mappings: Annotated[tuple[SiteApiMapping, ...], Field(min_length=1)]
    max_json_nodes: Annotated[int, Field(strict=True, gt=0)]
    max_json_depth: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def scoped(self) -> "SiteApiConfig":
        from ghimera.source_session_types import origin_key

        if len(set(self.source_origins)) != len(self.source_origins) or any(
            origin_key(origin) is None
            or urlsplit(origin).path not in {"", "/"}
            or urlsplit(origin).query
            or urlsplit(origin).fragment
            for origin in self.source_origins
        ):
            raise ValueError("site API mapping requires distinct exact source origins")
        return self


def _at(value: object, path: PathParts) -> object:
    for part in path:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "\n".join(item for item in value if isinstance(item, str))
    if type(value) is int:
        return str(value)
    if value is None:
        return ""
    raise ValueError("API fields require strings, integer identifiers or string arrays")


def _pointer(path: PathParts) -> str:
    return "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in path)


def api_entries(
    raw: bytes, source_url: str, policy: SiteApiConfig, maximum: int
) -> tuple["SourceFeedEntry", ...]:
    from ghimera.source_feed_types import SourceFeedEntry
    from ghimera.source_session_types import origin_key

    if origin_key(source_url) not in {origin_key(origin) for origin in policy.source_origins}:
        raise ValueError("API source is outside its configured origin mapping")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("API JSON cannot use ambiguous duplicate keys")
            result[key] = value
        return result

    try:
        value: object = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("malformed site API JSON") from exc
    pending = [(value, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > policy.max_json_nodes or depth > policy.max_json_depth:
            raise ValueError("site API JSON exceeds its admitted structure")
        if isinstance(node, dict):
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)
        elif isinstance(node, float) and not math.isfinite(node):
            raise ValueError("site API JSON cannot contain nonfinite numbers")
    entries: list[SourceFeedEntry] = []
    for mapping in policy.mappings:
        values = _at(value, mapping.entries_path)
        if values is None:
            continue
        items = values if isinstance(values, list) else [values]
        for index, item in enumerate(items):
            if len(entries) >= maximum:
                raise ValueError("site API entry boundary exhausted; no silent truncation")
            observed = _text(_at(item, mapping.url_path))
            if mapping.url_template is not None and observed:
                observed = mapping.url_template.replace("{value}", quote(observed, safe=""))
            locator = mapping.entries_path + ((str(index),) if isinstance(values, list) else ())
            entries.append(
                SourceFeedEntry(
                    role="feed" if mapping.relation == "pagination" else "document",
                    base_url=source_url,
                    observed_url=observed,
                    title=_text(_at(item, mapping.title_path))
                    if mapping.title_path is not None
                    else "",
                    content=_text(_at(item, mapping.content_path))
                    if mapping.content_path is not None
                    else "",
                    declared_date=_text(_at(item, mapping.date_path))
                    if mapping.date_path is not None
                    else "",
                    declared_id=_text(_at(item, mapping.id_path))
                    if mapping.id_path is not None
                    else "",
                    api_relation=mapping.relation,
                    api_locator=_pointer(locator + mapping.url_path),
                )
            )
    return tuple(entries)
