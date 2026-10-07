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

### Opt-in assigned-role questions

The non-active [assigned-role example](../examples/review-assigned-role.toml)
selects `prompt_profile = "assigned_role_checks"` inside verification/4.
This explicitly changes the review prompt revision to
`ghimera-semantic-verification/5` without changing extraction assertions,
partitioning, output schemas, checks, spend ownership or complete-set projection.
Omitting the field retains the exact original verification/4 prompt and
serialized policy. Earlier verification profiles refuse this option.

The clarification asks each review to locate the original mention's assigned
role and its corresponding configured definition. An institution need not be
an office or a person; an office need not have a named incumbent. Explicit native
functions, membership or election can identify a specific body without a
separate existential sentence. Literal presence or familiar names alone still
do not establish the type. Each verdict must match all its required dimensions;
the validator still rejects contradictory answers rather than repairing them.
The profile addresses reasoning observed in trial 15; instructions alone do
not establish real-model quality or full organization coverage.

The assigned-role source gate on 7 October 2026 UTC used
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing this
checkout's `src/ghimera`. The complete `scripts/gate.sh` passed 631 tests,
zero failed and zero skipped, in 534.41 seconds. Offline lock validation
resolved 137 packages; lint and formatting passed for 141 files and strict
typing for 95 source files. The installed browser was isolated and fixture
servers were local; no model or remote challenge provider was used by the gate.
The initial contract witness failed before implementation; its existing-prompt
fingerprint remained unchanged after the fix. A broader sandboxed focused run
timed out in fixture I/O and is not counted as a pass. This full source gate
does not establish live quality, publication or deployment.

## Optional original-date schema binding

`prompt_profile = "proposal_date_checks"` is a separate explicit option under
verification/4. It retains the assigned-role questions and selects request
revision `ghimera-semantic-verification/6`. Existing recipes without a profile
remain revision 4; `assigned_role_checks` remains revision 5 with its unchanged
request schema. See [the nonactive example](../examples/review-proposal-dates.toml).

This profile requires the selected reviewer binding to declare
`response_format = "json_schema"`; JSON-object-only bindings refuse at the
configuration boundary. Each selected original global relation index receives
its own schema branch. If both original dates are null, the branch requires
`validity.asserted = false` and `assessment = null`. If either original date is
present, it requires `asserted = true` and an independent, non-null date
assessment. This encodes an input fact, not whether that date is correct.
Entailment and direction retain their independent verdicts and reasons.

The schema does not rewrite dates, reduce the original proposal, change batch
selection, force a supported verdict, repair a response or relax native
validation. Even if a provider ignores its grammar, existing source/date and
aggregate checks still refuse mismatched answers. Mention-only and coverage
calls acquire no irrelevant relation branches. No provider, endpoint, runtime,
GPU allocation, retry or budget is selected by this option. Standard JSON-schema
checks establish the generated schema's shape, not a serving engine's actual
grammar support or model quality; those require bounded live acceptance.

The full `scripts/gate.sh` for this profile on 7 October 2026 UTC used
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout's `src/ghimera`. It passed **646 tests, zero failed, zero skipped**, in
536.71 seconds. Lint and formatting passed for 142 files; strict typing passed
for 95 source files. Offline lock validation resolved 137 packages; the existing
JSON-schema validator is now an explicit pinned development dependency, not
a runtime dependency. The first contract failed before implementation. The
focused date/role run passed 22 tests in 2.41 seconds in the same environment;
a broader sandboxed related run timed out in fixture I/O and is not counted as
a pass. The full gate used installed isolated Chromium and local fixture
servers, not a live model or real CAPTCHA service. Source/protocol validation
does not establish publication, deployment or model accuracy.

## Opt-in independent dimensions

The nonactive [independent-dimensions example](../examples/review-independent-dimensions.toml)
selects `prompt_profile = "independent_dimension_checks"` under verification/4.
It requires the reviewer's explicit `json_schema` response format and selects
prompt revision `ghimera-semantic-verification/7`. This is a new model-facing
`ghimera.semantic-review/5` wire: each selected mention and relationship returns
its independent checks and reasons, **not an overall verdict**. Supplying a
generated overall verdict is an unexpected-field refusal, not answer repair.

`derive_independent_review` computes the existing fail-closed aggregate: any
unsupported applicable check means unsupported; otherwise any ambiguous check
means ambiguous; only all-supported checks mean supported. There is no earlier
generated summary to constrain later factual judgments. The profile retains
assigned-role questions and binds date assertion state to original relation
indices. Each check's substantive correctness still depends on the model and
native evidence; deterministic summaries do not establish factual accuracy.

The client retains the original typed dimension payload in `dimension_response`
beside the actual call evidence in each grounded review. Replay checks every
original key/index, reason, check and coverage field against that payload.
The payload is client-owned provenance, never requested from a model; absent
fields leave older profile schemas, prompts and serialized records unchanged.
The selected profile cannot be replayed without the payload or as an older
profile. Batching, native grounding, omitted-item witnesses, date validation,
budgets and no-partial-projection guarantees remain unchanged. No service,
runtime, endpoint, retry or allocation is selected by this profile.

This source closes redundant-summary consistency, not served-model quality,
full Collector acceptance, publication or deployment. The real input used in
trial 17 must still pass a complete live review before that acceptance is
claimed; broader entity typing, relationship entailment and coverage calibration
remain separate requirements.

The complete `scripts/gate.sh` for this profile on 7 October 2026 UTC used
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout's `src/ghimera`. It passed **660 tests, zero failed, zero skipped**,
in 540.59 seconds. Offline lock validation resolved the unchanged 137 packages;
lint and formatting passed for 143 files and strict typing for 95 source files.
The new profile's first contract failed before implementation. Controlled
checks cover all supported/ambiguous/unsupported dimension combinations,
unchanged older request identity, original-date schema binding, refusal of
invented aggregate verdicts, retained-payload integrity, profile-bound replay,
late failure without retry and witnessed coverage gaps without invented nodes.
The gate used installed isolated Chromium and local fixture servers, not a live
model, GPU or real CAPTCHA provider. Its source acceptance is not publication,
deployment, complete Collector acceptance or model accuracy.

## Retained evidence and replay

Older-profile actual responses remain `ghimera.semantic-review/3` observations, with explicit
`ghimera.review-selection/1` metadata in each ledger row. The assembled
`ghimera.semantic-review/4` contains every actual part and the complete assessed
key/index set. Its `model_call` is the actual final coverage call, not an
invented aggregate request. Coverage-based research gaps cite that call; every
other part remains available in the window and durable ledger. Harvest/journal
validation requires the corresponding earlier exact observations and restores
all actual review spending.

The independent-dimensions profile also keeps its actual model-facing `/5`
payload inside each client-derived grounded `/3` observation. The aggregate
`/4` is still deterministic assembly of all observed parts, not a model answer.

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

Trial 16 explicitly selected `assigned_role_checks` on full-gated source
`fa95756`, retaining the same inputs, original proposals, model and bounds.
Both mention batches were structurally valid; the third call falsely asserted
dates on undated original relationships and contradicted its own dimensional
verdicts. Validation refused the complete review, without projecting partial
semantic claims. The consumer was released and its own startup job cancelled.
This narrows the next schema correction, not entity/relation accuracy or full
acceptance. See [trial 16](ORGANIZATION_GROUNDED_EVIDENCE.md#trial-16-assigned-role-review-diagnostic).

Trial 17 selected the explicit original-date schema profile on full-gated
`c0a4c98`. The first mention batch was valid; the second contradicted its own
dimension verdicts and was refused. No relationship batch ran, so live serving
acceptance of the new date binding remains unproven. The consumer was released
and its own startup job cancelled; replay verified unchanged evidence and no
partial semantic claims. See [trial 17](ORGANIZATION_GROUNDED_EVIDENCE.md#trial-17-original-date-schema-diagnostic).
