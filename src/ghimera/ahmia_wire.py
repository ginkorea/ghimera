"""Decode the upstream Ahmia Elasticsearch mapping; never turn snippets into evidence."""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ghimera.ahmia_config import AhmiaConfig


class AhmiaHitEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.ahmia-hit/1"] = Field(alias="schema")
    index_name: str
    index_revision: str
    hit_id: Annotated[str, Field(min_length=1)]
    updated_on: AwareDatetime
    retrieved_at: AwareDatetime
    retrieval_score: Annotated[float, Field(allow_inf_nan=False)] | None


class _Source(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    url: str
    title: str = ""
    meta: str = ""
    content: str = ""
    updated_on: AwareDatetime
    is_banned: bool


class _Hit(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    index: str = Field(alias="_index")
    id: Annotated[str, Field(min_length=1)] = Field(alias="_id")
    score: Annotated[float, Field(allow_inf_nan=False)] | None = Field(alias="_score")
    source: _Source = Field(alias="_source")


class _Hits(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    hits: tuple[_Hit, ...]


class _Shards(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    total: Annotated[int, Field(strict=True, gt=0)]
    successful: Annotated[int, Field(strict=True, ge=0)]
    skipped: Annotated[int, Field(strict=True, ge=0)]
    failed: Annotated[int, Field(strict=True, ge=0)]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    timed_out: bool
    shards: _Shards = Field(alias="_shards")
    hits: _Hits


class AhmiaLead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    title: str
    snippet: str
    index_evidence: AhmiaHitEvidence


def decode_ahmia(
    raw: bytes, policy: AhmiaConfig, *, limit: int, retrieved_at: datetime
) -> tuple[AhmiaLead, ...]:
    from ghimera.transport import is_onion_v3

    wire = _Wire.model_validate_json(raw)
    if (
        len(wire.hits.hits) > min(limit, policy.max_results)
        or wire.timed_out
        or wire.shards.failed != 0
        or wire.shards.successful != wire.shards.total
    ):
        raise ValueError("Ahmia search is partial or exceeds the requested result cap")
    leads = []
    for hit in wire.hits.hits:
        source = hit.source
        parsed = urlsplit(source.url)
        if (
            hit.index != policy.index_name
            or source.is_banned
            or parsed.scheme not in {"http", "https"}
            or parsed.port == 0
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.hostname
            or not is_onion_v3(parsed.hostname)
            or any(ord(char) < 33 for char in source.url)
            or source.updated_on > retrieved_at
            or (
                policy.max_observation_age_seconds is not None
                and source.updated_on
                < retrieved_at - timedelta(seconds=policy.max_observation_age_seconds)
            )
        ):
            raise ValueError("Ahmia hit has an invalid locator, index or observation age")
        leads.append(
            AhmiaLead(
                url=source.url,
                title=source.title,
                snippet=(source.meta or source.content)[: policy.snippet_chars],
                index_evidence=AhmiaHitEvidence(
                    schema="ghimera.ahmia-hit/1",
                    index_name=hit.index,
                    index_revision=policy.index_revision,
                    hit_id=hit.id,
                    updated_on=source.updated_on,
                    retrieved_at=retrieved_at,
                    retrieval_score=hit.score,
                ),
            )
        )
    return tuple(leads)
