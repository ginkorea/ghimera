# Identity-aware research questions

Status: unreleased feature-branch source. This is the research-planning side
of cross-document identity and claim reconciliation, not a global resolver.
Original graph nodes, relationship assertions and source evidence are unchanged.
No graph node is destructively merged, re-keyed, split or overwritten.

## Configuration and owners

Use `ghimera.graph-planning/3` with an explicit nested
`ghimera.identity-planning/1` policy; see the
[non-active example](../examples/graph-planning-identity.toml). Every selected
alias and exclusive rule must already exist in the configured extraction and
planning ontology. Invalid bindings refuse before collection. Legacy planning
profiles 1 and 2 keep their serialized policy and prompt behavior; they do not
gain identity fields or a resolver implicitly.

`GraphPlanningConfig` owns this policy boundary. The existing acknowledged
semantic ledger still owns the population. `build_identity_view` computes a
deterministic projection using existing source-local graph records; it creates
no second graph store, global registry or additional inference phase. The
planner uses its existing model binding and budgets.

## Candidate identities

`exact_surface_and_role` groups exact native labels of the same declared role
for investigation. It does not normalize, translate or transliterate names or
claim that a shared name proves shared identity. Source-local occurrences
remain separate even when the labels match exactly.

Configured alias edges can join a candidate group only when the original
model assertion and evidence are retained and both endpoints have the same
role. Alias edges remain model assertions, not corroboration. A cross-role
alias is counted but cannot equate an office with a person or organization.
Groups retain every selected member ID, contributing alias relation ID, their
bases and `status="unresolved"`.

Candidate IDs are content-bound to their selected membership and basis. They
can change as more observations enter the bounded view; they are not permanent
canonical entity IDs. No fuzzy-name or cross-language identity claim is made.

## Potentially competing claims and dates

`exclusive_relations` is an explicit operator ontology choice. There are no
inferred exclusive predicates: multiple memberships, offices or reporting
targets can be legitimate. The shipped example leaves this set empty.

For an explicitly selected exclusive predicate, different target hypotheses
for one source mention or candidate source group can become unresolved dispute
questions. A question retains both original relation IDs and the source/group
reference. It never establishes that either source is wrong, or that the
subject candidates really are identical.

Known overlapping inclusive ISO-date intervals produce `known_overlap`;
unknown bounds produce `unknown_time` when configured to report possible
overlap. Unknown-time policy can instead skip such questions. Provably disjoint
intervals do not compete. Unknown dates are never filled from collection time
or model memory, and malformed/reversed retained dates refuse.

Claims with the same target hypothesis remain represented by their original
relations and unresolved identity group rather than a target-difference
question. That is not proof that the targets or their sources agree. Resolving
one such group can expose further competing claims in a later view.

## Bounds, references and replay

Group count, members per group, dispute count and pair checks are explicit
bounds. Oversized groups are omitted whole, not clipped into a misleading
partial group. The view records omitted groups/members, examined/unchecked
pairs, omitted disputes and cross-role aliases. A pair check skipped because
of target identity, dates or unknown-time policy still counts as examined.

The scope is `selected_planning_population`. These counts describe only
entities and relations already selected by the outer graph context. Outer
omitted entities/relations/evidence counts remain visible; neither layer
claims global completeness. Group membership omitted from this view is not
used to compare cross-document claims.

Identity and dispute IDs join the existing `graph_refs` vocabulary. A planner
can request source-grounded alias or temporal research using the exact retained
reference. New references do not authorize URLs, expand source policy or
consume unrecorded resources. Prompt revision `ghimera-graph-planning/3` marks
groups, disputes and coverage gaps as unresolved hypotheses/assessments.

The concrete model client validates the view before I/O. Journal and result
readers reconstruct it from preceding acknowledged observations, including
membership, reasons, counters and content identities. Future observations,
forged members, omitted evidence or changed counters cannot retrospectively
rewrite an earlier planning input.

## Acceptance boundary

Controlled source/model/search fixtures exercise same-name preservation,
asserted aliases, cross-role refusal, overlapping/disjoint/unknown dates,
configuration and bounds, model prompt/reference composition, actual multi-round
research and durable journal/result tampering. These prove integration and
replay, not real-model identity resolution or organizational accuracy.

The fresh full `scripts/gate.sh` run used
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
worktree's `src/ghimera`: **558 passed, zero failed, zero skipped**, in 523.72
seconds. Offline lock validation resolved 137 packages, lint passed, formatting
passed for all 133 checked files, and strict typing passed for 92 source files.
The gate includes the new identity contract/composed-loop checks, legacy
semantic/planning and continuation readers, challenge gateways and the
isolated local Chromium fixtures. It used local fixture services/owned files,
not a live inference service or external challenge site. A first sandboxed
focused run timed out at asynchronous fixture work; it was not counted as
passing. Native bounded runs supplied the completed checks.

Read-only replay of the retained native Chinese-PDF diagnostic used
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout. All 32 original ledger rows and the retained source bytes/text
validated, without a new model call or rewriting any original evidence.
The diagnostic's full retained population contains 54 source-local occurrences
and four exact-native-name/role candidate groups. These are repeated occurrences
within one PDF, not independent corroboration or cross-document resolution.
The trial's known role/entailment errors remain unchanged.

The actual bounded planning view exposed a selection trade-off:

| Configured gap limit | Selected mentions | Selected relations | Omitted mentions | Omitted relations | Selected identity groups |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8 | 9 | 3 | 45 | 9 | 0 |
| 2 | 17 | 3 | 37 | 9 | 0 |

Both recipes retained the example's 24,000-character context ceiling; their
serialized views used 23,579 and 23,566 characters respectively. No claims were
added to make a group appear. Newest-first selection and gap priority can omit
every repeated-name group even when the full population has candidates. A
configured identity-first selection policy remains a needed optimization,
not an unrecorded change to legacy recipes or a completeness claim. The private
read-only diagnostic is `gate-work/identity-retained-replay.py`; it accepts an
explicit `--max-gaps` comparison and checks that source/ledger hashes did not
change. This replay establishes compatibility, not accuracy acceptance.

Still required: evidence-bound identity decisions and reversible merge/split
views; real alias/temporal/conflict resolution across independent documents;
multi-run persistence/expansion; representative organizational extraction,
review and completed-research quality acceptance. No release or live service
configuration changes are made by this feature.
