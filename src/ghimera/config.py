"""One parse boundary for operator configuration; no environment or hidden defaults."""

import ipaddress
import tomllib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.browser_config import BrowserConfig
from ghimera.challenge_config import ChallengeConfig
from ghimera.continuation_config import ContinuationConfig
from ghimera.dedup_config import DedupConfig
from ghimera.document_config import DocumentExtractionConfig
from ghimera.extraction_config import ExtractionConfig
from ghimera.graph_types import GraphConfig
from ghimera.human_browser_types import HumanBrowserConfig
from ghimera.journal_config import JournalConfig
from ghimera.local_input_types import LocalInputConfig
from ghimera.model_config import ModelBindingsConfig
from ghimera.reference_config import ReferenceConfig
from ghimera.research_config import ResearchConfig
from ghimera.scoring_config import ScoringConfig
from ghimera.search_config import SearxConfig
from ghimera.semantic_types import SemanticConfig
from ghimera.source_session_types import SourceSessionPolicy, validate_sessions
from ghimera.transport_types import TransportConfig

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


class GhimeraConfig(BaseModel):
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
    human_browser: HumanBrowserConfig | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    scoring: ScoringConfig | None = None
    search: SearxConfig | None = Field(default=None, exclude_if=lambda value: value is None)
    references: ReferenceConfig | None = None
    source_sessions: tuple[SourceSessionPolicy, ...] = ()
    challenges: ChallengeConfig | None = Field(default=None, exclude_if=lambda value: value is None)
    local_inputs: LocalInputConfig | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    semantics: SemanticConfig | None = Field(default=None, exclude_if=lambda v: v is None)
    continuation: ContinuationConfig | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def consistent(self) -> "GhimeraConfig":
        validate_sessions(self.source_sessions)
        if self.continuation is not None and (self.journal is None or self.research is None):
            raise ValueError("continuation requires the existing journal and research policy")
        if self.research is not None and self.research.graph_context is not None:
            planning, semantic, graph = self.research.graph_context, self.semantics, self.graph
            if (
                semantic is None
                or graph is None
                or not set(planning.entity_roles) <= set(semantic.entity_roles)
                or not set(planning.relation_rules) <= set(semantic.relation_rules)
            ):
                raise ValueError("graph planning requires its configured extraction ontology")
            relations = {rule.name: rule for rule in graph.relations}
            if any(
                not set(relations[name].source_roles + relations[name].target_roles)
                <= set(planning.entity_roles)
                for name in planning.relation_rules
                if name in relations
            ):
                raise ValueError("graph planning relations require all endpoint roles")
        if self.semantics is not None:
            graph, models, semantic = self.graph, self.models, self.semantics
            if (
                semantic.failure is not None
                and self.research is not None
                and (
                    self.research.graph_context is None
                    or self.research.graph_context.schema_version != "ghimera.graph-planning/4"
                )
            ):
                raise ValueError("failure continuation requires explicit graph-planning/4 gaps")
            if graph is None or not graph.enabled or not graph.capture_semantics or models is None:
                raise ValueError(
                    "semantic extraction requires enabled semantic graph and bound models"
                )
            roles = {item.name for item in graph.roles}
            if not set(semantic.entity_roles) <= roles or set(semantic.entity_roles) & {
                "intent",
                "source",
                "document",
                "question",
                "query",
            }:
                raise ValueError("semantic extraction requires explicit entity roles")
            rules = {item.name: item for item in graph.relations}
            mention = rules.get(semantic.mention_rule)
            if (
                mention is None
                or not mention.semantic
                or "document" not in mention.source_roles
                or not set(semantic.entity_roles) <= set(mention.target_roles)
            ):
                raise ValueError("semantic mention rule must bind documents to configured entities")
            for name in semantic.relation_rules:
                relation = rules.get(name)
                if (
                    relation is None
                    or not relation.semantic
                    or not set(relation.source_roles + relation.target_roles)
                    <= set(semantic.entity_roles)
                ):
                    raise ValueError("semantic relations require the configured entity ontology")
            service = models.service(semantic.model_role)
            if semantic.window_chars > min(service.context.max_chars, service.context.window_chars):
                raise ValueError("semantic window exceeds the bound model's native context")
            if semantic.verification is not None:
                reviewer = models.service(semantic.verification.model_role)
                if (
                    semantic.verification.prompt_profile
                    in {
                        "proposal_date_checks",
                        "independent_dimension_checks",
                        "native_quote_checks",
                    }
                    and reviewer.response_format != "json_schema"
                ):
                    raise ValueError("proposal date checks require provider json_schema responses")
                if reviewer.model_id == service.model_id:
                    raise ValueError("semantic verification requires a distinct declared model")
                if semantic.window_chars > min(
                    reviewer.context.max_chars, reviewer.context.window_chars
                ):
                    raise ValueError("semantic window exceeds the verifier's native context")
                if (
                    self.research is not None
                    and self.research.graph_context is not None
                    and self.research.graph_context.schema_version == "ghimera.graph-planning/1"
                ):
                    raise ValueError(
                        "verified semantic planning requires graph-planning/2 or /3 gap reporting"
                    )
        if self.local_inputs is not None and (
            self.document_extraction is None
            or self.local_inputs.max_input_bytes > self.document_extraction.max_input_bytes
        ):
            raise ValueError("local inputs require matching bounded document extraction")
        if self.challenges is not None:
            if self.http is None:
                raise ValueError("challenges require the ordinary HTTP policy")
            for origin in self.challenges.allowed_origins:
                value = urlsplit(origin)
                host = value.hostname or ""
                if host.endswith(".onion") or (
                    self.transport is not None and self.transport.mode_for(host) != "direct"
                ):
                    raise ValueError("this gateway cannot preserve Tor request identity")
                if value.scheme != "https" and self.http.network.mode != "loopback_fixture":
                    raise ValueError("public clearance cookies require HTTPS")
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
    def from_toml(cls, path: Path, *, max_bytes: int | None = None) -> "GhimeraConfig":
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
            raise ValueError("configuration byte allowance must be a positive integer")
        with path.open("rb") as stream:
            if max_bytes is None:
                return cls.model_validate(tomllib.load(stream))
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("configuration exceeds its explicit byte allowance")
        return cls.model_validate(tomllib.loads(raw.decode("utf-8")))
