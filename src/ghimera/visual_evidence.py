"""One derived visual reading for graph, answer templates and source validation.

OCR observations and reviewed claim text share retained region anchors, while
their basis stays distinct. No OCR string implies a diagram relationship.
"""

import hashlib

from ghimera.graph import ResearchGraph
from ghimera.graph_types import (
    GraphEvidence,
    GraphVisualReading,
    GraphVisualSpan,
    VisualProjectionConfig,
)
from ghimera.visual_types import ImageEvidence


def image_reading(image: ImageEvidence) -> GraphVisualReading | None:
    image = ImageEvidence.model_validate_json(image.model_dump_json())
    spans: list[GraphVisualSpan] = []
    cursor = 0
    for ocr in image.ocr.spans:
        spans.append(
            GraphVisualSpan(
                start=cursor,
                end=cursor + len(ocr.text),
                quote=ocr.text,
                basis="image_ocr",
                regions=(ocr.region,),
            )
        )
        cursor += len(ocr.text) + 2
    if image.interpretation is not None:
        for claim in image.interpretation.claims:
            spans.append(
                GraphVisualSpan(
                    start=cursor,
                    end=cursor + len(claim.text),
                    quote=claim.text,
                    basis="reviewed_visual_claim",
                    regions=claim.regions,
                )
            )
            cursor += len(claim.text) + 2
    if not spans:
        return None
    crop = image.candidate.pdf_crop
    return GraphVisualReading(
        schema="ghimera.graph-visual-reading/1",
        image_sha256=image.sha256,
        parent_sha256=image.candidate.parent_sha256,
        parent_url=image.candidate.parent_url,
        source_url=image.final_url,
        config_sha256=image.config_sha256,
        ocr_sha256=hashlib.sha256(image.ocr.model_dump_json().encode()).hexdigest(),
        interpretation_sha256=(
            hashlib.sha256(image.interpretation.model_dump_json().encode()).hexdigest()
            if image.interpretation is not None
            else None
        ),
        text="\n\n".join(span.quote for span in spans),
        spans=tuple(spans),
        pdf_page_index=crop.page_index if crop is not None else None,
        pdf_region=crop.region if crop is not None else None,
    )


def graph_visual_readings(images: tuple[ImageEvidence, ...]) -> tuple[GraphVisualReading, ...]:
    return tuple(reading for image in images if (reading := image_reading(image)) is not None)


async def project_visuals(
    graph: ResearchGraph,
    document_id: str,
    policy: VisualProjectionConfig,
) -> None:
    """Expose retained visual observations through the existing graph transaction owner."""
    if policy != graph.visual_projection:
        raise ValueError("visual projection must bind the effective graph policy")
    document = next((node for node in graph.snapshot().nodes if node.id == document_id), None)
    if document is None or document.role != "document":
        raise ValueError("visual projection requires an acknowledged parent document")
    for reading in document.visual_readings:
        if (
            len(reading.spans) > policy.max_spans_per_image
            or len(reading.text) > policy.max_reading_chars
        ):
            raise ValueError("visual reading exceeds its configured projection bound")
        nodes = tuple(
            graph.node(
                policy.observation_role,
                f"{document_id}:{reading.content_digest()}:{index}",
                span.quote,
                "ghimera.visual-observation/1",
            )
            for index, span in enumerate(reading.spans)
        )
        edges = tuple(
            graph.edge(
                policy.evidence_rule,
                document_id,
                node.id,
                "ghimera.visual-observation/1",
                evidence=(GraphEvidence.from_visual(document_id, reading, index),),
            )
            for index, node in enumerate(nodes)
        )
        await graph.append(nodes=nodes, edges=edges)
