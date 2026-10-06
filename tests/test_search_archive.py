"""Discovery provenance survives completed research and private archive readback."""

import asyncio
import hashlib

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.ledger import Ledger
from ghimera.models import ModelIdentity
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_types import ResearchPlan, ResearchResult, SearchQuery
from ghimera.result_archive import ResearchResultArchive
from ghimera.search_history import SearchHistory
from tests.test_c0 import config
from tests.test_intent_research import PlannerFixture, SearchFixture, policy, run


def test_search_bytes_and_binding_survive_complete_archive(tmp_path):
    result, _, _ = run()
    assert result.schema_version == "chimera.research-result/2"
    (observation,) = result.search_observations
    assert observation.response.raw == b'{"fixture":"search"}'
    assert observation.query.text == "ports reports"
    row = result.harvest.ledger[observation.sequence]
    assert row.reason == "grounded_search:" + hashlib.sha256(observation.response.raw).hexdigest()
    assert row.route == "search:fixture_search@1"
    assert row.bytes_read == len(observation.response.raw)
    archive = ResearchResultArchive.create(tmp_path / "result", run_id="search-evidence")
    try:
        archive.write(result, max_bytes=10000000)
        assert ResearchResultArchive.read(tmp_path / "result", max_bytes=10000000) == result
    finally:
        archive.close()


@pytest.mark.parametrize(
    "mutation", ("drop", "duplicate", "raw", "query", "provider", "sequence", "hit")
)
def test_result_reader_refuses_search_observations_that_do_not_bind(mutation):
    result, _, _ = run()
    raw = result.model_dump(mode="json")
    observed = raw["search_observations"]
    if mutation == "drop":
        raw["search_observations"] = []
    elif mutation == "duplicate":
        observed.append(observed[0])
    elif mutation == "raw":
        observed[0]["response"]["raw"] = "dGFtcGVyZWQ="
    elif mutation == "query":
        observed[0]["query"]["text"] = "other query"
    elif mutation == "provider":
        observed[0]["provider"] = "other-provider"
    elif mutation == "sequence":
        observed[0]["sequence"] = len(result.harvest.ledger)
    else:
        observed[0]["response"]["hits"][0]["url"] = "https://example.org/different"
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(raw)


def test_legacy_result_keeps_its_identity_without_claiming_search_retention():
    result, _, _ = run()
    raw = result.model_dump(mode="json")
    raw["schema"] = "chimera.research-result/1"
    raw.pop("search_observations")
    for row in raw["harvest"]["ledger"]:
        row.pop("search_response_sha256", None)
    legacy = ResearchResult.model_validate(raw)
    assert legacy.search_observations == ()
    assert legacy.model_dump(mode="json") == raw
    current = result.model_dump(mode="json")
    current["schema"] = "chimera.research-result/1"
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(current)


def test_concurrent_queries_keep_each_actual_completion_sequence():
    class TwoQueries(PlannerFixture):
        model = ModelIdentity(model_id="two-query-planner", revision="1", location="test_double")

        async def plan(self, request):
            plan = await super().plan(request)
            return ResearchPlan(
                questions=plan.questions,
                queries=(
                    SearchQuery(text="first", question_ids=("q1",)),
                    SearchQuery(text="second", question_ids=("q1",)),
                ),
            )

    result, _, _ = run(planner=TwoQueries())
    assert {o.query.text for o in result.search_observations} == {"first", "second"}
    assert len({o.sequence for o in result.search_observations}) == 2
    assert all(
        result.harvest.ledger[o.sequence].query == o.query.text for o in result.search_observations
    )


def test_delayed_completion_failure_and_concurrent_runs_keep_owned_history():
    class Delayed(SearchFixture):
        async def request(self, request):
            if request.query.text == "first":
                await asyncio.sleep(0.01)
            if request.query.text == "failed":
                raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
            return await super().request(request)

    async def scenario():
        cfg, provider = config(research=policy()), Delayed()
        first_ledger, second_ledger = Ledger(), Ledger()
        first = SearchHistory(provider, RunBudget(cfg, lambda: 0.0), first_ledger)
        second = SearchHistory(provider, RunBudget(cfg, lambda: 0.0), second_ledger)
        calls = ((first, "first"), (first, "second"), (first, "failed"), (second, "separate"))
        values = await asyncio.gather(
            *(
                history.discover(
                    SearchQuery(text=text, question_ids=("q1",)),
                )
                for history, text in calls
            ),
            return_exceptions=True,
        )
        assert isinstance(values[2], GhimeraRefused)
        assert [o.query.text for o in first.observations] == ["second", "first"]
        assert [o.query.text for o in second.observations] == ["separate"]
        assert len(first_ledger.snapshot()) == 3
        assert first_ledger.snapshot()[1].refusal == RefusalCode.SEARCH_UNAVAILABLE
        for history, ledger in ((first, first_ledger), (second, second_ledger)):
            assert all(
                ledger.snapshot()[o.sequence].query == o.query.text
                and ledger.snapshot()[o.sequence].search_response_sha256
                == o.response.content_digest()
                for o in history.observations
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ("anchor", "snippet"))
def test_citing_source_decision_cannot_invent_a_hit_beside_a_valid_search_digest(field):
    from tests.test_cited_by_expansion import run as cited_run

    result, _, _ = cited_run()
    assert len(result.search_observations) == 2
    raw = result.model_dump(mode="json")
    reference = next(row["reference"] for row in raw["harvest"]["ledger"] if row["reference"])
    reference["reference"][field] = "not supplied by this provider"
    with pytest.raises(ValidationError, match="retained actual search hit"):
        ResearchResult.model_validate(raw)
