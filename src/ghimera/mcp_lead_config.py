"""Explicit MCP discovery binding; defaults match the web_search lead envelope."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")]


class McpLeadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.mcp-leads/1"] = Field(alias="schema")
    endpoint: str
    tool_name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")] = "web_search"
    query_argument: Name = "query"
    limit_argument: Name | None = "limit"
    results_path: tuple[Annotated[str, Field(min_length=1)], ...] = ("data", "leads")
    url_field: Name = "url"
    title_field: Name = "title"
    snippet_field: Name = "snippet"

    @model_validator(mode="after")
    def explicit_binding(self) -> "McpLeadConfig":
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path
            or any(ord(char) < 33 for char in self.endpoint)
        ):
            raise ValueError("MCP endpoint must be an exact credential-free HTTP(S) URL")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("MCP endpoint port is invalid")
        if self.query_argument == self.limit_argument:
            raise ValueError("query and limit require distinct argument names")
        return self
