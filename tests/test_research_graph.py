"""Graph acceptance: durable early graph, vocabulary, evidence, replay and failures."""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from chimera.config import ChimeraConfig
from chimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from chimera.fetch import FetchLadder
from chimera.graph import DirectoryGraphSink, MemoryGraphSink, ResearchGraph
from chimera.graph_types import GraphConfig, GraphEvidence
from chimera.loop import GoalLoop
from chimera.models import Goal, Scope
from chimera.refusals import ChimeraRefused


def policy(path: Path, **updates: object) -> GraphConfig:
    raw: dict[str, object] = {
        "schema": "chimera.graph-config/1",
        "enabled": True,
        "profile": "research",
        "profile_version": "1",
        "identity_namespace": "test",
        "projection_mode": "research",
        "sink_path": str(path),
        "max_batch_bytes": 100_000,
        "max_nodes": 100,
        "max_edges": 100,
        "capture_semantics": True,
        "semantic_min_confidence": 0.8,
        "source_audiences": ["owner"],
        "handling_labels": ["research"],
        "roles": [
            {"name": "intent", "kind": "research_intent"},
            {"name": "source", "kind": "source"},
            {"name": "document", "kind": "content"},
            {"name": "entity", "kind": "entity"},
        ],
        "relations": [
            {
                "name": "discovered",
                "predicate": "discovered_from",
                "source_roles": ["source"],
                "target_roles": ["intent", "document"],
                "semantic": False,
            },
            {
                "name": "retrieved",
                "predicate": "retrieved_from",
                "source_roles": ["document"],
                "target_roles": ["source"],
                "semantic": False,
            },
            {
                "name": "mentions",
                "predicate": "mentions",
                "source_roles": ["document"],
                "target_roles": ["entity"],
                "semantic": True,
            },
        ],
    }
    raw.update(updates)
    return GraphConfig.model_validate(raw)


@pytest.mark.parametrize("disk", [False, True])
def test_incremental_ack_replay_and_idempotence(path_factory, disk):
    path = path_factory
    cfg = policy(path)
    sink = DirectoryGraphSink(cfg, "run-1") if disk else MemoryGraphSink()

    async def scenario():
        graph = ResearchGraph(cfg, "run-1", sink)
        await graph.start("find ports")
        assert len(graph.snapshot().nodes) == 1
        await graph.discovered("https://example.org/a", graph.intent_id)
        await graph.document("https://example.org/a", b"raw", "Port A opened.", "extractor@1")
        before = graph.snapshot()
        await graph.discovered("https://example.org/a", graph.intent_id)
        await graph.document("https://example.org/a", b"raw", "Port A opened.", "extractor@1")
        assert graph.snapshot() == before
        restored = ResearchGraph(cfg, "run-1", sink)
        await restored.start("find ports")
        assert restored.snapshot() == before
        return graph, before

    graph, snapshot = asyncio.run(scenario())
    assert len(snapshot.nodes) == 3 and len(snapshot.edges) == 2
    assert all(node.audiences == ("owner",) for node in snapshot.nodes)
    assert snapshot.checkpoint is not None
    assert graph.config_digest == snapshot.config_digest


@pytest.fixture
def path_factory(tmp_path):
    return tmp_path / "graph"


def test_configuration_and_citation_refuse_before_write(tmp_path):
    cfg = policy(tmp_path / "g")
    with pytest.raises(ValidationError):
        policy(tmp_path / "g", roles=[{"name": "intent", "kind": "intent"}])
    with pytest.raises(ValidationError):
        policy(tmp_path / "g", source_audiences=[])
    with pytest.raises(ValidationError):
        policy(tmp_path / "g", projection_mode="taipan")

    async def scenario():
        graph = ResearchGraph(cfg, "run-1", MemoryGraphSink())
        await graph.start("ports")
        doc_id = await graph.document(
            "https://example.org/a", b"raw", "Port A opened.", "extractor@1"
        )
        entity = graph.node("entity", "entity-1", "Port A", "judge@1")
        await graph.append(nodes=(entity,))
        before = graph.snapshot()
        doc = next(node for node in before.nodes if node.id == doc_id)
        citation = GraphEvidence(
            document_id=doc_id,
            document_sha256=doc.content_sha256,
            text_sha256=doc.text_sha256,
            start=0,
            end=6,
            quote="Port A",
        )
        edge = graph.edge(
            "mentions", doc_id, entity.id, "judge@1", evidence=(citation,), confidence=0.9
        )
        await graph.append(edges=(edge,))
        assert len(graph.snapshot().edges) == len(before.edges) + 1
        after = graph.snapshot()
        for bad in (
            graph.edge("mentions", doc_id, entity.id, "judge@1", confidence=0.9),
            graph.edge(
                "mentions", doc_id, graph.intent_id, "judge@1", evidence=(citation,), confidence=0.9
            ),
            graph.edge(
                "mentions", doc_id, entity.id, "judge@1", evidence=(citation,), confidence=0.1
            ),
            graph.edge(
                "mentions",
                doc_id,
                entity.id,
                "judge@1",
                evidence=(citation.model_copy(update={"quote": "other!"}),),
                confidence=0.9,
            ),
        ):
            with pytest.raises(ChimeraRefused, match="graph_contract"):
                await graph.append(edges=(bad,))
            assert graph.snapshot() == after

    asyncio.run(scenario())


def test_corrupt_disk_or_different_profile_never_looks_complete(tmp_path):
    cfg = policy(tmp_path / "g")

    async def scenario():
        graph = ResearchGraph(cfg, "run-1", DirectoryGraphSink(cfg, "run-1"))
        await graph.start("ports")
        return graph

    asyncio.run(scenario())
    changed = cfg.model_copy(update={"profile_version": "2"})
    with pytest.raises(ChimeraRefused, match="graph_contract"):
        asyncio.run(
            ResearchGraph(changed, "run-1", DirectoryGraphSink(changed, "run-1")).start("ports")
        )
    record = next((tmp_path / "g" / "run-1").glob("*.json"))
    record.write_text("truncated", encoding="utf-8")
    with pytest.raises(ChimeraRefused, match="graph_sink_failed"):
        asyncio.run(ResearchGraph(cfg, "run-1", DirectoryGraphSink(cfg, "run-1")).start("ports"))


def test_goal_loop_graph_before_fetch_and_disabled_no_writes(tmp_path):
    class InspectRoute(FakeRoute):
        name = "inspect"

        async def attempt(self, request):
            records = tuple((tmp_path / "g" / "run-1").glob("*.json"))
            assert records, "intent graph must be durable before network"
            return await super().attempt(request)

    cfg = ChimeraConfig.from_toml(Path("examples/chimera.toml"))
    cfg = cfg.model_copy(update={"graph": policy(tmp_path / "g"), "page_budget": 1})
    goal = Goal(text="ports", seeds=("https://example.org/start",))
    scope = Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",))
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((InspectRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    harvest = asyncio.run(loop.run(goal, scope, run_id="run-1"))
    assert harvest.graph is not None and len(harvest.graph.nodes) >= 3
    assert harvest.model_validate_json(harvest.model_dump_json()) == harvest
    off = cfg.model_copy(update={"graph": policy(tmp_path / "off", enabled=False)})
    disabled = GoalLoop(
        config=off,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = asyncio.run(disabled.run(goal, scope))
    assert result.graph is None and result.ledger
    assert not (tmp_path / "off").exists()


def test_sink_failure_and_bad_ack_never_advance_checkpoint(tmp_path):
    class BadSink(MemoryGraphSink):
        async def append(self, batch):
            return (await super().append(batch)).model_copy(update={"sequence": 100})

    async def scenario():
        graph = ResearchGraph(policy(tmp_path / "g"), "run-1", BadSink())
        with pytest.raises(ChimeraRefused, match="graph_sink_failed"):
            await graph.start("ports")
        assert graph.snapshot().nodes == () and graph.snapshot().checkpoint is None

    asyncio.run(scenario())


def test_example_configuration_changes_vocabulary_without_python(tmp_path):
    example = GraphConfig.from_toml(Path("examples/research-graph.toml"))
    raw = example.model_dump(mode="json", by_alias=True)
    raw["sink_path"] = str(tmp_path / "g")
    raw["roles"][0]["kind"] = "research_request"
    raw["relations"][0]["predicate"] = "found_via"
    cfg = GraphConfig.model_validate(raw)

    async def scenario():
        graph = ResearchGraph(cfg, "run-1", MemoryGraphSink())
        await graph.start("ports")
        await graph.discovered("https://example.org/a", graph.intent_id)
        snapshot = graph.snapshot()
        assert snapshot.nodes[0].kind == "research_request"
        assert snapshot.edges[0].predicate == "found_via"

    asyncio.run(scenario())


def test_concurrent_observations_checkpoint_order_and_resource_bound(tmp_path):
    cfg = policy(tmp_path / "g", max_nodes=4)

    async def scenario():
        graph = ResearchGraph(cfg, "run-1", DirectoryGraphSink(cfg, "run-1"))
        await graph.start("ports")
        await asyncio.gather(
            *(graph.discovered(f"https://example.org/{n}", graph.intent_id) for n in range(3))
        )
        before = graph.snapshot()
        assert len(before.nodes) == 4 and len(before.edges) == 3
        with pytest.raises(ChimeraRefused, match="graph_contract"):
            await graph.discovered("https://example.org/extra", graph.intent_id)
        assert graph.snapshot() == before
        restored = ResearchGraph(cfg, "run-1", DirectoryGraphSink(cfg, "run-1"))
        await restored.start("ports")
        assert restored.snapshot() == before

    asyncio.run(scenario())


def test_cancellation_waits_for_exact_ack_then_is_replayable(tmp_path):
    class DelayedSink(MemoryGraphSink):
        def __init__(self):
            super().__init__()
            self.writing = asyncio.Event()
            self.release = asyncio.Event()

        async def append(self, batch):
            if batch.sequence > 0:
                self.writing.set()
                await self.release.wait()
            return await super().append(batch)

    async def scenario():
        sink = DelayedSink()
        cfg = policy(tmp_path / "g")
        graph = ResearchGraph(cfg, "run-1", sink)
        await graph.start("ports")
        task = asyncio.create_task(graph.discovered("https://example.org/a", graph.intent_id))
        await sink.writing.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        sink.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(graph.snapshot().nodes) == 2
        restored = ResearchGraph(cfg, "run-1", sink)
        await restored.start("ports")
        assert restored.snapshot() == graph.snapshot()

    asyncio.run(scenario())
