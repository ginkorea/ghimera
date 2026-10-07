# Source-bound semantic extraction

Status: unreleased source after 0.3.0. The configured `Collector` can extract
named-entity mentions and explicitly asserted relations from accepted native
documents, including owned PDF/DOCX seeds. This is the semantic extraction
stage, not a claim of complete organizational network research.

## Configure once, reuse existing owners

Add `[semantics]` using [the non-active example](../examples/semantics.toml).
Select an existing `[models]` role (`analyst`, `reviewer` or `judge`); this does
not launch a model, acquire a GPU or introduce another credential boundary.
The normal self-hosted completion client owns authentication, input/output
limits, exact model binding, cancellation and call provenance.

Version-1 recipes retain `ghimera-semantic-extraction/1` and their original
serialized policy shape. To select the stronger mention-key instructions,
use [the version-2 example](../examples/semantics-mention-keys.toml):
`schema="ghimera.semantics/2"`, `prompt_profile="explicit_mention_keys"`.
This profile asks the model to build unique mention keys first and restrict
every relation endpoint to that same response's included mentions. The profile
and `ghimera-semantic-extraction/2` revision are retained in policy and call
evidence; archive readers reject mismatched revisions. Neither profile relaxes
native-span, citation, ontology or endpoint validation. Stronger instructions
are not a guarantee that a served model returns valid or accurate claims.

Version 3 explicitly selects `prompt_profile="native_span_keys"`; see
[the native-span example](../examples/semantics-native-spans.toml). It retains
the mention-key instructions and adds per-surface occurrence counting, literal
spaces/line breaks, and the prohibition on completing known titles from memory
or other document windows. Every distinct name's first exact occurrence is 0,
not its ordinal in the model's mention list. This binds
`ghimera-semantic-extraction/3` in call and graph evidence. Version-1 and
version-2 profiles do not silently acquire these instructions. No native text
normalization, offset repair or changed acceptance threshold is introduced.

Enable `[graph]` with `capture_semantics=true` and declare the entity roles,
document-to-entity mention rule and allowed entity-to-entity relation rules.
Keep the existing research question/query/source/document trace vocabulary.
Unknown roles, nonsemantic rules, wrong endpoint roles or windows exceeding the
bound model context refuse before collection. Entity roles are application
ontology, not a built-in country or organization list.

The default collector recipe has no semantic stage unless configured. If a
recipe declares it, the assembled collector supplies it; lower-level `GoalLoop`
callers must inject a matching `SemanticExtractor`. Missing policy/port pairs
refuse instead of silently skipping a declared stage.

## Extraction and identity

Version 4 adds explicitly defined ontology roles/relations and a required,
separately bound semantic reviewer. Unsupported items are quarantined without
modifying the original proposal or discarding valid neighbors; see
[semantic verification](SEMANTIC_VERIFICATION.md). Profiles 1–3 keep their
existing whole-window refusal behavior. This is not real-model accuracy proof.

Accepted documents pass through sequential bounded native-text windows. Calls
consume both `max_calls_per_run` and the existing shared judge/model-call
allowance and deadline. A document window limit records the unread native tail
in `omitted_chars`; this is not complete extraction of that tail. Current
windows are nonoverlapping, so boundary-crossing relations need subsequent
context/coverage work. An empty text has no extractable window.

The model selects supplied native citation IDs, exact surface names,
zero-based occurrences, configured roles and relation rules. The client checks
those choices, derives native offsets and builds deterministic graph identities.
Invented IDs, absent surfaces, unknown roles/rules and malformed responses
refuse. Names are not translated or silently normalized.

Entity identities are **source-local mentions**, bound to a document
representation, role and exact native occurrence. Same-name mentions across
sources remain distinct; this deliberately does not pretend an alias resolver
exists. Offices and office holders can use distinct configured roles. Graph
edges retain model/prompt/request revision, native evidence and any asserted
validity dates; unknown dates remain null.

Every generated edge is explicitly `model_asserted`. A source saying something
does not establish that it is true, and an exact quotation does not establish
entailment. Model confidence is not a calibrated probability. Relations below
the graph's configured confidence threshold, or with held endpoints, stay in
the semantic window's `held_edges` rather than the projected graph. This is a
threshold decision, not a corroboration upgrade.

The source and collection verdict remain unchanged. Semantic observations are
added beside originals and extraction provenance, not substituted for them.

## Durable output and failure

`semantic` ledger rows retain the proposal, exact window, resolved mention
evidence, projected nodes/edges, held edges, omitted tail, effective policy
digest and model-call telemetry. Graph writes use the existing immutable batch
owner; the success observation follows its acknowledged write. Cancellation
during that write drains the owned transaction, retains the acknowledged
projection, then propagates cancellation. Failed model calls retain their spend
and refusal rather than creating graph claims.

Harvest/archive readers recompute projections from retained native documents
and proposals, check model/policy/context bindings, window ordering and budgets,
and require every generated graph edge to have its matching semantic observation.
Missing windows require an explicit source refusal. Journal readers validate
the policy/call allowance even for unsealed runs; full source/projection
validation requires the complete harvest, not the journal alone.

Durable graph replay is not automatic research continuation. Changing a model,
ontology or recipe means a new explicitly configured run/version; old archives
are not rewritten. Entity resolution, contradiction handling and multi-run
projection need their own evidence-preserving contracts.

## Acceptance boundary

Checks cover native Chinese mention extraction and identities across source
occurrences, invented spans/citation IDs, configured ontology, threshold-held
relations, omitted tails, shared budgets, failed/cancelled calls and graph-ack
cancellation. An actual offline Docling DOCX passes through local intake and
this stage with graph/journal readback. A concrete Collector composition test
exercises its actual HTTP completion/search/embedding clients and native HTML
parser against local protocol fixtures.

Full package gate for the `cf5eff0` semantic extraction line on 6 October 2026:
**398 passed, 0 failed, 0 skipped** in
436.34 seconds. Ruff checks passed, 117 files were already formatted, and
strict mypy passed for 83 source files. The runner was
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout's `src/ghimera/__init__.py`, with no installed platform package.
Browser checks used the configured isolated local Chromium; parser/model/source
checks used owned files and local protocol fixtures, not remote collection or
a live inference service.

These fixture model responses do not establish real-model entity/relationship
accuracy, exhaustive chart coverage or independent entailment quality. The
subsequent [real-model PDF diagnostics](ORGANIZATION_MODEL_EVIDENCE.md) retain
four refused trials and the exact acceptance boundary; they are not a success
claim. The explicit mention-key profile passed a full 472-test gate, but actual
native occurrence matching remains unresolved.
The remaining organizational workflow is tracked in
[ORGANIZATION_RESEARCH.md](ORGANIZATION_RESEARCH.md): alias/temporal resolution,
real graph-driven discovery acceptance and persistent expansion, visual-chart evidence and real
organization-corpus acceptance. The published 0.3.0 artifacts are unchanged.
Within-run graph-aware planning is implemented in unreleased source; see
[GRAPH_PLANNING.md](GRAPH_PLANNING.md).
