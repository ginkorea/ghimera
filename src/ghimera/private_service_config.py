"""Pure configuration for private services; no source or model transport imports."""

import ipaddress
from typing import Annotated, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrivateJsonPolicy(Protocol):
    @property
    def endpoint(self) -> str: ...
    @property
    def approved_addresses(self) -> tuple[str, ...]: ...
    @property
    def allow_plaintext(self) -> bool: ...
    @property
    def allow_plaintext_credentials(self) -> bool: ...
    @property
    def authorization(self) -> Literal["none", "bearer", "api_key"]: ...
    @property
    def timeout_seconds(self) -> float: ...
    @property
    def max_request_bytes(self) -> int: ...
    @property
    def max_response_bytes(self) -> int: ...
    @property
    def max_header_bytes(self) -> int: ...


def validate_private_json(policy: PrivateJsonPolicy) -> None:
    parsed = urlsplit(policy.endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.port == 0
        or (parsed.scheme == "http" and not policy.allow_plaintext)
        or any(ord(char) < 33 for char in policy.endpoint)
        or not policy.approved_addresses
    ):
        raise ValueError("configure one credential-free approved private JSON endpoint")
    for address in policy.approved_addresses:
        value = ipaddress.ip_address(address)
        if (
            str(value) != address
            or not value.is_private
            or value.is_link_local
            or value.is_multicast
            or value.is_unspecified
            or (value.is_reserved and not value.is_loopback)
        ):
            raise ValueError("private service addresses must be exact private/loopback addresses")
    try:
        literal = str(ipaddress.ip_address(parsed.hostname))
    except ValueError:
        pass
    else:
        if literal not in policy.approved_addresses:
            raise ValueError("literal private service endpoint is not approved")


Positive = Annotated[int, Field(strict=True, gt=0)]


class PrivateJsonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    endpoint: Annotated[str, Field(min_length=1)]
    approved_addresses: Annotated[tuple[str, ...], Field(min_length=1)]
    allow_plaintext: bool
    allow_plaintext_credentials: bool
    authorization: Literal["none", "bearer", "api_key"]
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_request_bytes: Positive
    max_response_bytes: Positive
    max_header_bytes: Positive

    @model_validator(mode="after")
    def private_service(self) -> "PrivateJsonConfig":
        validate_private_json(self)
        return self
