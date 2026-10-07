# Bounded concurrent collection

Status: **full-gated source candidate**, not yet published. This closes part
of the infrastructure execution gap, not durable operation-level recovery or
representative language/model quality.

## Configuration and ownership

Set `GhimeraConfig.execution` using the non-active fragment
[`examples/execution.toml`](../examples/execution.toml). For a complete TOML
configuration, put its values under `[execution]`. The versioned
`ghimera.execution/1` contract requires every capacity explicitly:

| Field | Owned limit |
|---|---|
| `active_sources` | Live source-processing tasks, including stage waiters |
| `extraction_workers` | Simultaneous native HTML/PDF/DOCX extraction |
| `scoring_workers` | Simultaneous configured link/evidence scoring |
| `judge_workers` | Simultaneous document verdicts and whole-intent grading |
| `semantic_workers` | Simultaneous semantic extraction/review pipelines |
| `visual_workers` | Simultaneous selective-image enrichment pipelines |

These are collector concurrency requests, not physical GPU allocations or
permission to use a server. Model services and document workers retain their
own separately configured capacities. The original HTTP scheduler still owns
global/per-origin request limits, spacing, jitter, robots and server cooldowns.
Concurrency does not reduce those floors or broaden source scope.

The validated recipe is recorded in `receipt.effective_config`, including its
stage capacities. An absent `execution` field retains legacy serial scheduling
and is omitted from serialization, preserving existing recipe identities. A
present but incomplete/invalid policy refuses at configuration parsing; no
capacities or endpoints are inferred from the host.

## Flow

The existing `GoalLoop` claims URLs from its priority frontier before their
first await, then drives a bounded set of source tasks. Each source reuses the
same fetch, extraction, scoring, verdict, content-dedup, selective-visual,
semantic and reference pipeline. A stage semaphore is released before the
source waits for the next independent stage. The controller waits for **any**
task to complete and immediately replenishes available source capacity; there
is no whole-batch `gather` barrier between rounds of sources.

Different sources may be fetching, parsing, encoding and reviewing at once.
Raw/extracted data waiting downstream stays bounded by `active_sources` plus
the run's existing page/byte bounds. Configured stage slots apply backpressure
without reserving model spend merely because a source is waiting for a slot.
Temporary in-flight byte reservations wait for release within the wall budget;
they are not mislabeled as permanently spent bytes.

Grading uses an immutable accepted-document prefix while other source tasks
continue. If the final accepted prefix grew after that grade, it is graded
again before completion when allowed by the original model budget. Saturation
cannot be inferred from sources still being processed: an under-filled window
drains its existing tasks before the no-new-document decision. Research's
`fetch_limit` remains a scheduling quantum over actual reads, not an additional
resource grant; an admitted source can itself require robots, redirects,
browser resources or images. The authoritative page/byte/model/wall budgets
remain enforced at their existing spend boundaries.

## Evidence and lifecycle

- The append-only ledger remains owned by one asyncio loop. Reservation and
  sequence allocation are synchronous before a call; observations append only
  after the corresponding result or refusal exists.
- Existing graph transactions still serialize their exact immutable batches
  and wait for durable acknowledgement. This narrow storage serialization does
  not serialize independent fetch or model calls.
- A graph-document lock avoids concurrent semantic extraction of the same
  source identity; review/refusal references remain source-local even when
  unrelated reviewer calls interleave.
- Periodic grading cannot hide a concurrent storage/contract failure. Early
  satisfaction or caller cancellation cancels and drains owned tasks, preserving
  actual bytes/calls and any shielded graph acknowledgement before returning.
- A live session permits one driver operation. Concurrent collection/import,
  checkpoint, snapshot or finish over in-flight work refuses. A completed
  collection quantum is quiescent before research's existing round checkpoint.

This is **not** operation-level crash recovery. The existing journal records
observed requests, refusals and completed round checkpoints; uncertain calls,
browser interactions and killed-process frontiers still need the separate I03
durability work. Do not treat a cancellation record as permission to replay an
interactive action or as proof the source was completely processed.

## Verification scope

Contract witnesses cover a slow extractor beside a fast reviewed source,
independent parser backpressure, cancellation cleanup, exact byte accounting,
temporary-byte waiting, page/round bounds, saturation, overlapping/final grades,
and concurrent graph-error propagation. The actual network-flow witness uses
libcurl and a controlled loopback HTTP server, including the shared robots and
origin scheduler. Its judge is a declared fixture, not real-model acceptance.

Interleaved semantic-review failures are exercised through the existing model
wire adapters and source-bound graph/ledger validation. Controlled reviewer
responses prove binding and lifecycle, not extraction accuracy. Real Pacific
OCR, representative publishers, served-model quality, public-web/onion runtime
acceptance and unattended-service operation remain separately tracked in the
infrastructure PRD.

The private gate interpreter was checked as
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, importing
`/tmp/ghimera-cadence-20261007/src/ghimera`. It imports no platform SDK;
platform doctor/floor are not applicable to this standalone run. The broader
execution/scoring/continuation/local-input regressions passed **127 tests in
41.51 seconds** before the final grade-budget spin guard. The final execution,
semantic-interleaving and legacy C0 selection passed **27 tests in 2.02
seconds**, without skips. Strict mypy passed 124 source modules and Ruff
passed. Source/tests/examples were not edited during either reported run.
An earlier run failed because the new reviewer fixture used a keyword-only
argument positionally; that fixture invocation was corrected, not its expected
source-binding behavior.

The frozen combined source at `36d024f` then passed the full standalone gate:
**845 tests passed in 692.77 seconds**, no failures/skips, under that same
Python 3.11.16 interpreter importing `/tmp/ghimera-cadence-20261007/src/ghimera`.
Ruff, formatting, strict mypy (124 source modules) and the offline lock check
also passed. This includes the Pacific PDF candidate's contracts, not successful
representative OCR quality. The first full attempt reported 828 passes and 17
browser-launch failures because the selected TMPDIR made Chromium's Unix socket
path too long. The identical source passed with a shorter owned TMPDIR; no
browser assertion was removed or weakened. Later corpus code is a separate
candidate and is not covered by this source's gate result.
