# Ghimera 0.4.8 release acceptance

Status: **SOURCE GATE PASSED; ARTIFACT ACCEPTANCE AND PUBLICATION PENDING**.
This increment does not close the complete infrastructure PRD. Previous public
release identities remain immutable and are not rebuilt, retagged or replaced.

## Exact source gate

Source commit `19e99ceda1520e475b298fd506834bfcb15197ff`, tree
`e30ac983bbc772da69f174e17b3c6e0dca12acef`, combines cross-run source refresh
and repeated graph-cancellation acknowledgement ownership. The frozen full
`scripts/gate.sh` completed with 1,217 passed in 1376.95 seconds, no failures or
skips. Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python` imported
`/tmp/ghimera-recovery-release-20261008/src/ghimera/__init__.py`. The offline
lock check, Ruff, formatting and strict typing also passed. Staged tree equality,
absence of unstaged changes and diff checks were verified before committing.

These are controlled native/protocol tests, not a measurement of served-model
quality, publisher coverage, multilingual accuracy or unattended deployment.

The release metadata changes only the version, its assertion, lock self-version
and documentation. Production source and examples remain byte-identical to the
gated source commit. Five metadata checks passed without skips under the same
Python 3.11.16 interpreter/source binding; the offline lock check resolved the
same 142 packages using that explicit interpreter.

## Artifact and publication requirements

Pending: offline source archive and wheel-from-archive
build, complete archive membership/source-byte inspection, Twine validation,
independent installed-wheel API/CLI and native acceptance, exact GitHub refs,
official PyPI upload and original public artifact byte equality.

Required installed acceptance includes actual loopback RSS HTTP 200/304 across
fresh processes, changed originals with retained history, content-free cache
invalidation and subsequent full GET, robots refusal before target contact,
private-store ownership and actual byte accounting. Repeated graph cancellation
must preserve a real committed file, apply its exact acknowledgement and match
fresh disk replay. These controlled checks do not establish semantic accuracy.

## Capability increment and remaining scope

`ghimera.source-refresh/1` configures exact URLs, bootstrap, private storage,
age and capacities. Conditional reuse retains the exact original/request
partition and current transport evidence; failures refuse rather than serve
stale content, delete history or downgrade persistence. See SOURCE_REFRESH.md.

Native graph append/replay use the existing drained worker boundary. Caller
cancellation does not release ownership before a pending graph acknowledgement
is checked and applied. This is not general uncertain graph/model reconciliation.

The complete I01-I14 tracker remains open, including interrupted whole-research
adoption, durable uncertain model invocation and spend reconciliation, reversible
temporal entity resolution, unattended service/API and scale, remote delivery,
representative publishers/browser-onion workflows and actual multilingual model
quality. Simplified Chinese OCR still fails its controlled full-transcription
check. Qwen-VL is a candidate for the existing explicit transcription interface;
no actual Qwen run or Chinese quality acceptance is claimed.
