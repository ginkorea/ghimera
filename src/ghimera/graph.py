"""Incremental graph with content-grounded edges and acknowledged durable batches.

The graph owns validation/order/state. A sink owns storage only. No graph
checkpoint becomes visible until the exact immutable batch is acknowledged.
"""

import asyncio
import hashlib
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from ghimera.graph_types import (
    GraphBatch,
    GraphCheckpoint,
    GraphConfig,
    GraphEdge,
    GraphEvidence,
    GraphIdentityDecision,
    GraphNode,
    GraphPdfReading,
    GraphRetainedOrigin,
    GraphSnapshot,
    GraphVisualReading,
    VisualProjectionConfig,
)
from ghimera.human_browser_types import BrowserSourceEvidence
from ghimera.identity_resolution import (
    IdentityResolutionView,
    resolve_identities,
    validate_decisions,
)
from ghimera.local_input_types import LocalInputEvidence
from ghimera.owned_worker import off_loop
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_refresh_types import SourceRefreshUse
from ghimera.transport_types import TransportEvidence


class GraphSink(Protocol):
    async def append(self, batch: GraphBatch) -> GraphCheckpoint: ...

    async def replay(self) -> tuple[GraphBatch, ...]: ...


class MemoryGraphSink:
    """Explicit volatile sink for embedded callers and conformance tests."""

    def __init__(self) -> None:
        self._batches: list[GraphBatch] = []

    async def append(self, batch: GraphBatch) -> GraphCheckpoint:
        if batch.sequence < len(self._batches):
            if self._batches[batch.sequence] != batch:
                raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
        elif batch.sequence == len(self._batches):
            self._batches.append(batch)
        else:
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
        return GraphCheckpoint(sequence=batch.sequence, digest=batch.content_digest())

    async def replay(self) -> tuple[GraphBatch, ...]:
        return tuple(self._batches)


class DirectoryGraphSink:
    """One atomic immutable JSON file per transaction, fsynced before ack.

    No overwrite of an existing record. Crash-left temporary files are ignored;
    missing, altered or reordered committed files refuse replay. This is a single
    run writer, not a multi-process distributed graph database.
    """

    def __init__(self, config: GraphConfig, run_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        self._config = config
        self._run_id = run_id
        self._path = config.sink_path / run_id

    def _check_directory(self) -> None:
        # Refuse symlink redirection, including existing ancestors.
        if any(path.is_symlink() for path in (self._path, *self._path.parents)):
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)

    def _write(self, batch: GraphBatch) -> GraphCheckpoint:
        data = batch.canonical_bytes()
        if (
            batch.run_id != self._run_id
            or batch.config_digest != self._config.content_digest()
            or len(data) > self._config.max_batch_bytes
        ):
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
        self._check_directory()
        self._path.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._path / f"{batch.sequence:012d}.json"
        if target.is_symlink():
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
        if target.exists():
            if target.read_bytes() != data:
                raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
        else:
            fd, name = tempfile.mkstemp(prefix=".pending-", dir=self._path)
            staging = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(staging, target)
                except FileExistsError:
                    if target.is_symlink() or target.read_bytes() != data:
                        raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED) from None
            finally:
                staging.unlink(missing_ok=True)
        directory_fd = os.open(self._path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return GraphCheckpoint(sequence=batch.sequence, digest=batch.content_digest())

    async def append(self, batch: GraphBatch) -> GraphCheckpoint:
        try:
            return await off_loop(lambda: self._write(batch))
        except OSError:
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED) from None

    def _read(self) -> tuple[GraphBatch, ...]:
        self._check_directory()
        if not self._path.exists():
            return ()
        files = sorted(self._path.glob("*.json"))
        decision_limit = (
            self._config.identity_resolution.max_decisions
            if self._config.identity_resolution
            else 0
        )
        if len(files) > self._config.max_nodes + self._config.max_edges + decision_limit:
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
        result: list[GraphBatch] = []
        for sequence, path in enumerate(files):
            if (
                path.name != f"{sequence:012d}.json"
                or path.is_symlink()
                or path.stat().st_size > self._config.max_batch_bytes
            ):
                raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
            result.append(GraphBatch.model_validate_json(path.read_bytes()))
        return tuple(result)

    async def replay(self) -> tuple[GraphBatch, ...]:
        try:
            return await off_loop(self._read)
        except (OSError, ValidationError):
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED) from None


class ResearchGraph:
    def __init__(self, config: GraphConfig, run_id: str, sink: GraphSink) -> None:
        if not config.enabled or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        self._config = config
        self._run_id = run_id
        self._sink = sink
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, GraphEdge] = {}
        self._checkpoint: GraphCheckpoint | None = None
        self._identity_decisions: dict[str, GraphIdentityDecision] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._intent_id: str | None = None

    @property
    def config_digest(self) -> str:
        return self._config.content_digest()

    @property
    def visual_projection(self) -> VisualProjectionConfig | None:
        return self._config.visual_projection

    @property
    def intent_id(self) -> str:
        if self._intent_id is None:
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        return self._intent_id

    def snapshot(self) -> GraphSnapshot:
        return GraphSnapshot(
            schema="chimera.research-graph/1",
            run_id=self._run_id,
            config_digest=self.config_digest,
            nodes=tuple(self._nodes.values()),
            edges=tuple(self._edges.values()),
            checkpoint=self._checkpoint,
            identity_decisions=tuple(self._identity_decisions.values()),
        )

    def identity_view(self, *, as_of: date | None = None) -> IdentityResolutionView:
        return resolve_identities(
            tuple(self._nodes.values()), tuple(self._identity_decisions.values()), as_of=as_of
        )

    def _identity(self, role: str, identity: str) -> str:
        components = (self._config.identity_namespace, role, identity)
        encoded = "".join(f"{len(value.encode('utf-8'))}:{value}" for value in components)
        return f"{role}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"

    def node(self, role: str, identity: str, label: str, revision: str) -> GraphNode:
        kind = next((item.kind for item in self._config.roles if item.name == role), None)
        if kind is None:
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        return GraphNode(
            id=self._identity(role, identity),
            role=role,
            kind=kind,
            identity=identity,
            label=label,
            revision=revision,
            audiences=self._config.source_audiences,
            handling_labels=self._config.handling_labels,
        )

    def edge(
        self,
        rule: str,
        source: str,
        target: str,
        revision: str,
        *,
        evidence: tuple[GraphEvidence, ...] = (),
        confidence: float | None = None,
        claim_status: Literal["model_asserted"] | None = None,
        model_request_sha256: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> GraphEdge:
        relation = next((item for item in self._config.relations if item.name == rule), None)
        if relation is None:
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        # Evidence/revision are part of a claim version, never in-place overwrites.
        skeleton = GraphEdge(
            id="edge:" + "0" * 64,
            rule=rule,
            predicate=relation.predicate,
            source=source,
            target=target,
            revision=revision,
            audiences=self._config.source_audiences,
            handling_labels=self._config.handling_labels,
            confidence=confidence,
            evidence=evidence,
            claim_status=claim_status,
            model_request_sha256=model_request_sha256,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        return skeleton.model_copy(update={"id": "edge:" + skeleton.content_digest()})

    async def start(self, intent: str, *, expected: GraphSnapshot | None = None) -> None:
        async with self._lock:
            if self._started:
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            for batch in await self._sink.replay():
                self._validate_batch(batch)
                self._apply(batch)
            self._started = True
        initial = self.node("intent", self._run_id, intent, self._config.profile_version)
        if expected is not None:
            # Restore is a verification-only step. Missing/changed graph state
            # cannot be repaired by adding a fresh intent before comparison.
            if self.snapshot() != expected or self._nodes.get(initial.id) != initial:
                raise ValueError("graph changed after the checkpoint; reconcile first")
        else:
            await self.append(nodes=(initial,))
        self._intent_id = initial.id

    def _validate_batch(self, batch: GraphBatch) -> None:
        expected_sequence = self._checkpoint.sequence + 1 if self._checkpoint else 0
        expected_digest = self._checkpoint.digest if self._checkpoint else None
        if (
            batch.run_id != self._run_id
            or batch.config_digest != self.config_digest
            or batch.sequence != expected_sequence
            or batch.previous_digest != expected_digest
            or len(batch.canonical_bytes()) > self._config.max_batch_bytes
        ):
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        nodes = dict(self._nodes)
        for candidate in batch.nodes:
            node = GraphNode.model_validate_json(candidate.model_dump_json())
            role = next((item for item in self._config.roles if item.name == node.role), None)
            if (
                role is None
                or node.kind != role.kind
                or node.id != self._identity(node.role, node.identity)
                or node.audiences != self._config.source_audiences
                or node.handling_labels != self._config.handling_labels
                or (node.id in nodes and nodes[node.id] != node)
            ):
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            nodes[node.id] = node
        edges = dict(self._edges)
        for candidate_edge in batch.edges:
            edge = GraphEdge.model_validate_json(candidate_edge.model_dump_json())
            relation = next(
                (item for item in self._config.relations if item.name == edge.rule), None
            )
            source, target = nodes.get(edge.source), nodes.get(edge.target)
            skeleton = edge.model_copy(update={"id": "edge:" + "0" * 64})
            expected_id = "edge:" + skeleton.content_digest()
            if (
                relation is None
                or source is None
                or target is None
                or edge.predicate != relation.predicate
                or edge.id != expected_id
                or source.role not in relation.source_roles
                or target.role not in relation.target_roles
                or edge.audiences != self._config.source_audiences
                or edge.handling_labels != self._config.handling_labels
                or (edge.id in edges and edges[edge.id] != edge)
            ):
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            if relation.semantic and (
                not self._config.capture_semantics
                or not edge.evidence
                or edge.confidence is None
                or edge.confidence < self._config.semantic_min_confidence
            ):
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            if edge.claim_status is not None and not relation.semantic:
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            for evidence in edge.evidence:
                doc = nodes.get(evidence.document_id)
                if (
                    doc is None
                    or doc.role != "document"
                    or doc.text is None
                    or doc.content_sha256 is None
                    or not evidence.matches_reading(
                        doc.content_sha256,
                        doc.text,
                        pdf_reading=doc.pdf_reading,
                        visual_readings=doc.visual_readings,
                    )
                ):
                    raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            edges[edge.id] = edge
        try:
            validate_decisions(
                self._config.identity_resolution,
                tuple(nodes.values()),
                tuple(edges.values()),
                tuple(self._identity_decisions.values()),
                batch.identity_decisions,
            )
        except ValueError:
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT) from None
        if len(nodes) > self._config.max_nodes or len(edges) > self._config.max_edges:
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)

    def _apply(self, batch: GraphBatch) -> None:
        self._nodes.update((node.id, node) for node in batch.nodes)
        self._edges.update((edge.id, edge) for edge in batch.edges)
        self._identity_decisions.update((item.id, item) for item in batch.identity_decisions)
        self._checkpoint = GraphCheckpoint(sequence=batch.sequence, digest=batch.content_digest())

    async def append(
        self,
        *,
        nodes: tuple[GraphNode, ...] = (),
        edges: tuple[GraphEdge, ...] = (),
        identity_decisions: tuple[GraphIdentityDecision, ...] = (),
    ) -> None:
        async with self._lock:
            if not self._started:
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            # An identical observation is a no-op. Conflicting identity is refused.
            nodes = tuple(node for node in nodes if self._nodes.get(node.id) != node)
            edges = tuple(edge for edge in edges if self._edges.get(edge.id) != edge)
            identity_decisions = tuple(
                item for item in identity_decisions if self._identity_decisions.get(item.id) != item
            )
            if not nodes and not edges and not identity_decisions:
                return
            batch = GraphBatch(
                schema="chimera.graph-batch/1",
                run_id=self._run_id,
                config_digest=self.config_digest,
                sequence=self._checkpoint.sequence + 1 if self._checkpoint else 0,
                previous_digest=self._checkpoint.digest if self._checkpoint else None,
                nodes=nodes,
                edges=edges,
                identity_decisions=identity_decisions,
            )
            try:
                self._validate_batch(batch)
            except ValidationError:
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT) from None
            task = asyncio.create_task(self._sink.append(batch))
            cancelled = False
            try:
                ack = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Thread-backed commit may already be on disk. Finish the ack
                # before releasing the writer lock, including repeated caller
                # cancellation. Never pass cancellation into the sink task.
                cancelled = True
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                ack = task.result()
            if ack != GraphCheckpoint(sequence=batch.sequence, digest=batch.content_digest()):
                raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
            self._apply(batch)
            if cancelled:
                raise asyncio.CancelledError

    async def decide_identity(
        self,
        *,
        operation: Literal["merge", "split", "retract"],
        members: tuple[str, ...],
        evidence: tuple[GraphEvidence, ...],
        authority: str,
        revision: str,
        reason: str,
        retracts: tuple[str, ...] = (),
        valid_from: date | None = None,
        valid_to: date | None = None,
    ) -> GraphIdentityDecision:
        decision = GraphIdentityDecision(
            schema="ghimera.identity-decision/1",
            id="resolution:" + "0" * 64,
            operation=operation,
            members=tuple(sorted(members)),
            retracts=tuple(sorted(retracts)),
            evidence=evidence,
            authority=authority,
            revision=revision,
            reason=reason,
            basis="human_reviewed",
            valid_from=valid_from,
            valid_to=valid_to,
        )
        decision = decision.model_copy(update={"id": "resolution:" + decision.content_digest()})
        await self.append(identity_decisions=(decision,))
        return decision

    async def discovered(self, url: str, parent_id: str) -> str:
        source = self.node("source", url, url, self._config.profile_version)
        edge = self.edge("discovered", source.id, parent_id, self._config.profile_version)
        await self.append(nodes=(source,), edges=(edge,))
        return source.id

    def document_node(
        self,
        url: str,
        raw: bytes,
        text: str,
        revision: str,
        *,
        transport: TransportEvidence | None = None,
        local_input: LocalInputEvidence | None = None,
        human_browser: BrowserSourceEvidence | None = None,
        pdf_reading: GraphPdfReading | None = None,
        retained_source: GraphRetainedOrigin | None = None,
        source_refresh: SourceRefreshUse | None = None,
        visual_readings: tuple[GraphVisualReading, ...] = (),
    ) -> GraphNode:
        content_digest = hashlib.sha256(raw).hexdigest()
        text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Extraction changes create a new representation of the same source bytes.
        identity = GraphNode.document_identity(
            url,
            content_digest,
            text_digest,
            revision,
            human_browser=human_browser,
            pdf_reading=pdf_reading,
            retained_source=retained_source,
            source_refresh=source_refresh,
            visual_readings=visual_readings,
        )
        kind = next(role.kind for role in self._config.roles if role.name == "document")
        return GraphNode(
            id=self._identity("document", identity),
            role="document",
            kind=kind,
            identity=identity,
            label=url,
            revision=revision,
            audiences=self._config.source_audiences,
            handling_labels=self._config.handling_labels,
            source_url=url,
            content_sha256=content_digest,
            text_sha256=text_digest,
            text=text,
            transport=transport,
            local_input=local_input,
            human_browser=human_browser,
            pdf_reading=pdf_reading,
            retained_source=retained_source,
            source_refresh=source_refresh,
            visual_readings=visual_readings,
        )

    async def document(
        self,
        url: str,
        raw: bytes,
        text: str,
        revision: str,
        *,
        transport: TransportEvidence | None = None,
        local_input: LocalInputEvidence | None = None,
        human_browser: BrowserSourceEvidence | None = None,
        pdf_reading: GraphPdfReading | None = None,
        retained_source: GraphRetainedOrigin | None = None,
        source_refresh: SourceRefreshUse | None = None,
        visual_readings: tuple[GraphVisualReading, ...] = (),
    ) -> str:
        doc = self.document_node(
            url,
            raw,
            text,
            revision,
            transport=transport,
            local_input=local_input,
            human_browser=human_browser,
            pdf_reading=pdf_reading,
            retained_source=retained_source,
            source_refresh=source_refresh,
            visual_readings=visual_readings,
        )
        source = self.node("source", url, url, self._config.profile_version)
        await self.append(
            nodes=(source, doc), edges=(self.edge("retrieved", doc.id, source.id, revision),)
        )
        return doc.id
