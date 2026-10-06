# Restart-safe research continuation

This is post-0.3.0 feature-branch source, not a published upgrade or a real-model
accuracy claim. It supports **completed research-round checkpoints** and an
explicit suspend/resume API. It does not blindly replay an interrupted request.

## Configuration and use

Append [the typed policy](../examples/continuation.toml) to an intent recipe
that already declares its owner-private `[journal]` directory. Every successful
research round then replaces one bounded, fsynced `checkpoint.json` in that
run's existing journal directory. Missing/invalid policy refuses suspension;
it never silently switches to volatile persistence.

```python
from ghimera import Collector, ResearchSuspended

# Load the same configured recipe and explicit credentials on both processes.
# collector = Collector.from_toml(...)
try:
    result = await collector.run(
        "Map this organization's leadership and unresolved relationships",
        run_id="organization-research",
        suspend_after_rounds=2,
    )
except ResearchSuspended as paused:
    receipt = paused.receipt
    # Persist this non-secret receipt in your application's task record.

# Later, using a newly constructed Collector with the exact same recipe:
result = await collector.resume(
    receipt.run_id,
    checkpoint_sha256=receipt.sha256,
)
```

An explicit pause raises `ResearchSuspended` carrying a typed run/digest/byte/
completed-round receipt. It is not a completed answer or a failure report.
`suspend_after_rounds` counts newly completed rounds in this invocation, not a
new total-round allowance. Omit it to run normally. A run that answers before
the requested boundary returns its ordinary reviewed result.

Completed source bytes, native text, citations, occurrences, search responses,
question pack, prior assessments, acknowledged semantic graph and pending
priority frontier survive a process restart. Reference-hop/parent/query limits,
dedup representatives and latest observed source revisions remain run-owned.
The embedding scorer reuses its journaled original-intent vectors instead of
charging an unnecessary second preparation call.

If coverage was already adequate at suspension, resume begins with answer and
independent review, not another search/extraction/assessment pass. Otherwise it
plans from retained evidence and consumes unfinished frontier work. All prior
ledger rows remain an unchanged prefix of the eventual complete journal.

## Invariants and failure handling

- The checkpoint binds the exact request, recipe, model/search identities,
  private journal, graph snapshot and cumulative spent counters.
- Page, byte, model, encoding, reference and total-round budgets never reset.
  Configured wall time includes downtime. A backwards wall clock refuses;
  an expired budget returns a partial result without new source/model calls.
- An exclusive native journal writer lock prevents resuming beside a live
  writer. It releases on deliberate suspension, failure/cancellation and sealing.
- The checkpoint byte allowance applies before disk publication and parsing.
  Files remain owner-private; symlinks, changed recipes, wrong digests, altered
  graphs, sealed runs, torn journals and any later journal rows refuse resume.
- Source/session credentials and clearance cookies are not checkpointed. A
  resumed caller supplies its existing explicit credential bindings again.
- Hashes detect corruption/bind a caller's expected version. They are not a
  signature against an owner who can modify all local evidence.

If interruption happens **after** a saved boundary, preserve and reconcile its
later journal/graph observations; this implementation refuses to guess whether
an unfinished external request executed. It neither truncates observations nor
automatically spends again. Fine-grained in-flight reconciliation, continuation
after changing the recipe/model or increasing the original budget remain
separate open work. The versioned [resumable command](COMMAND_CONTINUATION.md)
now connects these APIs to ordinary terminal/task execution; its suspension
receipt is not a completed archive. The legacy command remains one-shot.

## Verification

Tests exercise a fresh Python process resuming and sealing the same journal,
answer-ready and unresolved-frontier paths, exact prior-ledger preservation,
shared budget exhaustion, downtime, graph mutation and no-write refusal,
changed models/recipes/pins, torn/later/sealed journal refusal and a live-writer
collision. Model and discovery responses are controlled fixtures, not evidence
of research quality on real organizations. No live inference or remote
collection is implied.

The full `scripts/gate.sh` run on 6 October 2026 used
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing this
worktree's `src/ghimera`. Offline lock validation resolved 137 packages; lint
and formatting passed for 124 files, strict typing passed for 88 source files,
and the suite passed **429 tests, zero failed, zero skipped**, in 446.33 seconds.
Browser fixtures used the configured isolated Chromium runtime and local fixture
servers. An initial attempt stopped at typecheck on a reused local variable;
renaming that variable preceded the clean full rerun. No test was skipped or
weakened to obtain this result.
