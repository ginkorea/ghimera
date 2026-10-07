# Continue research without accepting a failed extraction

Status: development source, not a published upgrade or real-model quality claim.
The real native-document reviewer diagnostics exposed a distinct reliability
defect: one invalid review dropped the affected page's follow-up references;
some refusal codes also terminated the whole research run.
Request preflight closes impossible request shapes; it cannot make a model's
valid JSON factually or semantically correct. Recovery belongs to collection
policy, not to a permissive validator or an indefinite prompt retry.

## Explicit policy, existing owners

The [non-active fragment](../examples/semantic-failure-policy.toml) adds a
versioned `[semantics.failure]` contract to a `ghimera.semantics/4` recipe.
`action="record_gap"` continues past only the selected model refusal codes,
up to `max_failed_windows_per_run`. The ordinary model-call allowance, wall
deadline and reviewer allowance remain in force. No reservation is refunded.
The next failure beyond the cap stops the run and retains its terminal refusal.
There is no retry, alternate model, fabricated assessment or graph assertion.

Absence of this policy preserves the existing per-source refusal behavior and
serialized recipe shape. Legacy semantic profiles cannot select the policy.
Cancellation, exhausted budgets, graph/ledger storage errors and projection
contract violations remain terminal. They cannot be added to `allowed_refusals`.
With the policy selected, an exhausted/disallowed recovery carries an explicit
`SemanticRecoveryStopped` signal through the existing refusal boundary. The
collector cannot silently skip past it as it does for some legacy source errors.

`SemanticStage` remains the window/application owner; the stateless
`semantic_recovery.failed_window` constructs an immutable observation.
`GoalLoop` continues normal source/reference handling only after the stage has
recorded each attempted window. The stage does not reserve a GPU, discover a
credential or change a served model.

## Failed work is not a coverage conclusion

A refused window records `ghimera.semantic-refusal/1` beside its unchanged
source and original extractor call. Fields bind:

- document graph identity, source URL, raw/text hashes and exact native offsets;
- extraction-policy digest, extraction/review/projection phase and omitted tail;
- the unchanged validated proposal if extraction reached review;
- exact preceding review-ledger sequences and whether continuation was allowed.

Successful neighboring windows retain their own acknowledged graph assertions.
No item from an incomplete or invalid review batch is projected as a fact.
The original review-call observations retain actual request/response hashes,
usage and refusal; they are not relabeled as successful reviews. Invalid model
output remains a failure, even when collection is permitted to continue.

Intent research with this policy requires `ghimera.graph-planning/4` and its
explicit gap/context limits. Its `ghimera.planning-graph/4` view includes a
distinct `kind="semantic_refusal"` gap with the original observation and
proposal digests. It deliberately has **no fabricated coverage verdict,
omission finding or independent-model confidence**. Existing accepted coverage
gaps and optional identity/dispute questions remain separate types in that view.
Planning uses revision `ghimera-graph-planning/4` and may cite the exact gap ID
to motivate a source-grounded follow-up query. A gap grants no new host/access
permission and no automatic retry of the failed source.

Gap/context caps remain observable through omitted counts. A source retained
in an archive is not an assertion that all of its semantics were extracted.
An answer supported directly by native documents is separate from complete
organizational graph coverage; the graph's failed-work report remains visible.

## Replay and continuation

Harvest readers check the original failure policy, per-run continuation cap,
native source identity, window ordering (successful **and failed** attempts),
proposal binding and exact earlier review records. A successful later window
is not renumbered to hide a failed earlier window. Planning views replay from
the original ledger; failed proposals never become graph entities.

Completed sources with acknowledged failure gaps belong to the same session's
completed semantic-source set. Completed-round resume must restore that set
and the original spend/failure history; it must not retry them or reset the cap.
Interrupted calls still require reconciliation under the existing continuation
contract, not this failure policy.

## Acceptance still required

Controlled native-language protocol checks are regression evidence, not model
quality. Real-model complete Collector acceptance, representative organization
charts/OCR, calibrated decisions, entitled publisher/browser/Tor acceptance
and platform compilation/activation remain on the broad capability tracker.
This policy does not claim to close those requirements.

## Bounded evidence from this change

The initial native regression run passed **83 tests** in 10.34 seconds on
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout's `src/ghimera/__init__.py`. It covered recovery plus semantic
extraction/verification, graph planning and completed-round continuation. The
controlled fixtures prove the cap survives resume, earlier failures remain
visible, prior recipes retain their behavior and storage failures propagate.
After subsequent replay-contract tightening, the final recovery module passed
**17 tests** in 2.03 seconds on the same interpreter/import path, including the
complete mixed failed/successful-window archive. Ruff lint and strict mypy (98
source files) passed. These bounded checks are separate from the complete gate
recorded below.

The subsequent full `scripts/gate.sh` run on 7 October 2026 UTC passed
**696 tests, zero failed, zero skipped**, in 548.21 seconds, using
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing
`/tmp/chimera-c0-20261006/src/ghimera/__init__.py`. Offline lock validation
resolved 137 packages; lint passed, all 149 checked files were formatted,
and strict typing passed for 98 source files. The installed Chromium fixture
used the configured network isolator; source/model services in these checks
were controlled local fixtures and ambient platform tokens were unset. No
fresh model call, GPU reservation, remote collection, publication or deployment
was performed by this gate. Real-model quality and the broader collector
acceptance requirements remain open.

An offline diagnostic replayed all six retained real trial-21 response bodies
over the unchanged native Chinese passage at offsets 15,000–16,500. It reproduced
`semantic_extraction_failed` and derived one source-bound failure gap, with zero
projected entities/relations, zero network/fresh model calls and all original
file hashes unchanged. Original extractor/reviewer call metadata was retained;
replayed client-call metadata did not replace actual historical evidence. This
is failure-policy replay, **not** a fresh model trial or complete Collector run.
The original diagnostic remains documented in
[grounded evidence](ORGANIZATION_GROUNDED_EVIDENCE.md#trials-2021-native-quote-wire-and-complete-request-preparation).
