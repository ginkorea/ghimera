# Completed serial source recovery

This opt-in slice resumes a research collection **after a fully completed web
source has been atomically acknowledged**, before a later research phase/round
checkpoint. It is not arbitrary crash recovery. Recovery /1 and ordinary
concurrent collection keep their existing behavior; source recovery /2 rejects
concurrent execution rather than silently changing it.

The native SourceWorkStore transaction acknowledges the original processed
operation and its bounded control capsule together. No second database or retry
engine is introduced. The capsule contains the merged Harvest, content/dedup
revisions, pending frontier, visited URLs, reference ancestry/book evidence,
search observations, retained-reader history, exact request/configuration,
current extraction/scoring/judge and research/search identities, native graph
checkpoint, round plan/questions and primary/cited-by collection cursor.

The source operation naturally ends before the snapshot is taken. A separate
serial-driver owner prevents overlapping collection. Budget quiescence and
native source/journal/output writer locks remain authoritative. Neither
`_operating` nor an unknown acknowledgement is fabricated to admit a snapshot.
Both source rows and the capsule roll back if bounded serialization/admission
fails. The capsule's bytes share source_work.max_store_bytes with originals and
frontier, and have their own explicit max_capsule_bytes.

## Configuration and exact command

Merge the research_recovery fragment in
[source-completion-recovery.toml](../examples/source-completion-recovery.toml)
into an exact operator recipe that already declares research, journal, retained
model work and source_work.frontier. Set all bounds deliberately; the fragment
alone starts nothing. No execution policy means the existing serial collection
mode. A concurrent execution policy is unsupported only when this source policy
is explicitly selected.

[collector-source-recover.toml](../examples/collector-source-recover.toml) is an
inert command /4 with execution /3 and explicit source_completion boundary.
Use the original run, unchanged recipe, original unfinished output directory
and exact acknowledged digest:

```python
from ghimera.source_work import SourceWorkStore
admitted = SourceWorkStore.completion(config, original_run_id)
print(admitted.sha256)  # Digest only; do not expose the private capsule.
```

Run the configured job with `python -m ghimera.command --job JOB.toml
--max-job-bytes OPERATOR_BOUND`. The native command resumes the existing output
reservation and owns all final archive, corpus and outbox handoff. It never
refetches/re-extracts/re-encodes/re-verdicts the already completed source. New
unstarted frontier work and subsequent assessment/answer/review remain real,
bounded work, not replayed fake outcomes.

The original starting_fetches and round quantum survive restart. Auxiliary
fetches can advance native counters during one source just as in ordinary
collection; adoption does not invent a new cap or reset the counters. The next
source is admitted only under the original driver checks. Wall-clock downtime
is included in the original wall budget and max_rounds is unchanged.

## Unattended service

Collection-service /2 may use the separate service_recovery fragment under its
own `recovery` key. Service-recovery /2 explicitly selects source_completion;
/1 still selects the implemented acknowledged-model boundary. `on_restart =
"hold"` is the inert/manual default example. `recover(run_id)` uses native
source preflight and requires the original service-saved request to match the
capsule. For explicit unattended adoption, configure `adopt_acknowledged`.

The native service persists digest, source boundary and original adoption
attempt count before launch, rechecks concurrent cancellation/stop and retains
failed/held outcomes. Its normal result handoff is unchanged. Status/health
expose bounded non-secret holds and attempts. Unattended terminal assistance
is still refused. Recovery preserves original run bounds and supports native
cancellation; it does not introduce immediate pause-at-round semantics.

## Refusals and remaining gaps

Unknown/torn model outcomes, any later journal row (even a retained model ACK),
later source intent, changed capsule/original/request/configuration/runtime,
frontier or native graph drift, sealed journal and active native writer/output
reservations hold before new contact. Admission never repairs/reconciles those
tails automatically. The existing explicitly selected model-recovery path may
admit its own acknowledged-model boundary; service source mode does not fall
back to a stale source capsule.

This does not resume mid-fetch, mid-extraction, mid-source model/encoding,
concurrent source overlap, interrupted discovery or retained-reader calls,
unacknowledged graph effects or local/retained-source projection cuts. Completed
discovery/reader history preceding a proved source cut is preserved, but those
calls need their own full-response/control acknowledgement to become separate
recovery boundaries. A crash after the source cut and after later uncheckpointed
control work remains conservatively held.

## Evidence scope

Dedicated tests use real process death after the native atomic source ACK and
fresh exact command/service restart with local HTTP extraction/model/embedding
fixtures. They compare source/search/encoding contacts, original ledger/spend,
round quota and reservation, native graph restoration and held tails/locks.
Additional native-owner behavioral witnesses cover a None/rejected document,
merged duplicate/frontier/reference preservation, runtime mismatch and charged
downtime. These prove protocol/control durability, not live site or model quality.
