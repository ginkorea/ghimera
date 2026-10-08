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

I03 remains open: the frontier/local-import candidates below still need release
acceptance; integrate explicit uncertain model/graph reconciliation and reservation
accounting, retained-reader control state and retained-source operations, and reconstruct a
whole resumed collection/research session without losing queued links or
inventing exact unknown spend. This web-operation capture is a necessary part
of that path, not an automatic replay engine or a replacement for I12's
general unattended collection lifecycle.

## Durable queued frontier candidate

`examples/source-frontier.toml` adds an explicit optional frontier policy to
the existing source-work recipe. Seeds, scored ordinary links and admitted
references are committed in that same owner-private database before entering
the existing priority heap. Each intent retains its exact scope, depth,
reference hops/origin, first observed enqueue time and priority. An out-of-scope
candidate is an intent, not permission to fetch it; the ordinary scope check
still refuses it and its discard is acknowledged. Duplicate intents retain
their first observation and the highest offered priority. Scope or ancestry
changes for the same queued URL/depth refuse instead of silently changing the
source boundary.

The operation's acquisition intent uses the same source-coordinate identity.
Inspection therefore distinguishes never-started `report.queued` work from
unresolved acquisition/processing without needing a separate non-atomic queue
deletion. Completed-round checkpoints must match the acknowledged queue;
missing, invented or reprioritized entries refuse. The source-work inspector
adds a bounded queued count only when this optional policy is enabled.

Entry counts and serialized byte bounds are explicit operator inputs. Queue
payload and active original/result reservations share `max_store_bytes`, in
addition to the separate queue bound. No physical disk quota is implied.
Collection concurrency and the native spend ledger keep their existing owners.
No queued or unresolved item is automatically refetched or replayed by inspection.

This candidate closes capture of queued web intents, not the full I03: interrupted
whole-session adoption still needs explicit uncertain model/graph reconciliation,
budget reservations, retained operation coverage and durable research
control state. It is not yet a published release or real model-quality acceptance.

## Owned local document capture candidate

With source work enabled, `GoalLoop.import_local` and the research loop's
`local_documents` use the same run-owned operation store. A typed
`ghimera.local-source-request/1` retains the exact caller-declared file path,
SHA-256, content type and local-input policy digest before the bounded read.
Local URNs do not become permitted HTTP URLs or invented web-frontier entries.
Existing web coordinate serialization and identities are unchanged. Local paths
are private recovery metadata, never added to public document provenance or the
bounded inspector CLI.

Captured local `Page` bytes and path-free `LocalInputEvidence` are acknowledged
before graph discovery, parsing, scoring or model work. Processing returns its
exact native `Document` through the same terminal result boundary as web work.
A controlled cancelled read is drained and its actual byte spend recorded; when
the pinned snapshot was acquired, the cancelled operation retains those original
bytes without claiming an accepted document. A changed input refuses and keeps
the observed read spend, not a false original. Lost read acknowledgement remains
`fetching` with unknown spend, not a fabricated zero-byte receipt. Storage
capacity is reserved before file I/O; failed graph acknowledgements remain
unresolved rather than being reclassified as ordinary source refusals.
Repeated caller cancellation does not abandon the owned read's drain. A reader
task itself cancelled before acknowledging its physical I/O is a fatal uncertain
read, not a recorded zero-byte cancellation.

This is operation capture, not automatic interrupted-import replay. The optional
pending-batch extension below preserves never-started local files; whole research
control state and resuming an interrupted operation still require explicit
reconciliation. No OCR or semantic-language quality claim follows from capturing
a pinned PDF correctly.

## Atomic pending local batches candidate

Enabling `[source_work.frontier]` now also captures a caller's complete local
PDF/DOCX seed batch before the first read. The existing private frontier table,
capacity policy, journal owner and writer lease are reused. Every request retains
its exact path/hash/MIME/input-policy pin. All new batch intents commit in one
transaction; invalid coordinates, changed pins, capacity exhaustion or a failed
insert roll back the whole batch before any file I/O. An acknowledged duplicate
pending intent keeps its first observation and order. A file that already has
an acquisition operation refuses implicit retry rather than reopening it.

Local requests never enter the web priority heap or acquire HTTP scope. The
shared entry/byte limits cover both kinds of intent, so choose them for the
actual workload. `report.queued` retains web intents only; `report.queued_local`
returns never-started local requests in acknowledged caller order. Queued
files do not imply file reads or spent bytes. A started read without its native
acknowledgement remains an unresolved operation, not an unstarted file.

Quiescent checkpoints retain the exact pending-local order and pins. Missing,
reordered, invented or changed local requests refuse restoration. After a
verified completed prefix is restored, an explicit
`await loop.import_local(restored_session, ())` drains only its pending files,
with the existing byte/file/model budgets; handled files are not reopened.
Inspection never drains or retries anything itself. The inspector CLI adds
only a bounded `local_queued` count when present, not private paths or contents.

This does not adopt an arbitrary interrupted research session. Unknown read,
model or graph acknowledgement still requires explicit reconciliation. Actual
subprocess termination, a mid-transaction SQLite failure, capacity-before-I/O
and restored native DOCX processing are the candidate's acceptance witnesses;
they are not real Chinese OCR or semantic-model quality measurements.
