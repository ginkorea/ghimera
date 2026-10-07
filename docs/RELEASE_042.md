# Ghimera 0.4.2 release candidate

Status: source candidate. No tag, main merge, artifact build or publication is
claimed by this record. Published 0.4.1 identities remain immutable.

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

The version metadata now names 0.4.2, so its exact combined source needs a new
full `scripts/gate.sh` result. Then build the exact wheel/sdist, verify installation
from the wheel in an independent environment, verify the GitHub main/tag pins,
publish to the configured official package index and independently read back
public artifact hashes/bytes. Record each step separately rather than inferring
publication from a successful upload command.

## What this release does not close

No claim of representative publisher or multilingual accuracy follows from
controlled native files and protocol fixtures. The Simplified Chinese scanned
PDF title failure remains unchanged. Existing requirements for inline/redirected
browser files, autonomous publisher-specific controls, pagination, verified
Tor browser routing, operation-level crash recovery, entity identity resolution,
visual graph/answer quality, unattended service and representative end-to-end
quality remain tracked in [the infrastructure PRD](PRD_INFRASTRUCTURE.md).
