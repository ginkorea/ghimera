# Ghimera 0.4.3 release acceptance

Status: **PUBLISHED AND ORIGINAL PUBLIC ARTIFACT BYTES VERIFIED**.
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

## Recorded publication

The new annotated tag `v0.4.3` is
`10109ee9a9fc9235cb19bc7affe33ad6d8bcb992`, pointing at source
`a3c104c078de4fb7f41661e5a17f7a91c41e7dd4`. GitHub main fast-forwarded to
that source; independent remote reads confirmed both identities. The original
owner checkout and its local edits were not changed. Code, tests, examples,
version and lock match the fully gated `5c07057`; later changes were documentation.

The final files were rebuilt offline, checked against tracked archive contents,
and passed Twine metadata validation. The final wheel was reinstalled into the
independent Python 3.11.16 wheel environment above; API/CLI and all three native
archive readbacks passed again. The documentation-only README/identity checks
passed five tests in 1.36 seconds under the source-gate Python 3.11.16, no skips.

| Original public artifact | Bytes | SHA-256 |
|---|---:|---|
| `ghimera-0.4.3-py3-none-any.whl` | 337,464 | `9984cce2b590927f1a35f1b540adb3f63592ce6f4816c00ce2ff40d53d24078b` |
| `ghimera-0.4.3.tar.gz` | 1,024,059 | `f7881c81289f00ad51d0f4cd182a7e75391e293b6667e28e084aa98ffcafbe0d` |

Only those files were uploaded through the named official PyPI profile.
Unauthenticated, TLS-verified, redirect-refusing readback of official metadata
and the original `files.pythonhosted.org` bytes proved exact names, sizes,
digests, no yanks and byte equality with local artifacts. Readback used Python
3.11.16 in the private tool environment; publication evidence and original
downloads remain under the owned `ghimera-043-release-R1PMMC` operator tree.
Public release: https://pypi.org/project/ghimera/0.4.3/.

## Still open

The complete smart-collector goal remains active: representative Pacific OCR,
semantic/retrieval quality, entitled publisher workflows, redirected files,
pagination, verified Tor-browser networking, operation-level crash recovery,
entity identity resolution, cached-evidence answer reuse, complete visual graph/
answer integration and unattended service/delivery remain tracked in the PRD.
The controlled Simplified Chinese OCR failure is not fixed by this release.
