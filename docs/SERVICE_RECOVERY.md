# Unattended service recovery

Service `/1` is unchanged: interrupted work is held, not blindly restarted.
`examples/collection-service-recovery.toml` uses service `/2` and an explicit
`ghimera.service-recovery/1` policy. Its safe example selects `on_restart = "hold"`;
`adopt_acknowledged` enables bounded automatic admission after restart. Configure
this policy **before** submitting the original jobs. Changing the service policy
or collector recipe does not migrate existing jobs into a new run.

The collector must already configure native `research_recovery`, retained
`model_work.results`, journal and continuation. The service reuses
`ResearchRecoveryStore`, the original private command output reservation and
collector-command `/4` with execution `/2`; it creates no replacement scheduler,
ledger, model acknowledgement or result store.

Admission verifies the exact original service request, recipe and native control
snapshot, one retained acknowledged model return, intact journal, native source
work and graph state, and original unfinished output reservation. Native service,
journal, source and output writer locks remain authoritative. Unknown model
intent, torn/changed evidence, missing output reservation or an already active
writer is held without contacting a source or model. Failed outcomes cannot be
reset into a fresh run by `resume` or `recover`.

Before launching, the service atomically persists `snapshot_sha256` and increments
`adoption_attempts`. A process death before launch still consumes that attempt;
restart never resets the original per-job allowance. If native recovery advances
to a later phase before another process death, that current snapshot can be
rebound only after the same exact original request/configuration/current runtime
model identities, intact journal/source/graph and retained-ACK checks. Its digest
replaces the prior reserved digest, but the original per-job attempt counter
continues; neither run bounds nor external calls are guessed or reset.

A completed native archive lacking its service receipt is independently admitted
by original request/reservation, immutable archive readback, exact sealed journal
and intact source/graph checks, then handed off without recollection. This does
not consume a model adoption attempt and remains available after that allowance
is exhausted. Partial or mismatched output remains held.

`POST /runs/<run_id>/recover` (empty body, existing bearer authentication) explicitly
requests the same admission checks for an inactive held interrupted job. It returns
202 for an admitted recovery, 409 for a hold/refusal. It does not reconcile unknown
calls or override exhausted attempts. Normal status includes retained request and
snapshot digests, attempts and a fixed non-secret `recovery_hold` code. Health
includes `recovering`, total adoption attempts and hold counts; no raw exceptions
or credential values are recorded.

Model-boundary recovery retains original run bounds and includes downtime. It
does not accept a new round checkpoint/pause policy: `pause` is refused while
`recovering`. Native cancellation remains available. Successful recovery finishes
through the same immutable archive, optional corpus append and delivery outbox
handoff as ordinary collection; handoff retries never recollect.

Tests use actual process termination after a native retained model ACK, fresh
service startup and concrete local HTTP adapters. They establish lifecycle and
evidence binding, not live-site coverage, factual model quality or deployed
off-host acceptance.
