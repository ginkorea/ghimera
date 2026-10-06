# Incremental research graph

The standalone package now has a real run-owned graph, not a TAIPAN graph
replacement. `ResearchGraph` owns ordering, vocabulary, identity and evidence
validation; injected `GraphSink` implementations own persistence. The memory
sink is explicitly volatile. `DirectoryGraphSink` writes immutable, atomic
JSON transactions in an operator-configured directory and fsyncs them before
acknowledging a checkpoint. Storage I/O runs off-thread.

## Configuration and lifecycle

`chimera.graph-config/1` is a frozen validated configuration, accepted as the
main TOML file's `[graph]` section. A standalone safe example is
`examples/research-graph.toml`. Put its root values under `[graph]`, its roles
under `[[graph.roles]]` and relations under `[[graph.relations]]` to use it in
the main file. No environment variables silently enable the graph. Omitting
it or setting `enabled=false` produces no graph writes; the ordinary collection
ledger remains enabled.

An enabled `GoalLoop.run(..., run_id="...")` creates and durably acknowledges
the intent node and source discovery records before its first fetch. Collected
native text, original-byte digest, text digest, URL and explicit extractor
revision are bound into document-version nodes as extraction progresses. The
graph also retains rejected documents as research evidence, not as accepted
harvest documents. Links record discovery provenance, not factual relationships.

Node kinds and predicates are configured. The required logical roles
`intent`, `source`, `document` and structural rules `discovered`, `retrieved`
are core mechanisms. Their configured kinds/predicates may change without
Python edits. Other roles and semantic predicates may be added with explicit
endpoint constraints. Identity is a length-delimited SHA-256 of namespace,
logical role and canonical identity, independent of today’s output path.
Extraction revisions/text changes create new representations, never overwrite
an existing node. Evidence-derived edges include their evidence and revision
in their identity. Identical appends are no-ops.

Semantic claims require enabled semantic capture, configured minimum
confidence, correct endpoint roles and retained-text citations. Each citation
binds a document ID, raw-byte version, native-text digest and exact nonempty
character span. Confidence cannot replace evidence. This validates that a quote
exists; it does **not** prove that the quoted text entails a model's claim.
Extraction/entailment adapters must supply and assess those claims.

## Replay and failure

Transactions are sequence-bound and hash-chained to run/configuration identity.
Nodes are validated before their edges. Replaying refuses changed configuration,
missing/reordered transactions, corrupt wire data, conflicting identities,
invalid endpoints, absent/mismatched citations or configured resource overruns.
An acknowledged checkpoint is included in the harvest's graph snapshot, along
with the effective graph-configuration digest. Directory files are private
(`0600`); run directories are created private. Existing modes are not changed.
Pending files left by a crash are not mistaken for committed transactions.

Graph failure raises `graph_contract` or `graph_sink_failed`; it cannot return
an apparently successful harvest with the required graph missing. Cancellation
during a write waits for that in-flight sink acknowledgment before releasing
the writer lock, then propagates cancellation. A run is single-writer; this
directory sink is not an inter-process graph database. Concurrent observations
within that run serialize only graph transactions, not other network calls.
Limits bound batch bytes, nodes and edges; the bounded background event queue
and sink deadline policy are still part of the research-controller work.

## Not yet complete

The [document-seeded organizational research acceptance case](ORGANIZATION_RESEARCH.md)
records the additional semantic extraction, entity resolution and persistent
graph-driven expansion needed to turn an organization PDF into a researched
network. Current discovery/document trace is not that capability.

The seeded collection loop and intent research use this graph; the intent loop
now records question/query nodes and discovery relations before search. The
configured Collector assembles that path without a custom sink. Gap/contradiction
nodes, model-backed semantic extraction, aliases/merge/split, background writer
queue, final graph/harvest file manifest and exact platform `information_graph`
projection are not yet implemented.
`projection_mode="taipan"` is therefore refused, rather than claiming arbitrary
research nodes satisfy that platform's closed vocabulary. Source audience/
handling is currently a configured run boundary; per-source governed handling
intersection and registration belong to the TAIPAN adapter and must be validated
before restricted sources or graph publication are admitted.

This is source functionality, not a publication, deployed crawler, or accepted
real-model research result.
