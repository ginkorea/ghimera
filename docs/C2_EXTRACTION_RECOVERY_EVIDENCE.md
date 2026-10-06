# HTML extraction recovery — candidate evidence

Status: local source candidate, not published or deployed. This implements F4's
one generic HTML reparse and typed attempt provenance; it does not establish
representative publisher accuracy or close the full collector objective.

## Environment

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
The standalone environment resolves no TAIPAN SDK; platform doctor/floor checks
are inapplicable. Platform credentials are removed from test invocations.

## Executable scope

The recovery checks run the actual pinned Scrapling/Crawl4AI HTML worker on a
controlled redesign: its configured selector finds an empty teaser while usable
native article text remains. Generic reparsing uses that same retained Page and
extracts the text. A collection run records one fetch and two parse attempts;
both attempts survive serialization and an omitted failed attempt is refused.
An orphan success with its extraction ledger evidence removed is also refused.

Additional checks inject a malformed worker response, an empty document, a
disabled recovery policy, a parser wall timeout, external cancellation, a caller
collection wall timeout, an unrelated adapter failure, and oversized input. They
establish bounded recovery, named terminal refusal, response-hash retention, and
normal cancellation/deadline behavior, not real-world model or parser accuracy.
Failure chains are retained even when no document is accepted. Raw diagnostic
text, source session secrets and ambient platform credentials are not receipts.

The configuration field is omitted from serialized legacy configurations when
unused. Enabling it changes the explicit effective-policy digest. The retry's
selector observations cannot masquerade as a direct/adaptive publisher match.

## Gate

Frozen-source full `scripts/gate.sh`: **287 passed in 308.66 seconds**, no
failures or skips, on the interpreter above. Ruff passed, **87 checked files**
were formatted, and strict mypy passed over **61 source files**. Browser checks
used the actual configured Chromium and `bwrap` isolation. No source/test edits
occurred during the full run. The earlier focused recovery run completed with
**10 passed in 20.99 seconds** on the same interpreter.

Re-run command (from this checkout):

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```

## Still open

Representative archived-publisher relocation and native PDF/OCR quality,
Marker, the remaining browser adapters, served-model research adequacy and
calibration, durable generic run checkpoints, and production runtime/egress
acceptance remain separate work. This does not introduce an access-control or
challenge bypass, a source refetch, or an automatic model-server dependency.
