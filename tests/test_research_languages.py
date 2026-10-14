"""0.4.12: preferred research languages and fair scoring of bare search leads."""

import asyncio

import pytest
from pydantic import ValidationError

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.model_client import LANGUAGE_PLAN_INSTRUCTIONS
from ghimera.research import ResearchLoop, scoring_anchor
from ghimera.research_types import (
    Question,
    ResearchPlan,
    ResearchRequest,
    SearchQuery,
    language_queries,
)
from tests.test_c0 import config
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
    policy,
)


class RecordingPlanner(PlannerFixture):
    def __init__(self, *, chinese: bool = False) -> None:
        self.requests = []
        self.chinese = chinese

    async def plan(self, request):
        self.requests.append(request)
        questions = request.questions or (Question(id="q1", text="Which port?"),)
        queries = (SearchQuery(text="ports reports", question_ids=("q1",), language="en"),)
        if self.chinese:
            queries += (SearchQuery(text="港口 报告", question_ids=("q1",), language="zh"),)
        return ResearchPlan(questions=questions, queries=queries)


def research(languages, planner):
    cfg = config(research=policy(max_rounds=1), page_budget=30)
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
        analyst=AnalystFixture(missing=True),
        reviewer=ReviewerFixture(),
    )
    request = ResearchRequest(intent="find ports", languages=languages)
    return asyncio.run(loop.run(request)), search


def test_every_question_is_searched_in_every_preferred_language():
    planner = RecordingPlanner()
    result, search = research(("en", "zh"), planner)
    assert planner.requests[0].languages == ("en", "zh")
    searched = [(r.query.text, r.query.language) for r in search.requests]
    assert searched == [("ports reports", "en"), ("ports reports", "zh")]
    round_ = result.rounds[0]
    assert [query.language for query in round_.language_queries] == ["zh"]
    assert round_.pages_by_language == {"en": 1}
    assert result.model_validate_json(result.model_dump_json()) == result


def test_planner_language_queries_take_precedence_over_added_ones():
    _, search = research(("en", "zh"), RecordingPlanner(chinese=True))
    assert [(r.query.text, r.query.language) for r in search.requests] == [
        ("ports reports", "en"),
        ("港口 报告", "zh"),
    ]


def test_no_languages_keeps_the_original_plan_and_identities():
    result, search = research((), RecordingPlanner())
    assert len(search.requests) == 1
    assert "language_queries" not in result.rounds[0].model_dump()
    assert "languages" not in ResearchRequest(intent="x").model_dump()
    assert "language" not in SearchQuery(text="x", question_ids=("q1",)).model_dump()


def test_language_codes_are_validated_and_distinct():
    with pytest.raises(ValidationError):
        ResearchRequest(intent="x", languages=("Chinese",))
    with pytest.raises(ValidationError):
        ResearchRequest(intent="x", languages=("zh", "zh"))
    assert ResearchRequest(intent="x", languages=("zh-Hant", "en")).languages == ("zh-Hant", "en")


def test_uncovered_questions_fall_back_to_their_own_text():
    questions = (Question(id="q1", text="Which port?"), Question(id="q2", text="Who owns it?"))
    added = language_queries(
        questions, (SearchQuery(text="port", question_ids=("q1",)),), ("ja",), 8
    )
    assert [(q.text, q.question_ids, q.language) for q in added] == [
        ("port", ("q1",), "ja"),
        ("Who owns", ("q2",), "ja"),
    ]
    assert "EACH listed language" in LANGUAGE_PLAN_INSTRUCTIONS


@pytest.mark.parametrize(
    ("url", "title", "snippet", "expected"),
    [
        ("https://example.org/reports/port-expansion-2026.html", "Port expansion", "", None),
        (
            "https://www.example.org/reports/port-expansion-2026.html",
            "example.org",
            "",
            "example.org reports port expansion",
        ),
        ("https://example.org/news/kaohsiung_port", "", "Kaohsiung terminal", None),
    ],
)
def test_bare_host_search_leads_score_on_path_words(url, title, snippet, expected):
    anchor = scoring_anchor(url, title, snippet)
    if expected is None and title:
        assert anchor == title
    elif expected is None:
        assert anchor == "news kaohsiung port Kaohsiung terminal"
    else:
        assert anchor == expected
