"""Behavioral witnesses for native reversible identity decisions, never similarity scores."""

import asyncio
from datetime import date

import pytest

from ghimera.graph import DirectoryGraphSink, MemoryGraphSink, ResearchGraph
from ghimera.graph_types import GraphEvidence, IdentityResolutionConfig
from ghimera.refusals import GhimeraRefused
from tests.test_research_graph import policy


def resolution_policy(tmp_path):
    return policy(tmp_path / "graph").model_copy(
        update={
            "identity_resolution": IdentityResolutionConfig(
                schema="ghimera.identity-resolution/1",
                roles=("entity",),
                max_decisions=20,
                max_members_per_decision=5,
                max_evidence_per_decision=5,
                max_reason_chars=1000,
            ),
        }
    )


async def observations(graph, labels=("海事局", "Maritime Bureau", "M. Bureau")):
    text = "海事局 is also named Maritime Bureau (M. Bureau). This name changed in 2020."
    document_id = await graph.document(
        "https://example.org/source", text.encode(), text, "native@1"
    )
    doc = next(node for node in graph.snapshot().nodes if node.id == document_id)
    evidence = GraphEvidence.from_reading(document_id, doc.content_sha256, text, 0, len(text))
    nodes = tuple(
        graph.node("entity", f"{document_id}:{index}", label, "source-mention@1")
        for index, label in enumerate(labels)
    )
    edges = tuple(
        graph.edge(
            "mentions", document_id, node.id, "native@1", evidence=(evidence,), confidence=0.9
        )
        for node in nodes
    )
    await graph.append(nodes=nodes, edges=edges)
    return nodes, evidence


def reviewed(graph, operation, members, evidence, **kwargs):
    return graph.decide_identity(
        operation=operation,
        members=members,
        evidence=(evidence,),
        authority="analyst:user-1",
        revision="review@1",
        reason="Inspected retained source identifies these native names.",
        **kwargs,
    )


@pytest.mark.parametrize("disk", (False, True))
def test_multilingual_temporal_merge_split_retract_replay(tmp_path, disk):
    cfg = resolution_policy(tmp_path)
    sink = DirectoryGraphSink(cfg, "resolution-1") if disk else MemoryGraphSink()

    async def scenario():
        graph = ResearchGraph(cfg, "resolution-1", sink)
        await graph.start("resolve organization aliases")
        nodes, evidence = await observations(graph)
        original_nodes, original_edges = graph.snapshot().nodes, graph.snapshot().edges
        members = tuple(node.id for node in nodes[:2])
        merge = await reviewed(
            graph,
            "merge",
            members,
            evidence,
            valid_from=date(2020, 1, 1),
            valid_to=date(2025, 12, 31),
        )
        assert graph.identity_view().omitted_temporal_decisions == 1
        assert all(len(item.members) == 1 for item in graph.identity_view().identities)
        assert any(
            set(item.members) == set(members)
            for item in graph.identity_view(as_of=date(2021, 1, 1)).identities
        )
        assert all(
            len(item.members) == 1
            for item in graph.identity_view(as_of=date(2019, 1, 1)).identities
        )
        split = await reviewed(graph, "split", members, evidence, retracts=(merge.id,))
        assert all(
            len(item.members) == 1
            for item in graph.identity_view(as_of=date(2021, 1, 1)).identities
        )
        await reviewed(graph, "merge", members, evidence, retracts=(split.id,))
        assert any(set(item.members) == set(members) for item in graph.identity_view().identities)
        assert graph.snapshot().nodes == original_nodes and graph.snapshot().edges == original_edges
        restored = ResearchGraph(cfg, "resolution-1", sink)
        await restored.start("resolve organization aliases")
        assert restored.snapshot() == graph.snapshot()
        assert restored.identity_view() == graph.identity_view()
        assert len(restored.snapshot().identity_decisions) == 3

    asyncio.run(scenario())


def test_homonyms_never_merge_without_explicit_review(tmp_path):
    async def scenario():
        graph = ResearchGraph(resolution_policy(tmp_path), "homonyms", MemoryGraphSink())
        await graph.start("homonyms")
        nodes, _ = await observations(graph, labels=("海事局", "海事局"))
        assert all(len(item.members) == 1 for item in graph.identity_view().identities)
        assert (
            len(
                {
                    item.representative
                    for item in graph.identity_view().identities
                    if item.representative in {node.id for node in nodes}
                }
            )
            == 2
        )

    asyncio.run(scenario())


def test_transitive_merge_cannot_override_dated_split(tmp_path):
    async def scenario():
        graph = ResearchGraph(resolution_policy(tmp_path), "split-conflict", MemoryGraphSink())
        await graph.start("identity")
        nodes, evidence = await observations(graph)
        a, b, c = (node.id for node in nodes)
        await reviewed(
            graph, "split", (a, c), evidence, valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1)
        )
        await reviewed(graph, "merge", (a, b), evidence, valid_from=date(2020, 1, 1))
        before = graph.snapshot()
        with pytest.raises(GhimeraRefused, match="graph_contract"):
            await reviewed(graph, "merge", (b, c), evidence)
        assert graph.snapshot() == before
        await reviewed(graph, "merge", (b, c), evidence, valid_from=date(2021, 1, 2))
        assert any(
            set(item.members) == {a, b, c}
            for item in graph.identity_view(as_of=date(2022, 1, 1)).identities
        )

    asyncio.run(scenario())


def test_forged_evidence_unknown_reversal_unobserved_nodes_refuse(tmp_path):
    async def scenario():
        graph = ResearchGraph(resolution_policy(tmp_path), "refusals", MemoryGraphSink())
        await graph.start("identity")
        nodes, evidence = await observations(graph)
        members = tuple(node.id for node in nodes[:2])
        for altered, kwargs in (
            (evidence.model_copy(update={"quote": "different source"}), {}),
            (evidence, {"retracts": ("resolution:" + "f" * 64,)}),
        ):
            before = graph.snapshot()
            with pytest.raises(GhimeraRefused, match="graph_contract"):
                await reviewed(graph, "merge", members, altered, **kwargs)
            assert graph.snapshot() == before
        unobserved = graph.node("entity", "unobserved", "海事局", "1")
        await graph.append(nodes=(unobserved,))
        with pytest.raises(GhimeraRefused, match="graph_contract"):
            await reviewed(graph, "merge", (unobserved.id, nodes[0].id), evidence)
        with pytest.raises(GhimeraRefused, match="graph_contract"):
            await reviewed(graph, "merge", (graph.intent_id, nodes[0].id), evidence)

    asyncio.run(scenario())


def test_idempotent_decision_and_retraction_do_not_erase_history(tmp_path):
    async def scenario():
        graph = ResearchGraph(resolution_policy(tmp_path), "reversal", MemoryGraphSink())
        await graph.start("identity")
        nodes, evidence = await observations(graph)
        members = tuple(node.id for node in nodes[:2])
        merge = await reviewed(graph, "merge", members, evidence)
        before = graph.snapshot()
        assert await reviewed(graph, "merge", members, evidence) == merge
        assert graph.snapshot() == before
        await reviewed(graph, "retract", (), evidence, retracts=(merge.id,))
        assert len(graph.snapshot().identity_decisions) == 2
        assert all(len(item.members) == 1 for item in graph.identity_view().identities)

    asyncio.run(scenario())
