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
requirements remain open and must never be silently retried. Existing
command/service schemas in this core candidate do not yet select
`source_acquisition`; their fixed single-boundary facade bindings are separate
integration work, not another recovery mechanism or automatic multi-boundary
restart policy. Recovery stores are private operational evidence, not accepted
corpus content or a claim of source/model quality.
