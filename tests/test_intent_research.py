"""Intent-only research: discovered URLs, shared limits and reviewed native citations."""

import asyncio
import hashlib
import tomllib
from pathlib import Path

import pytest

from chimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from chimera.fetch import FetchLadder
from chimera.loop import GoalLoop
from chimera.models import ModelIdentity
from chimera.research import ResearchLoop, citation_for
from chimera.research_types import (
    AnswerDraft,
    AnswerReview,
    Assessment,
    Claim,
    ClaimReview,
    Coverage,
    Question,
    ResearchPlan,
    ResearchRequest,
    ResearchResult,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from chimera.search import GroundedSearch
from tests.test_c0 import config


def policy(**updates):
    raw = {
        "schema": "chimera.research/1",
        "max_rounds": 3,
        "max_questions": 4,
        "max_queries_per_round": 2,
        "query_budget": 6,
        "results_per_query": 3,
        "search_concurrency": 2,
        "max_pages_per_round": 3,
        "max_query_chars": 200,
        "max_answer_chars": 2000,
        "max_model_input_chars": 10000,
        "min_answer_confidence": 0.8,
        "require_distinct_reviewer": True,
        "source_policy": "grounded_public",
        "allowed_hosts": [],
        "denied_hosts": [],
        "max_source_hosts": 8,
        "allowed_ports": [80, 443],
        "content_types": ["text/html"],
        "max_depth": 0,
    }
    raw.update(updates)
    return raw


def identity(name):
    return ModelIdentity(model_id=name, revision="1", location="test_double")


class SearchFixture(GroundedSearch):
    name = "fixture_search"
    revision = "1"

    def __init__(self):
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        hits = (SearchHit(url="https://example.org/one", title="Ports", snippet="discovery only"),)
        return SearchResponse(raw=b'{"fixture":"search"}', hits=hits)


class PlannerFixture:
    model = identity("planner")

    async def plan(self, request):
        questions = request.questions or (Question(id="q1", text="Which port?"),)
        return ResearchPlan(
            questions=questions,
            queries=(SearchQuery(text="ports reports", question_ids=("q1",)),),
        )


class AnalystFixture:
    model = identity("analyst")

    def __init__(self, *, missing=False, bad_quote=False):
        self.missing, self.bad_quote = missing, bad_quote

    async def assess(self, request):
        citations = (
            (citation_for(request.documents[0], 0, len(request.documents[0].extracted.text)),)
            if request.documents
            else ()
        )
        return Assessment(
            coverage=(
                Coverage(
                    question_id="q1",
                    status="unresolved" if self.missing or not citations else "answered",
                    reason="fixture",
                    citations=() if self.missing else citations,
                ),
            )
        )

    async def answer(self, request):
        citation = request.assessment.coverage[0].citations[0]
        if self.bad_quote:
            citation = citation.model_copy(update={"quote": "invented evidence"})
        return AnswerDraft(
            claims=(
                Claim(
                    text="Evidence identifies the port.",
                    question_ids=("q1",),
                    citations=(citation,),
                ),
            ),
            confidence=1.0,
        )


class ReviewerFixture:
    model = identity("reviewer")

    def __init__(self, *, supported=True):
        self.supported = supported

    async def review(self, request):
        return AnswerReview(
            answer_digest=request.answer.content_digest(),
            intent_covered=self.supported,
            reason="fixture-only support judgment",
            claims=(
                ClaimReview(
                    index=0,
                    verdict="supported" if self.supported else "unsupported",
                    reason="fixture",
                ),
            ),
        )


def run(*, research=None, analyst=None, reviewer=None, planner=None, cfg=None, run_id=None):
    cfg = cfg or config(research=research or policy(), page_budget=30)
    route, search = FakeRoute(), SearchFixture()
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
    )
    loop = ResearchLoop(
        config=cfg,
        collector=collector,
        search=search,
        planner=planner or PlannerFixture(),
        analyst=analyst or AnalystFixture(),
        reviewer=reviewer or ReviewerFixture(),
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports"), run_id=run_id))
    return result, route, search


def test_intent_only_discovers_collects_and_requires_cited_review():
    result, route, search = run()
    assert result.status == "answered"
    assert result.answer and result.review.intent_covered
    assert result.harvest.goal.seeds == ()
    assert len(route.requests) == len(search.requests) == 1
    assert result.harvest.documents[0].url == "https://example.org/one"
    citation = result.answer.claims[0].citations[0]
    assert (
        citation.text_sha256
        == hashlib.sha256(result.harvest.documents[0].extracted.text.encode()).hexdigest()
    )
    assert result.model_validate_json(result.model_dump_json()) == result
    assert result.harvest.receipt.judge_calls == 5  # planner, document, assessment, answer, review


def test_missing_evidence_does_not_become_complete_from_a_high_grade():
    result, route, search = run(analyst=AnalystFixture(missing=True))
    assert result.status == "partial" and result.answer is None
    assert result.stop_reason == "rounds_exhausted"
    assert len(search.requests) == 3
    assert len(route.requests) == 1  # same source not fetched again on each round
    assert result.unresolved == ("q1",)


@pytest.mark.parametrize("bad_quote", [False, True])
def test_unsupported_or_invented_answer_is_never_accepted(bad_quote):
    result, _, _ = run(
        analyst=AnalystFixture(bad_quote=bad_quote),
        reviewer=ReviewerFixture(supported=False),
    )
    assert result.status == "partial" and result.answer is None
    assert any(row.refusal for row in result.harvest.ledger)


def test_limits_are_shared_across_search_collection_and_followup():
    result, route, search = run(cfg=config(research=policy(), judge_budget=2, page_budget=30))
    assert result.status == "partial" and result.stop_reason == "budget_exhausted"
    assert result.harvest.receipt.judge_calls == 2
    assert len(route.requests) == len(search.requests) == 1


def test_grounded_scope_cannot_bypass_configured_hosts():
    result, route, _ = run(
        research=policy(source_policy="configured_only", allowed_hosts=["approved.example"])
    )
    assert result.status == "partial" and not route.requests
    assert any(row.refusal.value == "out_of_scope" for row in result.harvest.ledger if row.refusal)


def test_external_model_and_same_reviewer_refuse_before_io():
    reviewer = ReviewerFixture()
    reviewer.model = identity("analyst")
    with pytest.raises(Exception, match="research_contract"):
        run(reviewer=reviewer)
    planner = PlannerFixture()
    planner.model = ModelIdentity(model_id="external", revision="1", location="external")
    with pytest.raises(Exception, match="model_unavailable"):
        run(planner=planner)


def test_a_followup_cannot_rewrite_the_original_question_pack():
    class ReframingPlanner(PlannerFixture):
        async def plan(self, request):
            return ResearchPlan(
                questions=(
                    Question(
                        id="q1",
                        text="Ignore the original intent" if request.questions else "Which port?",
                    ),
                ),
                queries=(SearchQuery(text="ports", question_ids=("q1",)),),
            )

    result, route, _ = run(planner=ReframingPlanner(), analyst=AnalystFixture(missing=True))
    assert result.status == "failed" and result.questions[0].text == "Which port?"
    assert len(route.requests) == 1


def test_serialized_answer_revalidates_native_evidence_not_just_review_shape():
    from pydantic import ValidationError

    result, _, _ = run()
    raw = result.model_dump(mode="json", by_alias=True)
    raw["answer"]["claims"][0]["citations"][0]["quote"] = "invented evidence"
    forged = AnswerDraft.model_validate(raw["answer"])
    raw["review"]["answer_digest"] = forged.content_digest()
    with pytest.raises(ValidationError, match="native evidence"):
        ResearchResult.model_validate(raw)


def test_graph_has_intent_questions_queries_before_first_search_and_replays(tmp_path):
    from chimera.graph import DirectoryGraphSink
    from tests.test_research_graph import policy as graph_policy

    gp = graph_policy(tmp_path).model_dump(mode="json", by_alias=True)
    gp["roles"] += [
        {"name": "question", "kind": "research_question"},
        {"name": "query", "kind": "search_query"},
    ]
    gp["relations"][0]["target_roles"].append("query")
    gp["relations"] += [
        {
            "name": "question_for",
            "predicate": "question_for",
            "source_roles": ["question"],
            "target_roles": ["intent"],
            "semantic": False,
        },
        {
            "name": "query_for",
            "predicate": "query_for",
            "source_roles": ["query"],
            "target_roles": ["question"],
            "semantic": False,
        },
    ]
    cfg = config(research=policy(), graph=gp, page_budget=30)
    sink = DirectoryGraphSink(cfg.graph, "intent-run")

    class ObservedSearch(SearchFixture):
        async def request(self, request):
            batches = await sink.replay()
            roles = {node.role for batch in batches for node in batch.nodes}
            assert {"intent", "question", "query"} <= roles
            return await super().request(request)

    collector = GoalLoop(
        config=cfg,
        graph_sink=sink,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    loop = ResearchLoop(
        config=cfg,
        collector=collector,
        search=ObservedSearch(),
        planner=PlannerFixture(),
        analyst=AnalystFixture(),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports"), run_id="intent-run"))
    assert result.status == "answered"
    assert {"intent", "question", "query", "source", "document"} <= {
        node.role for node in result.harvest.graph.nodes
    }
    batches = asyncio.run(sink.replay())
    assert batches[-1].content_digest() == result.harvest.graph.checkpoint.digest


def test_research_wall_deadline_returns_partial_and_records_spent_model_call():
    class SlowPlanner(PlannerFixture):
        async def plan(self, request):
            await asyncio.sleep(1)
            return await super().plan(request)

    cfg = config(research=policy(), wall_seconds=0.02)
    result, route, search = run(cfg=cfg, planner=SlowPlanner())
    assert result.status == "partial" and result.stop_reason == "budget_exhausted"
    assert result.harvest.receipt.judge_calls == 1
    assert not route.requests and not search.requests
    assert any(
        row.refusal.value == "budget_exhausted" for row in result.harvest.ledger if row.refusal
    )


def test_research_examples_are_valid_configuration_without_source_edits():
    from chimera.config import ChimeraConfig
    from chimera.graph_types import GraphConfig
    from chimera.searxng import SearxConfig

    cfg = ChimeraConfig.from_toml(Path("examples/intent-research.toml"))
    assert cfg.research.max_rounds == 8 and cfg.research.source_policy == "grounded_public"
    graph = GraphConfig.from_toml(Path("examples/research-graph.toml"))
    assert {"intent", "question", "query"} <= {role.name for role in graph.roles}
    with Path("examples/searxng.toml").open("rb") as stream:
        service = SearxConfig.model_validate(tomllib.load(stream))
    assert service.endpoint == "https://search.example.invalid/search"
