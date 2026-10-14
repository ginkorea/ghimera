"""0.4.14: a compliant plan is never refused for its size; it is cut and recorded."""

import asyncio
from collections import Counter

import pytest

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.refusals import RefusalCode
from ghimera.research import ResearchLoop
from ghimera.research_types import (
    Assessment,
    Coverage,
    Question,
    ResearchPlan,
    ResearchRequest,
    SearchQuery,
    balance_queries,
    truncate_query_text,
)
from tests.test_c0 import config
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
    policy,
)

LANGUAGES = ("en", "zh", "ja")
QUESTIONS = tuple(Question(id=f"q{n}", text=f"Question {n}?") for n in range(1, 7))


def eighteen():
    return tuple(
        SearchQuery(text=f"{question.id} {language}", question_ids=(question.id,), language=code)
        for question in QUESTIONS
        for language, code in zip(LANGUAGES, LANGUAGES, strict=True)
    )


def test_eighteen_queries_cut_to_eight_balanced_across_languages():
    queries = eighteen()
    kept, omitted = balance_queries(queries, 8, LANGUAGES)
    assert len(kept) == 8 and len(omitted) == 10
    counts = Counter(query.language for query in kept)
    assert max(counts.values()) - min(counts.values()) <= 1 and set(counts) == set(LANGUAGES)
    # Within each language the plan's question order is kept, earliest first.
    assert [q.text for q in kept if q.language == "en"] == ["q1 en", "q2 en", "q3 en"]
    assert set(kept) | set(omitted) == set(queries) and not set(kept) & set(omitted)
    assert list(kept) == [q for q in queries if q in kept]  # plan order preserved
    assert balance_queries(queries[:8], 8, LANGUAGES) == (queries[:8], ())


def test_overlong_query_is_cut_at_a_word_boundary():
    assert truncate_query_text("lawful transit of the strait", 12) == "lawful"
    assert truncate_query_text("lawful transit", 14) == "lawful transit"
    assert truncate_query_text("lawful transit now", 14) == "lawful transit"
    assert truncate_query_text("台海局势与和平", 4) == "台海局势"


class OpenAnalyst(AnalystFixture):
    async def assess(self, request):
        return Assessment(
            coverage=tuple(
                Coverage(question_id=question.id, status="unresolved", reason="fixture")
                for question in request.questions
            )
        )


class WidePlanner(PlannerFixture):
    def __init__(self, queries):
        self.queries, self.requests = queries, []

    async def plan(self, request):
        self.requests.append(request)
        return ResearchPlan(questions=request.questions or QUESTIONS, queries=self.queries)


def research(planner, **updates):
    cfg = config(
        research=policy(max_queries_per_round=8, max_questions=6, search_concurrency=2, **updates)
    )
    search = SearchFixture()
    loop = ResearchLoop(
        config=cfg,
        collector=GoalLoop(
            config=cfg,
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=FakeJudge(satisfied=True),
        ),
        search=search,
        planner=planner,
        analyst=OpenAnalyst(),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports", languages=LANGUAGES)))
    return result, search


def test_a_plan_over_the_round_bound_runs_eight_and_records_ten_omitted():
    long = "x" * 150 + " tail words"
    queries = eighteen()[:-1] + (SearchQuery(text=long, question_ids=("q6",), language="ja"),)
    result, search = research(WidePlanner(queries), max_query_chars=152)
    first = result.rounds[0]
    assert len(search.requests) <= 8 * len(result.rounds)
    assert len(first.queries) + len(first.language_queries) <= 8
    assert len(first.omitted_queries) == 10
    assert Counter(q.language for q in first.queries) == {"en": 3, "zh": 3, "ja": 2}
    assert [item.text for item in first.truncated_queries] == ["x" * 150]
    assert first.truncated_queries[0].original_text == long
    reasons = [row.reason for row in result.harvest.ledger if row.event == "policy"]
    assert sum(reason.startswith("query_omitted") for reason in reasons) == 10 * len(result.rounds)
    assert sum(reason.startswith("query_truncated") for reason in reasons) == len(result.rounds)
    assert result.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "queries",
    [
        (SearchQuery(text="ports", question_ids=("q9",)),),  # unknown question id
        (SearchQuery(text="  ", question_ids=("q1",)),),  # blank
        (SearchQuery(text="ports", question_ids=("q1", "q1")),),  # duplicate ids
        (SearchQuery(text="ports", question_ids=("q1",), graph_refs=("node:x",)),),
    ],
)
def test_contract_checks_still_refuse(queries):
    result, search = research(WidePlanner(queries))
    assert result.status == "failed" and not search.requests
    assert any(
        row.refusal == RefusalCode.RESEARCH_CONTRACT and "_validate_plan" in (row.detail or "")
        for row in result.harvest.ledger
    )
