# Durable run journal — candidate evidence

Status: local source candidate, not published or deployed. This implements the
general JSONL run observations and summary portion of F10. Production node
doctor/egress, continuation checkpoints and real served-model acceptance remain
open; a fixture research answer is not evidence of model accuracy.

## Environment

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
No TAIPAN SDK resolves in the independent environment; its platform doctor/floor
checks are inapplicable. Platform credentials are removed from invocations.

## Evidence scope

Focused journal/core/intent checks passed **34 tests in 0.94 seconds** on this
interpreter before the CLI inspection check was added. Real local filesystem
storage (not a memory sink) exercised collection and intent-research fixtures,
confirmed exact persisted/returned ledger equality and receipt binding, and
verified 0700/0600 ownership permissions and immutable run identities.

Interruption leaves a typed unsealed prefix without a fabricated completion.
A torn final line stays on disk and is reported explicitly. Changed, missing,
reordered or truncated sealed events refuse. Limits, unsafe/shared/symlink paths,
missing run identities and failed fsync refuse; the volatile ledger does not
acknowledge a failed write and the sink does not silently retry it. Inspection
does not modify journal contents or perform source/model requests.

These are filesystem/protocol checks, not a simulated power-cut result, a disk
performance benchmark, an anti-forgery signature or automatic resume acceptance.
Existing omitted journal configuration preserves its old serialized identity.

## Final gate

Frozen-source full `scripts/gate.sh`: **295 passed in 308.67 seconds**, no
failures or skips, on the interpreter above. Ruff passed, all **91 checked files**
were formatted, and strict mypy passed over **64 source files**. Browser checks
used the actual configured Chromium with `bwrap` isolation. No source/test edits
occurred during the final run.

The final source also exercises `python -m chimera.journal` in a fresh process
against an actual persisted completed fixture run, with exact safe summary
stdout and empty stderr. An import-order warning found by separately launching
the CLI was removed before the final frozen-source run.

Re-run command (from this checkout):

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```
