"""Typed parsing/recovery observations; never arbitrary parser diagnostics."""

import asyncio
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.locator_types import LocatorEvent
from ghimera.refusals import GhimeraRefused, RefusalCode

if TYPE_CHECKING:
    from ghimera.extraction_config import ExtractionConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ExtractionRecoveryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.extraction-recovery/1"] = Field(alias="schema")
    mode: Literal["disabled", "generic_once"]


class HtmlExtractionAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.html-extraction-attempt/1"] = Field(alias="schema")
    sequence: Annotated[int, Field(strict=True, ge=0, le=1)]
    phase: Literal["initial", "generic_retry"]
    generic_only: bool
    source_url: Annotated[str, Field(min_length=1)]
    source_sha256: Digest
    rendered_sha256: Digest | None
    config_digest: Digest
    parser_revision: Annotated[str, Field(min_length=1)]
    outcome: Literal["success", "refused", "invalid_response", "cancelled"]
    refusal: RefusalCode | None
    response_sha256: Digest | None
    latency_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    locators: tuple[LocatorEvent, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def coherent(self) -> "HtmlExtractionAttempt":
        if (self.sequence == 0) != (self.phase == "initial"):
            raise ValueError("the initial parse must precede the one generic retry")
        if self.phase == "generic_retry" and not self.generic_only:
            raise ValueError("generic recovery cannot reuse configured selectors")
        if self.outcome in {"success", "cancelled"}:
            if self.refusal is not None:
                raise ValueError("success/cancellation is not a parser refusal")
        elif self.refusal is None:
            raise ValueError("failed parsing must name its refusal")
        if self.outcome in {"success", "invalid_response"} and self.response_sha256 is None:
            raise ValueError("received parser outcomes must bind their response bytes")
        if self.outcome == "invalid_response" and self.refusal != RefusalCode.ADAPTER_CONTRACT:
            raise ValueError("invalid worker responses must name their adapter contract fault")
        if self.generic_only and self.locators:
            raise ValueError("generic parsing cannot claim configured selector observations")
        return self

    def validate_policy(self, config: "ExtractionConfig") -> None:
        if self.config_digest != config.content_digest():
            raise ValueError("parse attempts must bind the effective extraction policy")
        if self.phase == "generic_retry" and (
            config.recovery is None or config.recovery.mode != "generic_once"
        ):
            raise ValueError("generic retry requires its configured recovery policy")
        profile = next(
            (p for p in config.profiles if p.host == urlsplit(self.source_url).hostname), None
        )
        expected = (
            {}
            if profile is None
            else {
                "body": profile.body,
                "title": profile.title,
                "byline": profile.byline,
                "date": profile.date,
            }
        )
        if len({e.field for e in self.locators}) != len(self.locators) or any(
            expected.get(e.field) != e.selector for e in self.locators
        ):
            raise ValueError("parse locator observations must bind their configured publisher")


def validate_chain(attempts: tuple[HtmlExtractionAttempt, ...]) -> None:
    if not attempts or tuple(a.sequence for a in attempts) != tuple(range(len(attempts))):
        raise ValueError("parse observations must be contiguous and bounded to two attempts")
    first = attempts[0]
    for item in attempts:
        if (
            item.source_url,
            item.source_sha256,
            item.rendered_sha256,
            item.config_digest,
            item.parser_revision,
        ) != (
            first.source_url,
            first.source_sha256,
            first.rendered_sha256,
            first.config_digest,
            first.parser_revision,
        ):
            raise ValueError("generic recovery must use the same retained bytes and policy")
    if len(attempts) == 2 and (
        (first.outcome, first.refusal)
        not in {
            ("refused", RefusalCode.EXTRACTION_FAILED),
            ("invalid_response", RefusalCode.ADAPTER_CONTRACT),
        }
    ):
        raise ValueError("only a parsing/validation failure permits generic recovery")


class ExtractionFailure(GhimeraRefused):
    def __init__(self, code: RefusalCode, attempts: tuple[HtmlExtractionAttempt, ...]) -> None:
        validate_chain(attempts)
        if (
            attempts[-1].outcome not in {"refused", "invalid_response"}
            or attempts[-1].refusal != code
        ):
            raise ValueError("terminal extraction failure must match the last parse observation")
        self.attempts = attempts
        super().__init__(code)


class ExtractionCancelled(asyncio.CancelledError):
    def __init__(self, attempts: tuple[HtmlExtractionAttempt, ...]) -> None:
        validate_chain(attempts)
        if attempts[-1].outcome != "cancelled":
            raise ValueError("cancelled extraction must preserve its cancelled parse observation")
        self.attempts = attempts
        super().__init__()


class InvalidExtractionResponse(GhimeraRefused):
    """Only this adapter's result-validation fault, not a damaged state store."""

    def __init__(self) -> None:
        super().__init__(RefusalCode.ADAPTER_CONTRACT)
