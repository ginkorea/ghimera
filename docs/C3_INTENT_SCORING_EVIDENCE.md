# Original-intent semantic references — unreleased source evidence

## Change and ownership

`ScoringConfig.reference_source` explicitly selects `pinned` or `intent`.
Pinned mode preserves the existing supplied-reference API and serialization.
Intent mode forbids a pinned digest/bundle and prepares the original intent
through the configured `EvidenceEncoder`. It does not rewrite the intent,
silently truncate it or substitute vectors/keywords after a model failure.

`EmbeddingScorer` owns a weakly run-budget-keyed preparation lock. Its state
binds the original goal hash and exact ledger; references are cached only after
the successful call and prepared-reference observation are acknowledged. No
run-owned budget is retained by the cache value. Concurrent scores share one
preparation; separate runs still perform and pay for their own encoding.

`IntentReferenceEvidence` is a typed observation containing model-bound vectors,
native goal hash and prior encoding sequence. The shared replay validator is
used by both `Harvest` and `JournalReport`. Preparation may be present in a
partial result without a subsequent scoring result when the remaining budget
expires; this is spent work, not a successful answer.

## Measured environment and gates

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
TAIPAN SDK: absent, expected for this independent library; platform doctor/floor
are not applicable. Pytest: 9.1.1. No source or tests changed during the full gate.

Focused scoring/journal/intent-research checks: **56 passed in 17.89 seconds**
on that interpreter, no failures or skips. Controlled loopback HTTP uses the
real `SelfHostedEncoder` and private transport. Research planning/judging use
explicit test doubles; this is not research-quality measurement.

Full command, from the owned worktree:

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```

Exit 0: **306 passed in 314.22 seconds**, zero failed/skipped, on the interpreter
above. Offline lock check resolved 137 packages; Ruff lint passed, all 92 Python
files formatted; strict mypy passed for 65 source files. The gate ran outside
the command sandbox because its asyncio executor fault was independently
reproduced; the actual browser worker still uses its configured Bubblewrap
isolation. No platform tokens or remote model service were used.

## Behavioral checks

- Explicit mode/configuration mismatches refuse before encoder requests;
  pinned mode excludes the new default field from serialized configuration.
- Unicode intent and configured prefix bind ordered request hashes and character
  spend. Two scored documents reuse one prepared intent reference.
- Concurrent same-run scoring performs one preparation. Another run encodes its
  own, different intent; changing the first run's intent or ledger refuses.
- Full goal/intent-research results round-trip with original source citations;
  durable journal readback retains the exact prepared-reference observation.
- Goal, call position, prefixed input, prefix, absent/late preparation, winning
  window identity and attempted pinned/intent rebinding mutations refuse.
- Call and character budgets reserve before I/O. Oversized intents produce no
  encoder request; no partial intent is treated as the original intent.
- Encoder model refusal and cancellation retain their call spend but never
  create a prepared reference, scoring evidence or fabricated accepted document.

## Packaging and boundaries

Offline `uv build --no-sources --python <interpreter>` built local wheel and
source archives under `gate-work/intent-dist`. Binary archive inspection checked
that the wheel's scorer configuration and shared validator exactly match this
source and that the source archive includes `examples/intent-scoring.toml`.
Neither artifact includes gate-work, a virtual environment or bytecode caches.
These are **local unreleased snapshots**, not replacement published 0.2.0 files.

No claim of real encoder accuracy, model-revision attestation, calibrated
probability, publisher coverage or completed C0–C5 spider follows from these
checks. Reference vectors are client-authored observations bound to a response
hash; the ledger does not retain every model response/vector for independent
numerical replay. Configuration-only collector assembly and the remaining
browser/document, representative-corpus, governed integration and live egress
acceptance remain explicit work.
