"""0.4.16: searches never count toward the saturation window; only pages do."""

import asyncio

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.research import ResearchLoop
from ghimera.research_types import (
    Question,
    ResearchPlan,
    ResearchRequest,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from tests.test_c0 import config
from tests.test_intent_research import AnalystFixture, ReviewerFixture, SearchFixture, policy

SEARCHES, PAGES, WINDOW = 30, 12, 12


class ManyQueries:
    model = AnalystFixture.model

    async def plan(self, request):
        questions = request.questions or (Question(id="q1", text="Which port?"),)
        return ResearchPlan(
            questions=questions,
            queries=tuple(
                SearchQuery(text=f"ports report {n}", question_ids=("q1",)) for n in range(SEARCHES)
            ),
        )


class DistinctHits(SearchFixture):
    async def request(self, request):
        self.requests.append(request)
        n = (len(self.requests) - 1) % SEARCHES + 1  # 30 distinct pages in total
        hit = SearchHit(url=f"https://example.org/page{n}", title="Ports", snippet="ports")
        return SearchResponse(raw=f'{{"n":{n}}}'.encode(), hits=(hit,))


def test_thirty_searches_do_not_saturate_a_round_before_its_pages():
    cfg = config(
        saturation_window=WINDOW,
        saturation_min_new=3,
        research=policy(
            max_queries_per_round=SEARCHES,
            max_pages_per_round=PAGES,
            search_concurrency=2,
            saturation_rounds=1,
        ),
    )
    route, search = FakeRoute(), DistinctHits()
    loop = ResearchLoop(
        config=cfg,
        collector=GoalLoop(
            config=cfg,
            fetcher=FetchLadder((route,)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ),
        search=search,
        planner=ManyQueries(),
        analyst=AnalystFixture(missing=True),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports")))
    first = result.rounds[0]
    assert first.searches_made == SEARCHES
    assert first.collection_stop != "saturated"
    assert len(route.requests) >= PAGES
    assert first.discovery_progress is None or first.discovery_progress.new_documents
    pages = {doc.url for doc in result.harvest.documents}
    assert len(pages) >= PAGES
    assert result.model_validate_json(result.model_dump_json()) == result
