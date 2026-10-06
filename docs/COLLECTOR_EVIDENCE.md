# Configured Collector — unreleased source evidence

## Owning change

The public `Collector` facade composes the existing concrete search, transport,
extraction, encoding, model and research ports from `ChimeraConfig`. Their
invariant-owning implementations are reused, not replaced. `SearxConfig` moved
to a lightweight configuration module with its old adapter import re-exported.
The optional main-config search field is excluded when absent, preserving the
old serialized shape; the new facade requires and retains its selected recipe.

Required recipes and supported MIME selections are validated before outbound
work. Constructor/config parsing makes no source or model request. Intent mode
uses the prior budgeted original-intent implementation; oversized intents refuse
before planning/storage. Each run has new collection/research state. Credentials
are explicit, separate in-memory inputs, never discovered from the platform.

## Measured gate and environment

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
TAIPAN SDK: absent, expected for this independent library; its doctor/floor do
not apply. Pytest: 9.1.1. No source or test files changed during either full gate.

Focused Collector acceptance: **8 passed in 20.14 seconds** on that interpreter.
The earlier Collector/search selection passed 15 checks before the additional
graph/journal composition witness was added.

First complete gate: **313 passed, 1 failed in 348.59 seconds** on the interpreter
above. The new README example had displaced the first, executable offline smoke
example and used top-level `await`. The repair restores the smoke position and
makes the service-backed example an actual async entry point. Its focused
release-metadata checks passed **3 in 0.62 seconds**; no acceptance assertion was
disabled or relaxed.

Frozen-checkout rerun:

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```

Exit 0: **314 passed in 348.63 seconds**, zero failures/skips, on the interpreter
above. Offline lock resolved 137 packages. Ruff lint passed; all 95 Python files
formatted; strict mypy passed for 67 production source files. The gate ran outside
the command sandbox because its asyncio executor fault was independently
reproduced; actual browser isolation still uses Bubblewrap. No platform token,
remote model service, public search instance or live configuration was used.

## Behavioral composition

Controlled local HTTP servers exercise the actual CurlRoute, SearxSearch,
Scrapling/Crawl4AI HTML worker, SelfHostedEncoder and completion client:

- Intent planning, native extraction, semantic relevance, coverage, answer and
  review complete as one composed path. The result round-trips and its answer
  citation matches the retained document bytes/native text. Title, byline, date
  and table text survive real HTML extraction.
- Constructor makes no outbound request. Two calls on one Collector retain
  fresh frontiers and budgets, paying their own intent encodings.
- Missing recipes/unsupported MIME selections, poisoned requests, oversized
  intents and mismatched credential bindings refuse before source requests.
- Effective SearXNG configuration round-trips; attempted provider rebinding is
  refused. Source requests do not inherit completion-service authorization.
- Enabling graph/journal uses only configuration and a run ID. The result has
  intent, question, query, source and document graph nodes; a fresh disk graph
  sink replays the same IDs. Journal readback is sealed and reconciles the exact
  ledger, receipt and effective search recipe. Result roundtrip still succeeds.
- The complete non-active TOML template validates and constructs without making
  outbound calls. Invalid/exceeded explicit configuration-read bounds refuse.

The server's completion and vector responses are protocol fixtures, not actual
LLM/encoder inference. A different declared reviewer identity is not evidence of
independent model weights. These checks establish composition/provenance, not
research accuracy, calibrated similarity or representative publisher coverage.

## Artifact checks

Offline build with cached dependencies:

```bash
UV_CACHE_DIR=/tmp/chimera-c0-uv-cache uv build --offline --no-sources \
  --python /tmp/chimera-c0-20261006/.venv/bin/python \
  --out-dir gate-work/collector-dist
```

The local wheel and source archive built successfully. Archive checks compared
the wheel's `__init__`, `collector`, `config`, `searxng` and `search_config`
payloads byte-for-byte with this checkout. The source archive contains the
Collector example, guide and tests. Neither archive includes gate-work, virtual
environments, bytecode caches or Git metadata.

The wheel was installed offline, with only its base dependencies, into the new
task-owned `/tmp/chimera-c0-20261006/gate-work/collector-wheel-env`. Its Python
3.11.16 interpreter imported `chimera` from that environment's site-packages,
not this checkout; no TAIPAN/pytest or HTML/browser/document/inference extras
were present. Public Collector import and effective-config JSON roundtrip passed
from `/tmp`, with PYTHONPATH/platform tokens unset and no network request.

These are unpublished **local source snapshots**, retaining development metadata
0.2.0. They must not replace published 0.2.0 files. No tag, registry publication,
repository rename or platform deployment occurred.

## Still open

The original completion tracker remains in force: remaining browser adapters,
Marker and representative multilingual PDF/OCR/publisher acceptance, real served
model quality/admission/calibration, source/reference adequacy, safe continuation,
platform seams and live runtime/egress acceptance. Plain text/additional MIME
adapters and a collection CLI are not delivered by this composition facade.
This closes the configuration-only assembly gap, not the full spider goal.
