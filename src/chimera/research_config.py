"""Operator-owned discovery/research limits, independent of model and provider code."""

import ipaddress
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.research/1"] = Field(alias="schema")
    max_rounds: Positive
    max_questions: Positive
    max_queries_per_round: Positive
    query_budget: Positive
    results_per_query: Positive
    search_concurrency: Positive
    max_pages_per_round: Positive
    max_query_chars: Positive
    max_answer_chars: Positive
    max_model_input_chars: Positive
    min_answer_confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    require_distinct_reviewer: bool
    source_policy: Literal["grounded_public", "configured_only"]
    allowed_hosts: tuple[str, ...]
    denied_hosts: tuple[str, ...]
    max_source_hosts: Positive
    allowed_ports: Annotated[
        tuple[Annotated[int, Field(strict=True, ge=1, le=65535)], ...], Field(min_length=1)
    ]
    content_types: Annotated[tuple[str, ...], Field(min_length=1)]
    max_depth: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def coherent(self) -> "ResearchConfig":
        if self.source_policy == "configured_only" and not self.allowed_hosts:
            raise ValueError("configured-only discovery needs explicit hosts")
        if self.search_concurrency > self.max_queries_per_round:
            raise ValueError("search concurrency exceeds per-round queries")
        if len(self.allowed_hosts) > self.max_source_hosts:
            raise ValueError("configured scope exceeds host budget")
        if set(self.allowed_hosts) & set(self.denied_hosts):
            raise ValueError("source policy cannot both allow and deny the same host")
        for host in self.allowed_hosts + self.denied_hosts:
            if (
                host != host.lower()
                or host.endswith(".")
                or "." not in host
                or any(
                    not label or not label.replace("-", "").isalnum() for label in host.split(".")
                )
            ):
                raise ValueError("source policy needs exact lowercase DNS/onion hosts")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("source policies cannot permit IP literals")
        return self
