"""Validate the historical/current boundary without relaxing fresh-source checks."""

from ghimera.config import GhimeraConfig
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.models import Harvest, LedgerRow, RetainedOriginal


def validate_original(config: GhimeraConfig, item: RetainedOriginal) -> None:
    policy = config.research.retained_evidence if config.research else None
    if policy is None or config.graph is None or not config.graph.enabled:
        raise ValueError("retained graph requires its configured reader and enabled graph")
    reader = policy.reader
    if (
        item.origin.corpus_id != reader.corpus_id
        or item.origin.config_sha256 != reader.corpus_config_sha256
        or item.encoding_call.service != reader.query_encoder
        or len(item.query_text) > reader.max_query_chars
        or len(item.document.model_dump_json().encode()) > reader.max_original_bytes
    ):
        raise ValueError("retained original must bind the configured current query reader")


def validate_harvest(harvest: Harvest) -> None:
    originals = harvest.retained_sources
    rows = tuple(row for row in harvest.ledger if row.retained_source is not None)
    nodes = (
        tuple(node for node in harvest.graph.nodes if node.retained_source is not None)
        if harvest.graph
        else ()
    )
    failures = tuple(row for row in harvest.ledger if row.retained_failure is not None)
    if (harvest.schema_version == "chimera.harvest/2") != bool(originals):
        raise ValueError("retained originals require the explicit versioned harvest boundary")
    if not originals:
        if rows or nodes or failures:
            raise ValueError("retained graph origins cannot exist without their original capsules")
        return
    config = harvest.receipt.effective_config
    policy = config.research.retained_evidence if config.research else None
    if policy is None or config.graph is None or not config.graph.enabled or harvest.graph is None:
        raise ValueError("retained graph requires its configured reader and enabled graph")
    by_origin = {item.origin.content_digest(): item for item in originals}
    if (
        len(by_origin) != len(originals)
        or len({item.origin.document_sha256 for item in originals}) != len(originals)
        or len(originals) > policy.max_source_documents
        or sum(len(item.document.model_dump_json().encode()) for item in originals)
        > policy.max_snapshot_bytes
        or len(rows) != len(originals)
        or len(nodes) != len(originals)
        or {row.retained_source.content_digest() for row in rows if row.retained_source}
        != by_origin.keys()
        or {node.retained_source.content_digest() for node in nodes if node.retained_source}
        != by_origin.keys()
    ):
        raise ValueError("retained graph must account for every distinct admitted original")
    graph = ResearchGraph(config.graph, harvest.graph.run_id, MemoryGraphSink())
    for failure in failures:
        origin = failure.retained_failure
        item = by_origin.get(origin.content_digest()) if origin is not None else None
        if item is None or failure != LedgerRow(
            sequence=failure.sequence,
            event="refusal",
            url=item.document.url,
            refusal=failure.refusal,
            reason="retained_semantics_refused",
            retained_failure=origin,
        ):
            raise ValueError("retained refusal must name its exact admitted original")
    for item in originals:
        origin, document = item.origin, item.document
        validate_original(config, item)
        row = next(row for row in rows if row.retained_source == origin)
        admission_sequence = row.sequence
        if any(
            failure.retained_failure == origin and failure.sequence <= admission_sequence
            for failure in failures
        ):
            raise ValueError("retained refusals must follow their original's admission")
        if row != LedgerRow(
            sequence=row.sequence,
            event="retained_source",
            url=document.url,
            reason="retained_original_admitted",
            retained_source=origin,
        ):
            raise ValueError("admission cannot smuggle old work into the current ledger")
        reading = document.extracted.pdf_transcription
        expected = graph.document_node(
            document.url,
            document.raw,
            document.extracted.text,
            item.revision,
            transport=document.transport,
            local_input=document.local_input,
            human_browser=document.human_browser,
            source_refresh=document.source_refresh,
            pdf_reading=reading.graph_reading() if reading else None,
            retained_source=origin,
        )
        actual = next(node for node in nodes if node.retained_source == origin)
        if actual != expected:
            raise ValueError(
                "retained graph node must preserve the exact original reading and provenance"
            )
        if any(
            observation.graph_document_id == actual.id and row.sequence <= admission_sequence
            for row in harvest.ledger
            for observation in (row.semantic_window or row.semantic_refusal,)
            if observation is not None
        ):
            raise ValueError("current semantic calls must follow the original's admission")
