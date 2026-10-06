"""Local clearance gateways: explicit provider wire, private volatile state.

The returned DOM is never treated as raw source evidence. Only declared
clearance cookies are accepted; the ordinary guarded HTTP route refetches the
source and proves that the challenge is gone. No source credentials are sent
to the gateway. Direct routing only: Tor's per-request circuits cannot preserve
the gateway's browser identity, so this adapter never retries Tor directly.
"""

import asyncio
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Annotated
from urllib.parse import urlsplit

from curl_cffi import AsyncCurl, Curl, CurlError, CurlInfo, CurlOpt
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from ghimera.challenge_config import ChallengeConfig, exact_origin
from ghimera.challenge_types import ChallengeEvidence
from ghimera.refusals import GhimeraRefused, RefusalCode


class ChallengeFailure(GhimeraRefused):
    def __init__(self, bytes_read: int) -> None:
        self.bytes_read = bytes_read
        super().__init__(RefusalCode.CHALLENGE_NOT_SOLVED)


class ChallengeCancelled(asyncio.CancelledError):
    def __init__(self, bytes_read: int) -> None:
        self.bytes_read = bytes_read
        super().__init__()


class Cookie(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)
    name: str
    value: SecretStr = Field(repr=False)
    domain: str
    path: str
    secure: bool
    expires: Annotated[float, Field(allow_inf_nan=False)] | None = None


class Solution(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)
    url: str
    status: Annotated[int, Field(strict=True, ge=100, le=599)] | None = None
    user_agent: str = Field(alias="userAgent", repr=False)
    cookies: tuple[Cookie, ...]


class WireSolution(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)
    status: str
    version: str
    solution: Solution | None = None


@dataclass(frozen=True)
class Clearance:
    evidence: ChallengeEvidence
    cookie: SecretStr = field(repr=False)
    user_agent: str = field(repr=False)


class ChallengeSessions:
    def __init__(self, policy: ChallengeConfig) -> None:
        self.policy = ChallengeConfig.model_validate(policy.model_dump())
        self._sessions: OrderedDict[str, Clearance] = OrderedDict()

    def select(self, url: str) -> Clearance | None:
        origin = exact_origin(url)
        selected = self._sessions.get(origin)
        if selected is not None:
            if selected.evidence.expires_at <= time.time():
                del self._sessions[origin]
                return None
            self._sessions.move_to_end(origin)
        return selected

    def discard(self, url: str) -> None:
        self._sessions.pop(exact_origin(url), None)

    async def resolve(
        self, url: str, *, timeout_seconds: float, max_bytes: int
    ) -> tuple[ChallengeEvidence, int]:
        # Import shared libcurl buffer primitives at invocation to avoid the
        # route/config assembly dependency cycle. No model transport is used.
        from ghimera.http import BoundedBody, BoundedHeaders, CurlMulti

        policy = self.policy
        origin = exact_origin(url)
        if origin not in policy.allowed_origins:
            raise ChallengeFailure(0)
        timeout = min(timeout_seconds, policy.timeout_seconds)
        if timeout <= 0:
            raise ChallengeFailure(0)
        payload: dict[str, str | int | bool] = {"cmd": "request.get", "url": url}
        if policy.wire_dialect == "byparr_seconds":
            # Byparr 2.x ignores maxTimeout and returns DOM despite unknown
            # returnOnlyCookies. Declare the real wire; never retain that DOM.
            payload["max_timeout"] = max(1, math.ceil(timeout))
        else:
            # Byparr 3.x interprets <1000 as seconds, unlike FlareSolverr.
            # The local deadline still bounds subsecond caller budgets.
            minimum = 1000 if policy.provider == "byparr" else 1
            payload["maxTimeout"] = max(minimum, int(timeout * 1000))
            payload["returnOnlyCookies"] = True
        if policy.tabs_till_verify is not None:
            payload["tabs_till_verify"] = policy.tabs_till_verify
        raw = json.dumps(payload).encode()
        if len(raw) > policy.max_request_bytes:
            raise ChallengeFailure(0)
        self.discard(url)
        body = BoundedBody(min(max_bytes, policy.max_response_bytes))
        headers = BoundedHeaders(policy.max_header_bytes)
        curl, multi = Curl(), AsyncCurl()
        manager: CurlMulti = multi
        try:
            async with asyncio.timeout(timeout):
                curl.setopt(CurlOpt.URL, policy.endpoint)
                curl.setopt(CurlOpt.PROXY, "")
                curl.setopt(CurlOpt.NOPROXY, "")
                curl.setopt(CurlOpt.NETRC, 0)
                curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
                curl.setopt(CurlOpt.PROTOCOLS_STR, "http")
                curl.setopt(CurlOpt.TIMEOUT_MS, max(1, int(timeout * 1000)))
                curl.setopt(CurlOpt.POSTFIELDS, raw)
                curl.setopt(CurlOpt.HTTPHEADER, [b"Content-Type: application/json"])
                curl.setopt(CurlOpt.WRITEFUNCTION, body.write)
                curl.setopt(CurlOpt.HEADERFUNCTION, headers.write)
                await manager.add_handle(curl)
            if (
                body.exhausted
                or headers.exhausted
                or curl.getinfo(CurlInfo.RESPONSE_CODE) != 200
                or headers.values.get("content-type", "").split(";", 1)[0] != "application/json"
            ):
                raise ChallengeFailure(len(body.data))
            wire = WireSolution.model_validate_json(bytes(body.data))
            clearance = self._clearance(url, wire)
            self._sessions[origin] = clearance
            while len(self._sessions) > policy.session_cache_entries:
                self._sessions.popitem(last=False)
            return clearance.evidence, len(body.data)
        except (CurlError, TimeoutError, ValidationError, ValueError):
            raise ChallengeFailure(len(body.data)) from None
        except asyncio.CancelledError:
            raise ChallengeCancelled(len(body.data)) from None
        finally:
            await manager.close()
            curl.close()

    def _clearance(self, url: str, wire: WireSolution) -> Clearance:
        policy, solution = self.policy, wire.solution
        origin = exact_origin(url)
        if (
            wire.status != "ok"
            or wire.version != policy.provider_version
            or solution is None
            or (policy.provider == "byparr" and solution.status is None)
            or (solution.status is not None and not 200 <= solution.status < 300)
            or exact_origin(solution.url) != origin
            or not solution.user_agent.strip()
            or len(solution.user_agent.encode()) > policy.max_header_bytes
            or any(ord(char) < 32 or ord(char) > 126 for char in solution.user_agent)
        ):
            raise ValueError("challenge solution does not bind the declared origin and provider")
        now = time.time()
        expires = now + policy.session_ttl_seconds
        host = urlsplit(url).hostname
        accepted: dict[str, str] = {}
        for cookie in solution.cookies:
            if cookie.name not in policy.allowed_cookie_names:
                continue  # Never take login/session/tracking cookies from the browser.
            value = cookie.value.get_secret_value()
            if (
                cookie.name in accepted
                or cookie.domain.lstrip(".") != host
                or cookie.path != "/"
                or (cookie.secure and urlsplit(url).scheme != "https")
                or not value
                or any(ord(char) < 33 or ord(char) > 126 or char in ';,"\\' for char in value)
            ):
                raise ValueError("invalid clearance cookie")
            if cookie.expires is not None and cookie.expires > 0:
                if cookie.expires <= now:
                    raise ValueError("expired clearance cookie")
                expires = min(expires, cookie.expires)
            accepted[cookie.name] = value
        cookie_header = "; ".join(f"{name}={value}" for name, value in accepted.items())
        if not accepted or len(cookie_header.encode()) > policy.max_cookie_bytes:
            raise ValueError("missing or oversized clearance")
        return Clearance(
            ChallengeEvidence(
                schema=(
                    "ghimera.challenge-evidence/1"
                    if policy.schema_version == "ghimera.challenges/1"
                    else "ghimera.challenge-evidence/2"
                ),
                origin=origin,
                provider=policy.provider,
                provider_version=policy.provider_version,
                policy_digest=policy.content_digest(),
                cookie_names=tuple(accepted),
                expires_at=expires,
            ),
            SecretStr(cookie_header),
            solution.user_agent,
        )
