"""Explicit discovery bindings and strategy; no endpoints or entitlements inferred."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.ahmia_config import AhmiaConfig
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.mcp_lead_config import McpLeadConfig
from ghimera.search_config import SearxConfig

Positive = Annotated[int, Field(strict=True, gt=0)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Domain = Literal["open_web", "onion"]


def _digest(value: BaseModel) -> str:
    raw = json.dumps(
        value.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class DiscoveryProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    id: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
    domains: Annotated[tuple[Domain, ...], Field(min_length=1)]
    binding: SearxConfig | McpLeadConfig | AhmiaConfig | CorpusSearchConfig
    # An operator must approve disclosure and retention separately from fetch scope.
    query_disclosure: Literal["planned_query"]
    use_contract: Literal["crawl_and_retain"]
    max_calls: Positive
    byte_budget: Positive
    call_seconds_budget: Seconds
    max_response_bytes: Positive
    max_results: Positive
    timeout_seconds: Seconds
    consecutive_failure_limit: Positive

    @model_validator(mode="after")
    def coherent(self) -> "DiscoveryProvider":
        if isinstance(self.binding, AhmiaConfig) and self.domains != ("onion",):
            raise ValueError("Ahmia bindings support onion discovery only")
        if len(set(self.domains)) != len(self.domains):
            raise ValueError("provider domains must be unique")
        if self.max_response_bytes > self.byte_budget:
            raise ValueError("provider response cap exceeds its byte budget")
        if self.timeout_seconds > self.call_seconds_budget:
            raise ValueError("provider timeout exceeds its service-call seconds budget")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return "binding:" + self.id, "discovery-binding/1:" + _digest(self)


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.discovery/1"] = Field(alias="schema")
    providers: Annotated[tuple[DiscoveryProvider, ...], Field(min_length=1)]
    target_domains: Annotated[tuple[Domain, ...], Field(min_length=1)]
    allow_cross_domain_expansion: bool
    mode: Literal["ordered_fallback", "fanout"]
    cold_start_fanout: bool
    provider_concurrency: Positive
    stagnation_window: Positive
    min_new_documents: Positive
    min_new_answers: Positive
    max_strategy_changes: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def coherent(self) -> "DiscoveryConfig":
        if len({provider.id for provider in self.providers}) != len(self.providers):
            raise ValueError("discovery provider IDs must be unique")
        if len(set(self.target_domains)) != len(self.target_domains):
            raise ValueError("discovery target domains must be unique")
        if len(self.target_domains) > 1 and not self.allow_cross_domain_expansion:
            raise ValueError("cross-domain discovery requires explicit expansion permission")
        supported = {domain for provider in self.providers for domain in provider.domains}
        if not set(self.target_domains) <= supported:
            raise ValueError("every target domain needs a configured discovery provider")
        if any(not set(p.domains) & set(self.target_domains) for p in self.providers):
            raise ValueError("each discovery provider must cover a selected target domain")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return "discovery", "discovery/1:" + _digest(self)


class SearchCallLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_bytes: Positive
    limit: Positive
    timeout_seconds: Seconds


class DiscoveryProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    new_documents: Annotated[int, Field(strict=True, ge=0)]
    new_answers: Annotated[int, Field(strict=True, ge=0)]
