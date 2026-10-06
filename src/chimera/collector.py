"""Configured real-adapter assembly; no registry, credential discovery or model launch."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

from chimera.browser import IsolatedBrowserRenderer
from chimera.config import ChimeraConfig
from chimera.documents import DOCX_TYPE, DocumentExtractionSuite, DocumentExtractor
from chimera.embedding import SelfHostedEncoder
from chimera.embedding_types import EmbeddingReferences
from chimera.extraction import HtmlExtractor
from chimera.fetch import FetchLadder
from chimera.http import CurlRoute
from chimera.loop import GoalLoop
from chimera.model_client import SelfHostedModels
from chimera.models import Goal, Harvest, Scope
from chimera.ports import Extractor
from chimera.refusals import ChimeraRefused, RefusalCode
from chimera.research import ResearchLoop
from chimera.research_types import ResearchRequest, ResearchResult
from chimera.searxng import SearxHtmlSearch, SearxSearch
from chimera.semantic_scoring import EmbeddingScorer
from chimera.source_sessions import SourceCredentials
from chimera.transport import Resolver

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
DOCUMENT_TYPES = frozenset({"application/pdf", DOCX_TYPE})


class Collector:
    """One reusable configuration, fresh run state, and existing invariant-owning ports.

    Ports remain available through their lower-level APIs for alternate providers.
    This facade assembles only concrete, shipped adapters, never test doubles.
    """

    def __init__(
        self,
        config: ChimeraConfig,
        *,
        references: EmbeddingReferences | None = None,
        model_credentials: Mapping[str, SecretStr] | None = None,
        encoder_credential: SecretStr | None = None,
        source_credentials: Mapping[str, SourceCredentials] | None = None,
        source_resolver: Resolver | None = None,
    ) -> None:
        # Revalidate injected models: model_copy(update=...) can bypass guards.
        config = ChimeraConfig.model_validate(config.model_dump())
        if (
            config.http is None
            or config.research is None
            or config.search is None
            or config.models is None
            or config.scoring is None
            or config.extraction is None
        ):
            raise ValueError(
                "Collector requires http, research, search, models, scoring and extraction recipes"
            )
        self._content_types = HTML_TYPES | (
            DOCUMENT_TYPES if config.document_extraction is not None else frozenset()
        )
        if not set(config.research.content_types) <= self._content_types:
            raise ValueError(
                "research content types require matching configured extraction adapters"
            )
        models = SelfHostedModels.from_config(config, credentials=model_credentials)
        encoder = SelfHostedEncoder(config.scoring.encoder, credential=encoder_credential)
        scorer = EmbeddingScorer(config.scoring, encoder, references)
        route = CurlRoute(config, resolver=source_resolver, source_credentials=source_credentials)
        extractor: Extractor = HtmlExtractor(config)
        if config.document_extraction is not None:
            extractor = DocumentExtractionSuite(html=extractor, documents=DocumentExtractor(config))
        renderer = IsolatedBrowserRenderer(config) if config.browser is not None else None
        collection = GoalLoop(
            config=config,
            fetcher=FetchLadder((route,), renderer=renderer),
            extractor=extractor,
            scorer=scorer,
            judge=models.judge,
        )
        search_type = SearxHtmlSearch if config.search.response_format == "html" else SearxSearch
        research = ResearchLoop(
            config=config,
            collector=collection,
            search=search_type(config, config.search, resolver=source_resolver),
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
    ) -> "Collector":
        return cls(
            ChimeraConfig.from_toml(path, max_bytes=max_config_bytes),
            references=references,
            model_credentials=model_credentials,
            encoder_credential=encoder_credential,
            source_credentials=source_credentials,
            source_resolver=source_resolver,
        )

    @property
    def config(self) -> ChimeraConfig:
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
                raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)

    async def run(
        self, request: str | ResearchRequest, *, run_id: str | None = None
    ) -> ResearchResult:
        return await self._research.run(self.validate_request(request), run_id=run_id)

    def validate_request(self, request: str | ResearchRequest) -> ResearchRequest:
        """Validate an intent before a caller reserves output or launches work."""
        request = (
            ResearchRequest(intent=request)
            if isinstance(request, str)
            else ResearchRequest.model_validate(request.model_dump())
        )
        self._validate_intent(request.intent)
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
