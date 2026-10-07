"""Per-provider costs, concurrent retention, fallback, domains and continuation."""

import asyncio
import time

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.discovery import DiscoveryProviders
from ghimera.discovery_config import DiscoveryConfig, DiscoveryProgress, DiscoveryProvider
from ghimera.ledger import Ledger
from ghimera.refusals import FetchFailure, GhimeraRefused, RefusalCode
from ghimera.research_types import (
    ResearchResult,
    ResearchRound,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from ghimera.result_archive import ResearchResultArchive
from ghimera.search import GroundedSearch
from ghimera.search_history import SearchHistory
from tests.test_c0 import config
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_http_fetch import ResolverFixture
from tests.test_intent_research import policy
from tests.test_mcp_leads import FixtureClient, envelope, recipe

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def provider(identifier, **updates):
    values = dict(
        id=identifier,
        domains=["open_web"],
        binding=recipe().model_dump(),
        query_disclosure="planned_query",
        use_contract="crawl_and_retain",
        max_calls=4,
        byte_budget=100000,
        call_seconds_budget=10,
        max_response_bytes=10000,
        max_results=2,
        timeout_seconds=2,
        consecutive_failure_limit=1,
    )
    values.update(updates)
    return DiscoveryProvider.model_validate(values)


def discovery(*providers, **updates):
    values = dict(
        schema="ghimera.discovery/1",
        providers=providers or (provider("first"), provider("second")),
        target_domains=["open_web"],
        allow_cross_domain_expansion=False,
        mode="ordered_fallback",
        cold_start_fanout=False,
        provider_concurrency=2,
        stagnation_window=1,
        min_new_documents=1,
        min_new_answers=1,
        max_strategy_changes=2,
    )
    values.update(updates)
    return DiscoveryConfig.model_validate(values)


class Adapter(GroundedSearch):
    name = "fixture_discovery"
    revision = "1"

    def __init__(self, *, urls=(), failure=False, pause=0):
        self.urls, self.failure, self.pause = urls, failure, pause
        self.calls = []

    async def request(self, request):
        self.calls.append(request)
        await asyncio.sleep(self.pause)
        if self.failure:
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, 3)
        return SearchResponse(
            raw=b"source leads",
            hits=tuple(
                SearchHit(url=url, title="Observed", snippet="Only a lead")
                for url in self.urls[: request.limit]
            ),
        )


def state(selected, adapters, *, ledger=None, observations=(), **updates):
    cfg = config(search=None, discovery=selected, research=policy(**updates))
    budget = RunBudget(cfg, time.monotonic)
    ledger = ledger if ledger is not None else Ledger()
    bound = DiscoveryProviders(selected, adapters)
    return SearchHistory(bound, budget, ledger, restored=observations), budget, ledger


def query(text="find ports"):
    return SearchQuery(text=text, question_ids=("q1",))


def test_discovery_example_is_explicit_and_non_active():
    import tomllib
    from pathlib import Path

    selected = DiscoveryConfig.model_validate(
        tomllib.loads(Path("examples/discovery.toml").read_text())["discovery"]
    )
    assert selected.target_domains == ("open_web", "onion")
    assert selected.providers[1].binding.tool_name == "onion_search"


def test_invalid_call_limits_refuse_before_reserving_or_contacting():
    from ghimera.discovery_config import SearchCallLimits

    adapter = Adapter()
    cfg = config(research=policy())
    budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
    limits = SearchCallLimits(max_bytes=10, limit=1, timeout_seconds=1).model_copy(
        update={"max_bytes": 0}
    )
    with pytest.raises(ValidationError):
        asyncio.run(adapter.discover(query(), budget, ledger, limits=limits))
    assert not adapter.calls and not ledger.snapshot() and budget.search_calls == 0


def test_empty_fallback_retains_each_actual_response_and_charges_each_call():
    first, second = Adapter(), Adapter(urls=("https://example.org/report",))
    history, budget, ledger = state(discovery(), {"first": first, "second": second})
    observations = asyncio.run(history.discover_many(query()))
    assert [obs.provider for obs in observations] == ["binding:first", "binding:second"]
    assert observations[0].response.hits == ()
    assert observations[1].response.hits[0].url == "https://example.org/report"
    assert budget.search_calls == budget.fetches == 2 and budget.bytes_read == 24
    assert len(ledger.snapshot()) == len(history.observations) == 2
    assert all(
        obs.response.content_digest() == ledger.snapshot()[obs.sequence].search_response_sha256
        for obs in observations
    )
    assert first.calls[0].max_bytes == 10000 and first.calls[0].limit == 2


def test_provider_failure_circuit_and_call_quota_survive_reconstruction():
    selected = discovery(provider("first"), provider("second", max_calls=1))
    first, second = Adapter(failure=True), Adapter(urls=("https://example.org/one",))
    history, _, ledger = state(selected, {"first": first, "second": second})
    asyncio.run(history.discover_many(query()))
    restored, _, _ = state(
        selected,
        {"first": first, "second": second},
        ledger=Ledger(restored_rows=ledger.snapshot()),
        observations=history.observations,
    )
    with pytest.raises(GhimeraRefused, match="search_unavailable"):
        asyncio.run(restored.discover_many(query("another query")))
    assert len(first.calls) == len(second.calls) == 1


def test_stagnation_rotation_uses_retained_round_progress_not_global_state():
    first, second = (
        Adapter(urls=("https://example.org/one",)),
        Adapter(urls=("https://example.org/two",)),
    )
    selected = discovery()
    history, _, _ = state(selected, {"first": first, "second": second})
    empty_round = ResearchRound(
        number=1,
        queries=(query(),),
        discovered_urls=(),
        assessment=None,
        collection_stop="frontier_empty",
        discovery_progress=DiscoveryProgress(new_documents=0, new_answers=0),
    )
    history.set_rounds((empty_round,))
    assert asyncio.run(history.discover_many(query()))[0].provider == "binding:second"
    other, _, _ = state(selected, {"first": first, "second": second})
    assert asyncio.run(other.discover_many(query()))[0].provider == "binding:first"


def test_concurrent_fanout_retains_every_neighbor_on_failure_and_reserves_quotas():
    async def scenario():
        first, second = (
            Adapter(failure=True, pause=0.01),
            Adapter(urls=("https://example.org/one",), pause=0.02),
        )
        history, budget, ledger = state(
            discovery(mode="fanout"), {"first": first, "second": second}
        )
        batches = await asyncio.gather(
            history.discover_many(query()), history.discover_many(query("other ports"))
        )
        assert all(batch for batch in batches)
        assert budget.search_calls == len(first.calls) + len(second.calls)
        assert budget.quiescent
        assert len(history.observations) == len(second.calls)
        for obs in history.observations:
            row = ledger.snapshot()[obs.sequence]
            assert row.query == obs.query.text and row.refusal is None
            assert row.search_response_sha256 == obs.response.content_digest()

    asyncio.run(scenario())


def test_onion_only_discovery_does_not_expand_to_clearnet_from_returned_hits():
    selected = discovery(provider("onions", domains=["onion"]), target_domains=["onion"])
    adapter = Adapter(urls=("http://" + "a" * 56 + ".onion/report", "https://example.org/report"))
    history, _, _ = state(selected, {"onions": adapter})
    response = asyncio.run(history.discover_many(query()))[0].response
    assert len(response.hits) == 1 and ".onion/" in response.hits[0].url
    assert response.raw == b"source leads"


def test_cancelled_fanout_releases_all_run_and_provider_reservations():
    async def scenario():
        adapters = {"first": Adapter(pause=10), "second": Adapter(pause=10)}
        history, budget, ledger = state(discovery(mode="fanout"), adapters)
        task = asyncio.create_task(history.discover_many(query()))
        while budget.search_calls < 2:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.quiescent and len(ledger.snapshot()) == 2
        assert all(row.refusal is not None for row in ledger.snapshot())

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["duplicate", "cross_domain", "unsupported", "single_search"])
def test_ambiguous_discovery_configuration_refuses_without_any_calls(fault):
    with pytest.raises(ValidationError):
        if fault == "duplicate":
            discovery(provider("same"), provider("same"))
        elif fault == "cross_domain":
            discovery(
                provider("all", domains=["open_web", "onion"]), target_domains=["open_web", "onion"]
            )
        elif fault == "unsupported":
            discovery(target_domains=["onion"])
        else:
            config(discovery=discovery(), search=recipe(), research=policy())
    assert "discovery" not in config().model_dump()


def test_collector_fallback_native_evidence_archive_and_provider_binding(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    selected = discovery()
    cfg = GhimeraConfig.model_validate(dict(cfg.model_dump(), search=None, discovery=selected))
    empty = envelope(url)
    empty["structuredContent"]["data"]["leads"] = []
    clients = {
        "first": FixtureClient(empty, echo_query=True),
        "second": FixtureClient(envelope(url), echo_query=True),
    }
    with pytest.raises(ValueError, match="exactly its provider client"):
        Collector(cfg, discovery_clients={"first": clients["first"]})
    assert not source_site[1] and not model_endpoint[1]
    result = asyncio.run(
        Collector(cfg, discovery_clients=clients, source_resolver=ResolverFixture()).run(
            "find ports"
        )
    )
    assert result.status == "answered" and result.search_calls == 2
    assert result.search_provider == "discovery"
    assert result.rounds[0].discovery_progress == DiscoveryProgress(new_documents=1, new_answers=1)
    assert result.answer.claims[0].citations[0].matches(result.harvest.documents[0])
    assert source_site[1]["/plain"] == 1 and not search_endpoint[1]
    archive = tmp_path / "archive"
    writer = ResearchResultArchive.create(archive, run_id="discovery-result")
    try:
        writer.write(result, max_bytes=10000000)
    finally:
        writer.close()
    assert ResearchResultArchive.read(archive, max_bytes=10000000) == result
    bad = result.model_dump()
    bad["search_observations"][0]["provider"] = "binding:unknown"
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(bad)


def test_stagnant_real_research_rounds_resume_on_another_provider_without_fresh_quotas(tmp_path):
    from ghimera.continuation import CheckpointStore, ResearchSuspended
    from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.loop import GoalLoop
    from ghimera.research import ResearchLoop
    from ghimera.research_types import ResearchRequest
    from tests.test_intent_research import AnalystFixture, PlannerFixture, ReviewerFixture
    from tests.test_research_continuation import configured

    class NeedsTwoSources(AnalystFixture):
        async def assess(self, request):
            self.missing = len(request.documents) < 2
            return await super().assess(request)

    selected = discovery()
    base = configured(tmp_path)
    cfg = GhimeraConfig.model_validate(
        dict(
            base.model_dump(),
            discovery=selected,
            search=None,
            research=policy(max_depth=0, max_pages_per_round=2, max_rounds=4, query_budget=10),
        )
    )

    def loop(adapters):
        collection = GoalLoop(
            config=cfg,
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        )
        return ResearchLoop(
            config=cfg,
            collector=collection,
            search=DiscoveryProviders(selected, adapters),
            planner=PlannerFixture(),
            analyst=NeedsTwoSources(),
            reviewer=ReviewerFixture(),
        )

    first = {
        "first": Adapter(urls=("https://example.org/one",)),
        "second": Adapter(urls=("https://example.org/two",)),
    }
    with pytest.raises(ResearchSuspended) as stopped:
        asyncio.run(
            loop(first).run(
                ResearchRequest(intent="body ports"), run_id="stagnant", suspend_after_rounds=2
            )
        )
    checkpoint = CheckpointStore(cfg, "stagnant").read(stopped.value.receipt.sha256)
    assert checkpoint.progress.rounds[-1].discovery_progress.new_documents == 0
    assert checkpoint.progress.search_calls == 2 and len(first["first"].calls) == 2
    resumed = {
        "first": Adapter(urls=("https://example.org/one",)),
        "second": Adapter(urls=("https://example.org/two",)),
    }
    result = asyncio.run(
        loop(resumed).resume("stagnant", checkpoint_sha256=stopped.value.receipt.sha256)
    )
    assert result.status == "answered" and result.search_calls == 3
    assert not resumed["first"].calls and len(resumed["second"].calls) == 1
    assert len(result.harvest.documents) == 2
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_citing_source_queries_bind_each_actual_fanout_provider(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    from tests.test_reference_expansion import references

    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    cfg = GhimeraConfig.model_validate(
        dict(
            cfg.model_dump(),
            search=None,
            discovery=discovery(mode="fanout"),
            references=references(discover_cited_by=True, cited_by_query_budget=2),
        )
    )
    clients = {name: FixtureClient(envelope(url), echo_query=True) for name in ("first", "second")}
    result = asyncio.run(
        Collector(cfg, discovery_clients=clients, source_resolver=ResolverFixture()).run(
            "find ports"
        )
    )
    queries = [
        row.reference_query for row in result.harvest.ledger if row.event == "reference_query"
    ]
    assert {item.provider for item in queries} == {"binding:first", "binding:second"}
    assert result.status == "answered" and result.search_calls == 4
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_provider_timeout_remains_a_failure_and_healthy_neighbor_is_retained():
    first, second = Adapter(pause=2), Adapter(urls=("https://example.org/one",))
    selected = discovery(provider("first", timeout_seconds=0.01), provider("second"), mode="fanout")
    history, budget, ledger = state(selected, {"first": first, "second": second})
    observations = asyncio.run(history.discover_many(query()))
    assert len(observations) == 1 and observations[0].provider == "binding:second"
    assert budget.quiescent and budget.search_calls == 2
    assert any(row.refusal == RefusalCode.SEARCH_UNAVAILABLE for row in ledger.snapshot())
