"""One parse boundary for operator configuration; no environment or hidden defaults."""

import ipaddress
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chimera.browser_config import BrowserConfig
from chimera.dedup_config import DedupConfig
from chimera.document_config import DocumentExtractionConfig
from chimera.extraction_config import ExtractionConfig
from chimera.graph_types import GraphConfig
from chimera.journal_config import JournalConfig
from chimera.model_config import ModelBindingsConfig
from chimera.reference_config import ReferenceConfig
from chimera.research_config import ResearchConfig
from chimera.scoring_config import ScoringConfig
from chimera.source_session_types import SourceSessionPolicy, validate_sessions
from chimera.transport_types import TransportConfig

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
PositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class NetworkPolicy(BaseModel):
    """Public crawling cannot inherit a test-network exception."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.network/1"] = Field(alias="schema")
    mode: Literal["public", "loopback_fixture"]
    allowed_ports: tuple[Annotated[int, Field(strict=True, ge=1, le=65535)], ...] = (80, 443)
    fixture_addresses: tuple[str, ...] = ()
    fixture_ports: tuple[Annotated[int, Field(strict=True, ge=1, le=65535)], ...] = ()

    @model_validator(mode="after")
    def isolated(self) -> "NetworkPolicy":
        if self.mode == "public" and (self.fixture_addresses or self.fixture_ports):
            raise ValueError("public networking cannot carry fixture exceptions")
        if self.mode == "loopback_fixture":
            if not self.fixture_addresses or not self.fixture_ports:
                raise ValueError("fixture mode requires exact addresses and ports")
            if any(not ipaddress.ip_address(value).is_loopback for value in self.fixture_addresses):
                raise ValueError("fixtures may permit only loopback addresses")
        return self


class RobotsDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision_id: Annotated[str, Field(min_length=1)]
    decided_by: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    allowed_hosts: Annotated[tuple[str, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def nonblank(self) -> "RobotsDecision":
        if not all(value.strip() for value in (self.decision_id, self.decided_by, self.reason)):
            raise ValueError("robots override must identify a decision, actor and reason")
        if any(host != host.lower() or "/" in host or ":" in host for host in self.allowed_hosts):
            raise ValueError("override scope must be exact lowercase host names")
        return self


class RobotsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.robots/1"] = Field(alias="schema")
    mode: Literal["honor", "recorded_override"]
    product_token: Annotated[str, Field(pattern=r"^[A-Za-z_-]+$")]
    decision: RobotsDecision | None = None

    @model_validator(mode="after")
    def explicit_override(self) -> "RobotsPolicy":
        if (self.mode == "recorded_override") != (self.decision is not None):
            raise ValueError("only recorded_override requires a scoped decision record")
        return self


class HttpPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.http/1"] = Field(alias="schema")
    max_response_bytes: PositiveInt
    max_header_bytes: PositiveInt
    max_redirects: Annotated[int, Field(strict=True, ge=0)]
    retry_backoff_seconds: PositiveFloat
    retry_jitter_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    robots_cache_seconds: PositiveFloat
    conditional_cache_entries: PositiveInt
    network: NetworkPolicy
    robots: RobotsPolicy


class ChimeraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    schema_version: Literal["chimera.config/1"] = Field(alias="schema")
    page_budget: PositiveInt
    byte_budget: PositiveInt
    wall_seconds: PositiveFloat
    judge_budget: PositiveInt
    grade_interval: PositiveInt
    grade_threshold: Probability
    saturation_window: PositiveInt
    saturation_min_new: PositiveInt
    max_links_per_page: PositiveInt
    min_link_score: Probability
    request_timeout_seconds: PositiveFloat
    per_host_delay_seconds: PositiveFloat
    per_host_concurrency: PositiveInt
    global_concurrency: PositiveInt
    global_requests_per_second: PositiveFloat
    retry_budget: Annotated[int, Field(strict=True, ge=0)]
    impersonation_profile: Annotated[str, Field(min_length=1)]
    user_agent: Annotated[str, Field(min_length=1)]
    egress_feature: Literal["crawl_egress"]
    model_policy: Literal["self_hosted_only"]
    http: HttpPolicy | None = None
    graph: GraphConfig | None = None
    journal: JournalConfig | None = Field(default=None, exclude_if=lambda value: value is None)
    transport: TransportConfig | None = None
    research: ResearchConfig | None = None
    models: ModelBindingsConfig | None = None
    extraction: ExtractionConfig | None = None
    document_extraction: DocumentExtractionConfig | None = None
    dedup: DedupConfig | None = None
    browser: BrowserConfig | None = None
    scoring: ScoringConfig | None = None
    references: ReferenceConfig | None = None
    source_sessions: tuple[SourceSessionPolicy, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> "ChimeraConfig":
        validate_sessions(self.source_sessions)
        if self.source_sessions and self.http is None:
            raise ValueError("source sessions require an HTTP policy")
        if (
            self.references is not None
            and self.references.discover_cited_by
            and self.research is None
        ):
            raise ValueError("cited-by discovery requires a research policy")
        if self.saturation_min_new > self.saturation_window:
            raise ValueError("saturation_min_new cannot exceed saturation_window")
        if self.per_host_concurrency > self.global_concurrency:
            raise ValueError("per_host_concurrency cannot exceed global_concurrency")
        if "\n" in self.user_agent or "\r" in self.user_agent:
            raise ValueError("user_agent must be one HTTP header line")
        if self.http is not None and self.http.robots.product_token not in self.user_agent:
            raise ValueError("robots product_token must identify this user_agent")
        if (
            self.models is not None
            and self.research is not None
            and self.research.require_distinct_reviewer
        ):
            analyst, reviewer = self.models.analyst, self.models.reviewer
            if (analyst.model_id, analyst.revision) == (reviewer.model_id, reviewer.revision):
                raise ValueError("research requires a distinct configured reviewer identity")
        return self

    @classmethod
    def from_toml(cls, path: Path) -> "ChimeraConfig":
        with path.open("rb") as stream:
            return cls.model_validate(tomllib.load(stream))
