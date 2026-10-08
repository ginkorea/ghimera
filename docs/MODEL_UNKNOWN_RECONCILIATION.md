# Explicit research-model UNKNOWN decisions

This opt-in native capability is a caller decision, not proof that a remote
request was cancelled, unbilled, or never executed. It does not reconcile a
server's true outcome. The original invocation stays UNKNOWN in the journal
and remains charged against its original judge, unanswered-call and retained
result reservations forever.

The finite supported boundary is one intact pre-call research control snapshot
and its exact uncertain `plan`, `assessment`, `answer` or `review` invocation.
Unsealed locally ended UNKNOWN rows may accompany that original invocation;
sealed failed/cancelled outcomes are not reopened. Later unrelated effects,
torn tails, changed request/config/model/graph, active native writers and
uncertain replacement attempts remain held.

## Original opt-in policy

Select `ghimera.research-recovery/3` with an explicit
`ghimera.model-reconciliation-policy/1` containing `max_decisions_per_run` and
`max_decision_bytes`. Existing `/1` and source-only `/2` policies are unchanged.
The policy must have been in the original journal header; changing it after an
interruption cannot increase the original allowance. Native journal record and
total byte limits still apply. An inert fragment is supplied in
`examples/model-unknown-reconciliation.toml`.

The original judge allowance, wall time (including downtime), model input and
result limits, source/encoding/search spend, rounds and phase cursor are
restored, never reset. Each replacement costs one new ordinary judge
reservation. Original UNKNOWN continues occupying an unanswered/result
reservation, so the original allowance must have room for both invocations.
An exhausted original budget refuses before any contact.

## Native caller workflow

1. Read `Collector.observe_model_unknown(run_id, snapshot_sha256=pin)`, or use
   command `/5`, execution `/4`, `operation = "observe_model_unknown"`.
   `examples/collector-model-unknown-observe.toml` is inert and performs no
   inference. Observation binds the original intent, optional local ACK, exact
   journal header/tail hashes, snapshot digest and charged call count.
2. Supply `ModelReconciliationDecision` `/1` with the exact returned
   `observed` and `observed_sha256`, action
   `abandon_and_authorize_new_attempt`, a unique `operation_id`, caller and
   reason. Attribution is caller-supplied, not verified human approval.
3. Call `Collector.reconcile_model(decision)`, or command `/5` execution `/4`
   `operation = "reconcile_model"` with `model_decision`. After native
   request/output/journal/source/graph admission, one native journal append
   atomically records the decision and a separately charged replacement intent.
   It returns `ModelAttemptAuthorization` `/1`, without contacting a model,
   source, discovery provider or encoder. Duplicate/stale/changed decisions
   refuse; receipt readback is not a new authorization.
4. Pass that receipt to `Collector.recover(..., attempt=receipt)` or command
   `/5` execution `/4`, `operation = "recover"`, `model_attempt = receipt`.
   Native consumption is fsynced before the single model contact. A retained
   returned ACK may instead be replayed through its original phase schema,
   with zero additional reservation or repeat contact.

`Collector.model_attempt_history(run_id)` reads native durable receipts. It
survives a lost receipt ACK and never grants a second contact. A consumed
attempt with no proved retained return remains UNKNOWN/held across restart;
no original or replacement is blindly retried. A replacement cannot itself
be the target of another decision in this slice.

## Unattended service

Select service recovery `/3`, `model_reconciliation = "caller_only"`, with
the original research policy above. All routes retain the existing bounded
authenticated loopback owner; terminal assistance remains forbidden.

- `POST /runs/<id>/observe-model-unknown` accepts observation request `/1`
  with the exact snapshot digest.
- `POST /runs/<id>/reconcile-model` accepts the typed caller decision.
- `GET /runs/<id>/model-attempts` reads durable non-secret receipts.
- `POST /runs/<id>/recover` with the authorization body admits that specific
  unconsumed attempt. An empty body keeps legacy acknowledged-only admission.

Restart never consumes an unstarted authorization automatically. A manual
caller may retrieve a receipt whose service ACK was lost, then explicitly
submit it. Existing original per-job adoption counters remain durable and
bounded; failed/cancelled jobs never become fresh runs. Current status includes
the receipt/digests, while manifest exposes configured bounds. Cancellation
and native output/source/journal locks remain authoritative.

Subsequent control snapshots and completed archive admission recognize only
the validated historical caller abandonment. They do not erase the original
UNKNOWN. Completed output follows the original corpus/outbox handoff; a lost
service output ACK reads the native sealed archive rather than recrawling.

## Boundaries and evidence

This does not close arbitrary mid-source, concurrent-source, discovery,
retained-reader, identity-model or unacknowledged graph recovery. Serial
completed-source recovery keeps its documented finite cursor contract; an
unreconciled later model/source/graph effect still holds. Recovery retains
original run bounds and cancellation, not immediate pause-at-round semantics.

Dedicated native tests use real process death, native private journal fsync and
locks, immutable graph readback, and local HTTP/model/encoder fixtures. They
exercise separate charging, one-shot consumption, retained ACK replay, original
UNKNOWN preservation, limits, changed inputs and service receipt/output ACK
loss through corpus/outbox handoff. These establish executable source recovery
behavior, not live model accuracy or production acceptance.
