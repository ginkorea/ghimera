# Defined ontology, independent checks and quarantine

Status: unreleased source on the feature branch, not in published 0.3.0.
This closes an integration gap, not real-model organization accuracy acceptance.

## Configuration and composition

Select `ghimera.semantics/4`, `prompt_profile="defined_ontology"`, with a
complete, unique definition for every configured entity role and relation.
Use [the non-active example](../examples/semantics-verified.toml).
The verification section binds an existing self-hosted model role and an
explicit per-run review allowance. The extractor and reviewer must declare
distinct model identities. Different endpoint names are not independence;
deployment acceptance must verify the actual served weights and revisions.
Ideally use distinct model families and evaluate their shared failure modes.

The concrete `Collector` supplies both ports from its existing model bindings.
Lower-level `GoalLoop`/`SemanticStage` callers must inject `semantic_reviewer` /
`reviewer` when this policy is configured. Missing ports refuse before I/O.
No model server or GPU is automatically started. Authentication, response
bounds, generation controls and deadlines use the existing completion client.
Legacy semantic profiles 1–3 retain their prompts, serialization and refusal
behavior; they do not silently acquire quarantine or a second model call.

## Explicit dimensioned review

Select `schema="ghimera.semantic-verification/2"` in `[semantics.verification]`
to opt into the new `ghimera-semantic-verification/2` prompt and
`ghimera.semantic-review/2` response. The non-active
[factorized example](../examples/semantics-factorized.toml) supplies the same
defined-ontology extraction policy with this explicit choice. It changes no
running configuration and starts no model or GPU job.

This addresses a failure seen in the actual
[independent native-document trial](ORGANIZATION_INDEPENDENT_EVIDENCE.md): a
reviewer accepted abstract concepts as organizations because the strings were
present, and accepted relationships without establishing their specific
predicate and direction. A single verdict/reason hid which judgment was made.

Every mention now retains two separate checks:

- `named_entity`: a specific named instance, not an abstract concept, generic
  class, unnamed population or phrase fragment;
- `role`: that instance satisfies the configured type definition, preserving
  distinctions such as institution, office and incumbent.

Every relationship retains three separate checks:

- `entailment`: the native source asserts this predicate for these endpoints;
- `direction`: the proposed source and target have the stated roles in it;
- `validity`: asserted dates have source support; unknown dates remain null.

Each check requires a supported/unsupported/ambiguous judgment and its own
nonblank reason. The summary verdict must equal their deterministic aggregate:
any unsupported check means unsupported; otherwise any ambiguous check means
ambiguous; only all-supported checks permit supported. An inconsistent summary,
missing check or undimensioned response refuses before graph projection.
Unsupported and ambiguous items use the existing quarantine behavior, including
relationships whose endpoint is quarantined. Literal presence or confidence
cannot override a failed dimension. Exact native matching remains independent.

The new response extends the existing review data contract; it does not replace
the model port, graph owner, budget, journal or archive. The original unversioned
review response/schema, verification/1 prompt and serialization remain unchanged.
Ledger/window readers retain and revalidate the dimensioned subtype rather
than silently discarding its extra evidence. A verification/1 recipe cannot
silently accept a verification/2 response, or vice versa.

Dimensioned reasons consume the existing configured request/response, output
token, call and wall budgets. Large proposals may need a smaller configured
window/list limit or a larger admitted output allowance; there is no hidden
budget increase, repair or fallback. Original model agreement still can be
wrong on every dimension. This contract makes the judgment explicit and
auditable; real-model role/entailment accuracy and calibration require fresh
source-bound acceptance, not merely a passing schema or fixture.

## What is checked

An explicit verification/3 extension now models unasserted dates separately
and requires exact native witnesses for coverage omissions. See
[SEMANTIC_GROUNDING.md](SEMANTIC_GROUNDING.md). Profiles /1 and /2 and their
retained diagnostics remain unchanged; the new contract is not accuracy proof.

Each original extraction proposal remains unchanged in the retained window.
Its model/service/context, bounds and list limits are checked before spending
a review call. The reviewer receives that proposal, its content digest, the
same native source window and the ontology definitions, without the extractor's
call telemetry. It must assess every mention key and relation index exactly
once, returning supported/unsupported/ambiguous and reasons, plus a window
coverage assessment. Missing, invented or duplicate keys/indices, a wrong
proposal digest, truncation or mismatched service/context refuse the window.

The deterministic projector still requires exact native surface occurrence,
native citation, declared role/rule and valid endpoint roles. Reviewer agreement
cannot repair an absent source name, translate it, or invent an offset.
Mechanically invalid or reviewer-rejected mentions go into `excluded_mentions`.
Relations with excluded endpoints go into `excluded_relations`; unsupported or
ambiguous relationships are excluded independently. Valid, supported items
remain usable without admitting their rejected neighbors. Original proposals,
review reasons and exclusion reasons remain auditable. Low-confidence valid
items retain the existing threshold-held behavior.

The versioned `ghimera.semantic-window/2` retains both actual model calls.
Its projected edges stay **model_asserted**, not corroborated facts. Agreement
between models is not external corroboration. A review saying coverage is
adequate is an assessment of that bounded window, not a completeness proof.

## Spend, durability and readers

Extraction and review each reserve the shared run model-call budget and
deadline, with separate configured stage counters. A review budget refusal
does not spend a second call or misattribute the extractor's telemetry.
Failed/cancelled review calls retain separate `semantic_review` observations;
the extraction failure retains its original call. No graph claim is written
before a successful, bound review and deterministic projection.

Graph acknowledgement still precedes the semantic success observation.
Journal/harvest readers check budgets and the bound reviewer, require exactly
one prior review observation for each reviewed projection, and reconstruct
native projections from retained originals. Budget restoration counts both
stages. This is not recovery of arbitrary interrupted external calls.

## Follow-up planning

When graph-aware research is configured, reviewed extraction requires
`ghimera.graph-planning/2` or `/3`; use
[the gap example](../examples/graph-planning-gaps.toml) or
[the identity-aware example](../examples/graph-planning-identity.toml).
This produces `ghimera.planning-graph/2` with bounded, newest-first gap records
for incomplete/uncertain reviewed coverage, quarantined items, held edges or
an unread native tail. Records identify the retained source/window, reviewer
model/revision/request and counts; excluded concepts do not become entities.
Stable gap IDs can motivate follow-up queries through `graph_refs`.
Configured gap/count/context limits retain explicit omission counts.
Readers replay planning views from earlier acknowledged semantic observations.
The planner receives `ghimera-graph-planning/2` (or `/3` with identity questions), explicitly marking gaps as
assessments, never facts. Existing planning/1 identities remain unchanged.

## Acceptance still required

For the dimensioned extension, tests first demonstrated that verification/2
was absent (configuration refused before any model call). The bounded expanded
semantic/graph/archive checks then passed **76 tests, zero failed, zero skipped**
in 7.94 seconds using `/tmp/chimera-c0-20261006/.venv/bin/python`, Python
3.11.16, importing this checkout's `src/ghimera`. They cover every failed or
uncertain dimension, inconsistent summaries, missing dimensions, downgrade
refusal, original proposals, serialized windows, full loop archive/journal
retention and call-budget restoration. A frozen schema digest checks that the
legacy model response is unchanged. One added test initially used the wrong
serialization alias; that test was corrected rather than changing the wire.
The sandboxed fixture run timed out at asynchronous journal/thread work and
was not counted as passing; the bounded native-thread run supplied the result.

A fresh read-only replay of the previous real-document trial also passed with
the changed readers: 15 retained windows, 32 ledger rows and 15 graph batches
on the same Python 3.11.16 interpreter. No model call was repeated and no
retained response was rewritten. This verifies legacy compatibility, not the
new prompt's accuracy. The subsequent bounded
[dimensioned real-model trial](ORGANIZATION_FACTORIZED_EVIDENCE.md) reviewed
three retained failure windows through one admitted GPU. Responses and fresh
graph/ledger replay passed, but it still rejected a present native name and
retained two abstract concepts as organizations. It is a failed quality result,
not acceptance of the recipe. Positive relationship controls, representative
role/coverage evaluation and full live composition remain required; explicit
dimensions alone do not correct a wrong model judgment.

The complete dimensioned-extension `scripts/gate.sh` run completed on 7 October
2026 UTC (6 October in Hawaii) with that same owned Python 3.11.16 interpreter
and checkout: **535 passed, zero failed, zero skipped**, 516.15 seconds.
Offline lock validation resolved 137 packages; lint passed, formatting passed
for all 130 checked files and strict typing passed for 90 source files.
The installed Chromium ran through the existing isolated local browser fixtures.
No public CAPTCHA gateway, public-corpus collection or real inference service
was used in this gate. This verifies implementation and compatibility only.

The complete `scripts/gate.sh` run on 6 October 2026 used
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing this
checkout's `src/ghimera`. Offline lock validation resolved 137 packages;
lint/formatting passed for 129 files, strict typing passed for 90 source files,
and the suite passed **509 tests, zero failed, zero skipped**, in 515.20 seconds.
The preceding bounded semantic/planning/Collector checks passed 62 tests in
40.94 seconds on the same interpreter. Actual HTTP completion, search and
embedding clients and native parser/browser adapters used local protocol
fixtures and owned files, not real served-model responses or public CAPTCHA
sites. An initial focused run inside the outer tool sandbox was interrupted
when fixture work stopped progressing; the bounded run and full gate used the
host's local sockets with the package's own isolated browser/parser workers.
No test was skipped or production policy relaxed to obtain the clean result.

Controlled completion-client and native-document fixtures check the contract,
quarantine, provenance, cancellation, budgets, journal/archive replay and
gap-driven planner requests. They do not establish actual extraction recall,
role accuracy, entailment or reviewer error correlation. The earlier actual
Chinese-PDF trials remain failures, not retroactively accepted results.
The subsequent [real-model two-phase diagnostic](ORGANIZATION_INDEPENDENT_EVIDENCE.md)
used pinned, distinct served families over the retained official native
document. Original-proposal review, quarantine and graph replay worked, but
both models accepted some abstract concepts as organizations, the reviewer
also made a false-negative native-text judgment, and two windows remained
unresolved. It is not a complete live `Collector` run or organization-quality
acceptance. Next acceptance must distinguish role/entailment support from
simple string presence and measure these source-bound failures without
rewriting the original responses.
Chart arrows, alias/temporal resolution, multi-run expansion, external graph
publication and representative OCR/Marker acceptance remain separate gaps.
