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
children before returning. The caller schedules the next dispatch; there is no
hidden background retry loop.

## Retention

`prune_acknowledged(id, sink)` is an explicit destructive action over one item.
It requires an acknowledged outbox state and a fresh, unchanged durable
destination readback. Pending, uncertain or unreadable destinations refuse.
Pruning removes only that outbox payload, retaining its exact acknowledgement
and deduplication tombstone. Enqueuing the same result does not redeliver it.
The destination copy and separately stored evidence corpus are untouched.

SQLite may reuse freed pages; pruning is not a claim of physical disk shrinkage.
Automatic age retention, compaction/rotation, a remote storage adapter and
unattended service lifecycle remain implementation/deployment requirements.
Item count, total item bytes including acknowledgement reservations, individual
payload/ack limits and attempt-record count prevent unbounded accepted growth;
filesystem/journal overhead still requires operator headroom and monitoring.

## Verification

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
