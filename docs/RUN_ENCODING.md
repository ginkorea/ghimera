# Run-bound scoring encoding (unpublished prerequisite)

Explicit `ghimera.scoring/3` selects `ghimera.run-encoding-recovery/1` with
operator-provided per-result/total retained byte limits and `unknown_policy="hold"`.
The existing scoring call/character allowances remain the only run encoding
quotas. Scoring /1 and /2 retain their original configuration dumps, request
bytes and accounting; they reject the new field, including explicit null.
The inert [example](../examples/run-encoding.toml) is not an active endpoint.
In /3 intent mode the operator must supply an explicit query encoder in the same
declared vector space; there is no implicit passage-encoder fallback. Pinned
mode supplies its exact reference digest and has no query encoder. Prefixes are
operator declarations, not inferred model defaults. Old /1–2 admission remains
unchanged, including the explicit /2 query/passage boundary.

`EmbeddingScorer` requires its real durable journal header, original effective
recipe, goal, writer-owned committed prefix and prior native parser/reading
observation. It records complete ordered source-window coordinates, original
link input strings, source/raw/text/parser/reading hashes and the exact extracted
document digest. Each batch records its purpose, full unchanged request texts,
encoder/prefix/runtime policy, wire request hash and original charged ordinal
before contacting the injected encoder. It does not invent a corpus identity or
generation. Generated PDF readings are not silently relabelled native text.

The existing `encoding` observation retains a validated original `EncodingBatch`
(vectors plus `EncodingCall`) and its digest before returning vectors to the
scorer. Native journal capacity is checked for intent plus bounded result
envelope before contact; actual oversized or invalid results refuse and leave
the original intent charged, not a successful vector ACK. The original response
hash/telemetry is preserved; vectors are not a fabricated response body.
UNKNOWN, refusal, cancellation and lost durable writes do not authorize a retry.

For explicit local replay, inject `encoding_decisions` into `EmbeddingScorer`:
an ordered tuple of `RunEncodingDecision(mode="replay",
original_intent_sequence=...)` or `RunEncodingDecision(mode="fresh")`, covering
the entire actual batch plan. A replay entry requires that exact original run,
recipe, purpose, source/parser/reading, extracted document and request plus its
single original ACK. A repeated sequence in one plan refuses. The replay row
references the original intent/ACK and neither contacts the encoder nor adds a
charge. Creating a fresh ordinary scorer means fresh invocation, never automatic
lookup-by-content replay. Restored budgets must carry the original intent counts
and characters, including unacknowledged intents; changing the receipt refuses.
An already committed intent-reference retains its original reuse behavior.
Only opted-in /3 score transactions serialize their ordered decisions on a
run-owned scoring lock. Other collection/model stages and legacy /1–2 scoring
keep their original concurrency. Cancellation releases the locks, not a charge.

`validate_run_encoding_rows` is routed through unsealed journal reports, native
journal readback, Harvest validation and budget restoration. It scans the full
original prefix, bounded by configured journal record/byte limits; this is not
an unbounded metadata store or a new transaction/quota framework. Orphan query
vector ACKs before `intent_reference` are admitted only under this explicit
run-bound contract, not as a general relaxation of query encoder attribution.
Runtime evidence fields are excluded from legacy model-facing JSON schemas,
but remain validated and present in native stored records.

This is not automatic source-processing, graph-application or whole-session
recovery. A future source cursor must retain its exact original encoding decision
schedule and local consumer state; the current acquired-page facade still holds
at later effects. Corpus/generation-bound encoding recovery remains a separate
owner and policy. Native subprocess cuts and controlled vectors test persistence
and no-contact replay only, not real encoder quality or representative retrieval.

## Finite fixture evidence

Using `/tmp/chimera-c0-20261006/.venv/bin/python` 3.11.16, with absolute
isolated source import and the SDK absent: the tests-first run initially refused
collection because this owner did not yet exist. The first four native witnesses
passed without skips in 17.43 seconds. The bounded owner/importer selection then
reported 83 passed and one new fixture failure, without skips, in 55.81 seconds:
the fixture supplied a string instead of the existing refusal enum. Correcting
only that fixture yielded one pass without skips in 3.34 seconds on an exact
case rerun. Production source stayed frozen throughout that selection and rerun.

The finite selection covers `test_run_encoding.py`, `test_embedding_scoring.py`,
`test_run_journal.py`, selected judgment readback/legacy-wire tests, original
continuation wall-time/spend tests and the two frozen legacy semantic prompt/
schema fingerprints. Ruff/format checks cover thirteen changed Python files;
strict mypy covers twelve production owners. No full gate, installed release,
remote encoder contact, GPU or real-model quality acceptance is claimed.

Review then added two narrowly approved controls: revalidation refuses a tampered
fresh decision carrying an original sequence before contact; the tests-first
missing-query witness failed because /3 had inherited the old /1 fallback.
After the /3-only explicit query admission fix and pinned-mode control,
`test_run_encoding.py` plus two original scoring policy tests reported 30 passes
without skips in 30.48 seconds using that same interpreter/source boundary.
The full importer selection was not repeated or represented as a full gate.
