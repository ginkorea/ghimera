"""Real corpus, journal and graph persistence; model replies are protocol fixtures."""

import asyncio

import pytest
from pydantic import ValidationError

from ghimera import CorpusEvidenceReader, DeliveryOutbox, DirectoryDeliverySink, GhimeraConfig
from ghimera.continuation import CheckpointStore, ResearchSuspended
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph_planning import validate_context
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.models import Harvest
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, ResearchResult
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_delivery_outbox import destination as delivery_destination
from tests.test_delivery_outbox import policy as delivery_policy
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus, endpoint, harvest
from tests.test_graph_planning import planning_config
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
)
from tests.test_pdf_transcription_corpus import reviewed_harvest
from tests.test_pdf_transcription_graph import OrganizationWire
from tests.test_research_reuse import reuse_policy
from tests.test_semantic_graph import SemanticWire
from tests.test_semantic_verification import ReviewWire, reviewed_config

__all__ = ["endpoint"]


class GraphPlanner(PlannerFixture):
    def __init__(self):
        self.requests = []

    async def plan(self, request):
        self.requests.append(request)
        assert request.graph_context.entities
        return await super().plan(request)


def configured(tmp_path, reader, *, resume=False):
    raw = planning_config(tmp_path).model_dump()
    raw["research"]["retained_evidence"] = reuse_policy(reader).model_dump()
    raw["journal"] = dict(
        schema="chimera.run-journal-config/1",
        directory=tmp_path / "journal",
        max_record_bytes=2_000_000,
        max_journal_bytes=10_000_000,
        max_summary_bytes=2_000_000,
        max_records=2000,
    )
    if resume:
        raw["continuation"] = dict(
            schema="ghimera.continuation/1",
            max_checkpoint_bytes=8_000_000,
            clock_policy="include_downtime",
        )
    return GhimeraConfig.model_validate(raw)


def assembled(cfg, reader, *, pdf=False):
    wire = OrganizationWire(cfg.models.analyst) if pdf else SemanticWire(cfg.models.analyst)
    route, search, planner = FakeRoute(), SearchFixture(), GraphPlanner()
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=wire),
    )
    return (
        ResearchLoop(
            config=cfg,
            collector=collector,
            search=search,
            planner=planner,
            analyst=AnalystFixture(),
            reviewer=ReviewerFixture(),
            retained_reader=reader,
        ),
        wire,
        route,
        search,
        planner,
    )


@pytest.mark.parametrize("reading", ["native", "reviewed_pdf"])
def test_retained_original_feeds_the_same_graph_planner_and_durable_delivery(
    tmp_path, endpoint, reading
):
    async def operation():
        pdf = reading == "reviewed_pdf"
        material = (
            await reviewed_harvest(tmp_path)
            if pdf
            else await harvest(("zh", "港口：甲委員會隸屬乙委員會。"))
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
            cfg = configured(tmp_path, reader)
            loop, wire, route, search, planner = assembled(cfg, reader, pdf=pdf)
            result = await loop.run(
                ResearchRequest(intent="unrelated" if pdf else "find ports"),
                run_id="retained-graph",
            )
            assert result.status == "answered" and not route.requests and not search.requests
            assert result.harvest.schema_version == "chimera.harvest/2"
            assert result.harvest.documents == result.harvest.source_documents == ()
            assert result.harvest.graph_source_documents == material.source_documents
            assert result.harvest.receipt.fetches == result.harvest.receipt.bytes_read == 0
            assert result.harvest.receipt.encoding_calls == 0  # Current queries are in retrieval.
            assert result.harvest.receipt.judge_calls == 5  # One new semantic call + research.
            assert not any(
                row.local_input or row.transcription_call for row in result.harvest.ledger
            )
            semantic = tuple(row for row in result.harvest.ledger if row.semantic_window)
            assert len(semantic) == 1 and semantic[0].model_call.task == "semantic_extract"
            (original,) = result.harvest.retained_sources
            assert original.document == material.documents[0]
            assert original.origin.source_age == "unknown"
            assert original == result.retrieval.graph_originals[0]
            (graph_document,) = (
                node for node in result.harvest.graph.nodes if node.role == "document"
            )
            assert graph_document.retained_source == original.origin
            assert graph_document.local_input == original.document.local_input
            assert graph_document.revision == original.revision
            validate_context(cfg, planner.requests[0].graph_context, result.evidence_documents)
            if pdf:
                assert (
                    graph_document.pdf_reading
                    == material.documents[0].extracted.pdf_transcription.graph_reading()
                )
                assert (
                    semantic[0].semantic_window.entities[0].evidence.basis
                    == "reviewed_pdf_transcription"
                )
                assert original.document.extracted.pdf_transcription.pages[0].calls
            else:
                assert len(wire.requests) == 1  # A second query cannot re-extract the original.
                assert any(edge.rule == "reports_to" for edge in result.harvest.graph.edges)
            report = read_journal(cfg.journal, "retained-graph")
            assert report.state == "complete"
            assert report.summary.schema_version == "chimera.run-journal-summary/2"
            assert report.summary.documents == ()
            assert report.summary.retained_documents[0].origin == original.origin
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
        finally:
            store.close()
        bound = delivery_policy(tmp_path / "outbox", max_item_bytes=8_000_000)
        pending = await DeliveryOutbox(bound, create=True).enqueue(result)
        reopened = DeliveryOutbox(bound)
        assert await reopened.result(pending.delivery_id) == result
        sink = DirectoryDeliverySink(
            delivery_destination(tmp_path / "destination", max_item_bytes=8_000_000), create=True
        )
        (done,) = await reopened.dispatch(sink)
        assert done.status == "acknowledged"
        assert await sink.result(done.delivery_id) == result
        assert not (await reopened.prune_acknowledged(done.delivery_id, sink)).payload_retained
        assert await sink.result(done.delivery_id) == result
        return result

    result = asyncio.run(operation())
    for update in ({"schema": "chimera.harvest/1"}, {"retained_sources": ()}):
        with pytest.raises(ValidationError):
            Harvest.model_validate(result.harvest.model_dump() | update)
    changed = result.model_dump()
    changed["harvest"]["retained_sources"][0]["origin"]["bundle_sha256"] = "a" * 64
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(changed)
    changed = result.model_dump()
    changed["harvest"]["retained_sources"][0]["document"]["verdict"]["reason"] = "tampered original"
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(changed)


def test_checkpoint_restores_graph_capsules_without_query_or_semantic_replay(tmp_path, endpoint):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口：甲委員會隸屬乙委員會。")))
            reader = CorpusEvidenceReader(reader_policy(store), store)
            cfg = configured(tmp_path, reader, resume=True)
            loop, first_wire, _, _, _ = assembled(cfg, reader)
            with pytest.raises(ResearchSuspended) as stopped:
                await loop.run(
                    ResearchRequest(intent="find ports"),
                    run_id="resume-graph",
                    suspend_after_rounds=1,
                )
            saved = CheckpointStore(cfg, "resume-graph").read(stopped.value.receipt.sha256)
            assert saved.progress.harvest.retained_sources and len(first_wire.requests) == 1
            before = len(endpoint[1])
            resumed, wire, route, search, _ = assembled(cfg, reader)
            result = await resumed.resume(
                "resume-graph", checkpoint_sha256=stopped.value.receipt.sha256
            )
            assert (
                result.status == "answered"
                and not wire.requests
                and not route.requests
                and not search.requests
            )
            assert len(endpoint[1]) == before
            assert result.harvest.retained_sources == saved.progress.harvest.retained_sources
            assert result.harvest.graph.nodes == saved.progress.harvest.graph.nodes
            assert (
                result.harvest.receipt.judge_calls == saved.progress.harvest.receipt.judge_calls + 2
            )
        finally:
            store.close()

    asyncio.run(operation())


def test_same_raw_source_with_two_readings_never_overwrites_graph_window_identity(
    tmp_path, endpoint
):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            first = await harvest(("zh", "港口：甲委員會隸屬乙委員會。"))
            second = await harvest(("zh", "港口：另一份甲委員會隸屬乙委員會。"))
            assert first.documents[0].raw == second.documents[0].raw
            await store.append(first)
            await store.append(second)
            reader = CorpusEvidenceReader(reader_policy(store), store)
            cfg = configured(tmp_path, reader)
            loop, wire, _, _, planner = assembled(cfg, reader)
            result = await loop.run(ResearchRequest(intent="find ports"), run_id="two-readings")
            assert result.status == "answered" and len(wire.requests) == 2
            assert len(result.harvest.retained_sources) == 2
            windows = tuple(
                row.semantic_window for row in result.harvest.ledger if row.semantic_window
            )
            assert len({window.graph_document_id for window in windows}) == 2
            assert len({window.text_sha256 for window in windows}) == 2
            assert len(planner.requests[0].graph_context.entities) == 4
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("defect", [None, "digest"])
def test_retained_graph_uses_current_independent_review_and_preserves_refusals(
    tmp_path, endpoint, defect
):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口：甲委員會隸屬乙委員會。")))
            reader = CorpusEvidenceReader(reader_policy(store), store)
            raw = configured(tmp_path, reader).model_dump()
            reviewed = reviewed_config(tmp_path)
            raw.update(semantics=reviewed.semantics, models=reviewed.models)
            raw["research"]["graph_context"].update(schema="ghimera.graph-planning/4", max_gaps=4)
            cfg = GhimeraConfig.model_validate(raw)
            extractor, reviewer = (
                SemanticWire(cfg.models.analyst),
                ReviewWire(cfg.models.reviewer, defect=defect),
            )
            collector = GoalLoop(
                config=cfg,
                fetcher=FetchLadder((FakeRoute(),)),
                extractor=FakeExtractor(),
                scorer=KeywordScorer(),
                judge=FakeJudge(),
                semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=extractor),
                semantic_reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=reviewer),
            )
            loop = ResearchLoop(
                config=cfg,
                collector=collector,
                search=SearchFixture(),
                planner=GraphPlanner(),
                analyst=AnalystFixture(),
                reviewer=ReviewerFixture(),
                retained_reader=reader,
            )
            result = await loop.run(ResearchRequest(intent="find ports"), run_id="reviewed-graph")
            assert len(extractor.requests) == len(reviewer.requests) == 1
            assert result.harvest.receipt.fetches == 0
            reviews = tuple(row for row in result.harvest.ledger if row.event == "semantic_review")
            assert len(reviews) == 1 and reviews[0].model_call.service == cfg.models.reviewer
            if defect:
                assert result.status == "failed"
                assert result.harvest.receipt.judge_calls == 2
                assert reviews[0].refusal is not None
                (failed,) = (
                    row.semantic_refusal for row in result.harvest.ledger if row.semantic_refusal
                )
                assert failed.phase == "review" and not failed.continued
                assert not any(edge.claim_status for edge in result.harvest.graph.edges)
                forged = result.model_dump()
                semantic_row = next(
                    row for row in forged["harvest"]["ledger"] if row.get("semantic_refusal")
                )
                semantic_row["semantic_refusal"]["continued"] = True
                with pytest.raises(ValidationError):
                    ResearchResult.model_validate(forged)
            else:
                assert result.status == "answered" and result.harvest.receipt.judge_calls == 6
                (window,) = (
                    row.semantic_window for row in result.harvest.ledger if row.semantic_window
                )
                assert window.review.model_call == reviews[0].model_call
                assert any(edge.rule == "reports_to" for edge in result.harvest.graph.edges)
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("window_chars", [1000, 18])
def test_retained_semantics_do_not_escape_the_shared_judge_budget(tmp_path, endpoint, window_chars):
    async def operation():
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口：甲委員會隸屬乙委員會。" * 2)))
            reader = CorpusEvidenceReader(reader_policy(store), store)
            raw = configured(tmp_path, reader).model_dump() | {"judge_budget": 1}
            raw["semantics"]["window_chars"] = window_chars
            cfg = GhimeraConfig.model_validate(raw)
            loop, wire, route, search, planner = assembled(cfg, reader)
            result = await loop.run(ResearchRequest(intent="find ports"), run_id="bounded-graph")
            assert (
                result.status != "answered"
                and result.harvest.receipt.stop_reason == "budget_exhausted"
            )
            assert result.harvest.receipt.judge_calls == 1 and len(wire.requests) == 1
            assert not planner.requests and not route.requests and not search.requests
            assert len(result.harvest.retained_sources) == 1
            if window_chars == 18:
                (failure,) = (row for row in result.harvest.ledger if row.retained_failure)
                assert failure.retained_failure == result.harvest.retained_sources[0].origin
                forged = result.model_dump()
                refusal = next(
                    row for row in forged["harvest"]["ledger"] if row.get("retained_failure")
                )
                refusal["retained_failure"]["bundle_sha256"] = "0" * 64
                with pytest.raises(ValidationError):
                    ResearchResult.model_validate(forged)
                forged = result.model_dump()
                forged["harvest"]["ledger"] = [
                    row for row in forged["harvest"]["ledger"] if not row.get("retained_failure")
                ]
                for sequence, row in enumerate(forged["harvest"]["ledger"]):
                    row["sequence"] = sequence
                with pytest.raises(ValidationError):
                    ResearchResult.model_validate(forged)
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
        finally:
            store.close()

    asyncio.run(operation())
