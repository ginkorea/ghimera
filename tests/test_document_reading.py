"""0.4.12: the judge READS a page to guide the collection; it never discards one."""

import asyncio
import hashlib
import json
import time

import pytest

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.judgment_types import DEFAULT_KEEP_AT_OR_ABOVE, reading_disposition
from ghimera.loop import CollectionSession, GoalLoop
from ghimera.model_client import READING_INSTRUCTIONS, SelfHostedModel, model_schema
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Document, DocumentReading, Extracted, Goal, LinkCandidate, Verdict
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, answer_order, planning_readings
from ghimera.spend import RunSpend
from tests.test_c0 import config, scope
from tests.test_intent_research import AnalystFixture, ReviewerFixture, SearchFixture, policy
from tests.test_judgment_context import CONTRIBUTION_POLICY, loop_for, prepared
from tests.test_research_languages import RecordingPlanner

FOCUS_TAIWAN = (
    "Lai's National Day speech steady, avoided provoking Beijing: Scholar. A scholar said "
    "President Lai's 10 October speech was steady and avoided provoking Beijing."
)


def reading(**updates):
    raw = dict(
        decision="accept",
        kind="news",
        publisher="fixture",
        language="en",
        reason="fixture reading",
    )
    raw.update(updates)
    return DocumentReading(**raw)


class ScriptedJudge(FakeJudge):
    """Returns a scripted reading per look; records the questions it was given."""

    def __init__(self, *looks, fail=None):
        super().__init__()
        self.looks, self.fail, self.calls, self.questions = list(looks), fail, 0, []

    async def document(self, goal, document, *, second_look, questions=()):
        self.calls += 1
        self.questions.append(tuple(question.id for question in questions))
        if self.fail is not None:
            raise GhimeraRefused(self.fail)
        look = self.looks[min(len(self.looks) - 1, 1 if second_look else 0)]
        return look


def collect(judge, *, extractor=None, max_depth=0):
    route = FakeRoute()
    result = asyncio.run(
        GoalLoop(
            config=config(),
            fetcher=FetchLadder((route,)),
            extractor=extractor or FakeExtractor(),
            scorer=KeywordScorer(),
            judge=judge,
        ).run(
            Goal(text="ports", seeds=("https://example.org/root",)),
            scope(max_depth=max_depth),
        )
    )
    return result, route


class Served:
    """A served judge's wire reply carrying a full reading."""

    def __init__(self, bound, **content):
        self.config, self.requests = bound, []
        self.content = dict(
            decision="accept",
            kind="news",
            publisher="Focus Taiwan",
            language="en",
            reason="scholar's analysis of the speech and Beijing's reaction",
        )
        self.content.update(content)

    async def post(self, body):
        self.requests.append(json.loads(body))
        return ModelHttpResponse(
            200,
            json.dumps(
                {
                    "id": "fixture-reading",
                    "model": self.config.served_model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": json.dumps(self.content)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode(),
            "application/json",
        )


def served_reading(tmp_path, judgment_policy, **content):
    cfg, goal, extracted, ledger, _ = prepared(tmp_path, judgment_policy=judgment_policy)
    transport = Served(cfg.models.judge, **content)
    judge = SelfHostedModel(cfg, cfg.models.judge, http=transport)
    session = CollectionSession(goal, RunSpend(cfg, time.monotonic), ledger, None)
    loop = loop_for(cfg, judge)
    try:
        verdict, disposition = asyncio.run(
            loop._document_verdict(
                session,
                extracted,
                extracted.document_parse.source_url,
                True,
                source_sha256=extracted.document_parse.source_sha256,
            )
        )
        evidence = ledger.snapshot()[-1].document_judgment
        url = extracted.document_parse.source_url
        kept = loop._kept_reading(session, verdict, disposition, url, url)
    finally:
        session.close()
    return verdict, disposition, evidence, kept, transport


def test_focus_taiwan_scholar_page_is_supporting_and_kept(tmp_path):
    verdict, disposition, evidence, kept, transport = served_reading(
        tmp_path,
        CONTRIBUTION_POLICY,
        relevance="supporting",
        subject="PRC reaction to Lai's 10/10 National Day speech",
        contribution="A scholar reads the speech as steady and not provoking Beijing.",
        informs=["q1"],
        follow=[{"kind": "query", "value": "Beijing Taiwan Affairs Office response Lai speech"}],
        names=[{"kind": "person", "text": "Lai Ching-te"}],
    )
    assert verdict.relevance == "supporting" and disposition == "accept"
    assert (evidence.client_disposition, evidence.client_relevance) == ("accept", "supporting")
    assert kept is verdict and kept.answer_rank is not None
    system = transport.requests[0]["messages"][0]["content"]
    assert "a scholar's analysis of the speech" in READING_INSTRUCTIONS
    assert "You never decide whether the page is kept" in json.dumps(transport.requests[0])
    schema = model_schema(Verdict)
    assert schema["properties"]["decision"]["enum"] == ["accept", "hold"]
    assert "unjudged" not in schema["properties"]["relevance"]["enum"]
    assert {"relevance", "informs", "follow", "names"} <= set(schema["required"])
    assert "DocumentReading" in system


def test_engagement_rate_page_is_unrelated_kept_and_out_of_answers(tmp_path):
    strict = dict(CONTRIBUTION_POLICY, keep_at_or_above=0.99)
    verdict, disposition, evidence, kept, _ = served_reading(
        tmp_path, strict, relevance="unrelated", reason="engagement-rate formulas"
    )
    assert disposition == "accept" and evidence.reason == "unrelated_kept"
    assert kept.decision == "accept" and kept.grade == "unrelated"
    assert kept.answer_rank is None
    assert "model said: accept: engagement-rate formulas" in kept.reason


def test_model_unrelated_above_the_floor_is_kept_as_background(tmp_path):
    verdict, disposition, evidence, kept, _ = served_reading(
        tmp_path, CONTRIBUTION_POLICY, relevance="unrelated", reason="not about the question"
    )
    assert evidence.reason == "similarity_floor"
    assert evidence.document_cosine >= evidence.keep_at_or_above == DEFAULT_KEEP_AT_OR_ABOVE
    assert kept.grade == "background"
    assert kept.reason.startswith("kept by similarity floor 0.8")
    assert "(model said: accept: not about the question)" in kept.reason


@pytest.mark.parametrize(
    ("decision", "relevance", "omitted", "second", "cosine", "expected"),
    [
        ("accept", "direct", 0, False, None, ("accept", "direct", "native_model_decision")),
        ("accept", "unrelated", 0, False, 0.5, ("accept", "unrelated", "unrelated_kept")),
        ("accept", "unrelated", 0, False, 0.9, ("accept", "background", "similarity_floor")),
        ("accept", "unrelated", 9, False, 0.5, ("hold", None, "incomplete_unrelated_hold")),
        ("accept", "unrelated", 9, True, 0.5, ("accept", "unrelated", "unrelated_kept")),
        ("reject", None, 0, False, None, ("accept", "unrelated", "unrelated_kept")),
        ("hold", None, 0, False, None, ("hold", None, "native_model_decision")),
        ("hold", None, 0, True, None, ("accept", "background", "undecided_after_second_look")),
    ],
)
def test_reading_disposition_never_rejects(decision, relevance, omitted, second, cosine, expected):
    assert (
        reading_disposition(
            decision,
            relevance,
            omitted_chars=omitted,
            second_look=second,
            document_cosine=cosine,
            keep_at_or_above=0.8,
        )
        == expected
    )


def test_unrelated_reading_is_kept_in_the_corpus():
    result, _ = collect(ScriptedJudge(reading(relevance="unrelated")))
    assert len(result.documents) == 1
    assert result.documents[0].verdict.relevance == "unrelated"
    assert answer_order(result.documents) == ()


def test_judge_failure_keeps_the_page_unjudged_and_the_round_goes_on():
    judge = ScriptedJudge(fail=RefusalCode.MODEL_UNAVAILABLE)
    result, route = collect(judge, max_depth=1)
    assert judge.calls >= 2 and len(route.requests) >= 2
    assert result.documents and all(
        doc.verdict.relevance == "unjudged" and doc.verdict.decision == "accept"
        for doc in result.documents
    )
    assert any(row.reason == "served_judge_failed" for row in result.ledger)
    assert answer_order(result.documents) == result.documents


def test_a_judge_bound_keeps_the_page_and_the_round_goes_on():
    result, route = collect(ScriptedJudge(fail=RefusalCode.BOUND_EXCEEDED), max_depth=1)
    assert len(route.requests) == 2
    assert {doc.verdict.relevance for doc in result.documents} == {"unjudged"}


def test_any_served_judge_refusal_keeps_the_page_unjudged():
    result, _ = collect(ScriptedJudge(fail=RefusalCode.ADAPTER_CONTRACT))
    assert [doc.verdict.relevance for doc in result.documents] == ["unjudged"]
    assert "adapter_contract" in result.documents[0].verdict.reason


def test_hold_is_resolved_by_the_second_look():
    judge = ScriptedJudge(reading(decision="hold"), reading(relevance="direct"))
    result, _ = collect(judge)
    assert judge.calls == 2 and result.documents[0].verdict.relevance == "direct"
    undecided = ScriptedJudge(reading(decision="hold"))
    result, _ = collect(undecided)
    kept = result.documents[0].verdict
    assert undecided.calls == 2 and (kept.decision, kept.grade) == ("accept", "background")
    assert kept.reason.startswith("undecided after the second look")


class QuietLinks(FakeExtractor):
    async def extract(self, page):
        return Extracted(
            title="fixture",
            text=page.body.decode() + " ports",
            language="en",
            links=(LinkCandidate(url="https://example.org/quiet", anchor="unscored words"),),
        )


def test_a_reading_link_lead_on_the_page_is_queued_and_an_invented_one_is_not():
    lead = reading(
        relevance="supporting",
        follow=(
            {"kind": "link", "value": "https://example.org/quiet"},
            {"kind": "link", "value": "https://example.org/invented"},
        ),
    )
    result, route = collect(ScriptedJudge(lead), extractor=QuietLinks(), max_depth=1)
    fetched = [request.url for request in route.requests]
    assert "https://example.org/quiet" in fetched
    assert "https://example.org/invented" not in fetched
    baseline, route = collect(ScriptedJudge(reading()), extractor=QuietLinks(), max_depth=1)
    assert [request.url for request in route.requests] == ["https://example.org/root"]


def test_research_round_counts_grades_coverage_and_queues_reading_leads():
    judge = ScriptedJudge(
        reading(
            relevance="supporting",
            informs=("q1",),
            contribution="Names the port.",
            follow=(
                {"kind": "query", "value": "port authority statement"},
                {"kind": "link", "value": "https://example.org/quiet"},
            ),
            names=({"kind": "organisation", "text": "Port Authority"},),
        )
    )
    planner = RecordingPlanner()
    cfg = config(research=policy(max_depth=1, saturation_rounds=1))
    loop = ResearchLoop(
        config=cfg,
        collector=GoalLoop(
            config=cfg,
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=QuietLinks(),
            scorer=KeywordScorer(),
            judge=judge,
        ),
        search=SearchFixture(),
        planner=planner,
        analyst=AnalystFixture(missing=True),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports")))
    first = result.rounds[0]
    assert ("q1",) in judge.questions
    assert first.pages_by_grade["supporting"] >= 1
    assert first.informed_questions == {"q1": first.pages_by_grade["supporting"]}
    assert "https://example.org/quiet" in first.followed_leads
    later = planner.requests[1].readings
    assert later and later[0].informs == ("q1",)
    assert later[0].queries == ("port authority statement",)
    assert later[0].names == ("organisation: Port Authority",)
    assert result.model_validate_json(result.model_dump_json()) == result


def test_legacy_verdict_rows_still_load_and_keep_their_meaning():
    legacy = {
        "decision": "reject",
        "kind": "fixture",
        "publisher": "fixture",
        "language": "en",
        "reason": "old ledger",
    }
    loaded = Verdict.model_validate_json(json.dumps(legacy))
    assert loaded.relevance is None and loaded.grade == "unrelated"
    assert json.loads(loaded.model_dump_json()) == dict(legacy, model_call=None, date=None)
    accepted = Verdict.model_validate(dict(legacy, decision="accept"))
    assert accepted.grade == "supporting" and accepted.answer_rank is not None
    document = Document.model_validate(
        {
            "url": "https://example.org/a",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "raw": b"x",
            "extracted": {"title": "t", "text": "x", "language": "en"},
            "verdict": dict(legacy, decision="accept"),
        }
    )
    assert planning_readings((document,)) == ()
    assert answer_order((document,)) == (document,)
