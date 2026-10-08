# Source-backed identity and visual evidence candidate

This candidate extends the existing research graph and answer contracts. It
does not introduce another graph database, identity registry, OCR model or
vision-service stack. Collector bindings are integrated separately by the
recovery owner; source completion does not mean accepted end-to-end quality.

## Identity decisions

`GraphConfig.identity_resolution` owns the typed versioned policy. The inert
[example](../examples/identity-resolution.toml) declares eligible roles and
history, membership, evidence and reason limits. Effective policy is included
in the graph configuration digest. Existing configurations omit the absent
field and preserve their canonical identity.

`ResearchGraph.decide_identity` appends an explicit human-reviewed `merge`,
`split` or `retract` decision into the same acknowledged, immutable graph
transaction sequence as source observations. Each decision identifies reviewer
authority, revision, reason, original source-local members, exact retained
evidence and any known validity dates. Each member needs a source observation;
the decision's source quotes must identify every member's original surface.
People, offices and organizations cannot merge across roles. Those checks are
structural grounding, not automatic entailment: a human reviewer still owns
the meaning of the decision. Model assertions, equal names and similarity
scores never silently merge nodes.

Original nodes and relationship assertions remain unchanged. A split may
atomically retract earlier merges and establish pairwise nonidentity among
its members. A subsequent merge cannot transitively override an active split
in an overlapping period. Reversal requires currently active prior decision
IDs; all prior decisions remain in the journal. Repeating an identical decision
is an idempotent no-op. Decisions count against the configured history budget.

`ResearchGraph.identity_view(as_of=...)` returns a deterministic dated projection
with original members and decision IDs. An undated query applies only decisions
without temporal bounds and reports omitted dated decisions; it never turns a
dated alias or reorganization into timeless identity. This is a resolved view
over reviewed decisions, separate from the existing unresolved identity
questions. Automatic resolver/reviewer quality and planner consumption of
resolved decisions still require integration and acceptance.

## Visual readings and citations

`graph_visual_readings(Document.images)` derives compact, immutable OCR and
reviewed-claim readings from accepted images. Parent/source identities, image
and recipe hashes, OCR receipt hash, interpretation/review receipt hash,
normalized regions and optional PDF page/crop anchors are retained. Graph
document identity includes these readings when present; absent visual fields
preserve historical native/PDF identities.

`GraphConfig.visual_projection` declares a source-local observation role, a
nonsemantic document-to-observation rule and explicit reading limits. The
[example](../examples/visual-projection.toml) is non-active. `project_visuals`
uses the existing graph append/ack/replay owner. OCR observations have
`basis="image_ocr"`; accepted reviewed description spans have
`basis="reviewed_visual_claim"`. No OCR string is promoted into an arrow,
organizational relationship or factual probability. Reviewed visual text is
still derived model evidence, not native document text.

`Citation.from_image` and the ordinary `ContextSelector` now carry exact
retained-image, reading, span and region anchors into answer and assessment
templates. OCR/claim offsets refer to their own explicitly identified derived
reading; they never pretend to be parent-native offsets. Template-ID resolution
preserves the complete citation. Required review citations receive priority,
and visual context shares configured document, window and character limits.
Visual omission counts are observable separately from parent-native text.
Citation matching rejects changed parent bytes, regions, quotes, interpretation
receipts and a derived reading relabelled as native. Existing native and
reviewed-PDF citation serialization remains unchanged when images are absent.

## PDF figure crops

`VisualConfig.pdf_figures` is an opt-in, typed versioned recipe. The non-active
[example](../examples/pdf-figures.toml) uses the existing pinned offline PDF
renderer and worker lifecycle. Native retained picture provenance supplies page
number, coordinate origin and region; exact retained caption references supply
admission evidence. Decorative/unmatched captions are omitted before rendering.
Candidate overflow refuses the population rather than claiming its prefix is
complete. Missing layout and ambiguous multipage regions are explicit gaps.

The owned, network-disabled worker renders bounded pages and crops admitted
regions with deterministic outward pixel rounding. Source PDF, layout,
renderer/crop recipe, page pixels, page index and normalized crop geometry
remain bound to each image candidate. `VisualStage.collect_pdf` sends those
pixels through the same configured OCR and separately reviewed vision route as
HTML images; it does not fetch an image from a fabricated URL. Rejected output
is not retained or indexed. Worker scratch is drained and cleaned through the
existing lifecycle. The archived PDF still contains its original embedded
images.

`validate_pdf_images` replays layout/caption admission and checks the exact
source/geometry/effective recipe bindings. Actual raster equality is checked
separately against the existing renderer. This candidate requires native layout
picture observations; scanned PDFs with no usable figure layout produce a gap.
Full-page model transcription remains the existing configured private route.

## Acceptance scope

Local fixture tests cover reversible/durable identity history, homonyms,
multilingual aliases, time-scoped reorganizations, split conflict refusal,
source evidence, visual graph replay, geometry tampering, citation resolution,
real offline PDF rendering/cropping, existing English Tesseract execution and
scratch cleanup. Scripted visual claims and review/judge replies establish
protocol behavior only. Controlled fixture geometry is not learned figure
detection acceptance.

The existing Simplified/Traditional Chinese controlled sources remain required
quality controls. Their prior required-term checks and existing failed
transcription do not establish character accuracy, representative semantic
organization extraction or diagram understanding. Actual vision transcription,
semantic extraction and independent review need admitted pinned endpoints and
inspection of unchanged source pixels/output. No such quality claim follows
from this source candidate.

Exact runtime, relevant test results and any real-source observations are
recorded below after execution. Source, tested candidate, installed release and
accepted model output remain separate states.

## Executed candidate evidence

All observations here used `/tmp/chimera-c0-20261006/.venv/bin/python` 3.11.16,
importing `/tmp/ghimera-evidence-graph-20261008/src/ghimera`. The platform SDK
was intentionally absent. No dependency/model installation, network request,
credential lookup, model-service call, GPU allocation or release publication
occurred in this lane.

The relevant identity, visual, PDF, graph, served-model protocol, retained-graph,
planning and research importer selection passed **154 tests in 88.35 seconds**,
with no failures/skips, under that interpreter and source. The final identity,
visual evidence and PDF figure selection passed **14 tests in 11.32 seconds**,
with no failures/skips, under the same interpreter and source. The final short
selection also checks the explicitly reported zero-figure coverage gap. This
is a focused importer gate, not a complete release gate.

Strict mypy passed the **14 changed/new source files**, Ruff passed the
**17 changed/new Python files**, and those same **17 files** passed formatting
under `/tmp/chimera-c0-20261006/.venv/bin/python` 3.11.16. All three non-active
configuration examples validated with their typed schema owners under that
interpreter. Source and test files were frozen during each test run.

The sandbox's existing thread-callback wakeup restriction stalled the unchanged
disk sink before new behavior ran; the parent independently reproduced that
failure on the clean baseline. Bounded local tests and real-source checks used
scoped execution escalation, with source frozen and owned disk scratch. No
graph cancellation or acknowledgment guarantee was weakened to obtain a pass.

An unchanged controlled Simplified-Chinese PDF and its existing native parser
receipt bound to original SHA-256
`eedb338d1d2733bdb51e4f6af6d13a78512d315cb02ca82a837a9d748c9b92dc`.
Under `/tmp/chimera-c0-20261006/.venv/bin/python` 3.11.16, importing this
candidate, its native graph reopened with the exact acknowledged snapshot:
**3 nodes and 1 edge**. The native citation matched unchanged source and
retained text. The existing offline renderer produced **1 page**, whose PNG
SHA-256 was
`4d4372ba838963f059460f1f7cac256f093dccb2e555cc2eaac80b10e694da98`;
worker scratch was empty after completion. No model call occurred.

The same real-source check found **0 native picture observations** in that
receipt under the same interpreter and source. Figure admission returned
`no_native_pdf_figure_observations` and **0 crops**. This is coverage-gap
acceptance, not successful figure understanding. The old OCR reading, including
the incorrect `交通部内` recognition, remains exact and uncorrected. Chinese OCR,
semantic organization extraction and chart/diagram quality remain unaccepted.

## Required collector bindings

The recovery owner binds accepted image enrichment before creating the final
graph document representation, passes `graph_visual_readings(document.images)`
into `ResearchGraph.document`, and then runs `project_visuals` when its explicit
graph policy is configured. Rejected/no-visual sources retain their ordinary
native graph representation. The retained-graph expected-node owner already
uses the same image reading helper.

For PDFs, invoke `VisualStage.collect_pdf` on an accepted `Document` and bind
returned images to it. `Document.validate_policy` must call
`validate_pdf_images(document, visuals)` and apply responsive-HTML selection
checks only to non-PDF image candidates. Harvest validation must compare compact
graph visual readings with the exact matching source images. Answer model
instructions must preserve supplied visual basis/region templates, and any
new visual prompt policy must not silently rewrite historical native identities.
The ordinary answer context and citation resolver already use the native
contract in this candidate. The parent owns the combined collector gate and
installed acceptance after those bindings land.
