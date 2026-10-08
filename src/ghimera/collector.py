"""Configured real-adapter assembly; no registry, credential discovery or model launch."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

from ghimera.ahmia import AhmiaIndexSearch
from ghimera.ahmia_config import AhmiaConfig
from ghimera.browser import IsolatedBrowserRenderer
from ghimera.config import GhimeraConfig
from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_evidence import CorpusEvidenceReader
from ghimera.corpus_search import CorpusLeadSearch
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.discovery import DiscoveryProviders
from ghimera.documents import DocumentExtractionSuite, DocumentExtractor
from ghimera.embedding import SelfHostedEncoder
from ghimera.embedding_types import EmbeddingReferences
from ghimera.extraction import HtmlExtractor
from ghimera.fetch import FetchLadder, FetchRoute
from ghimera.http import CurlRoute
from ghimera.human_browser import ChromiumHumanSession
from ghimera.human_browser_route import HumanBrowserRoute
from ghimera.human_browser_types import AuthorizedBrowserSession, HumanAssistant
from ghimera.image_ocr import TesseractOcr
from ghimera.loop import GoalLoop
from ghimera.mcp_lead_config import McpLeadConfig
from ghimera.mcp_leads import McpLeadClient, McpLeadSearch
from ghimera.model_client import SelfHostedModels
from ghimera.models import Goal, Harvest, Scope
from ghimera.page_renderer import PdfPageRenderer
from ghimera.page_transcriber import LocalPageTranscriber
from ghimera.pdf_transcription import PdfTranscriptionStage
from ghimera.ports import Extractor
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import ResearchLoop
from ghimera.research_recovery_types import ResearchRecoveryModels
from ghimera.research_types import ResearchRequest, ResearchResult
from ghimera.search import GroundedSearch
from ghimera.searxng import SearxHtmlSearch, SearxSearch
from ghimera.semantic_scoring import EmbeddingScorer
from ghimera.source_feeds import SourceFeedExtractionSuite, SourceFeedExtractor
from ghimera.source_refresh import SourceRefreshStore
from ghimera.source_sessions import SourceCredentials
from ghimera.transport import Resolver
from ghimera.visual_model import LocalVisionReader
from ghimera.visual_stage import VisualStage

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
        human_browser_session: AuthorizedBrowserSession | None = None,
        mcp_client: McpLeadClient | None = None,
        discovery_clients: Mapping[str, McpLeadClient] | None = None,
        ahmia_credential: SecretStr | None = None,
        discovery_credentials: Mapping[str, SecretStr] | None = None,
        vision_credential: SecretStr | None = None,
        visual_reviewer_credential: SecretStr | None = None,
        transcription_credential: SecretStr | None = None,
        transcription_reviewer_credential: SecretStr | None = None,
        corpus: EvidenceCorpus | None = None,
        discovery_corpora: Mapping[str, EvidenceCorpus] | None = None,
        retained_reader: CorpusEvidenceReader | None = None,
    ) -> None:
        # Revalidate injected models: model_copy(update=...) can bypass guards.
        config = GhimeraConfig.model_validate(config.model_dump())
        if (
            human_browser_session is None
            and config.human_browser is not None
            and (config.human_browser.adapter == "patchright_page")
        ):
            raise GhimeraRefused(RefusalCode.SOURCE_SESSION_UNAVAILABLE)
        if human_browser_session is not None and human_assistant is not None:
            raise ValueError("assistance belongs to the injected session; do not bind it twice")
        human_session = human_browser_session or (
            ChromiumHumanSession(config.human_browser, assistant=human_assistant)
            if config.human_browser is not None
            else None
        )
        if human_browser_session is not None:
            if config.human_browser is None:
                raise ValueError("an injected browser session requires its explicit policy")
            human_browser_session.validate_config(config.human_browser)
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
        if config.source_feeds is not None:
            self._content_types |= frozenset(config.source_feeds.content_types)
        if not set(config.research.content_types) <= self._content_types:
            raise ValueError(
                "research content types require matching configured extraction adapters"
            )
        search: GroundedSearch | DiscoveryProviders
        if ahmia_credential is not None and not isinstance(config.search, AhmiaConfig):
            raise ValueError("single Ahmia credentials require an Ahmia search binding")
        if discovery_credentials is not None and config.discovery is None:
            raise ValueError("discovery credentials require a discovery recipe")
        if corpus is not None and not isinstance(config.search, CorpusSearchConfig):
            raise ValueError(
                "a single discovery corpus requires its explicit corpus-search binding"
            )
        if discovery_corpora is not None and config.discovery is None:
            raise ValueError("discovery corpora require a discovery recipe")
        if config.discovery is not None:
            if mcp_client is not None:
                raise ValueError("discovery uses explicit provider-keyed MCP clients")
            clients = dict(discovery_clients or {})
            expected = {
                p.id for p in config.discovery.providers if isinstance(p.binding, McpLeadConfig)
            }
            if set(clients) != expected:
                raise ValueError("every discovery MCP binding requires exactly its provider client")
            credentials = dict(discovery_credentials or {})
            expected_credentials = {
                p.id
                for p in config.discovery.providers
                if isinstance(p.binding, AhmiaConfig) and p.binding.authorization != "none"
            }
            if set(credentials) != expected_credentials:
                raise ValueError(
                    "every private discovery binding requires exactly its own credential"
                )
            corpora = dict(discovery_corpora or {})
            expected_corpora = {
                p.id
                for p in config.discovery.providers
                if isinstance(p.binding, CorpusSearchConfig)
            }
            if set(corpora) != expected_corpora:
                raise ValueError(
                    "every corpus discovery binding requires exactly its provider corpus"
                )
            adapters: dict[str, GroundedSearch] = {}
            for provider in config.discovery.providers:
                binding = provider.binding
                if isinstance(binding, McpLeadConfig):
                    adapters[provider.id] = McpLeadSearch(binding, clients[provider.id])
                elif isinstance(binding, CorpusSearchConfig):
                    adapters[provider.id] = CorpusLeadSearch(binding, corpora[provider.id])
                elif isinstance(binding, AhmiaConfig):
                    adapters[provider.id] = AhmiaIndexSearch(
                        binding, credential=credentials.get(provider.id), resolver=source_resolver
                    )
                else:
                    provider_type = (
                        SearxHtmlSearch if binding.response_format == "html" else SearxSearch
                    )
                    adapters[provider.id] = provider_type(config, binding, resolver=source_resolver)
            search = DiscoveryProviders(config.discovery, adapters)
        elif discovery_clients is not None:
            raise ValueError("discovery clients require a discovery recipe")
        elif isinstance(config.search, CorpusSearchConfig):
            if corpus is None or mcp_client is not None or ahmia_credential is not None:
                raise ValueError("corpus discovery requires its own explicit, borrowed corpus")
            search = CorpusLeadSearch(config.search, corpus)
        elif isinstance(config.search, AhmiaConfig):
            if mcp_client is not None:
                raise ValueError("Ahmia index search does not borrow an MCP credential/client")
            search = AhmiaIndexSearch(
                config.search, credential=ahmia_credential, resolver=source_resolver
            )
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
        if config.source_feeds is not None:
            extractor = SourceFeedExtractionSuite(
                fallback=extractor, feeds=SourceFeedExtractor(config)
            )
        renderer = IsolatedBrowserRenderer(config) if config.browser is not None else None
        transcription = None
        if config.pdf_transcription is not None:
            transcription = PdfTranscriptionStage(
                config.pdf_transcription,
                renderer=PdfPageRenderer(config.pdf_transcription.pages.renderer),
                transcriber=LocalPageTranscriber(
                    config.pdf_transcription.pages,
                    transcription_credential=transcription_credential,
                    review_credential=transcription_reviewer_credential,
                ),
            )
        elif transcription_credential is not None or transcription_reviewer_credential is not None:
            raise ValueError(
                "transcription credentials require their explicit private service recipe"
            )
        visuals = None
        if config.visuals is not None:
            visuals = VisualStage(
                config.visuals,
                ocr=TesseractOcr(config.visuals),
                judge=models.judge,
                vision=LocalVisionReader(
                    config.visuals,
                    vision_credential=vision_credential,
                    reviewer_credential=visual_reviewer_credential,
                )
                if config.visuals.vision is not None
                else None,
            )
        elif vision_credential is not None or visual_reviewer_credential is not None:
            raise ValueError("visual credentials require a visual service recipe")
        refresh = SourceRefreshStore(config.source_refresh) if config.source_refresh else None
        collection = GoalLoop(
            config=config,
            fetcher=FetchLadder(routes, renderer=renderer, source_refresh=refresh),
            extractor=extractor,
            scorer=scorer,
            judge=models.judge,
            visual_stage=visuals,
            pdf_transcription=transcription,
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
            retained_reader=retained_reader,
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
        human_browser_session: AuthorizedBrowserSession | None = None,
        mcp_client: McpLeadClient | None = None,
        discovery_clients: Mapping[str, McpLeadClient] | None = None,
        ahmia_credential: SecretStr | None = None,
        discovery_credentials: Mapping[str, SecretStr] | None = None,
        vision_credential: SecretStr | None = None,
        visual_reviewer_credential: SecretStr | None = None,
        transcription_credential: SecretStr | None = None,
        transcription_reviewer_credential: SecretStr | None = None,
        corpus: EvidenceCorpus | None = None,
        discovery_corpora: Mapping[str, EvidenceCorpus] | None = None,
        retained_reader: CorpusEvidenceReader | None = None,
    ) -> "Collector":
        return cls(
            GhimeraConfig.from_toml(path, max_bytes=max_config_bytes),
            references=references,
            model_credentials=model_credentials,
            encoder_credential=encoder_credential,
            source_credentials=source_credentials,
            source_resolver=source_resolver,
            human_assistant=human_assistant,
            human_browser_session=human_browser_session,
            mcp_client=mcp_client,
            discovery_clients=discovery_clients,
            ahmia_credential=ahmia_credential,
            discovery_credentials=discovery_credentials,
            vision_credential=vision_credential,
            visual_reviewer_credential=visual_reviewer_credential,
            transcription_credential=transcription_credential,
            transcription_reviewer_credential=transcription_reviewer_credential,
            corpus=corpus,
            discovery_corpora=discovery_corpora,
            retained_reader=retained_reader,
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

    def recovery_models(self) -> ResearchRecoveryModels:
        """Expose native collaborator identities for inert service admission."""
        return self._research.recovery_models()

    async def recover(self, run_id: str, *, snapshot_sha256: str) -> ResearchResult:
        """Adopt an exact native model boundary; never retry an unknown contact."""
        return await self._research.recover(run_id, snapshot_sha256=snapshot_sha256)

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
