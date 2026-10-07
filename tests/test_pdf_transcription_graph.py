"""Real PDF/graph persistence; scripted model replies do not prove OCR accuracy."""

import asyncio
import hashlib
import json

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.documents import DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph import DirectoryGraphSink, MemoryGraphSink, ResearchGraph
from ghimera.graph_planning import build_context, validate_context
from ghimera.graph_types import GraphEvidence, GraphNode, GraphPdfReading, GraphReadingPage
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Goal, Harvest
from ghimera.refusals import GhimeraRefused
from tests.test_document_extraction import native_pdf
from tests.test_graph_planning import planning_config
from tests.test_local_inputs import seed
from tests.test_pdf_transcription_stage import configured, stage
from tests.test_research_graph import policy


class OrganizationWire:
    def __init__(self, config):
        self.config = config

    async def post(self, body):
        request = json.loads(body)
        packet = json.loads(request["messages"][1]["content"])
        window = packet["evidence"]["windows"][0]
        assert window["citation"]["basis"] == "reviewed_pdf_transcription"
        assert window["citation"]["page_indices"] == [0]
        proposal = {
            "mentions": [
                {
                    "key": "m1",
                    "role": "entity",
                    "surface": "臺灣港務公司",
                    "citation_id": window["citation_id"],
                    "occurrence": 0,
                    "confidence": 0.95,
                }
            ],
            "relations": [],
        }
        return ModelHttpResponse(
            200,
            json.dumps(
                {
                    "id": "protocol-fixture",
                    "model": self.config.served_model,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps(proposal)},
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                }
            ).encode(),
            "application/json",
        )


async def collect(tmp_path, semantics=False):
    cfg = configured(tmp_path)
    raw_config = cfg.model_dump()
    if semantics:
        semantic_recipe = planning_config(tmp_path)
        raw_config.update(
            graph=semantic_recipe.graph,
            semantics=semantic_recipe.semantics,
            models=semantic_recipe.models,
            research=semantic_recipe.research,
        )
    else:
        raw_config["graph"] = policy(tmp_path / "graph")
    cfg = GhimeraConfig.model_validate(raw_config)
    transcriber, _, _ = stage(cfg)
    semantic_extractor = (
        SelfHostedModel(cfg, cfg.models.analyst, http=OrganizationWire(cfg.models.analyst))
        if semantics
        else None
    )
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        pdf_transcription=transcriber,
        semantic_extractor=semantic_extractor,
    )
    original = native_pdf()
    path = tmp_path / "original.pdf"
    path.write_bytes(original)
    session = await loop.open(Goal(text="ports"), run_id="reviewed-pdf-fixture")
    try:
        await loop.import_local(session, (seed(path, original, content_type="application/pdf"),))
        result = loop.finish(session, "frontier_empty")
    finally:
        session.ledger.close()
    return result


def test_collector_graph_retains_actual_reading_calls_and_disk_replay(tmp_path):
    material = asyncio.run(collect(tmp_path))
    source = material.documents[0]
    proof = source.extracted.pdf_transcription
    graph = material.graph
    node = next(n for n in graph.nodes if n.role == "document")
    reading = node.pdf_reading
    assert reading == proof.graph_reading()
    assert reading.source_sha256 == source.sha256
    assert reading.pages[0].image_sha256 == proof.pages[0].page.image_sha256
    assert (
        reading.pages[0].transcription_call_sha256
        == hashlib.sha256(proof.pages[0].calls[0].model_dump_json().encode()).hexdigest()
    )
    assert "png" not in reading.model_dump_json()
    assert Harvest.model_validate_json(material.model_dump_json()) == material
    altered = node.model_dump()
    altered["pdf_reading"]["config_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="exact representation identity"):
        GraphNode.model_validate(altered)

    async def reopen():
        cfg = material.receipt.effective_config.graph
        restored = ResearchGraph(cfg, graph.run_id, DirectoryGraphSink(cfg, graph.run_id))
        await restored.start(material.goal.text, expected=graph)
        assert restored.snapshot() == graph

    asyncio.run(reopen())
    bad = material.model_dump()
    next(n for n in bad["graph"]["nodes"] if n["role"] == "document")["pdf_reading"] = None
    with pytest.raises(ValidationError, match="actual PDF reading evidence"):
        Harvest.model_validate(bad)


def test_reviewed_graph_edges_reject_native_or_fabricated_page_provenance(tmp_path):
    material = asyncio.run(collect(tmp_path))
    doc = next(n for n in material.graph.nodes if n.role == "document")
    cfg = material.receipt.effective_config.graph

    async def operation():
        graph = ResearchGraph(cfg, "source-bound-edges", MemoryGraphSink())
        await graph.start(material.goal.text)
        identity = await graph.document(
            doc.source_url,
            material.documents[0].raw,
            doc.text,
            doc.revision,
            pdf_reading=doc.pdf_reading,
        )
        node = graph.node("entity", "port", doc.text, "fixture")
        await graph.append(nodes=(node,))
        span = GraphEvidence.from_reading(
            identity,
            doc.content_sha256,
            doc.text,
            0,
            len(doc.text),
            pdf_reading=doc.pdf_reading,
        )
        assert span.basis == "reviewed_pdf_transcription" and span.page_indices == (0,)
        edge = graph.edge(
            "mentions", identity, node.id, "fixture", evidence=(span,), confidence=0.95
        )
        await graph.append(edges=(edge,))
        before = graph.snapshot()
        for changes in (
            {"basis": "native", "page_indices": (), "reading_sha256": None},
            {"page_indices": (1,)},
            {"reading_sha256": "0" * 64},
        ):
            changed = GraphEvidence.model_validate(span.model_dump() | changes)
            assert not changed.matches_reading(
                doc.content_sha256, doc.text, pdf_reading=doc.pdf_reading
            )
            with pytest.raises(GhimeraRefused, match="graph_contract"):
                await graph.append(
                    edges=(
                        graph.edge(
                            "mentions",
                            identity,
                            node.id,
                            "fixture",
                            evidence=(changed,),
                            confidence=0.95,
                        ),
                    )
                )
            assert graph.snapshot() == before
        altered_reading = GraphPdfReading.model_validate(
            doc.pdf_reading.model_dump() | {"config_sha256": "1" * 64}
        )
        another = await graph.document(
            doc.source_url,
            material.documents[0].raw,
            doc.text,
            doc.revision,
            pdf_reading=altered_reading,
        )
        assert another != identity

    asyncio.run(operation())


def test_semantic_projection_preserves_the_selected_machine_reading(tmp_path):
    material = asyncio.run(collect(tmp_path, semantics=True))
    observations = [r.semantic_window for r in material.ledger if r.semantic_window is not None]
    assert len(observations) == 1
    span = observations[0].entities[0].evidence
    assert span.basis == "reviewed_pdf_transcription" and span.page_indices == (0,)
    graph_doc = next(n for n in material.graph.nodes if n.role == "document")
    assert span.reading_sha256 == graph_doc.pdf_reading.content_digest()
    assert all(e.basis == span.basis for edge in material.graph.edges for e in edge.evidence)
    assert Harvest.model_validate_json(material.model_dump_json()) == material
    cfg = material.receipt.effective_config
    context = build_context(cfg, material.ledger)
    assert len(context.entities) == 1
    assert context.entities[0].evidence == span
    validate_context(cfg, context, material.documents)
    forged = context.model_dump()
    forged["entities"][0]["evidence"].update(basis="native", page_indices=(), reading_sha256=None)
    with pytest.raises(GhimeraRefused, match="research_contract"):
        validate_context(cfg, type(context).model_validate(forged), material.documents)


def test_native_graph_evidence_preserves_the_existing_wire_identity():
    text = "Port A opened."
    source = hashlib.sha256(b"raw").hexdigest()
    old = GraphEvidence(
        document_id="document:" + "0" * 64,
        document_sha256=source,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        start=0,
        end=6,
        quote="Port A",
    )
    current = GraphEvidence.from_reading(old.document_id, source, text, 0, 6)
    assert current.canonical_bytes() == old.canonical_bytes()
    assert not {"basis", "page_indices", "reading_sha256"} & current.model_dump().keys()
    revision = "extractor@1"
    assert GraphNode.document_identity(
        "https://example.org", source, old.text_sha256, revision
    ) == (f"19:https://example.org:{source}:{old.text_sha256}:{revision}")


def test_multi_page_graph_spans_preserve_exact_order_and_page_boundaries():
    text = "甲部門\n\n乙部門"
    raw_digest = hashlib.sha256(b"protocol-only-source").hexdigest()
    reading = GraphPdfReading(
        schema="ghimera.graph-pdf-reading/1",
        source_sha256=raw_digest,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        config_sha256="1" * 64,
        pages=(
            GraphReadingPage(
                page_index=0,
                start=0,
                end=3,
                text_sha256=hashlib.sha256("甲部門".encode()).hexdigest(),
                image_sha256="2" * 64,
                transcription_call_sha256="3" * 64,
                review_call_sha256="4" * 64,
            ),
            GraphReadingPage(
                page_index=1,
                start=5,
                end=8,
                text_sha256=hashlib.sha256("乙部門".encode()).hexdigest(),
                image_sha256="5" * 64,
                transcription_call_sha256="6" * 64,
                review_call_sha256="7" * 64,
            ),
        ),
    )
    reading.validate_text(text)
    for start, end, expected in ((0, 3, (0,)), (5, 8, (1,)), (2, 6, (0, 1))):
        span = GraphEvidence.from_reading(
            "document:" + "8" * 64,
            raw_digest,
            text,
            start,
            end,
            pdf_reading=reading,
        )
        assert span.page_indices == expected and span.quote == text[start:end]
        assert GraphEvidence.model_validate_json(span.model_dump_json()) == span
    with pytest.raises(ValueError, match="exact selected text"):
        reading.validate_text("甲部門  乙部門")
    with pytest.raises(ValueError, match="different original"):
        GraphEvidence.from_reading(
            "document:" + "8" * 64,
            "9" * 64,
            text,
            0,
            3,
            pdf_reading=reading,
        )
    broken = reading.model_dump()
    broken["pages"] = tuple(reversed(broken["pages"]))
    with pytest.raises(ValidationError, match="complete ordered"):
        GraphPdfReading.model_validate(broken)
