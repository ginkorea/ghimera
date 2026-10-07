# Graph-aware intent planning

Status: unreleased source after 0.3.0. An opt-in bounded planning view now joins
acknowledged semantic extraction to the existing intent research loop. Findings
from local document seeds are available to the first plan; web findings enter
the next plan. This is within-run graph-aware discovery, not persistent network
expansion or resolved global entity identity.

## Configuration and shared owners

Merge [the non-active example](../examples/graph-planning.toml) into a research
recipe. `[research.graph_context]` uses `ghimera.graph-planning/1` and declares
entity roles, relationship rules, selection order and maximum entity, relation,
native-quotation and serialized-context sizes. It requires the matching
`[semantics]` extraction ontology and enabled semantic graph. Invalid role or
relation bindings refuse before collection. Model inputs still obey the existing
planner's context, request and full-prompt limits; a large graph allowance does
not raise those limits or launch another model.

The population owner is the existing semantic success ledger, written after
graph acknowledgement. Failed calls, threshold-held relations and unacknowledged
proposals do not enter the view. The projector reuses graph entities, relation
records and source evidence; it is not another graph store or entity resolver.

## Selection, evidence and discovery

For reviewed semantic extraction, select the explicit version-2
[gap policy](../examples/graph-planning-gaps.toml). It adds bounded source-bound
coverage/quarantine assessments and query references, without turning excluded
proposals into graph entities. See [semantic verification](SEMANTIC_VERIFICATION.md).
Version-1 policy, serialization and prompt remain unchanged.

Newest acknowledged relationships are considered first. Each admitted relation
keeps both source-local endpoints and complete native quotations. Remaining
space can hold observed mentions whose relationships are still unknown. A limit
can omit a complete item but never shorten its quotation, fabricate a smaller
claim or silently promote held evidence. Views record exact omitted entity,
relation and quotation-character counts, and a digest of the full observed
configured eligible population. Absence from this bounded view does not establish absence from the
organization or from collected sources.

The existing planner receives the view beside native document context and
coverage. Its prompt labels every relationship as a model assertion, not a
corroborated fact, and forbids automatic same-name merging. A follow-up query
motivated by an entity or relationship retains its exact ID in `graph_refs`.
Unknown references refuse before discovery; other queries can have an empty
list. The original intent and question pack still cannot be rewritten, and
search hits, host policy, source collection and reference expansion keep their
existing owners. A graph reference grants neither a source URL nor access.

The view adds no extra model phase: it changes the input to the configured
planner call. Native quote/source/version checks and exact planner-role binding
run before model I/O. Changed output semantics use prompt revision
`ghimera-graph-planning/1`; recipes without this option keep their prior prompt
schema and omit the new empty fields from existing serialized queries.

## Audit and completion

Every planning call, including a failed or cancelled call, retains its bounded
view in the existing ledger. Sealed harvest and unsealed journal readers replay
each view from *earlier* semantic observations and reject altered selection,
omission or population evidence. A completed research result binds round queries
to an observed planning response and its supplied graph references.

Graph-assisted queries do not change completion into "the graph looks full".
The existing native-source coverage, answer citations and support review remain
required. Unknown or contradicted coverage, a budget stop or exhausted rounds
produce a partial result. Model confidence is not corroboration or calibrated
probability.

## Acceptance and remaining scope

Controlled native-language checks exercise exact source quotations, endpoint
closure, role/rule configuration, size and omission accounting, wrong-role and
pre-I/O refusal, actual completion-client prompt composition, multi-round
research/discovery and archive tampering. The composed loop demonstrates that a
new finding changes the next query. Fixture responses establish integration,
not real-model research quality or entity accuracy.

Full package gate on 6 October 2026: **411 passed, 0 failed, 0 skipped** in
441.05 seconds. Ruff checks passed, 120 files were already formatted, and
strict mypy passed for 85 source files. The runner was
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout's `src/ghimera/__init__.py`; no platform SDK was installed. Checks used
owned native files, isolated local Chromium and local model/search/embedding
protocol fixtures, not remote collection or a real inference service.

Still open: evidence-preserving aliases/temporal identity, contradictory-source
resolution, restart-safe pending frontier, multi-run expansion and projection,
visual-only chart claims, and real organizational-corpus/model acceptance. The
published 0.3.0 artifacts are unchanged. See
[the organization workflow](ORGANIZATION_RESEARCH.md).
