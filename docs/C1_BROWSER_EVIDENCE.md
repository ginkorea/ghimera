# Browser source and installed-wheel evidence — 6 October 2026

Status: local source candidate. **Not pushed, published, deployed, or full C0–C5
acceptance.** Source revision `f904c48fda073ad99564acd07323e7228352f0de` on
`gompert/chimera-c1-browser-20261006`, based on the accepted local dedup line.
This historical record describes that exact revision; later per-hop redirect
support and its new wheel are recorded in [redirect evidence](C1_BROWSER_REDIRECT_EVIDENCE.md).

## Interpreter and environment

Source gate: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**;
import `/tmp/chimera-c0-20261006/src/chimera/__init__.py`. `taipan` resolves none:
this is the standalone package, not a TAIPAN deployment check.

Installed-wheel acceptance: `/tmp/chimera-c1-browser-wheel-20261006/bin/python`,
Python **3.11.16**; import
`/tmp/chimera-c1-browser-wheel-20261006/lib64/python3.11/site-packages/chimera/__init__.py`.
The interpreter ran from `/tmp` with `-I` and no `PYTHONPATH` or TAIPAN credential.
`taipan` resolves none. Platform doctor/floor are not applicable to this check.

The installed environment was created for this task, from the exact offline
lock, with `browser` and `html` extras, no development dependencies and no
editable project install. `uv pip check` checked **103 compatible packages**.
No shared environment, browser artifact or production service was changed.

## Test-first and full gate

The first browser collection run failed with missing `chimera.browser` before
implementation. The final full `scripts/gate.sh` run on the source interpreter
above completed with **175 passed, 0 failed, 0 skipped in 158.01 seconds**. Lock
verification resolved 137 packages; Ruff and format passed for 58 source/test
files; strict mypy passed for 43 source files. Source was unchanged during the
gate. Gate handle was terminal, exit 0, before building and committing evidence.

The gate used explicit fixture artifact inputs:

```bash
CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh
```

Real Chromium executed JavaScript inside a separate network/PID namespace.
Controlled tests covered original bytes versus rendered DOM, externally sourced
script plus AJAX evidence retention, off-scope/POST/WebSocket refusal, pinned
executable failure, timeout and cancellation cleanup, resource-limit exhaustion,
shared HTTP request/byte accounting, CSP preservation, JS-created login refusal,
and DOM-aware extraction/harvest tamper rejection.

Two tests sent **initial HTML, robots and a browser subsidiary request** through
an owned real SOCKS protocol server: ordinary host with remote address resolution,
and valid v3 onion without ordinary DNS. The local DNS collaborator refused every
invocation; all successful network/resource records named Tor transport. These
are controlled routing tests, **not public Tor/anonymity or publisher acceptance**.

An initial real-browser failure exposed Chromium's Unix socket path limit in a
long host scratch path. The implemented remedy is a short explicit private
namespace mount; no host storage policy or network isolation was weakened.

## Built artifacts and installed execution

Built offline from the source revision above:

- wheel `dist/c1-browser/taipan_chimera-0.1.0-py3-none-any.whl`:
  `912a83d2fd6fc95f4ef206e0b930063d0f1986d773e64712bec13860d1233576`
- source archive `dist/c1-browser/taipan_chimera-0.1.0.tar.gz`:
  `c467f6de775faba01180bc67a45ae94f2341032fb59919098565c233e7eaa73c`

The installed wheel rendered a controlled native-English maritime report from
an originally empty article populated by JavaScript, then ran its actual pinned
Scrapling/Crawl4AI/Lingua extractor. It returned **238 native characters**, language
`en`, with original bytes preserved and distinct, source-bound DOM bytes. The
installed interpreter above produced those measurements; they are not a speed
or accuracy benchmark.

- controlled source SHA-256:
  `5fdc88e90321602bdeaab551bc2e8f5aedbe8c8f78dc989fb8576c7dc3cc1beb`
- rendered DOM SHA-256:
  `ef065cf7e3cc94f24961b01e182e8f322de9c924b3a63aee87371c92f4faab14`
- read-only provisioned Chromium executable SHA-256:
  `0b20b130e7edd9dd51873be867761295fe0cfad490c2b9a64f95bd3cfc08fa71`
- scratch: `/tmp/chimera-browser-installed-14gamk6m`

Readback proved distinct parent/worker network namespaces. `Page` JSON roundtrip
passed. The owned browser scratch was empty after reaping. The fixture used zero
subsidiary resources; actual subsidiary and Tor protocol routing are covered by
the full source gate, not attributed to this narrower installed-wheel run.
No source network request, model request, GPU, credential or TAIPAN service was
used by installed-wheel acceptance.

## Remaining closure work

Public browser/Tor corpus acceptance, Camoufox/nodriver, subsidiary redirect-hop
handling and hardened filesystem/artifact admission remain open. Full-PDF/Marker,
real encoder/scorer and served-model acceptance, one-hop references, governed
TAIPAN integration and full C5 runtime acceptance are unchanged requirements.
This evidence advances browser implementation; it does not shrink or complete
the full spider objective.
