"""Native Chromium proxy admission and bounded source-bound Tor probe evidence."""

import hashlib
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ghimera.browser_navigation_types import BrowserNavigationEvidence


class BrowserTorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.browser-tor/1"] = Field(alias="schema")
    proxy_url: str
    probe_url: str
    max_probe_bytes: Annotated[int, Field(strict=True, gt=0)]
    required_resolver_rule: str

    @model_validator(mode="after")
    def scoped(self) -> "BrowserTorConfig":
        proxy, probe = urlsplit(self.proxy_url), urlsplit(self.probe_url)
        try:
            local = ipaddress.ip_address(proxy.hostname or "").is_loopback
            port = proxy.port
        except ValueError:
            local, port = False, None
        if (
            not local
            or proxy.scheme != "socks5"
            or not port
            or proxy.path
            or proxy.query
            or (proxy.fragment or proxy.username is not None or proxy.password is not None)
        ):
            raise ValueError("Tor browser requires an exact caller-owned loopback SOCKS proxy")
        if (
            probe.scheme != "https"
            or not probe.hostname
            or probe.username is not None
            or (probe.password is not None or probe.fragment)
        ):
            raise ValueError("Tor probe requires an explicitly approved HTTPS JSON endpoint")
        if self.required_resolver_rule != f"MAP * ~NOTFOUND, EXCLUDE {proxy.hostname}":
            raise ValueError("Tor resolver rule must disable direct DNS except its loopback proxy")
        return self

    def admit_command_line(self, arguments: tuple[str, ...]) -> None:
        """Narrow route flags; never retain/log the vendor's complete arguments."""
        expected = {
            "--proxy-server": self.proxy_url,
            "--proxy-bypass-list": "<-loopback>",
            "--host-resolver-rules": self.required_resolver_rule,
        }
        actual: dict[str, str] = {}
        for argument in arguments:
            key, _, value = argument.partition("=")
            if key in {
                "--no-proxy-server",
                "--proxy-auto-detect",
                "--proxy-pac-url",
                "--ignore-certificate-errors",
                "--ignore-certificate-errors-spki-list",
                "--allow-insecure-localhost",
            }:
                raise ValueError("Tor browser cannot admit alternate or direct proxy modes")
            if key in expected:
                if key in actual:
                    raise ValueError("Tor browser cannot admit ambiguous route flags")
                actual[key] = value
        if actual != expected:
            raise ValueError("native Chromium route flags do not match the exact Tor policy")


class TorProbeReply(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    is_tor: Literal[True] = Field(alias="IsTor")
    ip: str = Field(alias="IP")

    @field_validator("is_tor", mode="before")
    @classmethod
    def observed_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Tor observation requires an actual true boolean")
        return value

    @model_validator(mode="after")
    def public_address(self) -> "TorProbeReply":
        if not ipaddress.ip_address(self.ip).is_global:
            raise ValueError("Tor probe must observe a public exit address")
        return self


class BrowserTorEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy: BrowserTorConfig
    target_id: str
    probe_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    probe_body: bytes
    native_proxy_flags_verified: Literal[True]
    navigation: BrowserNavigationEvidence
    network: "TorNetworkReply"

    @model_validator(mode="after")
    def observed_probe(self) -> "BrowserTorEvidence":
        if (
            not self.probe_body
            or len(self.probe_body) > self.policy.max_probe_bytes
            or (hashlib.sha256(self.probe_body).hexdigest() != self.probe_sha256)
        ):
            raise ValueError("Tor observation requires its exact bounded native probe body")
        TorProbeReply.model_validate_json(self.probe_body)
        if self.navigation.request_url != self.policy.probe_url or (
            self.navigation.final_url != self.policy.probe_url or len(self.navigation.hops) != 1
        ):
            raise ValueError("Tor probe cannot silently redirect or change its source")
        network = self.network
        if network.request_id != self.navigation.hops[0].network_id or (
            network.response.url != self.policy.probe_url
            or network.response.status != 200
            or network.response.mime_type != "application/json"
            or network.response.from_disk_cache
            or network.response.from_prefetch_cache
            or network.response.from_service_worker
        ):
            raise ValueError("Tor proof requires a native uncached JSON network response")
        return self


class BrowserCommandLine(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    arguments: tuple[str, ...] = Field(repr=False)


class TorNetworkResponse(BaseModel):
    # Drop headers, connection addresses and unrelated vendor fields immediately.
    model_config = ConfigDict(extra="ignore", frozen=True, serialize_by_alias=True)
    url: str
    status: int
    mime_type: str = Field(alias="mimeType")
    from_disk_cache: bool = Field(default=False, alias="fromDiskCache", strict=True)
    from_prefetch_cache: bool = Field(default=False, alias="fromPrefetchCache", strict=True)
    from_service_worker: bool = Field(default=False, alias="fromServiceWorker", strict=True)


class TorNetworkReply(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, serialize_by_alias=True)
    request_id: str = Field(alias="requestId")
    resource_type: Literal["Document"] = Field(alias="type")
    response: TorNetworkResponse


class TorProbeObservation:
    """Bounded metadata callback; does not retain secret vendor response payloads."""

    def __init__(self, source_url: str) -> None:
        self.source_url = source_url
        self.response: TorNetworkReply | None = None
        self.failed = False

    def observe(self, value: object) -> None:
        if not isinstance(value, dict) or value.get("type") != "Document":
            return
        try:
            parsed = TorNetworkReply.model_validate(value)
        except ValidationError:
            self.failed = True
            return
        if parsed.response.url == self.source_url:
            self.response = parsed


BrowserTorEvidence.model_rebuild()
