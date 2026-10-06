# Collector command — local source evidence

Status: unreleased source candidate, not published or deployed.

## Change and contract owners

`chimera.command.CommandOptions` owns versioned command paths, identity and read/
output allowances; `CredentialBindings` owns explicit environment-name binding,
not secrets. The command composes the existing concrete `Collector`; validation
of an oversized original intent is reused through `Collector.validate_request`,
not copied into the command. `ResearchResultArchive` owns one private output
directory, immutable result publication, receipt-last sealing and bounded replay.
It revalidates the existing research result instead of inventing another harvest.

The archive retains original bytes/native text, duplicate occurrences, exact
citations, ledger/graph and effective extraction/model policy. It cannot upgrade
partial research to answered or turn an interrupted write into a complete archive.
These are local storage/composition claims, not real-model quality or a platform
registration/deployment claim.

## Interpreter and executable verification

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
Pytest **9.1.1**. The TAIPAN SDK is absent as expected for the independent library;
its doctor/floor do not apply. Every run below used this interpreter/checkout.

Tests-first collection failed on the missing `chimera.command` module before
implementation. Concrete controlled-server acceptance exercises CurlRoute,
SearXNG, actual Scrapling/Crawl4AI extraction and the private completion/embedding
clients, followed by full archive readback and exact native citation matching.
Model and embedding responses are protocol fixtures, not inference or accuracy.

The complete initial frozen gate passed **325 tests in 362.57 seconds**, zero
failures/skips. A separate installed-wheel probe then demonstrated that default
argparse errors echoed a malformed argument value. Two new regression cases went
red on that behavior; the parser now emits only `command_arguments_invalid`.
Those two cases plus CLI-help/error acceptance passed **3 in 1.55 seconds**.

Final frozen source:

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```

Exit **0**, **327 passed in 363.34 seconds**, zero failures/skips. Offline lock:
137 packages. Ruff lint/format: clean, 99 Python files formatted. Strict mypy:
70 production source files clean. No source/test edits occurred during either
complete gate. The gate ran outside the command sandbox for its independently
verified asyncio worker fault; real browser isolation still used Bubblewrap.

Additional acceptance covers missing/invalid credentials and oversized input
before outbound work; preservation of originals after seal failure; no overwrite;
owner-private modes; symlink/hardlink/public-directory refusal; hash/size mismatch;
missing seal; honest partial-result replay; and body/credential-free CLI stdout.
The CLI status test uses a receipt fixture to exercise exit mapping; it is not
described as live model research.

## Local artifact verification

Offline local build uses `uv build --offline --no-sources`, the interpreter above,
`UV_CACHE_DIR=/tmp/chimera-c0-uv-cache` and `--out-dir gate-work/command-dist`.
The command, archive, entry-point and collector wheel payloads are checked against
the source; the source archive includes the guide, examples and behavioral tests.
The first archive audit found the worktree's root `.git` pointer file in the
source tarball: checking only `.git/` directories had missed it. Explicit Hatch
exclusions now cover the pointer file as well as directories, task artifacts,
environments and bytecode. Both rebuilt archives are checked for those entries.
This packaging-only repair follows the full source gate above; lock and release
metadata checks and a real offline rebuild/audit verify the changed build rules.
Release metadata acceptance passed **3 in 0.62 seconds** on the source interpreter
above; the offline lock still resolves 137 packages. Both final archive audits
passed, and the entry-point/command/archive/collector payloads plus this evidence
document match their source bytes.

A fresh base-dependency-only environment is
`/tmp/chimera-c0-20261006/gate-work/command-wheel-env`. Its Python **3.11.16** imports
from its own site-packages, not this checkout, with PYTHONPATH/platform tokens
unset. No TAIPAN SDK, pytest, Scrapling, Crawl4AI, Patchright, Docling or torch is
installed there. CLI help works without optional extras; malformed-argument
diagnostics do not echo their values. Optional workers remain required for actual
configured collection and are not claimed to be present in this wheel-only check.
That installed reader also replayed the controlled-server run's full archive:
status answered, one retained original document and a matching native citation.
This is the protocol-fixture result, not an independently established answer.

These are unpublished local snapshots with development metadata 0.2.0; they must
not replace published 0.2.0 files. No tag, remote rename, push, publication, model
startup, shared-runtime change or platform deployment occurred.

## Full-goal boundary

This closes the explicit command and complete-result persistence gaps, not the
entire spider. The original C0–C5 scope remains: remaining browser adapters,
Marker/representative document and publisher acceptance, admitted real models
and calibrated decisions, safe process-restart continuation, the platform seam,
and live runtime/egress acceptance. See C0.md and COLLECTOR_COMMAND.md.
