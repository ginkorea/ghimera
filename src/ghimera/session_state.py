"""Neutral continuation state owned by the existing collection session."""

from typing import Annotated

from pydantic import Field

from ghimera.dedup_types import ContentFingerprint
from ghimera.models import Record, Scope

Count = Annotated[int, Field(strict=True, ge=0)]


class SessionState(Record):
    frontier: tuple[
        tuple[Annotated[float, Field(ge=-1, le=0, allow_inf_nan=False)], str, Count], ...
    ]
    visited: tuple[str, ...]
    reference_hosts: tuple[str, ...]
    reference_scopes: dict[str, Scope]
    reference_hops: dict[str, Count]
    reference_origins: dict[str, str]
    window_start: Count
    window_new: Count
    last_grade: Count
    semantic_sources: tuple[str, ...]
    content_revisions: tuple[ContentFingerprint, ...]
