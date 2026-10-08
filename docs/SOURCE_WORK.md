# Durable source-operation evidence

This candidate adds durable capture to the existing web collector, not another
crawler or a claim that arbitrary interrupted research can already resume.

Append `examples/source-work.toml` to a collector configuration that already has
an explicit `[journal]`. Call `Collector.collect(..., run_id="your-run")` or
`Collector.research(..., run_id="your-run")` as before. No second storage root,
environment discovery, source credential, model service or background worker is
constructed by this capability. Disabled configurations serialize exactly as
before; enabling it changes the pinned run recipe deliberately.

## State and ownership

The native run journal owns the run identity, original goal/judge/recipe and
observed byte/model spend. Its `source-work/operations.sqlite` child stores one
request's exact URL, scope, depth and reference ancestry, captured `Page`, and
accepted `Document` when processing completes. An original is committed before
the extractor, scorer, judge or graph processing starts. The accepted candidate
keeps its own source identity even when the collection chooses a deduplicated
representative. Source-work storage shares the existing owner-private SQLite
boundary and holds a single writer lease for the collection session's lifetime.

```
fetching -> acquired -> processing -> processed
    |           |           |
    +-----------+-----------+-> refused / cancelled
```

`processed` means the source pipeline returned normally. Its result can be null
when the judge rejected it; it is not synonymous with accepted. Controlled
refusal/cancellation retains any already captured original and its reason.
A process killed during acquisition or processing leaves its last acknowledged
phase unchanged. Inspection reports these as unresolved only when it observes
no active writer. It never guesses that an active operation failed.

Start/acquisition/completion timestamps are local wall-clock observations, not
publisher dates, verified factual time, or monotonic duration measurements.
Actual ledger spend retains the existing monotonic timing and native call
evidence. Ledger indices in source operations are global high-water marks;
concurrent operations can interleave, so subtracting them does not measure
one source's cost. A fetch without its native acknowledgement has unknown
spend, not a manufactured zero-byte success.

## Inspect without repeating work

```python
from pathlib import Path
from ghimera import GhimeraConfig
from ghimera.source_work import read_source_work

config = GhimeraConfig.from_toml(Path("collector.toml"))
report = read_source_work(config, "your-run")
for operation in report.unresolved:
    # Capture metadata and originals for reconciliation. Do not silently
    # refetch or replay uncertain model/graph calls under the old run id.
    print(operation.operation_id, operation.state)
```

The CLI deliberately emits only bounded run/state counts, not source bodies,
URLs, headers, model contexts or deployment endpoints:

```bash
python -m ghimera.source_work --config collector.toml --run-id your-run
```

Inspection validates original recipe/header binding, record order/digests,
native journal boundaries, exact result/original bindings and declared storage
limits. These are consistency checks, not a signature against an attacker who
can rewrite every owner-private run artifact. It does not contact sources,
models, encoders or graph sinks and does not repair or rewrite files.

## Capacities and failure

The operator provides positive operation/page/result/envelope byte capacities,
total store payload capacity and database timeout. Sizes include serialized
provenance and base64 source bytes. Active operations reserve their full
envelope bound before collection; completion releases unused capacity. Choose
bounds compatible with the HTTP/browser/PDF/visual profiles, not just expected
HTML length. A source exceeding its declared page/result bound is a fatal
storage refusal, not an acknowledged durable capture. Previously recorded
native transfer/call observations remain in the run journal.

`max_store_bytes` limits logical stored/reserved operation payloads, not SQLite
page overhead, rollback files, filesystem capacity or a physical disk quota.
Provision disk headroom separately. Completed results and raw snapshots are
retained for recovery; this component never deletes originals automatically.
Delivery's acknowledged-payload lifecycle remains a separate owner.

Storage failures cannot be swallowed as ordinary source refusals. Unresolved
operations cannot be checkpointed or sealed. Existing completed-round research
continuation can reopen only quiescent source work belonging to that exact
journal prefix; an interrupted operation refuses and remains available for
reconciliation. Writer leases release on normal completion, cancellation and
session opening/restoring failure.

## Acceptance and the remaining full I03 requirement

The focused candidate checks include actual subprocess termination before
acquisition acknowledgement, after original capture and after an accepted
result; fresh-process readback; native byte ledger retention; out-of-order
concurrent completion; cancellation; completed-round continuation without
refetch; writer exclusion; capacity refusal; recipe/digest/symlink/journal
mutation; and bounded CLI output. Fixtures test recovery contracts, not real
language or model accuracy. Full release acceptance is still required.

I03 remains open: persist the *queued* frontier before scheduling, integrate
explicit uncertain model/graph call reconciliation and reservation accounting,
cover interrupted local imports/retained-source operations, and reconstruct a
whole resumed collection/research session without losing queued links or
inventing exact unknown spend. This web-operation capture is a necessary part
of that path, not an automatic replay engine or a replacement for I12's
general unattended collection lifecycle.
