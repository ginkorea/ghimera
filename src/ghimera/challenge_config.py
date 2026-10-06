"""Opt-in local browser-gateway policy, distinct from ordinary isolated rendering."""

import hashlib
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
ChallengeProvider = Literal["flaresolverr", "byparr"]
ChallengeDialect = Literal["flaresolverr", "byparr_seconds"]


def exact_origin(url: str) -> str:
    value = urlsplit(url)
    if (
        value.scheme not in {"http", "https"}
        or not value.hostname
        or value.username is not None
        or value.password is not None
        or any(ord(char) < 33 for char in url)
    ):
        raise ValueError("invalid challenge source origin")
    port = value.port
    suffix = f":{port}" if port and port != (443 if value.scheme == "https" else 80) else ""
    return f"{value.scheme}://{value.hostname}{suffix}"


class ChallengeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.challenges/1", "ghimera.challenges/2"] = Field(alias="schema")
    provider: ChallengeProvider
    wire_dialect: ChallengeDialect | None = Field(default=None, exclude_if=lambda v: v is None)
    provider_version: Annotated[str, Field(min_length=1)]
    endpoint: str
    # Unlike the passive renderer, this gateway executes source scripts with
    # network access. Its own egress isolation is an explicit operator duty.
    network_boundary: Literal["operator_managed_local_browser_gateway"]
    allowed_origins: Annotated[tuple[str, ...], Field(min_length=1)]
    allowed_cookie_names: Annotated[tuple[str, ...], Field(min_length=1)]
    max_attempts_per_run: Positive
    timeout_seconds: Seconds
    max_request_bytes: Positive
    max_response_bytes: Positive
    max_header_bytes: Positive
    max_cookie_bytes: Positive
    session_ttl_seconds: Seconds
    session_cache_entries: Positive
    tabs_till_verify: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def explicit(self) -> "ChallengeConfig":
        if self.schema_version == "ghimera.challenges/1":
            if self.provider != "flaresolverr" or self.wire_dialect is not None:
                raise ValueError(
                    "legacy challenge policy only admits the original FlareSolverr wire"
                )
        elif self.wire_dialect is None:
            raise ValueError("version-2 challenge policy requires an explicit wire dialect")
        if self.provider == "flaresolverr" and self.wire_dialect == "byparr_seconds":
            raise ValueError("FlareSolverr requires its millisecond wire dialect")
        if self.provider == "byparr" and self.tabs_till_verify is not None:
            raise ValueError("Byparr does not implement FlareSolverr tabs_till_verify")
        endpoint = urlsplit(self.endpoint)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname is None
            or not ipaddress.ip_address(endpoint.hostname).is_loopback
            or endpoint.port is None
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.path != "/v1"
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("challenge gateway must be an exact loopback HTTP /v1 endpoint")
        for origin in self.allowed_origins:
            if exact_origin(origin) != origin or urlsplit(origin).path:
                raise ValueError("challenge scope must use canonical exact origins")
        if len(set(self.allowed_origins)) != len(self.allowed_origins):
            raise ValueError("challenge origins cannot repeat")
        if len(set(self.allowed_cookie_names)) != len(self.allowed_cookie_names) or any(
            not name or not name.isascii() or not name.replace("_", "").replace("-", "").isalnum()
            for name in self.allowed_cookie_names
        ):
            raise ValueError("configure distinct safe clearance cookie names")
        if not self.provider_version.strip():
            raise ValueError("pin the local provider version")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()
