"""Native /5 prompt/accounting fixtures, not real-model quality or corroboration."""

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
from ghimera.model_http import ModelHttpResponse
from ghimera.model_work import FatalModelWorkFailure, ModelInvocation, port_input
from ghimera.models import Goal, Receipt
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_contract import build_graph_contract
from ghimera.semantic_graph import SemanticStage, validate_rows
from ghimera.semantic_types import SemanticConfig, SemanticProposal
from tests.test_semantic_graph import SemanticWire, configured, document
from tests.test_semantic_verification import reviewed_config

DEFINITIONS = dict(
    role_definitions=[
        dict(name="entity", definition="A named institutional entity in the source.")
    ],
    relation_definitions=[
        dict(
            name="reports_to",
            definition="Source institution explicitly reports to target institution.",
        )
    ],
)


def graph_bound_config(tmp_path):
    return configured(
        tmp_path,
        schema="ghimera.semantics/5",
        prompt_profile="graph_bound_native_spans",
        **DEFINITIONS,
    )


class CaptureWire(SemanticWire):
    def __init__(self, bound):
        super().__init__(bound)
        self.bodies = []

    async def post(self, body):
        self.bodies.append(body)
        return await super().post(body)


def extract(cfg, wire, doc=None):
    doc = doc or document()
    return asyncio.run(
        SelfHostedModel(cfg, cfg.models.analyst, http=wire).semantic_extract(
            "map the organization", doc, 0, len(doc.extracted.text), cfg.semantics
        )
    )


@pytest.mark.parametrize(
    "version,profile,policy_sha,wire_sha",
    [
        (
            1,
            None,
            "86fded6458d89c388b69825ec3b2670f0b0f4ade20eaff5014b02f41ac4d9587",
            "3fec88582c2d2bcf66ebdb6351e35f490692988a59e0cb8cc07b84dc3d47ae6d",
        ),
        (
            2,
            "explicit_mention_keys",
            "0f836e3850ed0e1d6c7fc4a03396dfe3d4edb4d61cb0ad0534621e1083535759",
            "c249c91422ae2fed8d81af084f04c3bb87ae9d5742571145de50645247be71b0",
        ),
        (
            3,
            "native_span_keys",
            "6ddc4029d44854559b4fc004c73e3502e2aabbb89c16c7642f3bff24778b0d21",
            "d8c5dd22e84c1d5fa8386c80e3ee695a64b1cc8f83cc68be412c9a7d216f67f4",
        ),
        (
            4,
            "defined_ontology",
            "a94c64d0af973742b087ff2ea4977d1532d3d1f607447091f00bef5aa864d55d",
            "87380136497b555acf39543dd69d4d74766a29f8dabd2663ebec9ebeba12ab0b",
        ),
    ],
)
def test_legacy_policy_and_entire_wire_fingerprints_are_frozen(
    tmp_path, version, profile, policy_sha, wire_sha
):
    cfg = (
        reviewed_config(tmp_path)
        if version == 4
        else configured(
            tmp_path,
            **(
                {}
                if version == 1
                else {"schema": f"ghimera.semantics/{version}", "prompt_profile": profile}
            ),
        )
    )
    wire = CaptureWire(cfg.models.analyst)
    proposal = extract(cfg, wire)
    assert cfg.semantics.content_digest() == policy_sha
    assert hashlib.sha256(wire.bodies[0]).hexdigest() == wire_sha
    assert proposal.model_call.request_sha256 == wire_sha
    assert proposal.model_call.prompt_revision == f"ghimera-semantic-extraction/{version}"
    assert "semantic_graph_contract" not in wire.requests[0]
    assert build_graph_contract(cfg, cfg.semantics) is None


@pytest.mark.parametrize("defect", ["profile", "roles", "relations", "duplicate", "verification"])
def test_v5_requires_complete_definitions_and_no_review_claim(tmp_path, defect):
    raw = graph_bound_config(tmp_path).model_dump()
    policy = raw["semantics"]
    if defect == "profile":
        policy["prompt_profile"] = "native_span_keys"
    elif defect == "roles":
        policy.pop("role_definitions")
    elif defect == "relations":
        policy["relation_definitions"][0]["name"] = "invented"
    elif defect == "duplicate":
        policy["role_definitions"] *= 2
    else:
        policy["verification"] = None  # Explicit null is not omitted verification.
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def test_v4_still_requires_independent_verification(tmp_path):
    raw = graph_bound_config(tmp_path).semantics.model_dump()
    raw.update(schema="ghimera.semantics/4", prompt_profile="defined_ontology")
    with pytest.raises(ValidationError, match="verification"):
        SemanticConfig.model_validate(raw)


def test_v5_supplies_exact_native_graph_contract_and_source_with_actual_telemetry(tmp_path):
    cfg = graph_bound_config(tmp_path)
    wire = CaptureWire(cfg.models.analyst)
    doc = document()
    proposal = extract(cfg, wire, doc)
    packet = wire.requests[0]
    contract = build_graph_contract(cfg, cfg.semantics)
    assert packet["semantic_graph_contract"] == contract.model_dump(mode="json")
    assert contract.graph_config_sha256 == cfg.graph.content_digest()
    assert contract.semantic_policy_sha256 == cfg.semantics.content_digest()
    assert contract.roles == tuple(r for r in cfg.graph.roles if r.name == "entity")
    assert contract.relations == tuple(r for r in cfg.graph.relations if r.name == "reports_to")
    assert contract.mention_rule == next(r for r in cfg.graph.relations if r.name == "mentions")
    assert packet["semantic_recipe"]["role_definitions"] == DEFINITIONS["role_definitions"]
    assert "verification" not in packet["semantic_recipe"]
    assert packet["evidence"]["windows"][0]["citation"]["quote"] == doc.extracted.text
    call = proposal.model_call
    assert call.prompt_revision == "ghimera-semantic-extraction/5"
    assert call.request_sha256 == hashlib.sha256(wire.bodies[0]).hexdigest()
    messages = json.loads(wire.bodies[0])["messages"]
    assert call.input_chars == sum(len(message["content"]) for message in messages)
    assert call.selected_spans == (("doc:" + doc.sha256, 0, len(doc.extracted.text)),)
    for instruction in (
        "BOTH explicit source support",
        "source_roles",
        "target_roles",
        "UNREVIEWED",
    ):
        assert instruction in wire.system


@pytest.mark.parametrize("defect", ["policy", "graph", "input_bound", "body_bound"])
def test_invalid_or_drifted_policy_and_actual_bounds_refuse_before_contact(tmp_path, defect):
    cfg = graph_bound_config(tmp_path)
    doc = document()
    reference_wire = CaptureWire(cfg.models.analyst)
    reference = extract(cfg, reference_wire, doc)
    if defect == "policy":
        cfg = cfg.model_copy(
            update={"semantics": cfg.semantics.model_copy(update={"role_definitions": ()})}
        )
    elif defect == "graph":
        wrong = cfg.graph.relations[-1].model_copy(update={"source_roles": ("unknown",)})
        cfg = cfg.model_copy(
            update={
                "graph": cfg.graph.model_copy(
                    update={"relations": cfg.graph.relations[:-1] + (wrong,)}
                )
            }
        )
    else:
        raw = cfg.model_dump()
        field = "max_input_chars" if defect == "input_bound" else "max_request_bytes"
        raw["models"]["analyst"][field] = (
            reference.model_call.input_chars - 1
            if defect == "input_bound"
            else len(reference_wire.bodies[0]) - 1
        )
        cfg = GhimeraConfig.model_validate(raw)
    wire = CaptureWire(cfg.models.analyst)
    with pytest.raises((ValidationError, GhimeraRefused)):
        extract(cfg, wire, doc)
    assert wire.bodies == []


def retained_config(tmp_path):
    raw = graph_bound_config(tmp_path).model_dump()
    raw["journal"] = dict(
        schema="chimera.run-journal-config/1",
        directory=str(tmp_path / "journal"),
        max_record_bytes=2000000,
        max_journal_bytes=10000000,
        max_summary_bytes=2000000,
        max_records=100,
    )
    raw["model_work"] = dict(
        schema="ghimera.model-work/1",
        max_input_bytes=100000,
        max_unanswered_calls=2,
        uncertain_policy="hold",
        results=dict(
            schema="ghimera.model-results/1", max_result_bytes=10000, max_total_result_bytes=100000
        ),
    )
    return GhimeraConfig.model_validate(raw)


def test_native_stage_reservation_and_original_ack_replay_bind_graph_contract(tmp_path):
    cfg = retained_config(tmp_path)
    goal, doc = Goal(text="map the organization"), document()
    wire = CaptureWire(cfg.models.analyst)
    client = SelfHostedModel(cfg, cfg.models.analyst, http=wire)
    budget = RunBudget(cfg, time.monotonic)
    sink = DirectoryLedgerSink(cfg, "graph-bound", goal, client.model)
    ledger = Ledger(sink=sink)
    graph = ResearchGraph(cfg.graph, "graph-bound", MemoryGraphSink())

    async def run():
        await graph.start(goal.text)
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "fixture@1")
        await SemanticStage(cfg, client).extract(goal.text, doc, identity, graph, budget, ledger)

    try:
        asyncio.run(run())
        rows = ledger.snapshot()
        intent_row = next(row for row in rows if row.model_intent)
        ack_row = next(row for row in rows if row.model_ack)
        semantic = rows[-1].semantic_window
        request = port_input(
            budget,
            doc,
            cfg.semantics,
            build_graph_contract(cfg, cfg.semantics),
            intent=goal.text,
            start=0,
            end=len(doc.extracted.text),
        )
        assert intent_row.model_intent.input_sha256 == hashlib.sha256(request).hexdigest()
        assert budget.judge_calls == budget.semantic_calls == len(wire.bodies) == 1
        assert semantic.review is None
        assert semantic.edges and all(e.claim_status == "model_asserted" for e in semantic.edges)
        assert validate_rows(cfg, rows) == (rows[-1],)
    finally:
        ledger.close()
    report = read_journal(cfg.journal, "graph-bound")
    restored = RunBudget(cfg, time.monotonic)
    restored.restore(
        Receipt(
            fetches=0,
            bytes_read=0,
            judge_calls=1,
            accepted_documents=0,
            elapsed_seconds=0,
            stop_reason="budget_exhausted",
            effective_config=cfg,
            judge=client.model,
        ),
        report.rows,
        0,
        0,
    )
    resumed = Ledger(
        sink=DirectoryLedgerSink(cfg, "graph-bound", goal, client.model, resume_rows=report.rows),
        restored_rows=report.rows,
    )
    try:
        replay = ModelInvocation(
            restored,
            resumed,
            phase="semantic_extract",
            model=client.model,
            url=doc.url,
            request=request,
            replay_intent_sequence=intent_row.sequence,
        )
        original = replay.replay(lambda stored: SemanticProposal.model_validate_json(stored.body()))
        assert original == semantic.proposal
        assert restored.judge_calls == restored.semantic_calls == len(wire.bodies) == 1
        assert resumed.snapshot()[-1].model_replay.ack_sequence == ack_row.sequence
        changed = cfg.model_copy(
            update={"graph": cfg.graph.model_copy(update={"profile_version": "3"})}
        )
        drift_request = port_input(
            restored,
            doc,
            cfg.semantics,
            build_graph_contract(changed, changed.semantics),
            intent=goal.text,
            start=0,
            end=len(doc.extracted.text),
        )
        with pytest.raises(FatalModelWorkFailure):
            ModelInvocation(
                restored,
                resumed,
                phase="semantic_extract",
                model=client.model,
                url=doc.url,
                request=drift_request,
                replay_intent_sequence=intent_row.sequence,
            )
    finally:
        resumed.close()
    final = read_journal(cfg.journal, "graph-bound")
    assert final.rows[-1].model_replay.ack_sequence == ack_row.sequence
    assert final.uncertain_model_calls == ()


def test_v5_native_harvest_readback_reprojects_original_source_and_refuses_role_tamper(tmp_path):
    from ghimera.documents import DocumentExtractor
    from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.graph import DirectoryGraphSink
    from ghimera.loop import GoalLoop
    from ghimera.models import Harvest
    from tests.test_document_extraction import config as doc_config
    from tests.test_document_extraction import docx
    from tests.test_local_inputs import recipe, seed

    raw_config = retained_config(tmp_path).model_dump()
    raw_config.update(
        document_extraction=doc_config(tmp_path).document_extraction,
        local_inputs=recipe(tmp_path),
    )
    cfg = GhimeraConfig.model_validate(raw_config)
    raw = docx("甲委員會隸屬乙委員會。")
    path = tmp_path / "organization.docx"
    path.write_bytes(raw)
    wire = CaptureWire(cfg.models.analyst)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
        semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=wire),
    )

    async def run():
        session = await loop.open(Goal(text="map the organization"), run_id="graph-bound-loop")
        await loop.import_local(session, (seed(path, raw),))
        return loop.finish(session, "frontier_empty")

    harvest = asyncio.run(run())
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    assert read_journal(cfg.journal, "graph-bound-loop").rows == harvest.ledger
    assert asyncio.run(DirectoryGraphSink(cfg.graph, "graph-bound-loop").replay())
    assert len(wire.bodies) == 1
    window = next(row.semantic_window for row in harvest.ledger if row.semantic_window)
    assert window.proposal.model_call.prompt_revision == "ghimera-semantic-extraction/5"
    assert window.review is None
    broken = harvest.model_dump()
    row = next(item for item in broken["ledger"] if item.get("semantic_window"))
    row["semantic_window"]["proposal"]["mentions"][0]["role"] = "document"
    with pytest.raises(ValidationError):
        Harvest.model_validate(broken)


def test_original_bad_holds_position_shape_still_refuses_without_graph_commit(tmp_path):
    raw = graph_bound_config(tmp_path).model_dump(mode="json")
    for role in ("person", "position"):
        raw["graph"]["roles"].append(dict(name=role, kind=role))
        raw["semantics"]["entity_roles"].append(role)
        raw["semantics"]["role_definitions"].append(dict(name=role, definition=f"A named {role}."))
    next(r for r in raw["graph"]["relations"] if r["name"] == "mentions")["target_roles"].extend(
        ["person", "position"]
    )
    raw["graph"]["relations"].append(
        dict(
            name="holds_position",
            predicate="holds_position",
            source_roles=["person"],
            target_roles=["position"],
            semantic=True,
        )
    )
    raw["semantics"]["relation_rules"].append("holds_position")
    raw["semantics"]["relation_definitions"].append(
        dict(name="holds_position", definition="Named person explicitly holds the named position.")
    )
    cfg = GhimeraConfig.model_validate(raw)

    class MaliciousWire(CaptureWire):
        async def post(self, body):
            response = await super().post(body)
            wire = json.loads(response.body)
            proposal = json.loads(wire["choices"][0]["message"]["content"])
            proposal["relations"][0]["rule"] = "holds_position"
            wire["choices"][0]["message"]["content"] = json.dumps(proposal)
            return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")

    wire = MaliciousWire(cfg.models.analyst)
    graph, ledger, budget = (
        ResearchGraph(cfg.graph, "bad-shape", MemoryGraphSink()),
        Ledger(),
        RunBudget(cfg, time.monotonic),
    )
    doc = document()

    async def run():
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "fixture@1")
        before = graph.snapshot()
        with pytest.raises(GhimeraRefused) as refused:
            await SemanticStage(cfg, SelfHostedModel(cfg, cfg.models.analyst, http=wire)).extract(
                "map the organization", doc, identity, graph, budget, ledger
            )
        assert refused.value.code == RefusalCode.SEMANTIC_EXTRACTION_FAILED
        assert graph.snapshot() == before

    asyncio.run(run())
    assert budget.judge_calls == budget.semantic_calls == len(wire.bodies) == 1
    assert ledger.snapshot()[-1].refusal == RefusalCode.SEMANTIC_EXTRACTION_FAILED


def test_graph_bound_example_is_inert_and_complete():
    raw = tomllib.loads(
        (Path(__file__).parents[1] / "examples/semantics-graph-bound.toml").read_text()
    )
    assert set(raw) == {"semantics"}
    policy = SemanticConfig.model_validate(raw["semantics"])
    assert policy.schema_version == "ghimera.semantics/5"
    assert policy.verification is None
    assert "verification" not in policy.model_dump()
