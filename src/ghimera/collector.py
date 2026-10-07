"""Configured real-adapter assembly; no registry, credential discovery or model launch."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

from ghimera.browser import IsolatedBrowserRenderer
from ghimera.config import GhimeraConfig
from ghimera.discovery import DiscoveryProviders
from ghimera.documents import DocumentExtractionSuite, DocumentExtractor
from ghimera.embedding import SelfHostedEncoder
from ghimera.embedding_types import EmbeddingReferences
from ghimera.extraction import HtmlExtractor
from ghimera.fetch import FetchLadder, FetchRoute
from ghimera.http import CurlRoute
from ghimera.human_browser import ChromiumHumanSession
from ghimera.human_browser_route import HumanBrowserRoute
from ghimera.human_browser_types import HumanAssistant
from ghimera.loop import GoalLoop
from ghimera.mcp_lead_config import McpLeadConfig
from ghimera.mcp_leads import McpLeadClient, McpLeadSearch
from ghimera.model_client import SelfHostedModels
from ghimera.models import Goal, Harvest, Scope
from ghimera.ports import Extractor
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, ResearchResult
from ghimera.search import GroundedSearch
from ghimera.searxng import SearxHtmlSearch, SearxSearch
from ghimera.semantic_scoring import EmbeddingScorer
from ghimera.source_sessions import SourceCredentials
from ghimera.transport import Resolver

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


class Collector:
    """One reusable configuration, fresh run state, and existing invariant-owning ports.

    Ports remain available through their lower-level APIs for alternate providers.
    This facade assembles only concrete, shipped adapters, never test doubles.
    """

    def __init__(
        self,
        config: GhimeraConfig,
        *,
        references: EmbeddingReferences | None = None,
        model_credentials: Mapping[str, SecretStr] | None = None,
        encoder_credential: SecretStr | None = None,
        source_credentials: Mapping[str, SourceCredentials] | None = None,
        source_resolver: Resolver | None = None,
        human_assistant: HumanAssistant | None = None,
        mcp_client: McpLeadClient | None = None,
        discovery_clients: Mapping[str, McpLeadClient] | None = None,
    ) -> None:
        # Revalidate injected models: model_copy(update=...) can bypass guards.
        config = GhimeraConfig.model_validate(config.model_dump())
        human_session = (
            ChromiumHumanSession(config.human_browser, assistant=human_assistant)
            if config.human_browser is not None
            else None
        )
        if human_assistant is not None and config.human_browser is None:
            raise ValueError("human assistance requires an explicit browser recipe")
        if (
            config.http is None
            or config.research is None
            or (config.search is None and config.discovery is None)
            or config.models is None
            or config.scoring is None
            or config.extraction is None
        ):
            raise ValueError(
                "Collector requires http, research, search, models, scoring and extraction recipes"
            )
        self._content_types = HTML_TYPES | (
            config.document_extraction.supported_content_types
            if config.document_extraction is not None
            else frozenset()
        )
        if not set(config.research.content_types) <= self._content_types:
            raise ValueError(
                "research content types require matching configured extraction adapters"
            )
        search: GroundedSearch | DiscoveryProviders
        if config.discovery is not None:
            if mcp_client is not None:
                raise ValueError("discovery uses explicit provider-keyed MCP clients")
            clients = dict(discovery_clients or {})
            expected = {
                p.id for p in config.discovery.providers if isinstance(p.binding, McpLeadConfig)
            }
            if set(clients) != expected:
                raise ValueError("every discovery MCP binding requires exactly its provider client")
            adapters: dict[str, GroundedSearch] = {}
            for provider in config.discovery.providers:
                binding = provider.binding
                if isinstance(binding, McpLeadConfig):
                    adapters[provider.id] = McpLeadSearch(binding, clients[provider.id])
                else:
                    provider_type = (
                        SearxHtmlSearch if binding.response_format == "html" else SearxSearch
                    )
                    adapters[provider.id] = provider_type(config, binding, resolver=source_resolver)
            search = DiscoveryProviders(config.discovery, adapters)
        elif discovery_clients is not None:
            raise ValueError("discovery clients require a discovery recipe")
        elif isinstance(config.search, McpLeadConfig):
            if mcp_client is None:
                raise ValueError("MCP discovery requires an explicitly bound MCP client")
            search = McpLeadSearch(config.search, mcp_client)
        else:
            if config.search is None:
                raise ValueError("Collector requires a discovery binding")
            if mcp_client is not None:
                raise ValueError("MCP client requires an MCP discovery recipe")
            search_type = (
                SearxHtmlSearch if config.search.response_format == "html" else SearxSearch
            )
            search = search_type(config, config.search, resolver=source_resolver)
        models = SelfHostedModels.from_config(config, credentials=model_credentials)
        encoder = SelfHostedEncoder(config.scoring.encoder, credential=encoder_credential)
        scorer = EmbeddingScorer(config.scoring, encoder, references)
        route = CurlRoute(config, resolver=source_resolver, source_credentials=source_credentials)
        routes: tuple[FetchRoute, ...] = (route,)
        if human_session is not None and config.human_browser is not None:
            routes = (HumanBrowserRoute(config.human_browser, human_session), route)
        extractor: Extractor = HtmlExtractor(config)
        if config.document_extraction is not None:
            extractor = DocumentExtractionSuite(html=extractor, documents=DocumentExtractor(config))
        renderer = IsolatedBrowserRenderer(config) if config.browser is not None else None
        collection = GoalLoop(
            config=config,
            fetcher=FetchLadder(routes, renderer=renderer),
            extractor=extractor,
            scorer=scorer,
            judge=models.judge,
            semantic_extractor=models.service(config.semantics.model_role)
            if config.semantics is not None
            else None,
            semantic_reviewer=models.service(config.semantics.verification.model_role)
            if config.semantics is not None and config.semantics.verification is not None
            else None,
        )
        research = ResearchLoop(
            config=config,
            collector=collection,
            search=search,
            planner=models.planner,
            analyst=models.analyst,
            reviewer=models.reviewer,
        )
        self._config, self._collection, self._research = config, collection, research

    @classmethod
    def from_toml(
        cls,
        path: Path,
        *,
        max_config_bytes: int,
        references: EmbeddingReferences | None = None,
        model_credentials: Mapping[str, SecretStr] | None = None,
        encoder_credential: SecretStr | None = None,
        source_credentials: Mapping[str, SourceCredentials] | None = None,
        source_resolver: Resolver | None = None,
        human_assistant: HumanAssistant | None = None,
        mcp_client: McpLeadClient | None = None,
        discovery_clients: Mapping[str, McpLeadClient] | None = None,
    ) -> "Collector":
        return cls(
            GhimeraConfig.from_toml(path, max_bytes=max_config_bytes),
            references=references,
            model_credentials=model_credentials,
            encoder_credential=encoder_credential,
            source_credentials=source_credentials,
            source_resolver=source_resolver,
            human_assistant=human_assistant,
            mcp_client=mcp_client,
            discovery_clients=discovery_clients,
        )

    @property
    def config(self) -> GhimeraConfig:
        """Frozen effective non-secret recipes, also retained by every run receipt."""
        return self._config

    def _validate_intent(self, text: str) -> None:
        # Intent embedding cannot truncate the original question. Reject before
        # planning/discovery or creating a run's persistent storage.
        policy = self.config.scoring
        if policy is not None and policy.reference_source == "intent":
            chars = len(text) + len(policy.encoder.text_prefix)
            if chars > min(
                policy.encoder.max_text_chars,
                policy.encoder.max_input_chars,
                policy.encoding_char_budget,
            ):
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)

    async def run(
        self,
        request: str | ResearchRequest,
        *,
        run_id: str | None = None,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult:
        return await self._research.run(
            self.validate_request(request), run_id=run_id, suspend_after_rounds=suspend_after_rounds
        )

    async def resume(
        self,
        run_id: str,
        *,
        checkpoint_sha256: str,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult:
        return await self._research.resume(
            run_id, checkpoint_sha256=checkpoint_sha256, suspend_after_rounds=suspend_after_rounds
        )

    def validate_request(self, request: str | ResearchRequest) -> ResearchRequest:
        """Validate an intent before a caller reserves output or launches work."""
        request = (
            ResearchRequest(intent=request)
            if isinstance(request, str)
            else ResearchRequest.model_validate(request.model_dump())
        )
        self._validate_intent(request.intent)
        if request.local_documents:
            policy = self.config.local_inputs
            if policy is None or len(request.local_documents) > policy.max_files_per_run:
                raise GhimeraRefused(RefusalCode.LOCAL_INPUT_FAILED)
            if any(not policy.permits(seed.path) for seed in request.local_documents):
                raise GhimeraRefused(RefusalCode.LOCAL_INPUT_FAILED)
        return request

    async def collect(self, goal: Goal, scope: Scope, *, run_id: str | None = None) -> Harvest:
        goal = Goal.model_validate(goal.model_dump())
        scope = Scope.model_validate(scope.model_dump())
        if not set(scope.content_types) <= self._content_types:
            raise ValueError(
                "collection content types require matching configured extraction adapters"
            )
        self._validate_intent(goal.text)
        return await self._collection.run(goal, scope, run_id=run_id)
