"""Real SQLite/FAISS and local model wires, not a claim of research accuracy."""

import asyncio
import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import CorpusEvidenceReader, ResearchReuseConfig
from ghimera.continuation import CheckpointStore, ResearchSuspended
from ghimera.corpus import EvidenceCorpus
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.embedding import SelfHostedEncoder
from ghimera.evidence_context import ContextSelector
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse, ModelWireCancelled
from ghimera.refusals import GhimeraRefused
from ghimera.research import CitationValidator, ResearchLoop, citation_for
from ghimera.research_reuse import ResearchRetrievalReport, RetainedResearchSession
from ghimera.research_types import EvidenceRequest, ResearchRequest, ResearchResult
from tests.test_c0 import config
from tests.test_collector import assembled, search_endpoint, source_site
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_delivery_outbox import destination as delivery_destination
from tests.test_delivery_outbox import policy as delivery_policy
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus, endpoint, harvest
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
    policy,
)
from tests.test_pdf_transcription_corpus import reviewed_harvest
from tests.test_served_models import endpoint as model_endpoint
from tests.test_served_models import service

__all__ = ["endpoint", "model_endpoint", "search_endpoint", "source_site"]


def reuse_policy(reader, **updates):
    return ResearchReuseConfig.model_validate(
        dict(
            schema="ghimera.research-reuse/1",
            reader=reader.policy,
            max_queries=8,
            max_input_chars=1000,
            max_snapshot_bytes=16_000_000,
            max_source_documents=10,
            query_mode="intent_and_planned_queries",
            assess_before_discovery=True,
            source_age_policy="explicit_unknown",
        )
        | updates
    )


def assemble(cfg, reader, *, analyst=None, planner=None, reviewer=None):
    route, search = FakeRoute(), SearchFixture()
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    loop = ResearchLoop(
        config=cfg,
        collector=collector,
        search=search,
        planner=planner or PlannerFixture(),
        analyst=analyst or AnalystFixture(),
        reviewer=reviewer or ReviewerFixture(),
        retained_reader=reader,
    )
    return loop, route, search


def configured(reader, **updates):
    return config(
        research=policy(retained_evidence=reuse_policy(reader), max_model_input_chars=30000),
        page_budget=30,
        **updates,
    )


def test_intent_finishes_from_retained_native_originals_without_refetch(tmp_path, endpoint):
    async def operation():
        material = await harvest(("zh", "港口基礎設施研究"))
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(material)
            before = len(endpoint[1])
            reader = CorpusEvidenceReader(reader_policy(store), store)
            loop, route, search = assemble(configured(reader), reader)
            result = await loop.run(ResearchRequest(intent="find ports"))
            assert result.status == "answered" and result.search_calls == 0
            assert not route.requests and not search.requests
            assert result.harvest.documents == () and result.harvest.receipt.fetches == 0
            assert result.harvest.receipt.encoding_calls == 0
            assert result.harvest.receipt.judge_calls == 4  # Plan, reassess, answer, review.
            assert result.evidence_documents == material.source_documents
            assert result.retrieval.intent == "find ports"
            assert len(endpoint[1]) == before + len(result.retrieval.observations) == before + 2
            assert all(item.outcome == "success" for item in result.retrieval.observations)
            assert all(item.source_age == "unknown" for item in result.retrieval.notices)
            assert result.rounds[0].collection_stop == "retained_evidence"
            assert result.answer.claims[0].citations[0].matches(material.documents[0])
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
            assert store.identity == reader.policy.corpus_id  # Borrowed, not closed.
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("mode", ["empty", "insufficient", "refused", "no_early_answer"])
def test_retrieval_cannot_replace_discovery_when_it_does_not_answer(tmp_path, endpoint, mode):
    class FreshEvidenceAnalyst(AnalystFixture):
        async def assess(self, request):
            # Simulates a current-time question: old acceptance is insufficient.
            retained = {notice.document_sha256 for notice in request.retained_sources}
            from ghimera.corpus_types import BoundCorpusDocument

            fresh = tuple(
                doc
                for doc in request.documents
                if BoundCorpusDocument(doc).identity not in retained
            )
            packet = EvidenceRequest(
                intent=request.intent, questions=request.questions, documents=fresh
            )
            return await super().assess(packet)

    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            if mode != "empty":
                await store.append(await harvest(("zh", "港口研究")))
            if mode == "refused":
                endpoint[2]["failure"] = "wrong_model"
            reader = CorpusEvidenceReader(reader_policy(store), store)
            bound = reuse_policy(reader, assess_before_discovery=mode != "no_early_answer")
            cfg = config(research=policy(retained_evidence=bound), page_budget=30)
            loop, route, search = assemble(cfg, reader, analyst=FreshEvidenceAnalyst())
            result = await loop.run(ResearchRequest(intent="find ports"))
            assert result.status == "answered"
            assert len(route.requests) == len(search.requests) == 1
            assert result.harvest.documents
            if mode == "refused":
                assert not result.retrieval.snapshots
                assert all(item.outcome == "refused" for item in result.retrieval.observations)
                assert all(
                    item.encoding_call.outcome == "refused"
                    for item in result.retrieval.observations
                )
        finally:
            store.close()

    asyncio.run(operation())


def test_zero_query_allowance_remaining_still_allows_source_work(tmp_path, endpoint):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            reader = CorpusEvidenceReader(reader_policy(store), store)
            cfg = config(research=policy(retained_evidence=reuse_policy(reader, max_queries=1)))
            loop, route, search = assemble(cfg, reader)
            result = await loop.run(ResearchRequest(intent="find ports"))
            assert result.status == "answered" and len(result.retrieval.observations) == 1
            assert len(route.requests) == len(search.requests) == 1
        finally:
            store.close()

    asyncio.run(operation())


def test_explicit_seed_is_not_skipped_when_cached_evidence_covers_question(tmp_path, endpoint):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            reader = CorpusEvidenceReader(reader_policy(store), store)
            loop, route, _ = assemble(configured(reader), reader)
            result = await loop.run(
                ResearchRequest(intent="find ports", seeds=("https://example.org/one",))
            )
            assert result.status == "answered" and len(route.requests) == 1
        finally:
            store.close()

    asyncio.run(operation())


def test_resume_reuses_admitted_capsule_without_query_or_assessment_replay(tmp_path, endpoint):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            original = await harvest(("zh", "港口研究"))
            await store.append(original)
            reader = CorpusEvidenceReader(reader_policy(store), store)
            cfg = configured(
                reader,
                journal=dict(
                    schema="chimera.run-journal-config/1",
                    directory=tmp_path / "journal",
                    max_record_bytes=2_000_000,
                    max_journal_bytes=10_000_000,
                    max_summary_bytes=2_000_000,
                    max_records=2000,
                ),
                continuation=dict(
                    schema="ghimera.continuation/1",
                    max_checkpoint_bytes=8_000_000,
                    clock_policy="include_downtime",
                ),
            )
            first, _, _ = assemble(cfg, reader)
            with pytest.raises(ResearchSuspended) as stopped:
                await first.run(
                    ResearchRequest(intent="find ports"), run_id="reuse", suspend_after_rounds=1
                )
            pin = stopped.value.receipt.sha256
            saved = CheckpointStore(cfg, "reuse").read(pin)
            assert saved.next_action == "answer"
            # The new store generation contains a changed reading of identical raw bytes.
            changed = await harvest(("zh", "港口研究 changed reading"))
            assert changed.documents[0].raw == original.documents[0].raw
            await store.append(changed)
            query_calls = len(endpoint[1])
            resumed, route, search = assemble(cfg, reader)
            result = await resumed.resume("reuse", checkpoint_sha256=pin)
            assert result.status == "answered"
            assert len(endpoint[1]) == query_calls
            assert result.retrieval == saved.progress.retrieval
            assert result.evidence_documents == original.source_documents
            assert not route.requests and not search.requests
            assert (
                result.harvest.receipt.judge_calls == saved.progress.harvest.receipt.judge_calls + 2
            )
        finally:
            store.close()

    asyncio.run(operation())


def test_reviewed_pdf_basis_survives_answer_without_old_model_spend(tmp_path, endpoint):
    async def operation():
        material = await reviewed_harvest(tmp_path)
        store = corpus(
            corpus_config(tmp_path, endpoint[0], max_document_bytes=2_000_000), create=True
        )
        try:
            await store.append(material)
            reader = CorpusEvidenceReader(
                reader_policy(store, max_original_bytes=2_000_000, max_response_bytes=4_000_000),
                store,
            )
            loop, route, search = assemble(configured(reader), reader)
            result = await loop.run(ResearchRequest(intent="unrelated"))
            assert result.status == "answered" and not route.requests and not search.requests
            citation = result.answer.claims[0].citations[0]
            assert citation.basis == "reviewed_pdf_transcription" and citation.page_indices == (0,)
            assert citation.matches(material.documents[0])
            assert result.harvest.receipt.judge_calls == 4
            assert not any(row.transcription_call for row in result.harvest.ledger)
            assert result.evidence_documents[0].local_input == material.documents[0].local_input
            assert result.evidence_documents[0].extracted.pdf_transcription == (
                material.documents[0].extracted.pdf_transcription
            )
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("reading", ["native", "reviewed_pdf"])
def test_retained_answer_survives_delivery_restart_readback_and_pruning(
    tmp_path, endpoint, reading
):
    from ghimera import DeliveryOutbox, DirectoryDeliverySink

    async def operation():
        material = (
            await reviewed_harvest(tmp_path)
            if reading == "reviewed_pdf"
            else await harvest(("zh", "港口研究"))
        )
        store = corpus(
            corpus_config(tmp_path, endpoint[0], max_document_bytes=2_000_000), create=True
        )
        try:
            await store.append(material)
            reader = CorpusEvidenceReader(
                reader_policy(store, max_original_bytes=2_000_000, max_response_bytes=4_000_000),
                store,
            )
            loop, route, search = assemble(configured(reader), reader)
            # The two-dimensional protocol fixture encodes this PDF's native
            # Chinese company text on its second axis, unlike its ports strings.
            intent = "unrelated" if reading == "reviewed_pdf" else "find ports"
            result = await loop.run(ResearchRequest(intent=intent))
            assert result.status == "answered" and not route.requests and not search.requests
            calls = len(endpoint[1])
        finally:
            store.close()

        # Delivery owns the complete result, not a pointer into a still-open corpus.
        bound = delivery_policy(tmp_path / "outbox", max_item_bytes=8_000_000)
        outbox = DeliveryOutbox(bound, create=True)
        pending = await outbox.enqueue(result)
        reopened = DeliveryOutbox(bound)
        assert await reopened.result(pending.delivery_id) == result
        sink = DirectoryDeliverySink(
            delivery_destination(tmp_path / "destination", max_item_bytes=8_000_000),
            create=True,
        )
        (done,) = await reopened.dispatch(sink)
        assert done.status == "acknowledged"
        delivered = await sink.result(done.delivery_id)
        assert isinstance(delivered, ResearchResult) and delivered == result
        assert delivered.retrieval == result.retrieval
        assert delivered.answer.claims[0].citations[0].matches(material.documents[0])
        assert delivered.evidence_documents == material.source_documents
        pruned = await reopened.prune_acknowledged(done.delivery_id, sink)
        assert not pruned.payload_retained
        assert await sink.result(done.delivery_id) == result
        assert len(endpoint[1]) == calls and not route.requests and not search.requests

    asyncio.run(operation())


def test_same_bytes_with_two_readings_preserve_both_citations_and_context(tmp_path):
    async def operation():
        first = (await harvest(("en", "ports original reading"))).documents[0]
        second = (await harvest(("en", "ports different reading"))).documents[0]
        assert first.sha256 == second.sha256 and first.url == second.url
        citations = tuple(citation_for(doc, 0, len(doc.extracted.text)) for doc in (first, second))
        CitationValidator((first, second)).validate(citations)
        context = ContextSelector(service(1).context).build(
            "ports", (first, second), required=citations
        )
        assert tuple(window.citation for window in context.windows) == citations
        assert len(context.documents) == 2 and not context.omitted_documents
        assert len({citation.text_sha256 for citation in citations}) == 2

    asyncio.run(operation())


def test_fresh_planning_graph_is_not_shadowed_by_retained_reading(tmp_path):
    from ghimera.graph_planning import build_context, validate_context
    from tests.test_graph_planning import observed, planning_config
    from tests.test_semantic_graph import document

    cfg = planning_config(tmp_path)
    fresh = document()
    old = fresh.model_copy(
        update={
            "extracted": fresh.extracted.model_copy(update={"text": "a different older reading"})
        }
    )
    _, rows, _ = observed(cfg, (fresh,))
    graph = build_context(cfg, rows)
    assert graph.entities
    validate_context(cfg, graph, (fresh, old))
    validate_context(cfg, graph, (old, fresh))
    with pytest.raises(GhimeraRefused, match="research_contract"):
        validate_context(cfg, graph, (old,))


def test_retained_graph_planning_pins_combined_prompt_without_changing_graph_identity(
    tmp_path, endpoint
):
    from ghimera.config import GhimeraConfig
    from ghimera.corpus_types import BoundCorpusDocument
    from ghimera.graph_planning import build_context, validate_rows
    from ghimera.models import LedgerRow
    from ghimera.research_reuse import RetainedSourceNotice
    from ghimera.research_types import PlanningRequest
    from tests.test_graph_planning import PlanningWire, document, observed, planning_config

    store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
    try:
        reader = CorpusEvidenceReader(reader_policy(store), store)
        base = planning_config(tmp_path)
        cfg = GhimeraConfig.model_validate(
            base.model_dump()
            | {"research": base.research.model_dump() | {"retained_evidence": reuse_policy(reader)}}
        )
        doc = document()
        _, rows, _ = observed(cfg, (doc,))
        context = build_context(cfg, rows)
        notice = RetainedSourceNotice(
            document_sha256=BoundCorpusDocument(doc).identity,
            source_sha256=doc.sha256,
            text_sha256=hashlib.sha256(doc.extracted.text.encode()).hexdigest(),
            source_url=doc.url,
        )
        request = PlanningRequest(
            intent="map the organization",
            questions=(),
            documents=(doc,),
            assessment=None,
            max_questions=4,
            max_queries=2,
            max_query_chars=200,
            graph_context=context,
            retained_sources=(notice,),
        )
        wire = PlanningWire(cfg.models.planner)
        model = SelfHostedModel(cfg, cfg.models.planner, http=wire)
        plan = asyncio.run(model.plan(request))
        assert context.prompt_revision == "ghimera-graph-planning/1"
        assert plan.model_call.prompt_revision == ("ghimera-graph-planning/1+retained-snapshots/1")
        assert wire.requests[0]["retained_sources"][0]["source_age"] == "unknown"
        row = LedgerRow(
            sequence=rows[-1].sequence + 1,
            event="plan",
            model=model.model,
            model_call=plan.model_call,
            reason="model_response:" + plan.content_digest(),
            planning_graph=context,
        )
        validate_rows(cfg, rows + (row,))
        wrong = row.model_copy(
            update={
                "model_call": plan.model_call.model_copy(
                    update={"prompt_revision": context.prompt_revision}
                )
            }
        )
        with pytest.raises(ValueError, match="configured planner"):
            validate_rows(cfg, rows + (wrong,))
    finally:
        store.close()


def test_query_cancel_preserves_actual_encoder_observation_and_can_checkpoint(tmp_path, endpoint):
    class CancelledWire:
        def __init__(self, cfg):
            self.config = cfg

        async def post(self, body):
            raise ModelWireCancelled(ModelHttpResponse(200, b"partial-vector", "application/json"))

    async def operation():
        bound = corpus_config(tmp_path, endpoint[0])
        store = EvidenceCorpus(
            bound,
            encoder=SelfHostedEncoder(bound.encoder),
            query_encoder=SelfHostedEncoder(
                bound.query_encoder, http=CancelledWire(bound.query_encoder)
            ),
            create=True,
        )
        try:
            reader = CorpusEvidenceReader(reader_policy(store), store)
            state = RetainedResearchSession(reuse_policy(reader), reader, "ports")
            with pytest.raises(asyncio.CancelledError):
                await state.query("ports", remaining_seconds=10)
            report = state.report
            assert not report.snapshots and len(report.observations) == 1
            call = report.observations[0].encoding_call
            assert report.observations[0].outcome == call.outcome == "cancelled"
            assert call.response_bytes == len(b"partial-vector")
            assert ResearchRetrievalReport.model_validate_json(report.model_dump_json()) == report
        finally:
            store.close()

    asyncio.run(operation())


def test_failed_snapshot_delivery_keeps_successful_query_spend(tmp_path, endpoint):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            reader = CorpusEvidenceReader(reader_policy(store, max_response_bytes=1), store)
            state = RetainedResearchSession(reuse_policy(reader), reader, "ports")
            with pytest.raises(GhimeraRefused):
                await state.query("ports", remaining_seconds=10)
            assert state.report.observations[0].outcome == "refused"
            assert state.report.observations[0].encoding_call.outcome == "success"
            assert not state.report.snapshots and len(endpoint[1]) == 2
        finally:
            store.close()

    asyncio.run(operation())


def test_actual_model_wire_receives_unknown_age_and_returns_new_call_evidence(
    tmp_path, endpoint, model_endpoint
):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            material = await harvest(("en", "ports native research"))
            await store.append(material)
            reader = CorpusEvidenceReader(reader_policy(store), store)
            cfg = configured(reader)
            model = SelfHostedModel(cfg, service(model_endpoint[0], max_input_chars=30000))
            reviewer = SelfHostedModel(
                cfg, service(model_endpoint[0], model_id="reviewer", max_input_chars=30000)
            )
            loop, route, search = assemble(
                cfg, reader, analyst=model, planner=model, reviewer=reviewer
            )
            result = await loop.run(ResearchRequest(intent="find ports"))
            assert result.status == "answered" and not route.requests and not search.requests
            assert len(model_endpoint[1]) == result.harvest.receipt.judge_calls == 4
            calls = [row.model_call for row in result.harvest.ledger if row.model_call]
            assert sum(call.total_tokens for call in calls) == 80
            assert all(
                call.prompt_revision == "ghimera-retained-research-prompts/1" for call in calls
            )
            for _, body, auth in model_endpoint[1]:
                assert auth is None
                assert "UNKNOWN age" in body["messages"][0]["content"]
                packet = json.loads(body["messages"][1]["content"])
                notice = packet["retained_sources"][0]
                assert notice["source_age"] == "unknown"
                assert (
                    notice["text_sha256"]
                    == hashlib.sha256(material.documents[0].extracted.text.encode()).hexdigest()
                )
        finally:
            store.close()

    asyncio.run(operation())


def test_configured_collector_injects_reader_into_its_normal_research_loop(
    tmp_path, endpoint, model_endpoint, search_endpoint, source_site
):
    from ghimera import Collector, GhimeraConfig
    from tests.test_http_fetch import ResolverFixture

    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("en", "ports retained research")))
            reader = CorpusEvidenceReader(reader_policy(store), store)
            cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, endpoint)
            cfg = GhimeraConfig.model_validate(
                cfg.model_dump()
                | {
                    "research": cfg.research.model_dump()
                    | {"retained_evidence": reuse_policy(reader)}
                }
            )
            collector = Collector(cfg, retained_reader=reader, source_resolver=ResolverFixture())
            result = await collector.run("find ports")
            assert result.status == "answered" and result.retrieval.documents
            assert not source_site[1] and not search_endpoint[1]
            assert len(model_endpoint[1]) == result.harvest.receipt.judge_calls == 4
            assert result.harvest.receipt.fetches == 0
            assert result.harvest.receipt.encoding_calls == 0  # No source-scoring work occurred.
            assert len(result.retrieval.observations) == 2
            assert collector.config == result.harvest.receipt.effective_config
        finally:
            store.close()

    asyncio.run(operation())


def test_rejected_retained_answer_seeks_new_sources_instead_of_repeating_cache(tmp_path, endpoint):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            reader = CorpusEvidenceReader(reader_policy(store), store)
            loop, route, search = assemble(
                configured(reader), reader, reviewer=ReviewerFixture(supported=False)
            )
            result = await loop.run(ResearchRequest(intent="find ports"))
            assert result.status == "partial" and result.answer is None
            assert route.requests and search.requests
            assert result.rounds[0].collection_stop == "retained_evidence"
            assert any(round_.discovered_urls for round_ in result.rounds[1:])
        finally:
            store.close()

    asyncio.run(operation())


def test_report_and_results_refuse_changed_binding_or_unsupported_citations(tmp_path, endpoint):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            reader = CorpusEvidenceReader(reader_policy(store), store)
            loop, _, _ = assemble(configured(reader), reader)
            result = await loop.run(ResearchRequest(intent="find ports"))
            for mutation in ("intent", "query", "snapshot", "assessment", "answer", "schema"):
                wire = json.loads(result.model_dump_json())
                if mutation == "intent":
                    wire["retrieval"]["intent"] = "another intent"
                elif mutation == "query":
                    wire["retrieval"]["observations"][0]["encoding_call"]["input_chars"] += 1
                elif mutation == "snapshot":
                    wire["retrieval"]["snapshots"] = []
                elif mutation == "assessment":
                    wire["rounds"][0]["assessment"]["coverage"][0]["citations"][0]["quote"] = "fake"
                elif mutation == "answer":
                    wire["answer"]["claims"][0]["citations"][0]["quote"] = "fake"
                else:
                    wire["schema"] = "chimera.research-result/2"
                with pytest.raises(ValidationError):
                    ResearchResult.model_validate(wire)
            notice = result.retrieval.notices[0].model_copy(update={"source_sha256": "0" * 64})
            with pytest.raises(ValidationError, match="exact original"):
                EvidenceRequest(
                    intent="ports",
                    questions=result.questions,
                    documents=result.evidence_documents,
                    retained_sources=(notice,),
                )
        finally:
            store.close()

    asyncio.run(operation())


def test_reader_policy_must_be_explicit_and_example_is_non_active(tmp_path, endpoint):
    store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
    try:
        reader = CorpusEvidenceReader(reader_policy(store), store)
        with pytest.raises(ValueError, match="exact configured"):
            assemble(configured(reader), None)
        with pytest.raises(ValueError, match="exact configured"):
            assemble(config(research=policy()), reader)
        raw = tomllib.loads(Path("examples/research-reuse.toml").read_text())
        bound = ResearchReuseConfig.model_validate(raw)
        assert bound.reader.query_encoder.endpoint.startswith("http://127.0.0.1:")
        assert bound.reader.corpus_id == "0" * 32
        assert bound.source_age_policy == "explicit_unknown"
    finally:
        store.close()
