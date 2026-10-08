# Durable model invocation records

Published in 0.4.9; full I03 closure remains open.

## Retained-answer development candidate

The next owned source candidate adds an explicit `[model_work.results]` policy
(`ghimera.model-results/1`), with per-answer and total original-byte bounds. See
`examples/model-results.toml`. It reuses the existing owner-private native
journal, single-writer lock and fsync; it does not introduce a second database.
Retention stores exact port-result bytes or original HTTP response bytes with
the observed status/media type inside their original acknowledgement. The
hash and size remain hashes/counts of the original bytes, not of the base64
storage envelope. Missing policy leaves 0.4.9 configuration and record
identities unchanged.

Admission reserves each outstanding call's maximum retained-answer space before
contact. Actual acknowledged sizes replace those reservations. Unknown outcomes
remain held; they are not treated as free capacity. Oversized returns are not
truncated into apparently valid results: their observation remains in the
journal, but they are refused and cannot be replayed. The journal's existing
record/total capacity still applies; a failed durable acknowledgement is fatal.
Retention bounds must leave room for their base64 encoding inside journal
bounds; actual envelope capacity is still checked by the journal writer.

`ModelInvocation(..., replay_intent_sequence=<original sequence>).replay(decode)`
binds one local read to the exact original phase, input scope/hash/size, model
identity, source URL and acknowledged output. The writer's committed prefix
must equal the restored ledger: another run's copied rows are not admission.
It does not reserve a new model call and has no service callback. Calling its
remote-invocation method instead refuses before contact. The local decoder must
validate the original result and an append-only `model_replay` observation
records the original intent/acknowledgement references. A replay observation is
not a promise that a downstream consumer applied the answer.

The actual research owner exposes `ModelCalls.replay(...)` for its four phases;
it restores the appropriate typed result without a planner/analyst/reviewer
call. Existing citation, native-text, ontology and independent-review checks
remain the consumer's responsibility. Reviewed PDF/visual ports retain actual
wire status/media type rather than inventing a successful response status.
Budget restore must match all original judge reservations before mutation;
semantic quotas are reconstructed from original phase intents, not subsequent
consumer/replay rows. A new call with a non-restored budget also refuses before
contact.

This candidate is not published or fully gated yet. Focused native acceptance
includes fresh child-process termination after answer fsync and before the
research phase applies it, followed by original-run replay without another
controlled contact. It does not constitute arbitrary whole-session adoption,
automatic retry of unknown calls, live model-quality acceptance or Chinese OCR
validation. The research loop still needs interrupted control-state adoption
to select these original sequences automatically.

A research request previously reserved a judge call only in memory. Its native
journal observation was appended in the completion handler. A controlled child
process terminating inside the injected planner could therefore leave a clean,
unsealed journal with no evidence of the already-started invocation.

`ModelInvocation` now reuses the run-owned `Ledger` and its existing sink. With
the explicit `[model_work]` policy and native `[journal]` configured, construction
requires the sink's durable capability and exact original recipe binding, checks
the input bound and unanswered-call capacity, reserves the phase budget,
and appends a typed `model_intent` before any model await. Native journal append
fsyncs before acknowledging it. Failed storage acknowledgement is fatal; it is
not a retryable model refusal or a reason to continue without persistence.

One instance can invoke its injected port at most once. An observed return
appends a `model_ack` with its original intent sequence, model identity, source
URL, output scope, digest and byte count. Exception, timeout and cancellation
retain an ended-local-call acknowledgement without inventing a response or
token usage. An explicitly parsed, rejected completion retains its actual
`ModelFailure` evidence, unlike an unanswered call. A process crash can leave
just the intent. Unobserved endings remain uncertain. The reader checks each
acknowledgement against exactly one preceding
intent and checks judge reservation ordinals against the run's original policy.

## Configuration and boundaries

Merge `examples/model-work.toml` into a recipe that also declares its private
native journal. Paths, model services, stage concurrency and retention still
belong to their existing configuration owners. `max_input_bytes` bounds the
logical port argument snapshot or exact wire request, depending on the phase.
`max_unanswered_calls` bounds unresolved reservations, including currently
in-flight calls; select it to accommodate declared model concurrency. The only
uncertain policy currently supported is `hold`, not automatic retry.

When the policy is absent, no new wire fields or invocation rows are emitted.
Historical config/ledger identities are preserved and logical model inputs are
not unnecessarily serialized. The invocation wrapper still enforces one use per
in-memory reservation; it does not manufacture durable recovery for such runs.

| Port | Intent input scope | Returned output scope |
|---|---|---|
| Plan, assessment, answer, answer review | `port_input` | `port_output` |
| Document/image-text verdict and collection grade | `port_input` | `port_output` |
| Semantic extraction and independent/partitioned review | `port_input` | `port_output` |
| Image vision/review and PDF transcription/review | `wire_request` | `wire_response` |

Port snapshots include the exact logical arguments supplied to an injected
collaborator. They are **not** claimed to be the service's HTTP request, selected
context or prompt. Existing `ModelCallEvidence` retains those separately when
the concrete service returns. Wire observations retain the original body hash.
A returned port result is not an accepted extraction, approved answer, known
token bill or evidence of model quality: the existing schema, citation,
independent-review and source-grounding checks still run afterwards.

## Inspection and continuation

`read_journal(policy, run_id).uncertain_model_calls` returns the original intent
sequences without contacting any source or model or modifying the journal.
Native completion accounting counts invocation intents once when this policy is
enabled, rather than counting both the intent and later semantic/result rows.
Budget restore refuses uncertainty instead of resetting it to zero or retrying.

This establishes an evidence-bearing prerequisite for recovery. It does **not**
yet implement whole interrupted-session adoption, acknowledged model-result
replay, evidence-backed operator reconciliation decisions, uncertain graph
transactions or retained-reader control-state restoration. Embedding/encoding
request intents and their separate budget are not yet covered. The existing
completed-round continuation remains a separate capability. No remote request
is declared unsent merely because the local process died, and no duplicate
service invocation is claimed to have been observed by the controlled witness.

## Acceptance and remaining limits

The focused candidate checks include native journal visibility inside a port,
controlled process termination before answer, cancellation/failure, storage
failure before contact and after return, duplicate/misbound acknowledgements,
policy bounds, completed research and historical disabled-policy behavior.
Protocol fixtures do not establish served-model accuracy or multilingual OCR
quality. The combined gate and independent installed-package crash/readback
acceptance passed; exact public artifacts were verified. Simplified Chinese OCR validation
remains open independently of this lifecycle work.

Candidate measurements used `/tmp/chimera-c0-20261006/.venv/bin/python`, Python
3.11.16, resolving this owned checkout's `src/ghimera`, with the platform SDK
absent by design. The broader changed-stage/importer selection passed 315 tests
without failures/skips. Subsequent durable-binding and no-status hardening
passed 30 focused model/journal checks without failures/skips. The exact frozen
final source then passed the complete gate: 1,239 tests in 1389.64 seconds, no
failures/skips, plus offline lock, Ruff, formatting and strict mypy for 178
source files. Source commit `2335afc599c27816f07d64ca91ee1133f9e585ab` retains
tree `e863a329c75fe5c1bdc8d9733751c8d15b80d0d8`. These counts are not
representative model quality or publication evidence. Tests used injected model ports and local
PDF/OCR fixtures, not an external model or actual token-spend measurement.

Independent installed acceptance exercised fresh process exits before reservation,
after durable intent, after controlled port contact and after acknowledged
return. Original pins, reservation counts and held uncertain restore survived
native journal readback under the fresh installed Python 3.11.16 runtime. It did
not test whole-session adoption or replay a model result. Complete release and
artifact evidence is in [RELEASE_049.md](RELEASE_049.md).
