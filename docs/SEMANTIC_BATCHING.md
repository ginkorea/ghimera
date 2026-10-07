# Configurable complete semantic-review batches

This is unreleased source, not a new published package or real-model accuracy
acceptance. The optional `ghimera.semantic-verification/4` profile reduces the
number of requested assessments per model answer without truncating the
original extraction proposal or pretending intermediate reasoning is a final
answer. See the non-active [recipe block](../examples/semantics-batched.toml).

## Configuration and owning boundaries

`SemanticVerificationConfig` requires explicit `max_mentions_per_call`,
`max_relations_per_call`, `max_coverage_findings`, and `max_calls_per_run`.
Older profiles refuse the new fields and retain their serialization and prompt
behavior. The configured `SelfHostedModel` exposes a narrow partitioned-review
port; `SemanticStage` owns scheduling, spend reservation, ledger observations,
and complete-set projection. No deployment choices are Python constants.

Each original mention is assessed in order, then each original relationship
using its original global index. Every request includes the **same complete
proposal, proposal digest and native source context**. One final coverage-only
request identifies concrete native-text omissions. Item calls cannot claim
whole-source coverage or introduce omission findings. Generated response schemas
bound selected array sizes and allowed keys/indices; runtime validation checks
the same sets, source spans, ontology, service pins and prompt provenance.

For `M` mentions, `R` relationships and configured bounds `m` and `r`, review
calls required are `ceil(M/m) + ceil(R/r) + 1`. An empty proposal still requires
its coverage call. Each actual request uses the existing review and global
judge budgets. Insufficient remaining allowance refuses before any review
request; an HTTP failure, invalid response, timeout or cancellation retains
previous observations and the current attempted call, with no automatic retry.
No semantic graph claims are projected from a partial batch.

## Retained evidence and replay

Actual responses remain `ghimera.semantic-review/3` observations, with explicit
`ghimera.review-selection/1` metadata in each ledger row. The assembled
`ghimera.semantic-review/4` contains every actual part and the complete assessed
key/index set. Its `model_call` is the actual final coverage call, not an
invented aggregate request. Coverage-based research gaps cite that call; every
other part remains available in the window and durable ledger. Harvest/journal
validation requires the corresponding earlier exact observations and restores
all actual review spending.

## Limits and acceptance

Smaller answers can require more prompt tokens and round trips because the full
proposal/source context is repeated. They do not guarantee final-answer output,
correct entity typing, relationship entailment, independent corroboration,
identity resolution or complete organization coverage. The observed served-model
HTTP/no-final-answer defects remain separate open acceptance items; see
[the real diagnostic record](ORGANIZATION_GROUNDED_EVIDENCE.md).

Controlled checks cover complete selection, proposal preservation, strict
schema bounds, insufficient budgets, late failure/cancellation, unchanged graph
on partial batches, coverage-gap planning, assembled collection, archive and
journal reconciliation, and exact budget restoration. These are protocol and
composition fixtures, not real-model quality evidence. Public challenge-provider
acceptance and the other [full capability tracker](C0.md) items also remain open.

## Source validation record — 7 October 2026 UTC

The full `scripts/gate.sh` used
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing
`/tmp/chimera-c0-20261006/src/ghimera`. Offline lock validation resolved 137
packages; lint passed, all 140 checked files were formatted, and strict typing
passed for 95 source files. The complete suite passed **624 tests, zero failed,
zero skipped**, in 533.69 seconds. The installed Chromium fixture ran isolated
with local fixture servers. No live CAPTCHA gateway, remote challenge target,
GPU or served inference service was used.

The prior focused invocation passed 128 checks and failed one browser fixture
because its required executable environment variable was omitted. The complete
gate supplied the installed browser/isolator explicitly; no test was skipped or
weakened. These results establish source/protocol composition and regressions,
not package publication, deployment, actual challenge solving or model quality.

## Real-model diagnostic — 7 October 2026 UTC

Trial 15 reused the unchanged native Chinese election passage and all eight
original mentions/five relationships. Explicit bounds of four mentions and
two relationships required six calls including coverage; the worker checked
that the complete set fit the remaining allowance before starting. The pinned reviewer
used low reasoning, 2,048 output tokens and a 100-second client deadline.

Two calls actually ran, returning HTTP 200 and nonempty final answers in
16.708 and 16.773 seconds. The first was structurally valid. The second
contradicted its own mention-review dimensions and was correctly refused;
the remaining calls were not made. No partial semantic graph was projected.
This improves the observed final-answer behavior in this one diagnostic,
not model accuracy, complete review acceptance or a production speed claim.

The consumer was released and its own startup job observed cancelled.
Fresh-process replay verified three ledger rows and two durable trace batches,
with zero semantic windows. See [the detailed evidence](ORGANIZATION_GROUNDED_EVIDENCE.md#trial-15-complete-batch-diagnostic).
