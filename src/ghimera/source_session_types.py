"""Non-secret exact-destination policy and source-session selection evidence."""

import hashlib
import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


def origin_key(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or not 1 <= port <= 65535
            or any(ord(char) < 33 for char in url)
        ):
            return None
        return parts.scheme, parts.hostname, port
    except ValueError:
        return None


def safe_path(url: str) -> str | None:
    """Do not attach credentials to paths with ambiguous server normalization."""
    try:
        path = urlsplit(url).path or "/"
        decoded = unquote(path, errors="strict")
        if (
            any(code in path.lower() for code in ("%2f", "%5c"))
            or "%" in decoded
            or "\\" in decoded
            or any(part in {".", ".."} for part in decoded.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
        ):
            return None
        return decoded
    except (ValueError, UnicodeError):
        return None


def path_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix if prefix.endswith("/") else prefix + "/")


def credential_header(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+", name)) and (
        name in {"authorization", "cookie"}
        or (name.startswith("x-") and not name.startswith("x-forwarded-") and name != "x-real-ip")
    )


class SourceSessionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.source-session/1"] = Field(alias="schema")
    session_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
    origin: Annotated[str, Field(min_length=1)]
    path_prefixes: Annotated[tuple[str, ...], Field(min_length=1)]
    header_names: Annotated[tuple[str, ...], Field(min_length=1)]
    allow_http: bool

    @model_validator(mode="after")
    def exact_scope(self) -> "SourceSessionPolicy":
        key, parts = origin_key(self.origin), urlsplit(self.origin)
        if key is None or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("source session requires an exact HTTP(S) origin without userinfo")
        if key[0] == "http" and not self.allow_http:
            raise ValueError("plain HTTP source credentials require explicit allow_http")
        host = key[1]
        if (
            host != parts.netloc.split(":")[0]
            or "." not in host
            or host.endswith(".")
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                for label in host.split(".")
            )
        ):
            raise ValueError("source session requires an exact lowercase DNS/onion host")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("source session cannot name an IP literal")
        if len(set(self.path_prefixes)) != len(self.path_prefixes) or any(
            not path.startswith("/")
            or "?" in path
            or "#" in path
            or "%" in path
            or safe_path(self.origin.rstrip("/") + path) != path
            for path in self.path_prefixes
        ):
            raise ValueError("source session requires unambiguous absolute path prefixes")
        if len(set(self.header_names)) != len(self.header_names) or not all(
            credential_header(name) for name in self.header_names
        ):
            raise ValueError("source session declares unique lowercase credential header names")
        return self

    def permits(self, url: str) -> bool:
        path = safe_path(url)
        return (
            origin_key(url) == origin_key(self.origin)
            and path is not None
            and any(path_matches(path, prefix) for prefix in self.path_prefixes)
        )

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()


class SourceSessionUse(BaseModel):
    """Selected policy, not proof that authentication or a source request succeeded."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.source-session-use/1"] = Field(alias="schema")
    session_id: str
    request_url: str
    origin: str
    header_names: tuple[str, ...]
    policy_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    def validate_policy(self, policies: tuple[SourceSessionPolicy, ...], url: str) -> None:
        if not any(
            self.session_id == policy.session_id
            and self.request_url == url
            and self.origin == policy.origin
            and self.header_names == policy.header_names
            and self.policy_digest == policy.content_digest()
            and policy.permits(url)
            for policy in policies
        ):
            raise ValueError("source session selection must bind its exact effective policy")


def validate_sessions(policies: tuple[SourceSessionPolicy, ...]) -> None:
    if len({item.session_id for item in policies}) != len(policies):
        raise ValueError("source session identifiers must be unique")
    for index, left in enumerate(policies):
        for right in policies[index + 1 :]:
            if origin_key(left.origin) == origin_key(right.origin) and any(
                path_matches(a, b) or path_matches(b, a)
                for a in left.path_prefixes
                for b in right.path_prefixes
            ):
                raise ValueError("source session path scopes must not overlap on the same origin")
