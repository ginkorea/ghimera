# Ghimera 0.4.2 release acceptance

Status: **PUBLISHED AND PUBLIC ARTIFACT READBACK VERIFIED**, 7 October 2026 UTC.
Combined gate, exact final installed-wheel acceptance and independent public
byte readback passed. Published 0.4.1 identities remain immutable.

## Scope

The candidate contains caller-bound browser document downloads and explicit
navigation-format admission for initially unknown attachments reached through
the existing scored native-link frontier. Exact config examples, API binding,
artifact limits, same-session human assistance and remaining workflow gaps are
in [browser downloads](BROWSER_DOWNLOADS.md).

Source commits before the version amendment:

- `ea34e21`: bound Page/driver connection, shared browser lifecycle, PDF/DOCX
  stream admission, source/ledger/graph/archive bindings, assistance and native
  cancellation coverage. The frozen complete gate passed **938 tests in 854.83
  seconds**, zero failures/skips, under
  `/tmp/chimera-c0-20261006/.venv/bin/python` 3.11.16 importing
  `/tmp/ghimera-delivery-outbox-20261007/src/ghimera`.
- `2ca7f2e`: configured navigation formats, ordinary-HTML continuation without
  a second fetch, native link → unknown file → parser → graph flow, with no
  operator URL list. The same interpreter/checkout passed **53 browser tests in
  116.73 seconds**. The final example and exact graph-edge checks passed **5 tests
  in 17.74 seconds**. Neither result skipped tests; neither is a combined
  release gate. Strict mypy passed across 145 source files.

## Combined release gate

The frozen versioned source `9f0c174` passed the complete `scripts/gate.sh`:
**943 passed in 845.28 seconds**, exit zero, no failures or skips. Interpreter:
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. The standalone interpreter
resolves no platform SDK; platform doctor/floor do not apply. Ruff check and
formatting passed (217 files); strict mypy passed (145 source files); the offline
dependency lock checked 142 packages. Source, tests, examples and metadata were
unchanged throughout the run. Gate work:
`/home/gompert/data/workspace/TAIPAN/.codex-tmp/ghimera-042-combined-gate`.

## Candidate artifact acceptance

The candidate wheel/sdist were built from `9f0c174` while that gate ran.
Inspection checked packaged source/docs/examples against tracked bytes and
refused duplicated/unsafe archive paths, symlinks, worktree data, credentials
and model weights. The candidate wheel installed in its own environment:
`/home/gompert/data/workspace/TAIPAN/.codex-tmp/ghimera-042-release-I8CvdR/wheel-env/bin/python`,
Python 3.11.16. Its imports resolved installed `site-packages`, not source or an
editable path. The declared browser extra reports Patchright 1.63.0.

The installed API and command ran. Both retained controlled native browser
PDF/DOCX archives reopened through the installed evidence reader, preserving
original file hashes/bytes, parsed text, exact citations, session/policy and
graph bindings. The navigation-format example validated without an attachment
URL list. This readback made no browser, source or model request and is not
independent publisher or model-quality evidence. Private evidence:
`ghimera-042-release-I8CvdR/CANDIDATE_EVIDENCE.md` in the owned operator tree.

The documentation update after the full gate changes no source, tests, examples,
version declaration or dependency lock. Its README/identity regression passed
5 tests in 1.31 seconds without skips, using the same Python 3.11.16 interpreter
and source checkout above. Because README content enters package metadata, rebuild the final
wheel/sdist and reinstall/check the exact final wheel, rather than assigning the
candidate artifacts new identities. Before publication, inspect both final
archives against the committed checkout and confirm gated source equality.
Then verify GitHub main/tag pins, publish to the configured official index and
independently read back public artifact hashes and original bytes. Publication
is not inferred from a successful upload command.

## Published identity and final artifact acceptance

- Tagged source: `aa4ecf8c3c5c1c45282b1b715a9b46cdcf2a6e0e`.
- Immutable annotated `v0.4.2` tag:
  `f2d22e93eb7b91640387bb752aff485c99340dd0`.
- [Public release](https://pypi.org/project/ghimera/0.4.2/).
- Wheel: 333,641 bytes; SHA-256
  `df3edb1f66522402c40b8dfb500c47d6a5d2b00dc6b612903fbaf211909f6353`.
- Source archive: 1,014,039 bytes; SHA-256
  `1a9852a4aab33b12befbd7312d7408ba5767b5043a4b231f2bbeb0be124bc6a7`.

The final archives were built from the committed checkout, inspected against
tracked file bytes, and passed Twine metadata checks. `git diff` confirmed that
source, tests, examples, version declaration and lock are unchanged from gated
`9f0c174`. The final wheel replaced the candidate in the independent Python
3.11.16 wheel environment above; the API/CLI and both native file archives passed
the same original-byte, exact-citation, policy and graph readback checks again.

GitHub main fast-forwarded from `e1da314` to the tagged source; a separate
remote read returned the same main, annotated tag and peeled commit identities.
Official PyPI version-specific metadata was absent before publication. Twine
6.2.0 uploaded only these artifacts through the existing named `pypi` profile
to exactly `https://upload.pypi.org/legacy/`; no credential was logged.

The first immediate metadata read returned HTTP 404 during propagation. The
same readback was repeated without re-uploading. It returned both exact
filenames, sizes and SHA-256 values, neither yanked. Both files were then
downloaded from the metadata's verified-HTTPS `files.pythonhosted.org` URLs,
without credentials or redirects, and compared byte-for-byte with the checked
final local files. Private original public bytes and metadata are retained in
`ghimera-042-release-I8CvdR/public-readback` in the owned operator tree.

This publication record is a later documentation-only commit; it is not
retroactively embedded into or substituted for the published source archive.
No existing release tag or artifact moved.

## What this release does not close

No claim of representative publisher or multilingual accuracy follows from
controlled native files and protocol fixtures. The Simplified Chinese scanned
PDF title failure remains unchanged. Existing requirements for inline/redirected
browser files, autonomous publisher-specific controls, pagination, verified
Tor browser routing, operation-level crash recovery, entity identity resolution,
visual graph/answer quality, unattended service and representative end-to-end
quality remain tracked in [the infrastructure PRD](PRD_INFRASTRUCTURE.md).
