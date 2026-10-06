# ghimera 0.3.0 release acceptance

The repository/distribution/import/command use `ghimera`. Primary source lives
under `src/ghimera`; the narrow legacy root facade owns no second implementation.
Nested imports migrate explicitly. Versioned data schemas and recorded prompt
revisions retain their existing identities. Old go-spider releases are untouched.

## Native gates

The sequential full `scripts/gate.sh` on 6 October 2026 used
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, importing
`/tmp/chimera-c0-20261006/src/ghimera/__init__.py`:

- Offline lock check: 137 resolved packages.
- Ruff lint and format: passed, 107 files.
- Strict mypy: passed, 76 source files.
- Entire pytest suite: **360 passed, 0 failed, 0 skipped**, 382.35 seconds.

Three new release-identity/compatibility/schema checks were observed failing
before the rename. The bounded release/package/command suite subsequently
passed 21 tests on the same Python **3.11.16** interpreter. A sandbox's initial
localhost socket denial was a fixture permission error, not a product failure;
the required native socket permission was used for the actual acceptance runs.

Source and test files were not edited while the full suite was in flight.
Subsequent documentation-only namespace clarification does not establish any
additional model/corpus accuracy claim.

## Installed wheel, not editable source

An isolated wheel environment at
`/tmp/chimera-c0-20261006/gate-work/ghimera-030-wheel-venv/bin/python`, Python
**3.11.16**, imported `ghimera` from that environment's `site-packages`, checked
distribution version 0.3.0, exact legacy-root object identity and console help.
It also read the retained real research archive through the installed reader,
validated native citations and answer-review digest binding, and confirmed the
first durable graph batch contains the intent node. No platform SDK or inference
stack was installed in that environment. Twine metadata checks passed for both
wheel and source archive. Private working files, .git pointers and bytecode were
excluded from the artifacts.

Publication is a separate native upload/readback outcome, not implied by these
checks. Final artifact digests and publication responses are retained in private
release records and can be checked against PyPI metadata; no credentials ship.

## Capability boundaries

See [concrete live research](C3_LIVE_RESEARCH.md) for the successful assembled
English run and its same-model/secondary-source limitations. See
[organization research](ORGANIZATION_RESEARCH.md) for the additional local-PDF,
semantic extraction, resolution and persistent graph-driven expansion required
by that owner use case. Representative publisher/browser/Tor, PDF/OCR/Marker,
independent calibration, checkpoint/resume and runtime/platform acceptance are
still open. This release consolidates working source; it does not label those
requirements complete.
