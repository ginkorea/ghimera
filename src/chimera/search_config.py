"""Lightweight search-provider recipe shared by configuration and the adapter."""

import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.searxng/1"] = Field(alias="schema")
    endpoint: str
    language: Annotated[str, Field(min_length=1)]
    safe_search: Annotated[int, Field(strict=True, ge=0, le=2)]
    time_range: Literal["", "day", "month", "year"]

    @model_validator(mode="after")
    def exact_endpoint(self) -> "SearxConfig":
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path
            or any(ord(char) < 33 for char in self.endpoint)
        ):
            raise ValueError("search endpoint must be one credential-free HTTP(S) URL")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("this source connector requires an exact DNS/onion hostname")
        return self
