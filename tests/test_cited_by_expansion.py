"""Citing-source candidates come from an accounted provider, never an LLM URL."""

import asyncio
import hashlib

import pytest
from pydantic import ValidationError

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Extracted, LinkCandidate
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, ResearchResult, SearchHit, SearchResponse
from ghimera.search import GroundedSearch
from tests.test_c0 import config
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    policy,
)
from tests.test_reference_expansion import reference, references


class CitedSearch(GroundedSearch):
    name = "fixture_citations"
    revision = "1"

    def __init__(self):
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        cited = "cited by" in request.query.text
        url = "https://citing.example/ports" if cited else "https://example.org/ports"
        return SearchResponse(
            raw=("observed response " + request.query.text).encode(),
            hits=(SearchHit(url=url, title="Port report", snippet="Discovery, not evidence"),),
        )


def run(
    *, reference_updates=None, missing=False, research_updates=None, search=None, extractor=None
):
    updates = dict(discover_cited_by=True, cited_by_query_budget=2, outside_scope="observed_public")
    updates.update(reference_updates or {})
    cfg = config(
        references=references(**updates),
        research=policy(**(research_updates or {})),
        page_budget=30,
    )
    route, search = FakeRoute(), search or CitedSearch()
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=extractor or FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    loop = ResearchLoop(
        config=cfg,
        collector=collector,
        search=search,
        planner=PlannerFixture(),
        analyst=AnalystFixture(missing=missing),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports")))
    return result, route, search


def test_cited_by_search_binds_native_parent_and_actual_provider_response():
    result, route, search = run()
    assert result.status == "answered"
    assert [item.url for item in route.requests] == [
        "https://example.org/ports",
        "https://citing.example/ports",
    ]
    assert len(search.requests) == 2
    query = next(row for row in result.harvest.ledger if row.reference_query)
    decision = next(row for row in result.harvest.ledger if row.reference)
    source = result.harvest.documents[0]
    assert query.reference_query.source.sha256 == source.sha256
    assert source.url in query.query and source.extracted.title in query.query
    assert decision.reference.reference.query_sequence == query.sequence
    assert (
        decision.reference.reference.response_sha256
        == hashlib.sha256(("observed response " + query.query).encode()).hexdigest()
    )
    assert result.harvest.receipt.fetches == 4  # two source fetches and two searches
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_query_budget_and_source_identity_survive_followup_rounds():
    result, _, search = run(missing=True, reference_updates={"cited_by_query_budget": 1})
    assert result.status == "partial"
    assert len([item for item in search.requests if "cited by" in item.query.text]) == 1
    assert len([row for row in result.harvest.ledger if row.reference_query]) == 1
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_native_and_cited_by_candidates_share_the_parent_cap():
    class NativeReferences(FakeExtractor):
        async def extract(self, page):
            target = "https://native-reference.example/ports"
            text = "Port source: " + target
            return Extracted(
                title="Ports",
                text=text,
                language="en",
                links=(LinkCandidate(url=target, anchor=text),),
                references=(reference(target, text),),
            )

    result, route, _ = run(
        reference_updates={"max_candidates_per_parent": 1}, extractor=NativeReferences()
    )
    assert {item.url for item in route.requests} == {
        "https://example.org/ports",
        "https://native-reference.example/ports",
    }
    citing = next(
        row.reference
        for row in result.harvest.ledger
        if row.reference and row.url == "https://citing.example/ports"
    )
    assert citing.outcome == "budget_limit"
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_failed_cited_by_search_is_accounted_without_inventing_candidates():
    class UnavailableCitations(CitedSearch):
        async def request(self, request):
            response = await super().request(request)
            if "cited by" in request.query.text:
                raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
            return response

    result, route, search = run(search=UnavailableCitations())
    assert result.status == "answered"  # the original evidence answers this fixture question
    assert len(search.requests) == 2 and len(route.requests) == 1
    assert result.harvest.receipt.fetches == 3
    assert not any(row.reference for row in result.harvest.ledger)
    assert any(row.refusal == RefusalCode.SEARCH_UNAVAILABLE for row in result.harvest.ledger)
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_followup_discovery_and_references_share_the_total_source_host_cap():
    class AdditionalHostSearch(CitedSearch):
        async def request(self, request):
            first_discovery = not self.requests
            response = await super().request(request)
            if "cited by" not in request.query.text and not first_discovery:
                return response.model_copy(
                    update={
                        "hits": (
                            SearchHit(
                                url="https://another.example/ports", title="Ports", snippet=""
                            ),
                        )
                    }
                )
            return response

    result, route, _ = run(
        missing=True,
        research_updates={"max_source_hosts": 2},
        search=AdditionalHostSearch(),
    )
    assert {item.url for item in route.requests} == {
        "https://example.org/ports",
        "https://citing.example/ports",
    }
    assert any(
        row.url == "https://another.example/ports" and row.refusal for row in result.harvest.ledger
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"discover_cited_by": False},
        {"denied_hosts": ("citing.example",)},
        {"outside_scope": "refuse"},
    ],
)
def test_cited_by_never_bypasses_configured_policy(updates):
    result, route, _ = run(reference_updates=updates)
    assert len(route.requests) == 1
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field", ["response_sha256", "query_sequence"])
def test_serialized_search_reference_cannot_lose_its_provider_binding(field):
    result, _, _ = run()
    raw = result.model_dump(mode="json", by_alias=True)
    reference = next(row["reference"] for row in raw["harvest"]["ledger"] if row["reference"])
    reference["reference"][field] = "0" * 64 if field == "response_sha256" else 0
    with pytest.raises(ValidationError, match="reference"):
        ResearchResult.model_validate(raw)
