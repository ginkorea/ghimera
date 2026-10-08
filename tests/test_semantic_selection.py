"""Native selection/archive contracts; injected replies do not prove model quality."""

import asyncio
import hashlib
import json
import time

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeJudge
from ghimera.embedding import SelfHostedEncoder
from ghimera.extraction_types import ExtractionEvidence
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.graph_planning import build_context, validate_context
from ghimera.graph_planning_types import PlanningSelectionGap
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.ledger import Ledger
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.model_work import (
    FatalModelWorkFailure,
    ModelInvocation,
    port_input,
    uncertain_model_sequences,
)
from ghimera.models import Document, Extracted, Goal, Harvest, LedgerRow, Receipt, Verdict
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_graph import SemanticStage, validate_rows
from ghimera.semantic_scoring import EmbeddingScorer
from ghimera.semantic_selection import make_selection
from ghimera.semantic_selection_types import SemanticSelectionRef
from ghimera.semantic_types import SemanticProposal
from tests.test_embedding_scoring import intent_policy, service
from tests.test_html_extraction import policy as extraction_policy
from tests.test_intent_research import policy as research_policy
from tests.test_semantic_graph import SemanticWire, configured

GOAL = "organization"
TEXT = "unrelated " * 60 + "甲委員會隸屬乙委員會。" + " remainder" * 30


def selection_config(tmp_path, *, durable=False, research=False, **changes):
    raw = configured(
        tmp_path,
        schema="ghimera.semantics/3",
        prompt_profile="native_span_keys",
        window_chars=80,
        max_windows_per_document=1,
    ).model_dump()
    raw["semantics"]["window_selection"] = dict(
        schema="ghimera.semantic-window-selection/1",
        strategy="intent_ranked",
        max_selected_chars=80,
        padding_chars=30,
    )
    raw["semantics"]["window_selection"].update(changes)
    raw["extraction"] = extraction_policy(tmp_path)
    raw["scoring"] = intent_policy(
        service(8999), window_chars=30, overlap_chars=10, max_windows=100, encoding_call_budget=100
    )
    if research:
        raw["research"] = research_policy(
            require_distinct_reviewer=False,
            max_model_input_chars=30000,
            graph_context=dict(
                schema="ghimera.graph-planning/4",
                entity_roles=["entity"],
                relation_rules=["reports_to"],
                selection="newest_first",
                max_entities=8,
                max_relations=4,
                max_evidence_chars=4000,
                max_context_chars=30000,
                max_gaps=4,
            ),
        )
    if durable:
        raw["journal"] = dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=2000000,
            max_journal_bytes=10000000,
            max_summary_bytes=2000000,
            max_records=2000,
        )
        raw["model_work"] = dict(
            schema="ghimera.model-work/1",
            max_input_bytes=1000000,
            max_unanswered_calls=4,
            uncertain_policy="hold",
            results=dict(
                schema="ghimera.model-results/1",
                max_result_bytes=200000,
                max_total_result_bytes=1000000,
            ),
        )
    return GhimeraConfig.model_validate(raw)


def native_document(cfg, *, text=TEXT, raw_suffix=b"", url="https://example.org/report"):
    raw = text.encode() + raw_suffix
    digest = hashlib.sha256(raw).hexdigest()
    # Controlled parser evidence over unchanged original text, not actual parser accuracy.
    parsed = ExtractionEvidence(
        schema="chimera.extraction-evidence/1",
        source_sha256=digest,
        source_url=url,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        config_digest=cfg.extraction.content_digest(),
        parser_revision="controlled-parser@1",
        encoding="utf-8",
        decode_replacements=0,
        selection="generic",
        profile_id=None,
        locators=(),
        missing_fields=(),
        declared_language="zh",
        language_confidence=1.0,
        language_margin=1.0,
        language_sample_chars=len(text),
        language_hint_disagrees=False,
        raw_markdown_sha256=hashlib.sha256(text.encode()).hexdigest(),
        omitted_links=0,
    )
    return Document(
        url=url,
        raw=raw,
        sha256=digest,
        extracted=Extracted(title="fixture", text=text, language="zh", extraction=parsed),
        verdict=Verdict(
            decision="accept",
            kind="report",
            publisher="fixture",
            language="zh",
            reason="controlled fixture",
        ),
    )


class EncodingWire:
    def __init__(self, bound):
        self.config, self.requests = bound, []

    async def post(self, body):
        packet = json.loads(body)
        self.requests.append(packet)
        vectors = [
            dict(
                object="embedding",
                index=i,
                embedding=[1.0, 0.0] if text == GOAL or "甲委員會" in text else [0.0, 1.0],
            )
            for i, text in enumerate(packet["input"])
        ]
        return ModelHttpResponse(
            200,
            json.dumps(
                dict(
                    object="list",
                    model="fixture-encoder",
                    data=vectors,
                    usage=dict(prompt_tokens=4, total_tokens=4),
                )
            ).encode(),
            "application/json",
        )


def exercise_selection(
    cfg, *, text=TEXT, failed=False, repeat=False, replay=False, reviewer_factory=None
):
    doc, goal = native_document(cfg, text=text), Goal(text=GOAL)
    sink = DirectoryLedgerSink(cfg, "selected", goal, FakeJudge().model) if cfg.journal else None
    ledger, budget = Ledger(sink=sink), RunBudget(cfg, time.monotonic)
    wire = SemanticWire(cfg.models.analyst, wrong_surface=failed)
    model = SelfHostedModel(cfg, cfg.models.analyst, http=wire)
    review_wire = reviewer_factory(cfg.models.reviewer) if reviewer_factory is not None else None
    reviewer = SelfHostedModel(cfg, cfg.models.reviewer, http=review_wire) if review_wire else None
    stage = SemanticStage(cfg, model, reviewer=reviewer)
    encoder_wire = EncodingWire(cfg.scoring.encoder)
    scorer = EmbeddingScorer(cfg.scoring, SelfHostedEncoder(cfg.scoring.encoder, http=encoder_wire))

    async def run():
        graph = ResearchGraph(cfg.graph, "selected", MemoryGraphSink())
        await graph.start(GOAL)
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "controlled-parser@1")
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="extraction",
                url=doc.url,
                extraction=doc.extracted.extraction,
                reason="controlled_native_parser",
            )
        )
        await scorer.rank(goal, doc.extracted, budget, ledger)
        if failed:
            with pytest.raises(GhimeraRefused):
                await stage.extract(GOAL, doc, identity, graph, budget, ledger)
        else:
            await stage.extract(GOAL, doc, identity, graph, budget, ledger)
        if repeat:
            count, calls = len(ledger.snapshot()), budget.judge_calls
            with pytest.raises(GhimeraRefused):
                await stage.extract(GOAL, doc, identity, graph, budget, ledger)
            assert (len(ledger.snapshot()), budget.judge_calls) == (count, calls)
        if replay:
            attempt_row = next(row for row in ledger.snapshot() if row.semantic_window)
            observation = attempt_row.semantic_window
            plan = next(
                row.semantic_selection for row in ledger.snapshot() if row.semantic_selection
            )
            original = next(row for row in ledger.snapshot() if row.model_intent)
            bound = port_input(
                budget,
                doc,
                cfg.semantics,
                plan,
                observation.selection,
                intent=GOAL,
                start=observation.start,
                end=observation.end,
            )
            invocation = ModelInvocation(
                budget,
                ledger,
                phase="semantic_extract",
                model=model.model,
                url=doc.url,
                request=bound,
                reserve=budget.reserve_semantic,
                replay_intent_sequence=original.sequence,
            )
            recovered = invocation.replay(
                lambda output: SemanticProposal.model_validate_json(output.body())
            )
            assert recovered == observation.proposal
            assert budget.judge_calls == 1 and len(wire.requests) == 1
            with pytest.raises(FatalModelWorkFailure):
                ModelInvocation(
                    budget,
                    ledger,
                    phase="semantic_extract",
                    model=model.model,
                    url=doc.url,
                    request=bound + b"changed",
                    reserve=budget.reserve_semantic,
                    replay_intent_sequence=original.sequence,
                )
        return graph.snapshot()

    try:
        snapshot = asyncio.run(run())
        ledger.append(
            LedgerRow(sequence=ledger.next_sequence, event="stop", reason="frontier_empty")
        )
        rows = ledger.snapshot()
        receipt = Receipt(
            fetches=0,
            bytes_read=0,
            judge_calls=budget.judge_calls,
            encoding_calls=budget.encoding_calls,
            encoding_chars=budget.encoding_chars,
            accepted_documents=1,
            elapsed_seconds=0.0,
            stop_reason="frontier_empty",
            effective_config=cfg,
            judge=FakeJudge().model,
        )
        harvest = Harvest(
            schema="chimera.harvest/1",
            goal=goal,
            documents=(doc,),
            ledger=rows,
            receipt=receipt,
            graph=snapshot,
        )
        if sink is not None:
            sink.finish(harvest)
        return harvest, wire, encoder_wire
    finally:
        ledger.close()


def test_ranked_window_reaches_late_native_text_and_replays_complete_archive(tmp_path):
    cfg = selection_config(tmp_path, durable=True, research=True)
    harvest, wire, encoder = exercise_selection(cfg, repeat=True, replay=True)
    plan = next(row.semantic_selection for row in harvest.ledger if row.semantic_selection)
    attempt = next(row.semantic_window for row in harvest.ledger if row.semantic_window)
    assert attempt.start > 80 and attempt.selection.window_index == 0
    assert attempt.omitted_chars == len(TEXT) - plan.context.selected_chars
    assert attempt.omitted_chars != len(TEXT) - attempt.end
    assert "甲委員會隸屬乙委員會。" in plan.context.windows[0].text
    assert (
        plan.context.windows[0].anchors
        and plan.context.scoring_source.source_sha256 == harvest.documents[0].sha256
    )
    assert len(wire.requests) == harvest.receipt.judge_calls == 1
    assert len(encoder.requests) == harvest.receipt.encoding_calls
    assert validate_rows(cfg, harvest.ledger)
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    report = read_journal(cfg.journal, "selected")
    assert report.rows == harvest.ledger and report.state == "complete"
    context = build_context(cfg, harvest.ledger)
    gap = next(item for item in context.gaps if isinstance(item, PlanningSelectionGap))
    assert gap.omitted_chars == plan.context.omitted_chars
    assert "coverage" not in gap.model_dump()
    validate_context(cfg, context, harvest.documents)
    changed = gap.model_copy(update={"omitted_chars": gap.omitted_chars - 1})
    with pytest.raises(GhimeraRefused):
        validate_context(cfg, context.model_copy(update={"gaps": (changed,)}), harvest.documents)


def test_ranked_refusal_keeps_original_ack_and_exact_failed_window(tmp_path):
    cfg = selection_config(tmp_path, durable=True)
    harvest, wire, _ = exercise_selection(cfg, failed=True, repeat=True)
    failed = next(row.semantic_refusal for row in harvest.ledger if row.semantic_refusal)
    assert failed.start > 80 and failed.selection.window_index == 0 and not failed.continued
    assert len(wire.requests) == harvest.receipt.judge_calls == 1
    ack = next(row.model_ack for row in harvest.ledger if row.model_ack)
    assert ack.outcome == "returned" and ack.stored_output is not None
    assert not any(row.model_replay for row in harvest.ledger)
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    assert read_journal(cfg.journal, "selected").rows == harvest.ledger


def test_unknown_selected_invocation_remains_charged_and_cannot_restart(tmp_path):
    cfg = selection_config(tmp_path, durable=True)
    doc, goal = native_document(cfg), Goal(text=GOAL)
    sink = DirectoryLedgerSink(cfg, "unknown", goal, FakeJudge().model)
    ledger, budget = Ledger(sink=sink), RunBudget(cfg, time.monotonic)

    class CancelledWire(SemanticWire):
        async def post(self, body):
            self.requests.append(json.loads(body))
            raise asyncio.CancelledError

    wire = CancelledWire(cfg.models.analyst)
    model = SelfHostedModel(cfg, cfg.models.analyst, http=wire)
    stage = SemanticStage(cfg, model)
    scorer = EmbeddingScorer(
        cfg.scoring, SelfHostedEncoder(cfg.scoring.encoder, http=EncodingWire(cfg.scoring.encoder))
    )

    async def run():
        graph = ResearchGraph(cfg.graph, "unknown", MemoryGraphSink())
        await graph.start(GOAL)
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "controlled-parser@1")
        ledger.append(
            LedgerRow(
                sequence=0,
                event="extraction",
                url=doc.url,
                extraction=doc.extracted.extraction,
                reason="controlled_native_parser",
            )
        )
        await scorer.rank(goal, doc.extracted, budget, ledger)
        with pytest.raises(asyncio.CancelledError):
            await stage.extract(GOAL, doc, identity, graph, budget, ledger)
        plan = next(row.semantic_selection for row in ledger.snapshot() if row.semantic_selection)
        window = plan.context.windows[0]
        reference = SemanticSelectionRef(selection_sha256=plan.content_digest(), window_index=0)
        bound = port_input(
            budget,
            doc,
            cfg.semantics,
            plan,
            reference,
            intent=GOAL,
            start=window.start,
            end=window.end,
        )
        prefix, calls = ledger.snapshot(), budget.judge_calls
        assert calls == 1 and len(uncertain_model_sequences(prefix)) == 1
        failed = next(row.semantic_refusal for row in prefix if row.semantic_refusal)
        assert failed.selection == reference and not failed.continued
        with pytest.raises(GhimeraRefused):
            await stage.extract(GOAL, doc, identity, graph, budget, ledger)
        assert (
            ledger.snapshot() == prefix and budget.judge_calls == calls and len(wire.requests) == 1
        )
        original = next(row.sequence for row in prefix if row.model_intent)
        with pytest.raises(FatalModelWorkFailure):
            ModelInvocation(
                budget,
                ledger,
                phase="semantic_extract",
                model=model.model,
                url=doc.url,
                request=bound,
                reserve=budget.reserve_semantic,
                replay_intent_sequence=original,
            )

    try:
        asyncio.run(run())
    finally:
        ledger.close()
    report = read_journal(cfg.journal, "unknown")
    assert report.state == "unsealed" and len(uncertain_model_sequences(report.rows)) == 1


def test_refused_first_selected_window_does_not_reorder_following_success(tmp_path):
    from tests.test_semantic_recovery import FirstReviewFails
    from tests.test_semantic_verification import reviewed_config

    cfg = selection_config(tmp_path)
    raw = cfg.model_dump()
    semantic = reviewed_config(
        tmp_path, window_chars=80, max_windows_per_document=2
    ).semantics.model_dump()
    semantic["window_selection"] = dict(
        cfg.semantics.window_selection.model_dump(), max_selected_chars=160
    )
    semantic["failure"] = dict(
        schema="ghimera.semantic-failure-policy/1",
        action="record_gap",
        allowed_refusals=["semantic_extraction_failed"],
        max_failed_windows_per_run=2,
    )
    raw["semantics"] = semantic
    raw["models"] = reviewed_config(tmp_path).models
    cfg = GhimeraConfig.model_validate(raw)
    text = (
        "unrelated " * 30
        + "甲委員會隸屬乙委員會。"
        + " middle" * 60
        + "甲委員會隸屬乙委員會。"
        + " ending" * 10
    )
    harvest, _, _ = exercise_selection(cfg, text=text, reviewer_factory=FirstReviewFails)
    attempts = validate_rows(cfg, harvest.ledger)
    assert attempts[0].semantic_refusal.selection.window_index == 0
    assert attempts[0].semantic_refusal.continued
    assert attempts[1].semantic_window.selection.window_index == 1
    assert attempts[1].semantic_window.start > attempts[0].semantic_refusal.start
    assert harvest.receipt.judge_calls == 4
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    swapped = tuple(
        row.model_copy(
            update={
                "semantic_refusal": row.semantic_refusal.model_copy(
                    update={
                        "selection": row.semantic_refusal.selection.model_copy(
                            update={"window_index": 1}
                        )
                    }
                )
            }
        )
        if row.semantic_refusal
        else row
        for row in harvest.ledger
    )
    with pytest.raises(ValueError):
        validate_rows(cfg, swapped)


@pytest.mark.parametrize("defect", ["goal", "raw", "source", "missing", "legacy_score"])
def test_wrong_or_unbound_scoring_refuses_selection_before_contact(tmp_path, defect):
    cfg = selection_config(tmp_path)
    harvest, _, _ = exercise_selection(cfg)
    selection_row = next(row for row in harvest.ledger if row.semantic_selection)
    prefix = harvest.ledger[: selection_row.sequence]
    doc = harvest.documents[0]
    goal = GOAL
    if defect == "goal":
        goal = "another intent"
    elif defect == "raw":
        doc = native_document(cfg, raw_suffix=b"different original bytes")
    elif defect == "source":
        doc = native_document(cfg, url="https://example.org/other")
    elif defect == "missing":
        prefix = ()
    else:
        prefix = tuple(
            row.model_copy(update={"scoring_source": None}) if row.similarity else row
            for row in prefix
        )
    with pytest.raises(ValueError):
        make_selection(cfg, doc, selection_row.semantic_selection.graph_document_id, goal, prefix)


@pytest.mark.parametrize(
    "defect", ["index", "span", "omission", "source", "plan_digest", "missing_plan", "policy_none"]
)
def test_readback_rejects_changed_selected_attempts_and_bindings(tmp_path, defect):
    cfg = selection_config(tmp_path)
    harvest, _, _ = exercise_selection(cfg)
    index = next(i for i, row in enumerate(harvest.ledger) if row.semantic_window)
    row, rows = harvest.ledger[index], list(harvest.ledger)
    window = row.semantic_window
    if defect == "index":
        window = window.model_copy(
            update={"selection": window.selection.model_copy(update={"window_index": 1})}
        )
    elif defect == "span":
        window = window.model_copy(update={"start": 0})
    elif defect == "omission":
        window = window.model_copy(update={"omitted_chars": len(TEXT) - window.end})
    elif defect == "source":
        window = window.model_copy(update={"document_sha256": "0" * 64})
    elif defect == "plan_digest":
        window = window.model_copy(
            update={"selection": window.selection.model_copy(update={"selection_sha256": "0" * 64})}
        )
    elif defect == "missing_plan":
        rows = [item for item in rows if item.semantic_selection is None]
    else:
        cfg = cfg.model_copy(
            update={"semantics": cfg.semantics.model_copy(update={"window_selection": None})}
        )
    if defect not in {"missing_plan", "policy_none"}:
        rows[index] = row.model_copy(update={"semantic_window": window})
    with pytest.raises(ValueError):
        validate_rows(cfg, tuple(rows))


def test_configuration_opt_in_is_versioned_and_legacy_serialization_unchanged(tmp_path):
    legacy = configured(tmp_path)
    assert "window_selection" not in legacy.semantics.model_dump()
    assert "SemanticWindowSelectionConfig" in GhimeraConfig.model_json_schema()["$defs"]
    for changes in (
        {"schema": "ghimera.semantic-window-selection/2"},
        {"padding_chars": -1},
        {"strategy": "memory"},
        {"max_selected_chars": 0},
    ):
        with pytest.raises(ValidationError):
            selection_config(tmp_path, **changes)
    cfg = selection_config(tmp_path)
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(dict(cfg.model_dump(), scoring=None))
