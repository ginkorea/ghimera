"""Native durable fixtures prove accounting/replay, not multilingual ranking quality."""

import asyncio
import hashlib
import time
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.corpus_evidence import CorpusEvidenceReader
from ghimera.corpus_search import CorpusLeadSearch
from ghimera.corpus_search_wire import CorpusSearchWire
from ghimera.discovery_config import DiscoveryConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.journal_types import JournalReport
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.model_work import FatalModelWorkFailure, uncertain_model_sequences
from ghimera.models import Goal, LedgerRow, Receipt
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import ResearchLoop
from ghimera.research_reranking import RunBoundReranker, validate_rerank_rows, validate_run_evidence
from ghimera.research_reranking_types import RerankDecision, ResearchRerankingConfig
from ghimera.research_reuse import ResearchRetrievalReport, RetainedResearchSession
from ghimera.research_types import ResearchRequest, ResearchResult
from ghimera.search_history import SearchHistory
from tests.test_c0 import config
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_corpus_search import binding, request
from tests.test_encoding_recovery import FixtureHttp
from tests.test_evidence_corpus import harvest
from tests.test_hybrid_retrieval import policy as hybrid_policy
from tests.test_intent_research import AnalystFixture, PlannerFixture, ReviewerFixture, policy
from tests.test_offline_reranking import ScoresPort, learned_corpus, native_store
from tests.test_research_reuse import assemble, reuse_policy


def run_policy(**updates):
    return ResearchRerankingConfig.model_validate(
        dict(
            schema="ghimera.research-reranking/1",
            max_calls=6,
            max_pairs=30,
            max_input_chars=30000,
            uncertain_policy="hold",
            replay_policy="explicit_original_ack",
        )
        | updates
    )


def decision(key="query-1", *, original=None):
    return RerankDecision(
        schema="ghimera.rerank-decision/1",
        action="fresh" if original is None else "replay",
        operation_key=key,
        original_intent_sequence=original,
    )


def run_config(tmp_path, store, *, retained=False, reranking=None, **updates):
    reader = CorpusEvidenceReader(reader_policy(store, retrieval=hybrid_policy()), store)
    return config(
        judge_budget=40,
        search=None
        if retained
        else binding(store, retrieval=hybrid_policy(), max_response_bytes=1000000),
        research=policy(
            reranking=reranking or run_policy(),
            retained_evidence=reuse_policy(reader) if retained else None,
            max_model_input_chars=1000000,
        ),
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=tmp_path / "journal",
            max_record_bytes=4000000,
            max_journal_bytes=32000000,
            max_summary_bytes=4000000,
            max_records=2000,
        ),
        model_work=dict(
            schema="ghimera.model-work/1",
            max_input_bytes=2000000,
            max_unanswered_calls=4,
            uncertain_policy="hold",
            results=dict(
                schema="ghimera.model-results/1",
                max_result_bytes=1000000,
                max_total_result_bytes=10000000,
            ),
        ),
        **updates,
    )


def opened(cfg, *, run_id="reranking", rows=None):
    sink = DirectoryLedgerSink(
        cfg, run_id, Goal(text="find ports"), FakeJudge().model, resume_rows=rows
    )
    return RunBudget(cfg, time.monotonic), Ledger(sink=sink, restored_rows=rows or ())


async def prepared(tmp_path, *, port_type=ScoresPort):
    recipe = learned_corpus(
        tmp_path,
        encoding_recovery=dict(
            schema="ghimera.encoding-recovery/1",
            max_calls=50,
            max_input_chars=100000,
            max_stored_bytes=1000000,
        ),
    )
    port = port_type(recipe.reranking)
    query = FixtureHttp(recipe.query_encoder)
    store = native_store(recipe, port, create=True, query=query)
    await store.append(await harvest(("en", "ports report")))
    return store, port, query


def test_versioned_operator_policy_is_inert_and_legacy_bytes_stay_omitted():
    old = config(research=policy())
    assert "reranking" not in old.research.model_dump()
    assert (
        "rerank_reservation" not in LedgerRow(sequence=0, event="policy", reason="old").model_dump()
    )
    parsed = ResearchRerankingConfig.model_validate(
        tomllib.loads(Path("examples/research-reranking.toml").read_text())
    )
    assert parsed.uncertain_policy == "hold"
    for updates in (dict(max_calls=0), dict(uncertain_policy="retry"), dict(max_pairs=True)):
        with pytest.raises(ValidationError):
            run_policy(**updates)
    with pytest.raises(ValidationError):
        config(research=policy(reranking=parsed))
    with pytest.raises(ValidationError):
        RerankDecision(schema="ghimera.rerank-decision/1", action="replay", operation_key="x")
    with pytest.raises(ValidationError):
        decision(original=0).model_copy(update={"action": "fresh"}).model_validate(
            dict(decision(original=0).model_dump(), action="fresh")
        )


@pytest.mark.parametrize("channel", ["discovery", "retained"])
def test_native_run_consumers_ack_before_admitting_scores_and_replay_without_contact(
    tmp_path, channel
):
    async def operation():
        store, port, encoding = await prepared(tmp_path)
        cfg = run_config(tmp_path, store, retained=channel == "retained")
        budget, ledger = opened(cfg)
        original_score = port.score

        async def inspected(native):
            observed = read_journal(cfg.journal, "reranking")
            last = observed.rows[-1]
            assert last.model_intent.phase == "reranking"
            assert last.rerank_reservation.request == native
            assert last.model_intent.judge_reservation == budget.judge_calls == 1
            assert observed.uncertain_model_calls == (last.sequence,)
            assert validate_rerank_rows(cfg, observed.rows)[0] == budget.rerank_calls == 1
            return await original_score(native)

        port.score = inspected
        try:
            if channel == "discovery":
                provider = CorpusLeadSearch(cfg.search, store, run_policy=cfg.research.reranking)
                response = await provider.discover(
                    request().query, budget, ledger, rerank_decision=decision()
                )
                native = CorpusSearchWire.model_validate_json(response.raw).query
            else:
                reader = CorpusEvidenceReader(cfg.research.retained_evidence.reader, store)
                session = RetainedResearchSession(
                    cfg.research.retained_evidence,
                    reader,
                    "find ports",
                    budget=budget,
                    ledger=ledger,
                )
                await session.query("ports", remaining_seconds=10.0, rerank_decision=decision())
                native = session.report.snapshots[0].query
                session.report.validate_run(cfg, ledger.snapshot())
            proof = native.reranking_run
            assert proof is not None and native.reranking is not None
            before = encoding.calls, len(port.requests), port.prepared
            if channel == "discovery":
                returned = await provider.discover(
                    request().query,
                    budget,
                    ledger,
                    rerank_decision=decision(original=proof.intent_sequence),
                )
                replay = CorpusSearchWire.model_validate_json(returned.raw).query
                assert budget.search_calls == budget.fetches == 2
            else:
                await session.query(
                    "ports",
                    remaining_seconds=10.0,
                    rerank_decision=decision(original=proof.intent_sequence),
                )
                replay = session.report.snapshots[-1].query
                assert session.report.schema_version == "ghimera.research-retrieval/2"
                session.report.validate_run(cfg, ledger.snapshot())
            assert (encoding.calls, len(port.requests), port.prepared) == before
            assert budget.judge_calls == budget.rerank_calls == 1
            assert replay.reranking == native.reranking
            assert replay.reranking_run.replay_sequence is not None
            validate_run_evidence(
                cfg,
                ledger.snapshot(),
                replay.reranking.request,
                replay.reranking.scores,
                replay.reranking_run,
                channel=channel,
            )
            with pytest.raises(FatalModelWorkFailure):
                await store.search(
                    "ports",
                    top_k=2,
                    retrieval=hybrid_policy(),
                    rerank_invoker=RunBoundReranker(
                        budget, ledger, channel=channel, decision=decision()
                    ),
                )
            assert len(port.requests) == 1
        finally:
            ledger.close()
            store.close()
        report = read_journal(cfg.journal, "reranking")
        assert report.state == "unsealed" and not report.uncertain_model_calls

    asyncio.run(operation())


@pytest.mark.parametrize("retained", [False, True])
def test_actual_research_loop_accepts_learned_native_discovery_and_retained_originals(
    tmp_path, retained
):
    async def operation():
        store, port, _ = await prepared(tmp_path)
        cfg = run_config(tmp_path, store, retained=retained)
        try:
            if retained:
                reader = CorpusEvidenceReader(cfg.research.retained_evidence.reader, store)
                loop, route, search = assemble(cfg, reader)
            else:
                route = FakeRoute()
                collector = GoalLoop(
                    config=cfg,
                    fetcher=FetchLadder((route,)),
                    extractor=FakeExtractor(),
                    scorer=KeywordScorer(),
                    judge=FakeJudge(),
                )
                search = CorpusLeadSearch(cfg.search, store, run_policy=cfg.research.reranking)
                loop = ResearchLoop(
                    config=cfg,
                    collector=collector,
                    search=search,
                    planner=PlannerFixture(),
                    analyst=AnalystFixture(),
                    reviewer=ReviewerFixture(),
                )
            result = await loop.run(ResearchRequest(intent="find ports"), run_id="complete")
            assert result.status == "answered"
            assert bool(route.requests) != retained
            assert port.requests and sum(
                row.rerank_reservation is not None for row in result.harvest.ledger
            ) == len(port.requests)
            assert result.harvest.receipt.judge_calls > len(port.requests)
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
            report = read_journal(cfg.journal, "complete")
            assert report.state == "complete" and not report.uncertain_model_calls
            if retained:
                assert result.retrieval.schema_version == "ghimera.research-retrieval/2"
                assert not search.requests
            else:
                assert result.search_observations
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("failure", ["cancelled", "bad_scores"])
def test_unknown_is_charged_and_neither_fresh_key_nor_replay_can_retry(tmp_path, failure):
    class FailedPort(ScoresPort):
        async def score(self, native):
            self.requests.append(native)
            if failure == "cancelled":
                raise asyncio.CancelledError
            self.bad = "partial"
            return await super().score(native)

    async def operation():
        store, port, encoder = await prepared(tmp_path, port_type=FailedPort)
        cfg = run_config(tmp_path, store)
        budget, ledger = opened(cfg)
        try:
            with pytest.raises((asyncio.CancelledError, ValueError)):
                await store.search(
                    "ports",
                    top_k=2,
                    retrieval=hybrid_policy(),
                    rerank_invoker=RunBoundReranker(
                        budget, ledger, channel="discovery", decision=decision()
                    ),
                )
            rows = ledger.snapshot()
            sequence = rows[0].sequence
            assert uncertain_model_sequences(rows) == (sequence,)
            assert budget.judge_calls == budget.rerank_calls == 1
            assert budget.rerank_pairs == 1 and budget.rerank_chars > 0
            assert validate_rerank_rows(cfg, rows) == (1, budget.rerank_pairs, budget.rerank_chars)
            before = len(port.requests), encoder.calls
            for selected in (
                decision(),
                decision("another-explicit-fresh-key"),
                decision(original=sequence),
            ):
                with pytest.raises(FatalModelWorkFailure):
                    await store.search(
                        "ports",
                        top_k=2,
                        retrieval=hybrid_policy(),
                        rerank_invoker=RunBoundReranker(
                            budget, ledger, channel="discovery", decision=selected
                        ),
                    )
            assert (len(port.requests), encoder.calls) == before
            ledger.close()
            assert read_journal(cfg.journal, "reranking").uncertain_model_calls == (sequence,)
        finally:
            ledger.close()
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("drift", ["generation", "query", "recipe", "sequence", "operation_key"])
def test_replay_refuses_drift_before_encoding_or_score_contact(tmp_path, drift):
    async def operation():
        store, port, encoder = await prepared(tmp_path)
        cfg = run_config(tmp_path, store)
        budget, ledger = opened(cfg)
        try:
            native = await store.search(
                "ports",
                top_k=2,
                retrieval=hybrid_policy(),
                rerank_invoker=RunBoundReranker(
                    budget, ledger, channel="discovery", decision=decision()
                ),
            )
            if drift == "generation":
                await store.append(await harvest(("en", "new ports text")))
            if drift == "recipe":
                port.config = port.config.model_copy(update={"batch_size": 1})
            selected = decision(
                "changed" if drift == "operation_key" else "query-1",
                original=100 if drift == "sequence" else native.reranking_run.intent_sequence,
            )
            before = len(port.requests), encoder.calls
            with pytest.raises((FatalModelWorkFailure, ValueError)):
                await store.search(
                    "different" if drift == "query" else "ports",
                    top_k=2,
                    retrieval=hybrid_policy(),
                    rerank_invoker=RunBoundReranker(
                        budget, ledger, channel="discovery", decision=selected
                    ),
                )
            assert (len(port.requests), encoder.calls) == before
        finally:
            ledger.close()
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("quota", ["calls", "pairs", "chars", "shared_judge"])
def test_exhausted_allowance_refuses_before_score_contact(tmp_path, quota):
    async def operation():
        store, port, _ = await prepared(tmp_path)
        selected = run_policy(
            max_calls=1 if quota == "calls" else 6,
            max_pairs=1 if quota == "pairs" else 30,
            max_input_chars=20 if quota == "chars" else 30000,
        )
        cfg = run_config(tmp_path, store, reranking=selected)
        budget, ledger = opened(cfg)
        try:
            await store.search(
                "ports",
                top_k=2,
                retrieval=hybrid_policy(),
                rerank_invoker=RunBoundReranker(
                    budget, ledger, channel="discovery", decision=decision()
                ),
            )
            # The shared allowance is additionally checked before any contact.
            if quota == "shared_judge":
                budget.judge_calls = cfg.judge_budget
            with pytest.raises(GhimeraRefused) as refusal:
                await store.search(
                    "ports",
                    top_k=2,
                    retrieval=hybrid_policy(),
                    rerank_invoker=RunBoundReranker(
                        budget, ledger, channel="discovery", decision=decision("next-explicit")
                    ),
                )
            assert refusal.value.code == RefusalCode.BUDGET_EXHAUSTED
            assert len(port.requests) == budget.rerank_calls == 1
        finally:
            ledger.close()
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("missing", ["policy", "sink", "encoding_owner", "decision"])
def test_missing_required_owners_refuse_without_encoding_or_score(tmp_path, missing):
    async def operation():
        store, port, encoder = await prepared(tmp_path)
        cfg = run_config(tmp_path, store)
        if missing == "policy":
            cfg = GhimeraConfig.model_validate(dict(cfg.model_dump(), research=policy()))
        if missing == "encoding_owner":
            store.config = store.config.model_copy(update={"encoding_recovery": None})
        budget, ledger = opened(cfg)
        bare = Ledger() if missing == "sink" else ledger
        before = encoder.calls
        try:
            if missing == "decision":
                with pytest.raises(GhimeraRefused):
                    await CorpusLeadSearch(
                        cfg.search, store, run_policy=cfg.research.reranking
                    ).discover(request().query, budget, bare)
                with pytest.raises(GhimeraRefused):
                    await CorpusLeadSearch(
                        cfg.search, store, run_policy=cfg.research.reranking
                    ).request(request())
            else:
                with pytest.raises((FatalModelWorkFailure, ValueError)):
                    await store.search(
                        "ports",
                        top_k=2,
                        retrieval=hybrid_policy(),
                        rerank_invoker=RunBoundReranker(
                            budget, bare, channel="discovery", decision=decision()
                        ),
                    )
            assert not port.requests and encoder.calls == before
        finally:
            ledger.close()
            store.close()

    asyncio.run(operation())


def test_restoration_preserves_original_counters_and_archive_mutations_refuse(tmp_path):
    async def operation():
        store, port, encoder = await prepared(tmp_path)
        cfg = run_config(tmp_path, store)
        budget, ledger = opened(cfg)
        try:
            native = await store.search(
                "ports",
                top_k=2,
                retrieval=hybrid_policy(),
                rerank_invoker=RunBoundReranker(
                    budget, ledger, channel="discovery", decision=decision()
                ),
            )
            rows = ledger.snapshot()
            receipt = Receipt(
                judge=FakeJudge().model,
                effective_config=cfg,
                fetches=0,
                bytes_read=0,
                judge_calls=1,
                accepted_documents=0,
                elapsed_seconds=0.1,
                stop_reason="frontier_empty",
            )
            ledger.close()
            restored, reopened = opened(cfg, rows=rows)
            restored.restore(receipt, rows, 0, 0.2)
            assert restored.judge_calls == restored.rerank_calls == 1
            assert (
                restored.rerank_pairs == budget.rerank_pairs
                and restored.rerank_chars == budget.rerank_chars
            )
            before = encoder.calls, len(port.requests)
            replayed = await store.search(
                "ports",
                top_k=2,
                retrieval=hybrid_policy(),
                rerank_invoker=RunBoundReranker(
                    restored,
                    reopened,
                    channel="discovery",
                    decision=decision(original=native.reranking_run.intent_sequence),
                ),
            )
            assert (encoder.calls, len(port.requests)) == before
            assert replayed.reranking_run.replay_sequence is not None
            reopened.close()
            report = read_journal(cfg.journal, "reranking")
            changed = report.model_dump()
            changed["header"]["config"]["research"].pop("reranking")
            with pytest.raises(ValidationError):
                JournalReport.model_validate(changed)
            changed = report.model_dump()
            changed["rows"][0]["rerank_reservation"]["request"]["query"] = "changed"
            with pytest.raises(ValidationError):
                JournalReport.model_validate(changed)
            proof = native.reranking_run.model_copy(
                update={"output_sha256": hashlib.sha256(b"foreign").hexdigest()}
            )
            with pytest.raises(ValueError):
                validate_run_evidence(
                    cfg,
                    rows,
                    native.reranking.request,
                    native.reranking.scores,
                    proof,
                    channel="discovery",
                )
            with pytest.raises(ValidationError):
                ResearchRetrievalReport(
                    schema="ghimera.research-retrieval/1",
                    policy=reuse_policy(
                        CorpusEvidenceReader(reader_policy(store, retrieval=hybrid_policy()), store)
                    ),
                    intent="find ports",
                    observations=(),
                    snapshots=(
                        await CorpusEvidenceReader(
                            reader_policy(store, retrieval=hybrid_policy()), store
                        ).read("ports"),
                    ),
                )
        finally:
            ledger.close()
            store.close()

    asyncio.run(operation())


def test_actual_collector_federated_assembly_forwards_run_owner_and_filters_once(tmp_path):
    async def operation():
        store, port, _ = await prepared(tmp_path)
        original = run_config(tmp_path, store)
        discovery = DiscoveryConfig(
            schema="ghimera.discovery/1",
            providers=(
                dict(
                    id="retained",
                    domains=("open_web",),
                    binding=original.search,
                    query_disclosure="planned_query",
                    use_contract="crawl_and_retain",
                    max_calls=4,
                    byte_budget=4000000,
                    call_seconds_budget=30.0,
                    max_response_bytes=1000000,
                    max_results=3,
                    timeout_seconds=10.0,
                    consecutive_failure_limit=2,
                ),
            ),
            target_domains=("open_web",),
            allow_cross_domain_expansion=False,
            mode="ordered_fallback",
            cold_start_fanout=False,
            provider_concurrency=1,
            stagnation_window=1,
            min_new_documents=1,
            min_new_answers=1,
            max_strategy_changes=1,
        )
        raw = GhimeraConfig.from_toml(Path("examples/collector.toml")).model_dump()
        raw.update(
            research=original.research,
            search=None,
            discovery=discovery,
            judge_budget=original.judge_budget,
            journal=original.journal,
            model_work=original.model_work,
        )
        cfg = GhimeraConfig.model_validate(raw)
        try:
            # Real public facade assembles native bound adapters. No source or served
            # model request is sent: only its resulting corpus discovery is exercised.
            collector = Collector(cfg, discovery_corpora={"retained": store})
            budget, ledger = opened(cfg)
            try:
                history = SearchHistory(collector._research._search, budget, ledger)
                observed = await history.discover_many(request().query)
                assert len(observed) == 1 and observed[0].response.hits
                assert observed[0].provider == "binding:retained"
                assert budget.search_calls == budget.fetches == budget.judge_calls == 1
                assert len(port.requests) == 1
                assert [row.event for row in ledger.snapshot()] == [
                    "model_intent",
                    "model_ack",
                    "fetch",
                ]
                wire = CorpusSearchWire.model_validate_json(observed[0].response.raw)
                validate_run_evidence(
                    cfg,
                    ledger.snapshot(),
                    wire.query.reranking.request,
                    wire.query.reranking.scores,
                    wire.query.reranking_run,
                    channel="discovery",
                    before_sequence=observed[0].sequence,
                )
            finally:
                ledger.close()
        finally:
            store.close()

    asyncio.run(operation())


def test_concurrent_owned_calls_are_not_orphan_unknown_and_share_reservations(tmp_path):
    class ConcurrentPort(ScoresPort):
        def __init__(self, policy):
            super().__init__(policy)
            self.entered = 0
            self.both = asyncio.Event()

        async def score(self, native):
            self.entered += 1
            if self.entered == 2:
                self.both.set()
            async with asyncio.timeout(3.0):
                await self.both.wait()
            return await super().score(native)

    async def operation():
        store, port, _ = await prepared(tmp_path, port_type=ConcurrentPort)
        cfg = run_config(tmp_path, store)
        budget, ledger = opened(cfg)
        try:
            returned = await asyncio.gather(
                *(
                    store.search(
                        text,
                        top_k=2,
                        retrieval=hybrid_policy(),
                        rerank_invoker=RunBoundReranker(
                            budget, ledger, channel="discovery", decision=decision(key)
                        ),
                    )
                    for key, text in (("first", "ports"), ("second", "report"))
                )
            )
            assert (
                len(returned)
                == len(port.requests)
                == budget.judge_calls
                == budget.rerank_calls
                == 2
            )
            assert not budget.active_rerank_sequences and not uncertain_model_sequences(
                ledger.snapshot()
            )
            assert validate_rerank_rows(cfg, ledger.snapshot()) == (
                2,
                budget.rerank_pairs,
                budget.rerank_chars,
            )
        finally:
            ledger.close()
            store.close()

    asyncio.run(operation())
