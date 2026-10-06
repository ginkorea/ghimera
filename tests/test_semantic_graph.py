"""Source-bound semantic extraction; protocol fixtures are not real-model accuracy."""

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
from ghimera.ledger import Ledger
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Document, Extracted, Verdict
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_graph import SemanticStage, validate_rows
from ghimera.semantic_types import SemanticConfig
from tests.test_c0 import config
from tests.test_served_models import service


def configured(tmp_path, **updates):
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph.update(sink_path=str(tmp_path / "graph"), semantic_min_confidence=0.8)
    graph["relations"].append(
        dict(
            name="reports_to",
            predicate="reports_to",
            source_roles=["entity"],
            target_roles=["entity"],
            semantic=True,
        )
    )
    bound = service(8769)
    semantics = dict(
        schema="ghimera.semantics/1",
        model_role="analyst",
        window_chars=1000,
        max_windows_per_document=3,
        max_calls_per_run=8,
        max_mentions_per_window=8,
        max_relations_per_window=8,
        entity_roles=["entity"],
        relation_rules=["reports_to"],
        mention_rule="mentions",
    )
    semantics.update(updates)
    raw = config(judge_budget=30).model_dump()
    raw.update(
        graph=graph,
        semantics=semantics,
        models=dict(
            schema="chimera.model-bindings/1",
            planner=bound,
            analyst=bound,
            reviewer=bound,
            judge=bound,
        ),
    )
    return GhimeraConfig.model_validate(raw)


def document(text="甲委員會隸屬乙委員會。", url="https://example.org/report"):
    raw = text.encode()
    return Document(
        url=url,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        extracted=Extracted(title="organization", language="zh", text=text),
        verdict=Verdict(
            decision="accept", kind="report", publisher="unknown", language="zh", reason="fixture"
        ),
    )


class SemanticWire:
    def __init__(self, bound, *, confidence=0.95, wrong_surface=False, unknown_citation=False):
        self.config = bound
        self.confidence, self.wrong_surface = confidence, wrong_surface
        self.unknown_citation, self.requests = unknown_citation, []

    async def post(self, body):
        request = json.loads(body)
        self.system = request["messages"][0]["content"]
        packet = json.loads(request["messages"][1]["content"])
        self.requests.append(packet)
        window = packet["evidence"]["windows"][0]
        citation = "cite:" + "0" * 64 if self.unknown_citation else window["citation_id"]
        output = dict(
            mentions=[
                dict(
                    key="m1",
                    role="entity",
                    surface="錯誤" if self.wrong_surface else "甲委員會",
                    citation_id=citation,
                    occurrence=0,
                    confidence=0.95,
                ),
                dict(
                    key="m2",
                    role="entity",
                    surface="乙委員會",
                    citation_id=citation,
                    occurrence=0,
                    confidence=0.95,
                ),
            ],
            relations=[
                dict(
                    rule="reports_to",
                    source="m1",
                    target="m2",
                    confidence=self.confidence,
                    citation_ids=[citation],
                    valid_from=None,
                    valid_to=None,
                )
            ],
        )
        reply = dict(
            id="fixture",
            model="fixture-model",
            choices=[
                dict(
                    index=0,
                    finish_reason="stop",
                    message=dict(role="assistant", content=json.dumps(output)),
                )
            ],
            usage=dict(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )
        return ModelHttpResponse(200, json.dumps(reply).encode(), "application/json")


def exercise(cfg, wire, docs):
    ledger, budget = Ledger(), RunBudget(cfg, time.monotonic)
    client = SelfHostedModel(cfg, cfg.models.analyst, http=wire)
    stage = SemanticStage(cfg, client)

    async def run():
        graph = ResearchGraph(cfg.graph, "semantic-run", MemoryGraphSink())
        await graph.start("map the organization")
        for doc in docs:
            identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
            await stage.extract("map the organization", doc, identity, graph, budget, ledger)
        return graph.snapshot(), ledger.snapshot(), budget

    return asyncio.run(run())


def test_automatic_semantics_are_source_bound_assertions_not_name_based_identity(tmp_path):
    cfg = configured(tmp_path)
    wire = SemanticWire(cfg.models.analyst)
    first = document()
    second = document(url="https://example.org/independent")
    snapshot, rows, budget = exercise(cfg, wire, (first, second))
    entities = [node for node in snapshot.nodes if node.role == "entity"]
    assert len(entities) == 4 and len({node.id for node in entities}) == 4
    claims = [edge for edge in snapshot.edges if edge.rule == "reports_to"]
    assert len(claims) == 2
    assert all(edge.claim_status == "model_asserted" for edge in claims)
    assert all(edge.model_request_sha256 for edge in claims)
    assert len(rows) == budget.semantic_calls == budget.judge_calls == 2
    for row in rows:
        assert row.event == "semantic" and row.model_call.task == "semantic_extract"
        assert row.semantic_window.omitted_chars == 0
        assert row.semantic_window.proposal.model_call == row.model_call
        assert row.semantic_window.entities[0].evidence.quote == "甲委員會"
    assert wire.requests[0]["semantic_recipe"]["relation_rules"] == ["reports_to"]


def test_explicit_mention_key_profile_is_versioned_and_recorded(tmp_path):
    cfg = configured(tmp_path, schema="ghimera.semantics/2", prompt_profile="explicit_mention_keys")
    wire = SemanticWire(cfg.models.analyst)
    _, rows, _ = exercise(cfg, wire, (document(),))
    assert cfg.semantics.effective_prompt_revision == "ghimera-semantic-extraction/2"
    assert rows[0].model_call.prompt_revision == cfg.semantics.effective_prompt_revision
    assert wire.requests[0]["semantic_recipe"]["prompt_profile"] == "explicit_mention_keys"
    assert rows[0].semantic_window.policy_digest == cfg.semantics.content_digest()
    assert "Build the bounded mentions list FIRST" in wire.system
    forged = rows[0].model_copy(
        update={
            "model_call": rows[0].model_call.model_copy(
                update={"prompt_revision": "ghimera-semantic-extraction/1"}
            )
        }
    )
    with pytest.raises(ValueError, match="provenance differs"):
        validate_rows(cfg, (forged,))


def test_legacy_semantic_profile_identity_and_invalid_version_choices(tmp_path):
    cfg = configured(tmp_path)
    policy = cfg.semantics
    assert policy.effective_prompt_revision == "ghimera-semantic-extraction/1"
    assert "prompt_profile" not in policy.model_dump(by_alias=True)
    wire = SemanticWire(cfg.models.analyst)
    _, rows, _ = exercise(cfg, wire, (document(),))
    assert rows[0].model_call.prompt_revision == "ghimera-semantic-extraction/1"
    assert "Build the bounded mentions list FIRST" not in wire.system
    with pytest.raises(ValidationError):
        configured(tmp_path, prompt_profile="explicit_mention_keys")
    with pytest.raises(ValidationError):
        configured(tmp_path, schema="ghimera.semantics/2")
    with pytest.raises(ValidationError):
        configured(tmp_path, schema="ghimera.semantics/2", prompt_profile="guess_endpoints")


def test_explicit_profile_retains_refusal_for_unlisted_relationship_endpoints(tmp_path):
    cfg = configured(tmp_path, schema="ghimera.semantics/2", prompt_profile="explicit_mention_keys")

    class DanglingWire(SemanticWire):
        async def post(self, body):
            response = await super().post(body)
            raw = json.loads(response.body)
            proposal = json.loads(raw["choices"][0]["message"]["content"])
            proposal["relations"][0]["target"] = "missing_key"
            raw["choices"][0]["message"]["content"] = json.dumps(proposal)
            return ModelHttpResponse(200, json.dumps(raw).encode(), "application/json")

    with pytest.raises(GhimeraRefused, match="model_unavailable"):
        exercise(cfg, DanglingWire(cfg.models.analyst), (document(),))


def test_native_span_profile_records_its_revision_and_disambiguates_occurrences(tmp_path):
    cfg = configured(tmp_path, schema="ghimera.semantics/3", prompt_profile="native_span_keys")
    wire = SemanticWire(cfg.models.analyst)
    _, rows, _ = exercise(cfg, wire, (document(),))
    assert cfg.semantics.effective_prompt_revision == "ghimera-semantic-extraction/3"
    assert rows[0].model_call.prompt_revision == cfg.semantics.effective_prompt_revision
    assert wire.requests[0]["semantic_recipe"]["prompt_profile"] == "native_span_keys"
    assert "not the mention's list position" in wire.system
    assert "Build the bounded mentions list FIRST" in wire.system
    forged = rows[0].model_copy(
        update={
            "model_call": rows[0].model_call.model_copy(
                update={"prompt_revision": "ghimera-semantic-extraction/2"}
            )
        }
    )
    with pytest.raises(ValueError, match="provenance differs"):
        validate_rows(cfg, (forged,))
    with pytest.raises(ValidationError):
        configured(tmp_path, schema="ghimera.semantics/2", prompt_profile="native_span_keys")
    with pytest.raises(ValidationError):
        configured(tmp_path, schema="ghimera.semantics/3", prompt_profile="explicit_mention_keys")


@pytest.mark.parametrize("bad", ("surface", "ordinal"))
def test_native_span_profile_still_refuses_memory_names_and_global_ordinals(tmp_path, bad):
    cfg = configured(tmp_path, schema="ghimera.semantics/3", prompt_profile="native_span_keys")

    class WrongNativeWire(SemanticWire):
        async def post(self, body):
            response = await super().post(body)
            raw = json.loads(response.body)
            proposal = json.loads(raw["choices"][0]["message"]["content"])
            proposal["mentions"][1]["surface" if bad == "surface" else "occurrence"] = (
                "未在來源出現的委員會" if bad == "surface" else 1
            )
            raw["choices"][0]["message"]["content"] = json.dumps(proposal)
            return ModelHttpResponse(200, json.dumps(raw).encode(), "application/json")

    with pytest.raises(GhimeraRefused, match="semantic_extraction_failed"):
        exercise(cfg, WrongNativeWire(cfg.models.analyst), (document(),))


@pytest.mark.parametrize("bad", ("wrong_surface", "unknown_citation"))
def test_hallucinated_entities_and_citations_refuse_before_semantic_graph_write(tmp_path, bad):
    cfg = configured(tmp_path)
    wire = SemanticWire(cfg.models.analyst, **{bad: True})

    async def refused():
        doc, ledger = document(), Ledger()
        graph = ResearchGraph(cfg.graph, "refused", MemoryGraphSink())
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        before = graph.snapshot()
        stage = SemanticStage(cfg, SelfHostedModel(cfg, cfg.models.analyst, http=wire))
        with pytest.raises(GhimeraRefused, match="semantic_extraction_failed"):
            await stage.extract(
                "map the organization", doc, identity, graph, RunBudget(cfg, time.monotonic), ledger
            )
        assert graph.snapshot() == before
        assert len(ledger.snapshot()) == 1 and ledger.snapshot()[0].refusal

    asyncio.run(refused())
    assert len(wire.requests) == 1


def test_real_owned_docx_enters_semantic_stage_and_replays_native_claims(tmp_path):
    from ghimera.documents import DocumentExtractor
    from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.graph import DirectoryGraphSink
    from ghimera.journal import read_journal
    from ghimera.loop import GoalLoop
    from ghimera.models import Goal, Harvest
    from tests.test_document_extraction import config as doc_config
    from tests.test_document_extraction import docx
    from tests.test_local_inputs import recipe, seed

    data = configured(tmp_path).model_dump()
    data.update(
        document_extraction=doc_config(tmp_path).document_extraction,
        local_inputs=recipe(tmp_path),
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=1_000_000,
            max_journal_bytes=10_000_000,
            max_summary_bytes=1_000_000,
            max_records=1000,
        ),
    )
    cfg = GhimeraConfig.model_validate(data)
    raw = docx("甲委員會隸屬乙委員會。")
    path = tmp_path / "organization.docx"
    path.write_bytes(raw)
    wire = SemanticWire(cfg.models.analyst)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
        semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=wire),
    )

    async def run():
        session = await loop.open(Goal(text="map the organization"), run_id="organization-1")
        await loop.import_local(session, (seed(path, raw),))
        return loop.finish(session, "frontier_empty")

    harvest = asyncio.run(run())
    assert harvest.receipt.fetches == 0 and len(harvest.documents) == 1
    assert len([edge for edge in harvest.graph.edges if edge.rule == "reports_to"]) == 1
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    assert read_journal(cfg.journal, "organization-1").rows == harvest.ledger
    assert asyncio.run(DirectoryGraphSink(cfg.graph, "organization-1").replay())
    broken = harvest.model_dump()
    row = next(item for item in broken["ledger"] if item["event"] == "semantic")
    entity = row["semantic_window"]["entities"][0]
    entity["node"]["label"] = entity["evidence"]["quote"] = "丙委員會"
    with pytest.raises(ValidationError):
        Harvest.model_validate(broken)


def test_cancelling_a_model_call_retains_spend_without_semantic_graph_writes(tmp_path):
    cfg = configured(tmp_path)

    async def run():
        ready = asyncio.Event()

        class BlockingWire(SemanticWire):
            async def post(self, body):
                ready.set()
                await asyncio.Event().wait()

        wire = BlockingWire(cfg.models.analyst)
        graph, ledger, doc = (
            ResearchGraph(cfg.graph, "cancelled", MemoryGraphSink()),
            Ledger(),
            document(),
        )
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        before = graph.snapshot()
        stage = SemanticStage(cfg, SelfHostedModel(cfg, cfg.models.analyst, http=wire))
        budget = RunBudget(cfg, time.monotonic)
        task = asyncio.create_task(
            stage.extract("map the organization", doc, identity, graph, budget, ledger)
        )
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert graph.snapshot() == before
        assert budget.semantic_calls == budget.judge_calls == 1
        row = ledger.snapshot()[0]
        assert row.refusal and row.model_call.outcome == "cancelled"
        assert row.model_call.request_sha256 != hashlib.sha256(b"").hexdigest()

    asyncio.run(run())


def test_below_threshold_relations_are_retained_but_not_promoted(tmp_path):
    cfg = configured(tmp_path)
    snapshot, rows, _ = exercise(
        cfg, SemanticWire(cfg.models.analyst, confidence=0.2), (document(),)
    )
    assert not any(edge.rule == "reports_to" for edge in snapshot.edges)
    held = rows[0].semantic_window.held_edges
    assert len(held) == 1 and held[0].confidence == 0.2


def test_schema_and_unknown_ontology_are_rejected_before_model_calls(tmp_path):
    with pytest.raises(ValidationError):
        configured(tmp_path, entity_roles=["unknown"])
    with pytest.raises(ValidationError):
        configured(tmp_path, relation_rules=["retrieved"])
    with pytest.raises(ValidationError):
        SemanticConfig.model_validate(dict(schema="ghimera.semantics/99"))


def test_window_limits_record_omitted_text_and_share_the_run_model_budget(tmp_path):
    cfg = configured(tmp_path, window_chars=16, max_windows_per_document=1)
    doc = document("甲委員會隸屬乙委員會。" + "後文" * 100)
    _, rows, budget = exercise(cfg, SemanticWire(cfg.models.analyst), (doc,))
    assert rows[0].semantic_window.omitted_chars == len(doc.extracted.text) - 16
    assert budget.judge_calls == 1 and budget.semantic_calls == 1
    limited = configured(tmp_path, max_calls_per_run=1)
    wire = SemanticWire(limited.models.analyst)
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        exercise(limited, wire, (document(), document(url="https://example.org/b")))
    assert len(wire.requests) == 1


def test_cancellation_during_graph_ack_retains_the_acknowledged_projection(tmp_path):
    cfg = configured(tmp_path)

    async def run():
        ready, release = asyncio.Event(), asyncio.Event()

        class SlowAck(MemoryGraphSink):
            async def append(self, batch):
                if any(node.role == "entity" for node in batch.nodes):
                    ready.set()
                    await release.wait()
                return await super().append(batch)

        graph, ledger, doc = ResearchGraph(cfg.graph, "slow-ack", SlowAck()), Ledger(), document()
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        client = SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst))
        stage = SemanticStage(cfg, client)
        task = asyncio.create_task(
            stage.extract(
                "map the organization",
                doc,
                identity,
                graph,
                RunBudget(cfg, time.monotonic),
                ledger,
            )
        )
        await ready.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = ledger.snapshot()[0]
        assert row.refusal is None and row.semantic_window
        assert all(edge in graph.snapshot().edges for edge in row.semantic_window.edges)
        assert all(node in graph.snapshot().nodes for node in row.semantic_window.nodes)

    asyncio.run(run())


def test_wrong_bound_role_refuses_before_sending_native_evidence(tmp_path):
    data = configured(tmp_path).model_dump()
    data["models"]["reviewer"]["model_id"] = "different-reviewer"
    cfg = GhimeraConfig.model_validate(data)
    wire = SemanticWire(cfg.models.reviewer)
    client = SelfHostedModel(cfg, cfg.models.reviewer, http=wire)
    doc = document()
    with pytest.raises(GhimeraRefused, match="semantic_extraction_failed"):
        asyncio.run(client.semantic_extract("map", doc, 0, len(doc.extracted.text), cfg.semantics))
    assert not wire.requests
