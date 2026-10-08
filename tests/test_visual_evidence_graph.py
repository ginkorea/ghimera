"""Retained visual geometry survives graph replay and answer template resolution."""

import asyncio
import hashlib

import pytest

from ghimera.evidence_context import ContextSelector
from ghimera.graph import DirectoryGraphSink, ResearchGraph
from ghimera.graph_types import GraphEvidence
from ghimera.model_citations import ModelCitationResolver, citation_id
from ghimera.model_config import EvidenceContextConfig
from ghimera.models import Document, Extracted, Verdict
from ghimera.refusals import GhimeraRefused
from ghimera.research_types import AnswerDraft, Citation
from ghimera.visual_evidence import graph_visual_readings, project_visuals
from ghimera.visual_types import (
    ImageCandidate,
    ImageEvidence,
    ImageRegion,
    OcrResult,
    OcrSpan,
    VisualClaim,
    VisualInterpretation,
)
from tests.test_research_graph import policy


def image_document():
    parent, raw = b"retained original parent", b"exact accepted image bytes"
    digest = hashlib.sha256(raw).hexdigest()
    region = ImageRegion(left=0.1, top=0.2, right=0.8, bottom=0.9)
    image = ImageEvidence(
        schema="ghimera.image-evidence/1",
        candidate=ImageCandidate(
            url="https://example.org/chart.png",
            parent_url="https://example.org/p",
            parent_sha256=hashlib.sha256(parent).hexdigest(),
            element_index=0,
            caption="Ports organization chart",
            attributes="",
            declared_width=500,
            declared_height=500,
        ),
        final_url="https://example.org/chart.png",
        sha256=digest,
        raw=raw,
        config_sha256="a" * 64,
        ocr=OcrResult(
            image_sha256=digest,
            width=500,
            height=500,
            media_type="image/png",
            spans=(OcrSpan(text="海事局 Maritime Bureau", confidence=93, region=region),),
            engine_revision="test@1",
            language_pack_sha256={"eng": "b" * 64},
        ),
        interpretation=VisualInterpretation(
            schema="ghimera.visual-interpretation/1",
            image_sha256=digest,
            relevant=True,
            claims=(VisualClaim(text="Ports chart arrow links A to B.", regions=(region,)),),
            model_id="fixture-vision",
            model_revision="1",
            reviewer_model_id="fixture-review",
            reviewer_model_revision="1",
            request_sha256="1" * 64,
            response_sha256="2" * 64,
            review_request_sha256="3" * 64,
            review_response_sha256="4" * 64,
        ),
        relevance_reason="reviewed fixture",
    )
    return Document(
        url="https://example.org/p",
        raw=parent,
        sha256=hashlib.sha256(parent).hexdigest(),
        extracted=Extracted(title="Organization", text="Native text.", language="en"),
        images=(image,),
        verdict=Verdict(
            decision="accept",
            kind="source",
            publisher="example",
            language="en",
            reason="retained evidence",
        ),
    )


def graph_policy(tmp_path):
    raw = policy(tmp_path / "graph").model_dump(mode="json", by_alias=True)
    raw["relations"].append(
        {
            "name": "visual_evidence",
            "predicate": "visual_observation",
            "source_roles": ["document"],
            "target_roles": ["entity"],
            "semantic": False,
        }
    )
    raw["visual_projection"] = {
        "schema": "ghimera.visual-projection/1",
        "observation_role": "entity",
        "evidence_rule": "visual_evidence",
        "max_spans_per_image": 10,
        "max_reading_chars": 1000,
    }
    return policy(tmp_path / "graph").model_validate(raw)


def test_visual_projection_geometry_replay_and_no_inferred_semantic_relation(tmp_path):
    document = image_document()
    cfg = graph_policy(tmp_path)

    async def scenario():
        graph = ResearchGraph(cfg, "visual", DirectoryGraphSink(cfg, "visual"))
        await graph.start("ports")
        doc_id = await graph.document(
            document.url,
            document.raw,
            document.extracted.text,
            "native@1",
            visual_readings=graph_visual_readings(document.images),
        )
        await project_visuals(graph, doc_id, cfg.visual_projection)
        observations = [edge for edge in graph.snapshot().edges if edge.rule == "visual_evidence"]
        assert len(observations) == 2
        assert {edge.evidence[0].basis for edge in observations} == {
            "image_ocr",
            "reviewed_visual_claim",
        }
        assert all(edge.claim_status is None and edge.confidence is None for edge in observations)
        assert all(edge.source == doc_id for edge in observations)
        before = graph.snapshot()
        await project_visuals(graph, doc_id, cfg.visual_projection)
        assert graph.snapshot() == before
        evidence = observations[0].evidence[0]
        forged = evidence.model_copy(
            update={
                "visual_anchor": evidence.visual_anchor.model_copy(
                    update={"regions": (ImageRegion(left=0, top=0, right=1, bottom=1),)}
                )
            }
        )
        bad = graph.edge(
            "visual_evidence", doc_id, observations[0].target, "different", evidence=(forged,)
        )
        with pytest.raises(GhimeraRefused, match="graph_contract"):
            await graph.append(edges=(bad,))
        restored = ResearchGraph(cfg, "visual", DirectoryGraphSink(cfg, "visual"))
        await restored.start("ports")
        assert restored.snapshot() == before

    asyncio.run(scenario())


def test_visual_citations_context_required_review_and_native_identity():
    document = image_document()
    ocr = Citation.from_image(document, 0, 0)
    diagram = Citation.from_image(document, 0, 1)
    assert ocr.basis == "image_ocr" and diagram.basis == "reviewed_visual_claim"
    assert ocr.matches(document) and diagram.matches(document)
    assert not diagram.model_copy(update={"basis": "native", "visual_anchor": None}).matches(
        document
    )
    assert not diagram.model_copy(update={"quote": "invented arrow"}).matches(document)
    selector = ContextSelector(
        EvidenceContextConfig(
            schema="chimera.evidence-context/1",
            max_documents=2,
            max_chars=1000,
            window_chars=100,
            max_windows_per_document=5,
            overlap_chars=0,
        )
    )
    context = selector.build("Ports 海事局", (document,), required=(diagram,))
    assert any(window.citation == diagram for window in context.windows)
    assert any(window.citation == ocr for window in context.windows)
    assert all(window.citation.matches(document) for window in context.windows)
    resolver = ModelCitationResolver(tuple(window.citation for window in context.windows))
    from ghimera.model_citations import ReferencedAnswer

    referenced = ReferencedAnswer.model_validate(
        {
            "claims": [
                {
                    "text": diagram.quote,
                    "question_ids": ["q1"],
                    "citations": [{"citation_id": citation_id(diagram)}],
                }
            ],
            "confidence": 0.9,
        }
    )
    answer = AnswerDraft.model_validate_json(
        resolver.content(referenced.model_dump_json(), AnswerDraft)
    )
    assert answer.claims[0].citations == (diagram,)
    native = Citation.from_document(document, 0, len(document.extracted.text))
    assert "visual_anchor" not in native.model_dump() and "basis" not in native.model_dump()
    assert native.matches(document)


def test_visual_binding_cannot_be_relabelled_as_native_or_changed_parent(tmp_path):
    document = image_document()
    reading = graph_visual_readings(document.images)[0]
    evidence = GraphEvidence.from_visual("document:test", reading, 1)
    assert evidence.matches_reading(
        document.sha256, document.extracted.text, visual_readings=(reading,)
    )
    assert not evidence.matches_reading(
        "f" * 64, document.extracted.text, visual_readings=(reading,)
    )
    assert not evidence.matches_reading(document.sha256, document.extracted.text)
    changed = reading.model_copy(update={"interpretation_sha256": "f" * 64})
    assert not evidence.matches_reading(
        document.sha256, document.extracted.text, visual_readings=(changed,)
    )
