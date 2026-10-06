# Deduplication candidate evidence

Source candidate: `6ad59a6` on `gompert/chimera-c2-dedup-20261006`.
Not pushed, published, deployed, or claimed as the completed spider.

## Environment and full gate

`/tmp/chimera-c0-20261006/.venv/bin/python` is Python **3.11.16**, importing
`/tmp/chimera-c0-20261006/src/chimera/__init__.py`. TAIPAN resolves none;
platform doctor/floor checks are not applicable to this standalone package.

Command: `env CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh`.
The full run used the normal local process namespace for its owned loopback
model/search/network fixtures and bounded mutation subprocesses. Results on
that interpreter: **159 passed, 0 failed, 0 skipped**, **108.90 seconds**;
strict mypy passed on 39 source files; Ruff checks/formatting passed on 53 files;
offline lock verification resolved 137 packages. No source was edited during
the gate. The README was corrected to install both parser extras before it.

Earlier focused coverage on the same interpreter passed **67 tests in 28.33
seconds**. A restricted-namespace attempt was interrupted/terminated by its
exact owned PID after a loopback fixture stalled; another restricted mutation
run produced network-fixture setup errors. Those are not accepted runs. No
production process, shared runtime, credential or GPU was used or modified.

## Executed behavior

- Explicit canonicalization strips only configured tracking keys, preserving
  meaningful queries; a foreign-host canonical hint cannot alias the source.
- Raw bytes, normalized native text and SimHash near matches carry separate
  reasons. English and Chinese character-shingle fixtures execute the actual
  matching implementation, not a dedup-shaped double.
- Near matching is language-bound and minimum-length/length-ratio gated.
  Changed numerical sequences are not merged. Capacity/text limits refuse
  rather than evicting evidence or comparing truncated prose.
- Two source URLs are collected through the loop with independent extraction
  and judge verdicts; one representative retains both occurrences' bytes,
  native text, verdicts, hashes and policy-bound similarity evidence.
- Tracking aliases with changed content produce a drift row naming both
  retained revisions. The harvest reader recomputes similarity and drift.
- Mutating a representative digest or ledger policy digest is rejected on read.
- A distinct duplicate occurrence is independently citable. Identical-byte
  mirrors retain URL-qualified citation lookup and model-context windows,
  preventing the second URL from shadowing the first.
- Existing saturation, budgets, configured-model refusals, extraction,
  transport, graph and intent-research suites remain green in the full gate.

## Limits of this acceptance

The new content cases are controlled fixtures, not measured false-merge
accuracy over a representative public corpus. SimHash is a similarity signal,
not proof of factual equivalence or a calibrated probability. This change
does not eliminate the independent judge call for each accepted occurrence.
Content drift is observed within a collection session; it is not cross-run
change monitoring or the required locator-drift doctor. Full PDF layout/OCR,
Marker, browser-wide Tor verification, production model admission and TAIPAN
C4/C5 acceptance remain open in the main tracker.
