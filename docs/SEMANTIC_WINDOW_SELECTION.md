# Intent-ranked original semantic windows

Semantic extraction remains prefix-based unless the operator supplies the
versioned `semantics.window_selection` policy in
[`semantic-window-selection.toml`](../examples/semantic-window-selection.toml).
Existing semantics/1 through /4 profiles, prompt revisions and omitted-default
serialization are unchanged. The operational configuration schema advertises
the optional policy; additive client evidence is excluded only from frozen
model-facing JSON-schema projections, never from runtime admission.

The policy declares `strategy = "intent_ranked"`, `max_selected_chars` and
`padding_chars`. Existing `semantics.window_chars`,
`max_windows_per_document`, model context and run call budgets remain outer
bounds. Research recipes require a gap-enabled graph-planning policy. Configure
native intent scoring first; selection never silently encodes another document
or substitutes a different goal/reference set.

The shared pure selector ranks observed native similarity anchors by descending
cosine and then original start/end. Cosine is not a relevance probability.
Padding expands only into the original reading, within the explicit per-window
and union-character limits. Overlapping anchors are merged or omitted
deterministically under those bounds. Execution uses selected spans in original
source order. The model receives the unchanged full Document contract and exact
native offsets, not a fabricated document containing a rewritten subset.

Before encoder contact, the native scorer commits one exact reading through the
existing private journal, bound to the observed parser operation, source URL,
raw digest and full text digest. The score observation references that committed
reading. Semantic selection requires its exact earlier score row, acknowledged
original intent reference, original anchors and padded-slice hashes. It commits
one `semantic_selection` plan before the first semantic model intent. Native
journal limits/fsync remain the storage owner; this event consumes no judge or
encoder reservation and asserts neither model completion nor graph ACK.

Each accepted or refused attempt binds its plan digest and selected index.
Extraction/review reservations, retained native ACKs, source locks, graph
transactions and original graph evidence continue through their existing owners.
Readback replays the same selector, native source proof, attempt order and count.
The final attempt reports unselected characters as the complement of the unique
selected original spans, not merely the tail after its end. Failed selected
windows remain explicit failures, not accepted coverage. A separately typed
client selection gap exposes unselected source coverage without fabricating a
model-assessed coverage verdict. Planning context/evidence caps count its text.

Limitations are explicit. Selection can only rank anchors actually observed by
the existing bounded scorer; it cannot recover relevance outside that pool.
Missing, stale, different-source or different-goal scoring refuses before
semantic contact. Retained originals need a current exact parser/scoring chain;
historical same-text evidence is insufficient. Generated PDF transcriptions are
not relabeled as native parser text and are unsupported by this policy.

An existing selection plan cannot be restarted automatically, including after
an UNKNOWN model operation. Existing native ModelInvocation can replay a retained
return only with its exact committed input and ACK; this change does not add
automatic mid-semantic recovery or retry. Protocol fixtures establish binding,
coverage and accounting behavior, not real-model extraction quality.
