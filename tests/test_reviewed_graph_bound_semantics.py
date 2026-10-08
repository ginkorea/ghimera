"""Reviewed /6 native contracts; controlled replies are not model quality."""

import asyncio
import hashlib
import json
import time
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.ledger import Ledger
from ghimera.model_client import SelfHostedModel
from ghimera.model_work import FatalModelWorkFailure, ModelInvocation, port_input
from ghimera.models import Goal, Receipt
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_batching import review_selections
from ghimera.semantic_contract import build_graph_contract
from ghimera.semantic_graph import SemanticStage, validate_rows
from ghimera.semantic_types import GroundedSemanticReview, SemanticConfig, SemanticProposal
from tests import test_semantic_verification as verification_contract
from tests.test_graph_bound_semantics import (
    CaptureWire,
    extract,
    graph_bound_config,
    retained_config,
)
from tests.test_semantic_graph import document
from tests.test_semantic_quote_selection import QuoteWire, native_document, quote_config
from tests.test_semantic_review_dimensions import DimensionWire, dimension_config
from tests.test_semantic_verification import reviewed_config, run_stage

PROFILE = "reviewed_graph_bound_native_spans"


def test_original_v5_entire_transport_and_revision_remain_frozen(tmp_path):
    # Independently measured from frozen cd4f7f57, C0 Python 3.11.16. The
    # contract includes the configured sink path; this inert fixture path is
    # fixed on both sides and is never opened by the injected transport.
    raw = graph_bound_config(tmp_path).model_dump()
    raw["graph"]["sink_path"] = "/tmp/ghimera-legacy-graph-contract-fixture"
    cfg = GhimeraConfig.model_validate(raw)
    wire = CaptureWire(cfg.models.analyst)
    proposal = extract(cfg, wire)
    assert cfg.semantics.content_digest() == (
        "df8af2492210ed5102c01f288de7d18b11087e94766354df232204e753e8621e"
    )
    assert hashlib.sha256(wire.bodies[0]).hexdigest() == (
        "fec04c38b0d8e9b1e92e3d690984b1f2367b0059ba7acdb0fd60f31e146af340"
    )
    assert len(wire.bodies[0]) == 8561 and proposal.model_call.input_chars == 7838
    assert proposal.model_call.prompt_revision == "ghimera-semantic-extraction/5"
    assert "separately configured independent reviewer" not in wire.system
    assert "verification" not in wire.requests[0]["semantic_recipe"]


def reviewed_graph_bound_config(tmp_path, **changes):
    raw = reviewed_config(tmp_path, **changes).model_dump()
    raw["semantics"].update(schema="ghimera.semantics/6", prompt_profile=PROFILE)
    return GhimeraConfig.model_validate(raw)


def partitioned_config(tmp_path, *, quotes=False, durable=False):
    raw = (quote_config(tmp_path) if quotes else dimension_config(tmp_path)).model_dump()
    raw["semantics"].update(schema="ghimera.semantics/6", prompt_profile=PROFILE)
    if durable:
        existing = retained_config(tmp_path).model_dump()
        raw.update(journal=existing["journal"], model_work=existing["model_work"])
    return GhimeraConfig.model_validate(raw)


@pytest.mark.parametrize(
    "defect", ["profile", "roles", "relations", "duplicate", "verification", "null", "same_model"]
)
def test_reviewed_graph_bound_requires_complete_independent_policy(tmp_path, defect):
    raw = partitioned_config(tmp_path).model_dump()
    policy = raw["semantics"]
    if defect == "profile":
        policy["prompt_profile"] = "graph_bound_native_spans"
    elif defect == "roles":
        policy.pop("role_definitions")
    elif defect == "relations":
        policy["relation_definitions"][0]["name"] = "invented"
    elif defect == "duplicate":
        policy["role_definitions"] *= 2
    elif defect == "verification":
        policy.pop("verification")
    elif defect == "null":
        policy["verification"] = None
    else:
        raw["models"]["reviewer"] = raw["models"]["analyst"]
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


@pytest.mark.parametrize(
    "check",
    [
        verification_contract.test_defined_ontology_and_independent_check_reach_the_wire_and_replay,
        verification_contract.test_rejected_mention_and_dependent_edge_are_quarantined_not_repaired,
        verification_contract.test_reviewer_cannot_rescue_a_missing_native_span,
    ],
)
def test_existing_independent_review_conformance_applies_to_v6(tmp_path, monkeypatch, check):
    # Only substitute the fixture's typed recipe factory, never a production
    # stage, validator, transport response or claimed reviewer identity.
    monkeypatch.setattr(verification_contract, "reviewed_config", reviewed_graph_bound_config)
    check(tmp_path)


@pytest.mark.parametrize("defect", ["digest", "missing_key", "truncated"])
def test_existing_invalid_review_refusal_applies_to_v6(tmp_path, monkeypatch, defect):
    monkeypatch.setattr(verification_contract, "reviewed_config", reviewed_graph_bound_config)
    verification_contract.test_invalid_independent_reply_refuses_before_projection(tmp_path, defect)


@pytest.mark.parametrize("quotes", [False, True])
def test_v6_actual_native_packet_review_and_telemetry(tmp_path, quotes):
    cfg = partitioned_config(tmp_path, quotes=quotes)
    doc = native_document() if quotes else document()
    extraction = CaptureWire(cfg.models.analyst)
    reviews = QuoteWire(cfg.models.reviewer) if quotes else DimensionWire(cfg.models.reviewer)
    graph, rows, budget = run_stage(cfg, extraction, reviews, (doc,))
    window = rows[-1].semantic_window
    contract = build_graph_contract(cfg, cfg.semantics)
    assert extraction.requests[0]["semantic_graph_contract"] == contract.model_dump(mode="json")
    assert contract.graph_config_sha256 == cfg.graph.content_digest()
    assert contract.semantic_policy_sha256 == cfg.semantics.content_digest()
    assert window.proposal.model_call.prompt_revision == "ghimera-semantic-extraction/6"
    assert (
        window.proposal.model_call.request_sha256
        == hashlib.sha256(extraction.bodies[0]).hexdigest()
    )
    assert window.proposal.model_call.input_chars == sum(
        len(message["content"]) for message in json.loads(extraction.bodies[0])["messages"]
    )
    assert "separately configured independent reviewer" in extraction.system
    assert "BOTH explicit source support" in extraction.system
    assert window.review.proposal_digest == window.proposal.content_digest()
    assert budget.semantic_calls == 1 and budget.semantic_review_calls == len(reviews.requests) == 4
    assert budget.judge_calls == 5
    for part, (request, packet) in zip(window.review.parts, reviews.requests, strict=True):
        assert packet["semantic_recipe"]["schema"] == "ghimera.semantics/6"
        assert packet["semantic_proposal"] == window.proposal.model_dump(
            mode="json", exclude={"model_call"}, exclude_none=True
        )
        assert packet["evidence"]["windows"][0]["citation"]["quote"] == doc.extracted.text
        assert part.review.model_call.service == cfg.models.reviewer
        assert part.review.model_call.selected_spans == (
            ("doc:" + doc.sha256, 0, len(doc.extracted.text)),
        )
        assert "untrusted DATA" in request["messages"][0]["content"]
        observed = part.review.quote_response if quotes else part.review.dimension_response
        assert observed is not None
    assert window.edges and all(edge.claim_status == "model_asserted" for edge in window.edges)
    assert any(edge.rule == "reports_to" for edge in graph.snapshot().edges)
    assert validate_rows(cfg, rows) == (rows[-1],)


def test_v6_cannot_adopt_historical_v5_extraction_or_wrong_reviewer(tmp_path):
    cfg, doc = partitioned_config(tmp_path), document()
    old_raw = cfg.model_dump()
    old_raw["semantics"].update(
        schema="ghimera.semantics/5", prompt_profile="graph_bound_native_spans"
    )
    old_raw["semantics"].pop("verification")
    old = GhimeraConfig.model_validate(old_raw)
    proposal = extract(old, CaptureWire(old.models.analyst), doc)
    assert proposal.model_call.prompt_revision == "ghimera-semantic-extraction/5"
    reviews = DimensionWire(cfg.models.reviewer)
    reviewer = SelfHostedModel(cfg, cfg.models.reviewer, http=reviews)
    selection = review_selections(cfg.semantics.verification, proposal)[0]
    with pytest.raises(GhimeraRefused):
        asyncio.run(
            reviewer.semantic_review_part(
                "map the organization",
                doc,
                0,
                len(doc.extracted.text),
                cfg.semantics,
                proposal,
                selection,
            )
        )
    assert reviews.requests == []
    extractor = SelfHostedModel(cfg, cfg.models.analyst, http=CaptureWire(cfg.models.analyst))
    with pytest.raises(ValueError):
        SemanticStage(cfg, extractor)
    with pytest.raises(ValueError, match="configured model"):
        SemanticStage(cfg, extractor, reviewer=extractor)


def test_native_v6_ack_replay_preserves_original_partition_and_graph_binding(tmp_path):
    cfg, doc = partitioned_config(tmp_path, durable=True), document()
    goal = Goal(text="map the organization")
    extraction, reviews = CaptureWire(cfg.models.analyst), DimensionWire(cfg.models.reviewer)
    extractor = SelfHostedModel(cfg, cfg.models.analyst, http=extraction)
    reviewer = SelfHostedModel(cfg, cfg.models.reviewer, http=reviews)
    budget = RunBudget(cfg, time.monotonic)
    ledger = Ledger(sink=DirectoryLedgerSink(cfg, "reviewed-graph-bound", goal, extractor.model))
    graph = ResearchGraph(cfg.graph, "reviewed-graph-bound", MemoryGraphSink())

    async def run():
        await graph.start(goal.text)
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "fixture@1")
        await SemanticStage(cfg, extractor, reviewer=reviewer).extract(
            goal.text, doc, identity, graph, budget, ledger
        )

    try:
        asyncio.run(run())
        rows = ledger.snapshot()
        window = rows[-1].semantic_window
        extraction_input = port_input(
            budget,
            doc,
            cfg.semantics,
            build_graph_contract(cfg, cfg.semantics),
            intent=goal.text,
            start=0,
            end=len(doc.extracted.text),
        )
        original_extraction = next(
            row for row in rows if row.model_intent and row.model_intent.phase == "semantic_extract"
        )
        assert (
            original_extraction.model_intent.input_sha256
            == hashlib.sha256(extraction_input).hexdigest()
        )
        assert budget.judge_calls == 5
        receipt = Receipt(
            fetches=0,
            bytes_read=0,
            judge_calls=budget.judge_calls,
            accepted_documents=0,
            elapsed_seconds=budget.elapsed,
            stop_reason="frontier_empty",
            effective_config=cfg,
            judge=extractor.model,
        )
    finally:
        ledger.close()
    report = read_journal(cfg.journal, "reviewed-graph-bound")
    assert report.rows == rows and report.uncertain_model_calls == ()
    restored = RunBudget(cfg, time.monotonic)
    restored.restore(receipt, report.rows, 0, 0)
    assert restored.semantic_calls == 1 and restored.semantic_review_calls == 4
    resumed = Ledger(
        sink=DirectoryLedgerSink(
            cfg, "reviewed-graph-bound", goal, extractor.model, resume_rows=rows
        ),
        restored_rows=rows,
    )
    try:
        extraction_replay = ModelInvocation(
            restored,
            resumed,
            phase="semantic_extract",
            model=extractor.model,
            url=doc.url,
            request=extraction_input,
            replay_intent_sequence=original_extraction.sequence,
        )
        assert (
            extraction_replay.replay(
                lambda stored: SemanticProposal.model_validate_json(stored.body())
            )
            == window.proposal
        )
        first = window.review.parts[0]
        request = port_input(
            restored,
            doc,
            cfg.semantics,
            window.proposal,
            first.selection,
            intent=goal.text,
            start=0,
            end=len(doc.extracted.text),
        )
        original = next(
            row for row in rows if row.model_intent and row.model_intent.phase == "semantic_review"
        )
        invocation = ModelInvocation(
            restored,
            resumed,
            phase="semantic_review",
            model=reviewer.model,
            url=doc.url,
            request=request,
            replay_intent_sequence=original.sequence,
        )
        replayed = invocation.replay(
            lambda stored: GroundedSemanticReview.model_validate_json(stored.body())
        )
        assert replayed == first.review
        assert (
            restored.judge_calls == 5 and len(extraction.bodies) == 1 and len(reviews.requests) == 4
        )
        changed = request + b" "
        with pytest.raises(FatalModelWorkFailure):
            ModelInvocation(
                restored,
                resumed,
                phase="semantic_review",
                model=reviewer.model,
                url=doc.url,
                request=changed,
                replay_intent_sequence=original.sequence,
            )
    finally:
        resumed.close()
    assert read_journal(cfg.journal, "reviewed-graph-bound").uncertain_model_calls == ()


@pytest.mark.parametrize("research_chars", [None, 10_000, 20_000])
def test_v6_native_document_harvest_journal_and_graph_readback(tmp_path, research_chars):
    from ghimera.documents import DocumentExtractor
    from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.graph import DirectoryGraphSink
    from ghimera.loop import GoalLoop
    from ghimera.models import Harvest
    from ghimera.research import ResearchLoop
    from ghimera.research_types import ResearchRequest
    from ghimera.result_archive import ResearchResultArchive
    from tests.test_document_extraction import config as doc_config
    from tests.test_document_extraction import docx
    from tests.test_intent_research import (
        AnalystFixture,
        PlannerFixture,
        ReviewerFixture,
        SearchFixture,
    )
    from tests.test_intent_research import (
        policy as research_policy,
    )
    from tests.test_local_inputs import recipe, seed

    raw_config = partitioned_config(tmp_path, durable=True).model_dump()
    raw_config.update(
        document_extraction=doc_config(tmp_path).document_extraction, local_inputs=recipe(tmp_path)
    )
    complete_archive = research_chars is not None
    if complete_archive:
        # The separate ACK test covers native model-work retention. Here the
        # existing controlled research ports exercise actual result assembly;
        # no invented ResearchResult/Harvest or quality claim is manufactured.
        raw_config.pop("model_work")
        raw_config["research"] = research_policy(max_model_input_chars=research_chars)
    cfg = GhimeraConfig.model_validate(raw_config)
    raw = docx("甲委員會隸屬乙委員會。")
    path = tmp_path / "organization.docx"
    path.write_bytes(raw)
    extraction, reviews = CaptureWire(cfg.models.analyst), DimensionWire(cfg.models.reviewer)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
        semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=extraction),
        semantic_reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=reviews),
    )

    async def run():
        session = await loop.open(Goal(text="map the organization"), run_id="reviewed-graph-loop")
        await loop.import_local(session, (seed(path, raw),))
        return loop.finish(session, "frontier_empty")

    if complete_archive:

        class SeedPlanner(PlannerFixture):
            async def plan(self, request):
                assert request.documents[0].raw == raw
                return (await super().plan(request)).model_copy(update={"queries": ()})

        search = SearchFixture()
        research = ResearchLoop(
            config=cfg,
            collector=loop,
            search=search,
            planner=SeedPlanner(),
            analyst=AnalystFixture(),
            reviewer=ReviewerFixture(),
        )
        result = asyncio.run(
            research.run(
                ResearchRequest(intent="map the organization", local_documents=(seed(path, raw),)),
                run_id="reviewed-graph-loop",
            )
        )
        assert not search.requests
        assert result.status == ("partial" if research_chars == 10_000 else "answered")
        archive_path = tmp_path / "archive"
        archive = ResearchResultArchive.create(archive_path, run_id="reviewed-graph-loop")
        try:
            archive.write(result, max_bytes=10_000_000)
        finally:
            archive.close()
        reread = ResearchResultArchive.read(archive_path, max_bytes=10_000_000)
        assert reread == result and reread.harvest.documents[0].raw == raw
        harvest = reread.harvest
    else:
        harvest = asyncio.run(run())
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    assert read_journal(cfg.journal, "reviewed-graph-loop").rows == harvest.ledger
    assert asyncio.run(DirectoryGraphSink(cfg.graph, "reviewed-graph-loop").replay())
    if research_chars == 10_000:
        # Preserve the original insufficient-context refusal: an actual
        # extraction returned, but review preflight admitted no reviewer
        # contact or semantic graph projection. The private archive retains
        # the honest partial result, not an invented completed answer.
        assert result.stop_reason == harvest.receipt.stop_reason == "budget_exhausted"
        assert result.answer is None and result.review is None
        assert len(extraction.bodies) == 1 and reviews.requests == []
        assert not any(row.semantic_window for row in harvest.ledger)
        assert any(
            row.reason == "semantic_window_refused" and row.refusal.value == "budget_exhausted"
            for row in harvest.ledger
        )
        assert harvest.receipt.judge_calls == 2
        return
    window = next(row.semantic_window for row in harvest.ledger if row.semantic_window)
    assert window.proposal.model_call.prompt_revision == "ghimera-semantic-extraction/6"
    assert window.review.model_call.service == cfg.models.reviewer
    altered = harvest.model_dump()
    row = next(item for item in altered["ledger"] if item.get("semantic_window"))
    row["semantic_window"]["proposal"]["model_call"]["prompt_revision"] = (
        "ghimera-semantic-extraction/5"
    )
    with pytest.raises(ValidationError):
        Harvest.model_validate(altered)


def test_reviewed_graph_bound_example_is_inert_and_complete():
    raw = tomllib.loads(Path("examples/semantics-reviewed-graph-bound.toml").read_text())
    assert set(raw) == {"semantics"}
    policy = SemanticConfig.model_validate(raw["semantics"])
    assert policy.schema_version == "ghimera.semantics/6" and policy.prompt_profile == PROFILE
    assert policy.effective_prompt_revision == "ghimera-semantic-extraction/6"
    assert policy.verification.prompt_profile == "native_quote_checks"
