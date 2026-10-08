"""Borrow an admitted corpus to discover sources through the existing search template."""

import asyncio

from ghimera.budget import RunBudget
from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_bindings import validate_reader
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.corpus_search_wire import CorpusLeadOmission, CorpusSearchWire, corpus_source_url
from ghimera.corpus_types import BoundCorpusDocument
from ghimera.ledger import Ledger
from ghimera.refusals import FetchFailure, GhimeraRefused, RefusalCode
from ghimera.research_reranking import RunBoundReranker
from ghimera.research_reranking_types import RerankDecision, RerankInvoker, ResearchRerankingConfig
from ghimera.research_types import SearchHit, SearchRequest, SearchResponse
from ghimera.search import GroundedSearch


def corpus_leads(wire: CorpusSearchWire, policy: CorpusSearchConfig) -> tuple[SearchHit, ...]:
    """Deterministic projection from original sources, not model-written URLs."""
    wire.validate_policy(policy, wire.query_text)
    sources = {BoundCorpusDocument(source).identity: source for source in wire.sources}
    selected = set(wire.selected_passages)
    result: list[SearchHit] = []
    for hit in wire.query.hits:
        if hit.passage_id not in selected:
            continue
        source = sources[hit.passage.document_id]
        if not corpus_source_url(source.url):
            raise ValueError("corpus discovery leads require original HTTP(S) source URLs")
        result.append(
            SearchHit(
                url=source.url,
                title=source.extracted.title[: policy.max_title_chars],
                snippet=hit.passage.text[: policy.max_snippet_chars],
            )
        )
    if len({hit.url for hit in result}) != len(result):
        raise ValueError("corpus source leads cannot repeat a URL")
    return tuple(result)


class CorpusLeadSearch(GroundedSearch):
    name = "evidence-corpus"
    revision = "corpus-search/1"

    def __init__(
        self,
        policy: CorpusSearchConfig,
        corpus: EvidenceCorpus,
        *,
        run_policy: ResearchRerankingConfig | None = None,
    ) -> None:
        self.policy = CorpusSearchConfig.model_validate(policy.model_dump())
        self._corpus = corpus
        self._run_policy = (
            ResearchRerankingConfig.model_validate(run_policy.model_dump())
            if run_policy is not None
            else None
        )
        self._check()

    def _check(self) -> None:
        corpus, policy = self._corpus, self.policy
        if corpus.config.reranking is not None and self._run_policy is None:
            raise ValueError("learned corpus discovery requires explicit run-bound rerank policy")
        validate_reader(
            corpus,
            corpus_id=policy.corpus_id,
            config_sha256=policy.corpus_config_sha256,
            query_encoder=policy.query_encoder,
            max_query_chars=policy.max_query_chars,
            max_passage_hits=policy.max_passage_hits,
            minimum_cosine=policy.minimum_cosine,
        )

    @property
    def identity(self) -> tuple[str, str]:
        return self.policy.identity

    async def request(self, request: SearchRequest) -> SearchResponse:
        if self._corpus.config.reranking is not None:
            raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
        try:
            return await self._request(request)
        except (ValueError, OSError) as exc:
            raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE) from exc

    async def request_for_run(
        self,
        request: SearchRequest,
        budget: RunBudget,
        ledger: Ledger,
        rerank_decision: RerankDecision | None,
    ) -> SearchResponse:
        if self._corpus.config.reranking is None:
            return await self.request(request)
        if (
            rerank_decision is None
            or budget.config.research is None
            or budget.config.research.reranking != self._run_policy
        ):
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        invoker = RunBoundReranker(budget, ledger, channel="discovery", decision=rerank_decision)
        try:
            return await self._request(request, rerank_invoker=invoker)
        except (ValueError, OSError) as exc:
            raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE) from exc

    async def _request(
        self, request: SearchRequest, *, rerank_invoker: RerankInvoker | None = None
    ) -> SearchResponse:
        request = SearchRequest.model_validate(request.model_dump())
        policy = self.policy
        self._check()
        if not request.query.text.strip() or len(request.query.text) > policy.max_query_chars:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        async with asyncio.timeout(request.timeout_seconds):
            query = await self._corpus.search(
                request.query.text,
                top_k=policy.max_passage_hits,
                languages=policy.languages,
                retrieval=policy.retrieval,
                rerank_invoker=rerank_invoker,
            )
            if (
                query.corpus_id != policy.corpus_id
                or query.config_sha256 != policy.corpus_config_sha256
            ):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            base = CorpusSearchWire(
                schema="ghimera.corpus-leads/1",
                binding_revision=policy.identity[1],
                query_text=request.query.text,
                query=query,
                sources=(),
                selected_passages=(),
                omissions=(),
            )
            cap = min(request.max_bytes, policy.max_response_bytes)
            if len(base.model_dump_json().encode()) > cap:
                raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, cap)
            used: set[str] = set()
            omissions: list[CorpusLeadOmission] = []
            for hit in query.hits:
                passage = hit.passage
                if len(base.sources) >= min(request.limit, policy.max_results):
                    omissions.append("result_limit")
                    break
                if hit.cosine < policy.minimum_cosine:
                    omissions.append("minimum_cosine")
                    continue
                if passage.source_url in used:
                    omissions.append("same_source")
                    continue
                if not corpus_source_url(passage.source_url):
                    omissions.append("non_http_source")
                    continue
                source = (await self._corpus.documents((passage.document_id,)))[0]
                if len(source.model_dump_json().encode()) > policy.max_original_bytes:
                    omissions.append("original_bytes")
                    continue
                candidate = CorpusSearchWire(
                    schema="ghimera.corpus-leads/1",
                    binding_revision=base.binding_revision,
                    query_text=base.query_text,
                    query=query,
                    sources=base.sources + (source,),
                    selected_passages=base.selected_passages + (hit.passage_id,),
                    omissions=tuple(dict.fromkeys(omissions)),
                )
                if len(candidate.model_dump_json().encode()) > cap:
                    omissions.append("response_bytes")
                    continue
                base = candidate
                used.add(passage.source_url)
            final = base.model_copy(update={"omissions": tuple(dict.fromkeys(omissions))})
            raw = final.model_dump_json().encode()
            if len(raw) > cap:
                # Even an omission record must fit. Never silently truncate provenance.
                raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, cap)
            return SearchResponse(raw=raw, hits=corpus_leads(final, policy))
