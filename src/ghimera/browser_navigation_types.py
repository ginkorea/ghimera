"""Bounded caller-delegated main-frame navigation, not browser-wide egress control."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.source_session_types import origin_key, safe_path

if TYPE_CHECKING:
    from ghimera.human_browser_types import HumanBrowserConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class BrowserNavigationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.browser-navigation/1"] = Field(alias="schema")
    adapter_revision: Literal["ghimera-chromium-navigation-guard/1"]
    interception_owner: Literal["exclusive_main_frame_during_operation"]
    max_redirects: Annotated[int, Field(strict=True, ge=0)]
    admission_timeout_seconds: PositiveSeconds
    cleanup_timeout_seconds: PositiveSeconds

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class BrowserNavigationAdmission(Protocol):
    """The run owner checks scope, robots, cadence and budget before continuation.

    Raises the existing GhimeraRefused vocabulary on denial. The implementation
    must not navigate the borrowed page or recursively use this interception
    session. Returning is admission, not evidence that the request succeeded.
    """

    async def admit(self, url: str) -> None: ...


class BrowserNavigationHop(BaseModel):
    """Only native CDP chain identifiers and URL; never headers or cookies."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    request_id: Identifier
    redirected_from: Identifier | None
    frame_id: Identifier
    network_id: Identifier

    @model_validator(mode="after")
    def source_url(self) -> BrowserNavigationHop:
        if origin_key(self.url) is None or safe_path(self.url) is None:
            raise ValueError("navigation hops require unambiguous HTTP(S) URLs")
        return self


class BrowserNavigationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.browser-navigation-evidence/1"] = Field(alias="schema")
    adapter_revision: Literal["ghimera-chromium-navigation-guard/1"]
    policy_digest: Digest
    browser_policy_digest: Digest
    target_id: Identifier
    request_url: str
    final_url: str
    hops: Annotated[tuple[BrowserNavigationHop, ...], Field(min_length=1)]
    observation: Literal["admitted_before_main_frame_request"]
    browser_subresource_requests: None

    @model_validator(mode="after")
    def continuous_chain(self) -> BrowserNavigationEvidence:
        first = self.hops[0]
        if (
            first.url != self.request_url
            or first.redirected_from is not None
            or self.hops[-1].url != self.final_url
            or len({hop.url for hop in self.hops}) != len(self.hops)
            or len({hop.request_id for hop in self.hops}) != len(self.hops)
        ):
            raise ValueError("navigation evidence must bind one non-looping native chain")
        previous = first
        for hop in self.hops[1:]:
            if (
                hop.redirected_from != previous.request_id
                or hop.frame_id != first.frame_id
                or hop.network_id != first.network_id
            ):
                raise ValueError("redirects must continue the same native frame/network chain")
            previous = hop
        return self

    def validate_policy(self, policy: BrowserNavigationConfig, browser: HumanBrowserConfig) -> None:
        from ghimera.human_browser_types import HumanBrowserConfig

        checked = BrowserNavigationEvidence.model_validate(self.model_dump())
        policy = BrowserNavigationConfig.model_validate(policy.model_dump())
        browser = HumanBrowserConfig.model_validate(browser.model_dump())
        if (
            checked.policy_digest != policy.content_digest()
            or checked.browser_policy_digest != browser.content_digest()
            or checked.adapter_revision != policy.adapter_revision
            or checked.target_id != browser.target_id
            or len(checked.hops) - 1 > policy.max_redirects
            or any(not browser.permits(hop.url) for hop in checked.hops)
        ):
            raise ValueError("navigation evidence must bind its exact effective policies")
