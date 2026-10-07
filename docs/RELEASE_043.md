# Ghimera 0.4.3 release acceptance

Status: **FULL GATE AND INSTALLED CANDIDATE ACCEPTANCE PASSED**;
tag, final artifacts and public readback must be recorded separately.
Existing release tags and artifacts remain immutable.

## Scope

An explicit versioned inline-document policy permits one bounded same-origin
GET through the caller's actual browser session after an admitted inline
navigation. It exports no cookies and does not substitute viewer HTML for the
original. Distinct response evidence survives the existing parser, citations,
graph, journal and archive. Cancellation aborts only the owned fetch and leaves
caller tabs open. See BROWSER_INLINE_DOCUMENTS.md for configuration, extra-request
behavior, unknown browser traffic and refusal conditions.

## Frozen source and complete gate

Source `5c070574e1dfe34b79abaa22899b836e764768d8` passed the complete
`scripts/gate.sh`: **950 passed in 869.41 seconds**, zero failures/skips, exit zero.
Interpreter `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, imported
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. No platform SDK resolves in
this standalone environment; platform doctor/floor do not apply. Ruff check and
formatting passed 219 files; strict mypy passed 146 source files; the offline lock
checked 142 packages. Source/tests/examples/metadata were frozen throughout.
Gate outputs live in the owned `ghimera-043-combined-gate` operator directory.

Prior checks on that same interpreter and owned source returned 63 browser and
importer regressions in 101.99 seconds, then 12 final policy/provenance checks in
30.76 seconds, neither with failures or skips. The complete gate supersedes
those as release-software evidence, not as a broader quality benchmark.

## Independent candidate wheel

The initial wheel/sdist from the frozen source passed tracked-byte archive
inspection and Twine metadata checks. Only declared source/docs/examples entered
the archives: no weights, private stores, credentials or Git metadata.

Python 3.11.16 in the owned
`ghimera-043-release-R1PMMC/wheel-env/bin/python` installed the exact wheel and
browser extra offline; imports resolve installed `site-packages/ghimera`, not
an editable checkout. Patchright reports 1.63.0. The installed public API/CLI
ran. Its reader reopened the real controlled native PDF, DOCX and inline-PDF
archives from the full gate, retaining original byte hashes, native parsing,
exact citations, session/policy, observed inline response and graph identities.
The non-active inline example validated. That readback made no source/model
request; planner/judge/reviewer fixtures are not real-model accuracy evidence.

The installed candidate also captured the public W3C dummy PDF with robots
honored through the normal FetchLadder. It retained 13,264 original bytes and
replayed their `browser_response` provenance; BROWSER_INLINE_DOCUMENTS.md records
the hash and reconciled ledger. This establishes one public sample, not general
publisher coverage or entitled-account acceptance.

## Final artifact and publication procedure

This post-gate update changes documentation only. Rebuild and inspect the exact
final wheel/sdist, reinstall that wheel independently and repeat archive/API/CLI
acceptance. Confirm code, tests, examples, version and lock match the gated
source; do not assign old artifacts new identities. Push a new immutable tag
and fast-forward the remote main, without altering the owner's dirty checkout.
Upload only those checked files to the named official PyPI destination. Read
back public metadata and both original artifact files over verified HTTPS and
compare them byte-for-byte. Upload success alone is not publication acceptance.

## Still open

The complete smart-collector goal remains active: representative Pacific OCR,
semantic/retrieval quality, entitled publisher workflows, redirected files,
pagination, verified Tor-browser networking, operation-level crash recovery,
entity identity resolution, cached-evidence answer reuse, complete visual graph/
answer integration and unattended service/delivery remain tracked in the PRD.
The controlled Simplified Chinese OCR failure is not fixed by this release.
