# Ghimera 0.4.2 release acceptance

Status: **COMBINED GATE AND CANDIDATE ARTIFACT ACCEPTANCE PASSED**.
Final artifacts, tag, main merge and publication still require the separate
checks below. Published 0.4.1 identities remain immutable.

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

## What this release does not close

No claim of representative publisher or multilingual accuracy follows from
controlled native files and protocol fixtures. The Simplified Chinese scanned
PDF title failure remains unchanged. Existing requirements for inline/redirected
browser files, autonomous publisher-specific controls, pagination, verified
Tor browser routing, operation-level crash recovery, entity identity resolution,
visual graph/answer quality, unattended service and representative end-to-end
quality remain tracked in [the infrastructure PRD](PRD_INFRASTRUCTURE.md).
