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

## What is checked

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
`ghimera.graph-planning/2`; use [the gap example](../examples/graph-planning-gaps.toml).
This produces `ghimera.planning-graph/2` with bounded, newest-first gap records
for incomplete/uncertain reviewed coverage, quarantined items, held edges or
an unread native tail. Records identify the retained source/window, reviewer
model/revision/request and counts; excluded concepts do not become entities.
Stable gap IDs can motivate follow-up queries through `graph_refs`.
Configured gap/count/context limits retain explicit omission counts.
Readers replay planning views from earlier acknowledged semantic observations.
The planner receives `ghimera-graph-planning/2`, explicitly marking gaps as
assessments, never facts. Existing planning/1 identities remain unchanged.

## Acceptance still required

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
