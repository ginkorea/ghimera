"""Versioned operator routing inputs, separate from HTTP/browser implementation."""

import ipaddress
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

RouteMode = Literal["direct", "tor"]


class TorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.tor/1"] = Field(alias="schema")
    proxy_endpoint: str
    bridge_directory: Path
    connect_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    allow_open_web: bool
    allow_onion: bool
    allowed_ports: Annotated[
        tuple[Annotated[int, Field(strict=True, ge=1, le=65535)], ...], Field(min_length=1)
    ]
    isolation: Literal["request"]

    @model_validator(mode="after")
    def local_hostname_proxy(self) -> "TorPolicy":
        try:
            value = urlsplit(self.proxy_endpoint)
            if (
                value.scheme != "socks5h"
                or value.hostname is None
                or value.port is None
                or not ipaddress.ip_address(value.hostname).is_loopback
                or value.username is not None
                or value.password is not None
                or value.path
                or value.query
                or value.fragment
            ):
                raise ValueError("proxy must be a credential-free loopback socks5h endpoint")
        except ValueError:
            raise ValueError("proxy must be a credential-free loopback socks5h endpoint") from None
        if not self.allow_open_web and not self.allow_onion:
            raise ValueError("Tor must permit at least one destination class")
        if not self.bridge_directory.is_absolute() or len(str(self.bridge_directory).encode()) > 70:
            raise ValueError("bridge directory must be absolute and short enough for a Unix socket")
        return self


class SourceRoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: Annotated[str, Field(min_length=1)]
    route: RouteMode

    @model_validator(mode="after")
    def exact_host(self) -> "SourceRoutingRule":
        if (
            self.host != self.host.lower()
            or self.host.endswith(".")
            or "." not in self.host
            or any(
                not label or not label.replace("-", "").isalnum() for label in self.host.split(".")
            )
        ):
            raise ValueError("route scope must be an exact lowercase DNS/onion host")
        return self


class TransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.transport/1"] = Field(alias="schema")
    default_route: RouteMode
    rules: tuple[SourceRoutingRule, ...]
    allow_cross_network_redirects: bool
    allow_route_change_redirects: bool
    tor: TorPolicy | None = None

    @model_validator(mode="after")
    def coherent(self) -> "TransportConfig":
        if len({rule.host for rule in self.rules}) != len(self.rules):
            raise ValueError("ambiguous host routing")
        if (
            self.default_route == "tor" or any(rule.route == "tor" for rule in self.rules)
        ) and self.tor is None:
            raise ValueError("Tor routing requires explicit Tor policy")
        if any(rule.host.endswith(".onion") and rule.route != "tor" for rule in self.rules):
            raise ValueError("onion destinations can never use direct routing")
        return self

    def mode_for(self, host: str) -> RouteMode:
        if host.endswith(".onion"):
            return "tor"
        return next((rule.route for rule in self.rules if rule.host == host), self.default_route)


class TransportEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.transport-evidence/1"] = Field(alias="schema")
    mode: RouteMode
    network_class: Literal["open_web", "onion"]
    connector_revision: str
    target_dns: Literal["local_pinned", "tor_remote_pinned", "onion_no_dns"]
    # Non-secret local endpoint only. No circuit-isolation token is retained.
    proxy_endpoint: str | None = None
