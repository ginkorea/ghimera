"""Explicit self-hosted HTTPS control origins; never a generic public JSON policy."""

import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.private_service_config import PrivateJsonPolicy

GatewayAddressScope = Literal["global", "global_or_shared"]


def _gateway_pin(address: str, scope: GatewayAddressScope) -> None:
    value = ipaddress.ip_address(address)
    shared = (
        scope == "global_or_shared"
        and isinstance(value, ipaddress.IPv4Address)
        and value in ipaddress.IPv4Network("100.64.0.0/10")
    )
    if (
        str(value) != address
        or not (value.is_global or shared)
        or value.is_private
        or value.is_loopback
        or value.is_link_local
        or value.is_multicast
        or value.is_unspecified
        or value.is_reserved
        # Special-use platform/metadata endpoints remain excluded even when
        # their numbering overlaps an admitted scope. Domain safety invariants,
        # never operator pins or an allocation-specific configuration.
        or address in {"168.63.129.16", "100.100.100.200"}
        or isinstance(value, ipaddress.IPv6Address)
        and (
            value.ipv4_mapped is not None
            or value.sixtofour is not None
            or value.teredo is not None
            or value in ipaddress.IPv6Network("64:ff9b::/96")
            or value in ipaddress.IPv6Network("64:ff9b:1::/48")
        )
    ):
        raise ValueError("gateway pins must be canonical unicast addresses within the exact scope")


def _https_origin(endpoint: str, scope: GatewayAddressScope) -> str:
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    port = parsed.port
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in endpoint
        or "#" in endpoint
        or port == 0
        or any(ord(char) < 33 or ord(char) > 126 for char in endpoint)
        or "\\" in endpoint
    ):
        raise ValueError("gateway requires one credential-free exact HTTPS origin")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in host.split(".")
        ):
            raise ValueError("gateway host must be an exact canonical ASCII DNS name") from None
        authority = host
    else:
        _gateway_pin(host, scope)
        authority = f"[{host}]" if ":" in host else host
    if port is not None and port != 443:
        authority += ":" + str(port)
    canonical = "https://" + authority
    if parsed.netloc != authority or not endpoint.startswith(canonical):
        raise ValueError("gateway origin must use canonical host and port spelling")
    return canonical


class SelfHostedGatewayConfig(BaseModel):
    """Operator-declared origin approval, carried in native recipes and evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.self-hosted-gateway/1"] = Field(alias="schema")
    origin: Annotated[str, Field(min_length=1)]
    address_scope: GatewayAddressScope = Field(
        default="global", exclude_if=lambda value: value == "global"
    )

    @model_validator(mode="after")
    def exact_origin(self) -> "SelfHostedGatewayConfig":
        if _https_origin(self.origin, self.address_scope) != self.origin:
            raise ValueError("gateway origin cannot contain a path or trailing slash")
        return self


def validate_model_gateway(policy: PrivateJsonPolicy, gateway: SelfHostedGatewayConfig) -> None:
    gateway = SelfHostedGatewayConfig.model_validate(gateway.model_dump())
    if (
        _https_origin(policy.endpoint, gateway.address_scope) != gateway.origin
        or policy.allow_plaintext
        or policy.allow_plaintext_credentials
        or not policy.approved_addresses
        or len(set(policy.approved_addresses)) != len(policy.approved_addresses)
    ):
        raise ValueError(
            "model gateway requires exact origin, TLS and distinct explicit scoped pins"
        )
    for address in policy.approved_addresses:
        _gateway_pin(address, gateway.address_scope)
    host = urlsplit(policy.endpoint).hostname
    if host is None:
        raise ValueError("gateway lost its exact host")
    try:
        literal = str(ipaddress.ip_address(host))
    except ValueError:
        pass
    else:
        if literal not in policy.approved_addresses:
            raise ValueError("literal gateway endpoint is not an approved scoped pin")
