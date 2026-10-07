# Durable result delivery

Status: published in 0.4.1; full release evidence is in [RELEASE_041.md](RELEASE_041.md).
This closes neither the collector's
operation-level crash frontier nor deployment of a remote publication service.
The outbox preserves completed results between collection and destination
availability; it does not acquire source entitlement or certify model accuracy.

## Ownership and composition

`DeliveryOutboxConfig` (`ghimera.delivery-outbox/1`) owns an explicit private
directory, logical destination identity/revision, item/ack/audit limits,
per-dispatch concurrency and claim/retry/deadline policy. No paths, endpoints,
credentials, scheduler or daemon are discovered. The caller creates the parent;
`create=True` creates a new directory and never overwrites an existing store.
Reopening requires the exact recorded target. Non-secret tuning may change only
when retained records still fit the new bounds.

```python
from ghimera import DeliveryCollector, DeliveryOutbox, DirectoryDeliverySink

outbox = DeliveryOutbox(outbox_policy, create=True)
service = DeliveryCollector(collector, outbox)
queued = await service.run("Find evidence answering this question")
print(queued.delivery.delivery_id, queued.delivery.status)

# Separately driven: collection does not wait for this destination to be online.
sink = DirectoryDeliverySink(destination_policy, create=True)
states = await outbox.dispatch(sink)
```

`collector` may be the existing `Collector` or a composed `PersistentCollector`.
`collect`, `run` and `resume` queue the exact completed result before reporting
success. Their outer `ghimera.queued-collection/1` acknowledgement binds the
delivery identity without changing existing harvest, research or corpus schemas.
Explicit queue retry uses `service.persist(result)`; it performs no source/model
work. Handoff failure/cancellation carries the completed result for retention by
the caller. This is not a crash-durable acknowledgement until enqueue commits.
Suspended research keeps its existing round checkpoint; it is not a finished
result to publish.

The standalone [outbox](../examples/delivery-outbox.toml) and
[local destination](../examples/directory-delivery.toml) fragments are inactive
examples. The source/model budget and corpus audit remain independent; outbox
dispatch adds no model call and does not silently reset those budgets.

## Invariants and retry

Each immutable `ghimera.delivery-item/1` contains its target and complete
schema-qualified result. Its stable hash is the delivery/idempotency identity.
SQLite transactions (`synchronous=FULL`) commit complete bytes before enqueue
returns. Owner-private directory/database descriptors, symlink/hardlink/inode
checks and owned blocking workers reuse the corpus's storage primitives. These
checks are storage ownership protections, not an authorization system.

`DeliverySink.deliver` is final. An adapter implements target, lookup and write:

1. Check the item's exact configured destination and prior durable lookup.
2. If absent, write idempotently without overwriting an existing identity.
3. Validate the returned target, payload hash/size and delivery ID.
4. Require exact durable destination readback before returning acknowledgement.

An adapter's lookup must read durable content and return its matching receipt,
not a pending/volatile copy. The included `DirectoryDeliverySink` is a real
local SQLite destination with immutable results and readback. Its `result(id)`
returns the retained native result; a second directory on the same host is not
off-host backup. Other destinations require explicit adapters that preserve this
contract and own their transport, credentials and entitlement.

Claims are transactional and bounded per dispatch. Concurrent workers can make
progress beside a slow delivery; other processes can claim different items.
Every admitted attempt records the effective policy digest and a non-secret
outcome before contact. Failure details/bodies/credentials are not persisted as
diagnostic strings. A temporary transport/database timeout yields `unavailable`;
contract refusal yields `refused`. Unexpected exceptions remain visible to the
caller, leaving uncertain durable claims rather than pretending completion.

Crash/cancellation leaves the claim `delivering`/`uncertain`. After its configured
epoch-time expiry, a dispatcher first reads the same destination ID and can
acknowledge an already durable write without writing another copy. This is
**at-least-once with required destination idempotency**, not exactly-once network
delivery. Clock corrections affect expiry. Retry/audit exhaustion retains the
payload and needs an explicit operator policy increase or rotation; it never
turns failure into success. Cancellation drains owned storage work and dispatch
children before returning. The outbox alone is caller-driven. An explicitly
owned `DeliveryWorker` can schedule subsequent dispatches without changing
those durable retry rules.

## Retention

`prune_acknowledged(id, sink)` is an explicit destructive action over one item.
It requires an acknowledged outbox state and a fresh, unchanged durable
destination readback. Pending, uncertain or unreadable destinations refuse.
Pruning removes only that outbox payload, retaining its exact acknowledgement
and deduplication tombstone. Enqueuing the same result does not redeliver it.
The destination copy and separately stored evidence corpus are untouched.

SQLite may reuse freed pages; pruning is not a claim of physical disk shrinkage.
Automatic age retention, compaction/rotation and a remote storage adapter remain
implementation/deployment requirements. The 0.4.4 worker below adds
explicit background lifecycle and readback-gated payload pruning, not physical
disk shrinkage or automatic tombstone/audit rotation.
Item count, total item bytes including acknowledgement reservations, individual
payload/ack limits and attempt-record count prevent unbounded accepted growth;
filesystem/journal overhead still requires operator headroom and monitoring.

## Explicit background worker (0.4.4 release line)

`DeliveryWorkerConfig` (`ghimera.delivery-worker/1`) owns polling cadence,
shutdown grace, bounded pruning and `keep` versus `prune_acknowledged` policy.
It is separate from the immutable result/outbox identities. The inactive
[worker fragment](../examples/delivery-worker.toml) retains payloads by default.
Construction/import does not start a task, discover credentials, create stores
or contact a destination. An async context owns start, stop and error propagation:

```python
from ghimera import DeliveryWorker

async with DeliveryWorker(worker_policy, outbox=outbox, sink=sink) as worker:
    queued = await service.run("Find evidence answering this question")
    # Polling discovers this and future arrivals without manual dispatch.
    # Optionally call worker.wake() after enqueue to shorten the polling delay.
    print(worker.status.model_dump_json())
```

Collection and delivery are independent; the context above stops its worker on
exit, so an application should own it for the application's intended lifetime.
Graceful stop finishes the active cycle. At the configured grace deadline it
cancels/drains owned tasks, leaving unfinished durable claims uncertain for
normal expiry/readback reconciliation. Cancellation of the owner also drains
its worker before returning. Unexpected failures reach the lifecycle owner and
set `phase=failed`; no raw exception text is stored in status.

Delivery and retention run concurrently within a bounded cycle. Retention scans
only acknowledged items with retained payloads and calls the same fresh-readback
pruning method; there is no generic directory deletion. Its stable bounded
cursor advances on refused readback and wraps later, so one missing destination
copy cannot starve healthy neighbors. Pending, uncertain and retry-exhausted
results remain retained. Worker counters are process-local observations, not a
globally unique delivery ledger. Queue counts and retained-envelope bytes are
a recent SQLite snapshot; they do not measure free disk space. Claims and
exhaustion remain authoritative in the durable outbox across process restarts.

The new command can own a local delivery worker independently of collection:

```bash
ghimera-delivery --config /absolute/path/delivery-command.toml --max-config-bytes 65536
# Equivalent from an explicitly installed interpreter:
python -m ghimera.delivery_command --config /absolute/path/delivery-command.toml --max-config-bytes 65536
```

The [complete command example](../examples/delivery-command.toml) declares both
existing stores, their shared exact target, worker policy and status cadence.
Create those stores explicitly with the existing library constructors first;
this command never overwrites or silently creates them. It emits flushed JSONL
health without source contents or credentials, handles SIGTERM/SIGINT, applies
the shutdown grace even mid-delivery, and exits nonzero on worker failure.
Signal ownership belongs to this standalone Linux process. Embedding callers
use `DeliveryWorker` and their own lifecycle instead of installing its command's
signal handlers. An external service manager can run/restart the command, but
no operating-system service or remote destination has been deployed by these
source changes. General collection run/status/pause/resume/cancel API and
operation-level crash recovery remain tracked separately.

`keep` is the safe example policy. `prune_acknowledged` explicitly opts into
immediate bounded cleanup after verified destination durability; it is not an
age-based policy. Tombstones and attempt audit are retained, so their configured
limits still require explicit operator rotation rather than silently forgetting
delivered identities. A local second directory is not an off-host archive.

## Verification

The combined browser/delivery source `73129ea` passed the complete gate: **999
passed in 980.63 seconds**, zero failures/skips, Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-browser-redirects-20261007/src/ghimera`. Final artifact identity and
publication are tracked separately in RELEASE_044.md. This supersedes the
parent-only gate below, not representative remote destination or model quality.

The background-worker candidate's final delivery/command/package-boundary
selection returned **37 passed in 15.82 seconds**, zero failures/skips, using
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-unattended-delivery-20261007/src/ghimera`. It exercised actual
SQLite stores and fresh command processes, SIGTERM both after delivery and
during an interrupted durable write, owner cancellation/draining, restart
readback without a duplicate, exhausted retry retention, fair pruning beside
a missing copy, inert examples and safe diagnostics. Ruff check/format passed;
strict mypy passed 148 source files. No source changes occurred during the run.

That same interpreter/source then drove a separate actual command process over
three retained native PDF/DOCX/inline-PDF research archives from the 0.4.3 gate.
All three complete results reached a distinct durable destination, were read
back exactly, and only then lost their outbox payloads. Reopening both stores
preserved original bytes/parser/session/citation/graph records; re-enqueue did
not redeliver. SIGTERM exited zero with `phase=stopped`, acknowledged=3,
pruned=3, pending=0 and no retained outbox payload bytes. Records and effective
configuration remain in the owned `ghimera-native-delivery-aktBCP` operator
directory. These are controlled native archives, not representative publisher
or served-model quality; no new source/model request, platform change or shared
runtime occurred. The frozen delivery source at `9e060f4` then passed the full
`scripts/gate.sh`: **967 passed in 872.15 seconds**, zero failures/skips, on the
same Python 3.11.16 interpreter and unattended-delivery checkout. Offline lock,
Ruff check/format and strict mypy passed first. This completed gate does not
cover later browser-navigation source changes. Independent next-version wheel
acceptance subsequently passed for the combined candidate; final release
artifact checks and publication are tracked in RELEASE_044.md.

The initial delivery-only run returned 15 passes in 9.21 seconds, no skips,
using Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. It used real private SQLite
stores, a fresh-process dispatcher and credential-free loopback research
fixtures. Subsequent concurrency/shared-storage changes and their importers
must pass before this candidate is called source-complete. These witnesses
establish software/durability contracts, not remote-storage deployment or
native-language model quality.

The first expanded importer run returned 87 passes and one failure in 103.99
seconds on that same interpreter/checkout: the invocation omitted
`GHIMERA_TEST_TESSERACT`, so the actual visual-OCR witness refused at fixture
setup. No source behavior or quality assertion was relaxed; subsequent runs
must supply the full explicitly installed OCR/browser fixture environment.

With those inputs the affected delivery/storage/corpus/collector/continuation
selection returned **89 passed in 104.96 seconds**, no failures or skips, on
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. It includes bounded
concurrent delivery beside a slow neighbor and the shared storage's rollback,
writer exclusion and ownership guards. Ruff and strict mypy passed. The later
destination-loss pruning witness and combined full gate remain pending.

The final delivery/private-storage modules then returned **21 passed in 9.68
seconds**, no failures or skips, on that same Python 3.11.16 interpreter and
delivery-outbox checkout. This includes the missing-destination cleanup refusal;
the complete combined release gate remains pending.
