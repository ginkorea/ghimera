# Durable model invocation records

Source-complete release candidate; not yet published or full I03 closure.

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

## Acceptance still required

The focused candidate checks include native journal visibility inside a port,
controlled process termination before answer, cancellation/failure, storage
failure before contact and after return, duplicate/misbound acknowledgements,
policy bounds, completed research and historical disabled-policy behavior.
Protocol fixtures do not establish served-model accuracy or multilingual OCR
quality. The combined gate passed; independent installed-package crash/readback
acceptance is still required before publication. Simplified Chinese OCR validation
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
