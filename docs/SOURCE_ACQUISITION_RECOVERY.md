# Acquired original-page recovery

This explicit serial policy covers one additional I03 boundary: a fetched Page
or initial owned-file Page has returned locally, and the existing SourceWork
transaction commits that exact Page together with its original research and
session cursor, before parser/judge processing starts. It does not close full
I03 or I12 recovery. Default and recovery /1–3 recipes remain unchanged.
Recovery /5 selects acquisition only. Recovery /4 selects original serial
query control only; explicit /6 composes both native boundaries in the same
original run. See [query recovery](QUERY_RECOVERY.md).

Merge [source-acquisition-recovery.toml](../examples/source-acquisition-recovery.toml)
into a recipe already declaring research, native journal, retained model work,
source_work and source_work.frontier. Capsule bytes are explicitly bounded and
share the original source-work capacity with Page originals and the frontier.
No new database, journal, retry engine or worker is introduced.

The native journal first records a typed **local return observation** with the
original operation/request/Page/raw-source digests and acquisition kind. It is
not an assertion about an unknown remote outcome and adds no second charge.
The atomic acquired cursor points to this exact operation-specific sequence
and row digest, not an unowned journal high-water mark. If that observation
commits but the SourceWork transaction fails, recovery holds: it cannot invent
a Page/control acknowledgement, fetch again or reopen the original owned file.

After the original writer has ended, the library facade can inspect and adopt
the exact committed cut:

```python
from ghimera.source_work import SourceWorkStore

cut = SourceWorkStore.acquisition(config, original_run_id,
                                  expected_request=original_request)
# Do not publish the private capsule or retained source bytes.
result = await collector.recover(original_run_id,
                                 snapshot_sha256=cut.sha256,
                                 boundary="source_acquisition")
```

`collector` is the original configured native Collector, not a replacement
loop. Recovery requires the exact configuration, run, request, native
extraction/scoring/judge and research/search identities. Source/journal writer
fences and graph/session prefix checks remain authoritative. The original
budgets, collection leg/quantum, search history, source/reference ancestry,
visited/frontier and dedup state survive; downtime spends the original wall
budget. Exhaustion admits no parser/model or source contact.

The exact retained Page enters the existing parser, judge and graph pipeline.
It is neither refetched nor reopened, and already observed discovery/model
returns are not repeated. Previously unstarted sources or research phases may
still run once under the original remaining limits; they are not free replay.
Missing or drifted proof, a later journal/graph effect, a live writer, duplicate
adoption, or any unrelated outstanding native operation refuses adoption.
Ordinary checkpoints still require full quiescence.

This slice does **not** recover an unknown fetch/read, journal return without
the atomic acquired control, interrupted parsing/judging/graph application,
arbitrary retained-reader or concurrent-source stages. Those original I03
requirements remain open and must never be silently retried. The native facade
bindings use explicit command /8 with execution /6 and service-recovery /5 with
jobs /6, not another recovery mechanism. Recovery stores are private operational evidence, not accepted
corpus content or a claim of source/model quality.

Use [collector-acquisition-recover.toml](../examples/collector-acquisition-recover.toml)
with the exact original run, recipe, output reservation and acquired cut hash;
omit the request file. Both fetched and initial owned-file Pages use the native
owner, even if the original owned file has since been removed. The service's
[fixed acquisition policy](../examples/collection-service-acquisition-recovery.toml)
still accepts network submissions only and cannot select a query cut.

For one original run needing both query and acquired recovery, the original
recipe must select research-recovery /6 before work starts. Command /6 with
execution /5 selects a query cut; command /8 with execution /6 selects an
acquired cut. Both retain the same original recipe/output bytes. Corpus command
/7 remains the existing wrapper around either explicit nested command.

The [combined service policy](../examples/collection-service-query-acquisition-recovery.toml)
uses service-recovery /6, exactly two unique permitted boundaries and
`on_restart="hold"`. Its jobs /7 retain original lifetime adoption attempts.
The operator manually calls `service.recover(run_id, boundary="query_return")`
or `boundary="source_acquisition"`; the exact selection and native cut digest
are persisted before launch. The existing HTTP recover route accepts a strict
`ghimera.service-recovery-request/1` body containing only `schema` and `boundary`.
Duplicate/unknown fields, foreign fixed-profile selections, UNKNOWN outcomes and
missing native proof refuse without a fresh contact or budget reset. Empty
recover bodies cannot select a combined boundary. Fixed service /4 (query) and
/5 (acquisition) retain their original fixed-boundary semantics.

The service does not infer a boundary from filenames, automatically select the
latest cut, or migrate a retained job to broader permissions. Unrelated legacy
controls, including explicit nulls, are invalid in native recovery /4–6 and new
service profiles. Disabled keys must be absent before a new recipe is persisted;
never rewrite an original retained recipe to make recovery pass. These connected
facade cuts still do not close arbitrary processing/graph/retained-reader or
concurrent interruption recovery, I03/I12 as a whole, or model quality.
