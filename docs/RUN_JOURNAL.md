# Durable run observations

Status: included in the ghimera 0.3.0 release line; production deployment is separate. This implements the
general per-run JSONL ledger and completion summary, independently of the graph
journal. It is not automatic checkpoint/resume or distributed storage.

## Configuration and ownership

Merge the `[journal]` table from `examples/journal.toml` into your main config.
`chimera.run-journal-config/1` requires an explicit absolute directory, maximum
record/journal/summary byte counts and row count. The storage root and every run
directory must belong to the process owner with mode 0700; files must be regular,
owner-private 0600 files with no extra hard links. Symlink redirection is refused.
Choose the data volume intentionally. There is no implicit home or cache path.

Pass a new `run_id` to `GoalLoop.run` or `ResearchLoop.run`. When journaling is
configured, missing, unsafe or existing identities refuse **before collection**.
An existing run directory is evidence, never a location to truncate or overwrite.
Omitting `[journal]` preserves explicit embedded-library volatile operation and
the old config serialization; it does not claim durable observations.

`Ledger` owns append order and depends on the narrow `LedgerSink` protocol.
The configured directory implementation writes and fsyncs each row before the
in-memory ledger acknowledges it. Storage/bounds failure becomes the named
`ledger_sink_failed` refusal; it never silently falls back to volatile storage.
After a failed write, that sink refuses further appends. Source requests and
model calls are not repeated by the storage layer.

## Files and completion

Each run retains:

- `header.json`: versioned run id, intent/seeds, effective non-secret config and
  judge identity, written atomically before the first observation.
- `ledger.jsonl`: versioned entries containing the full typed `LedgerRow` and a
  previous-entry hash. The first entry binds the header. Fetches, parse attempts,
  extraction, model and encoder calls, refusals, references and stop events all
  use this same ledger, including intent-driven research rounds.
- `summary.json`: atomically published only after a validated harvest reconciles
  with the persisted ledger. It binds the header, final entry, row count, receipt
  and accepted document source/native-text hashes. It does not duplicate all raw
  document bodies; retain the returned harvest for those originals.

Hashes detect accidental corruption/order mismatch; they are not signatures or
proof against a malicious owner rewriting all records. Journals can contain
authorized source content and model input/output observations. Keep the private
permissions and apply your retention policy. Configured source sessions contain
only binding metadata: credential values are not written into these files.

The reader checks versions, ownership, file/row/byte bounds, hash-chain order,
effective-policy binding and summary spend counts. A journal without a valid
summary is `unsealed`, not "running" or "complete". A torn final line in an
unsealed journal returns its valid prefix and `incomplete_tail=true`; those bytes
remain untouched. A torn, shortened or mismatched sealed journal refuses. This
is observation replay, not permission to resume/refetch or reset budgets.

```python
from ghimera.journal import read_journal

report = read_journal(config.journal, "your-run-id")
```

```bash
python -m ghimera.journal --config /path/to/collector.toml --run-id your-run-id
```

The CLI prints only run state, row count, incomplete-tail flag and stop reason;
not source bodies, cookies, model context or endpoints. Exit 0 means a verified
completion summary, 1 means an unsealed prefix, 2 means storage/validation refusal.
Completion of the observation journal is not an "answered" research verdict;
inspect the stop reason and the research result's evidence/review decision.
It performs no source/network request and changes no journal file.

## Remaining work

Frontier/content-index/research-plan checkpoints and safe continuation across a
process restart remain a separate adapter. Neither an interrupted journal nor a
graph checkpoint alone can prove that all paid/in-flight calls have reconciled.
Node runtime/egress doctor and production deployment acceptance are also distinct.
