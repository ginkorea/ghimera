# Retained evidence context and research reuse

Published in 0.4.6, not in the immutable 0.4.5 artifacts. This
implements the direct-context boundary and configured research-loop reuse of
I05. The integrated follow-up connects retained native/PDF originals to
the current evidence graph. Freshness, reranking, visual graph projection and
representative multilingual quality acceptance still require further work.

`CorpusEvidenceReader(policy, corpus)` borrows an explicitly opened corpus and
returns ranked original passages with their full original `Document` objects.
It does not fetch URLs, parse/OCR originals again, synthesize a new answer,
reuse an old relevance verdict for the current intent, or close the borrowed
store. Native, reviewed-PDF and visual passage kinds preserve their exact source
bindings; URLs and snippets are not substituted for originals. Several useful
passages of one source remain available rather than becoming one short lead.

The typed `ghimera.corpus-evidence-config/1` binds an exact corpus identity,
recipe, query encoder, languages, threshold, passage/document/original/response
bounds and deadline. See the non-active `examples/corpus-evidence.toml`.
The returned `ghimera.corpus-evidence/1` bundle includes the actual query/model
observation, selected passage ids, originals and one explicit omission per
unselected hit. Decode revalidates selection completeness, source bindings and
the response bound. No model-written source ids or URLs are accepted.

```python
from ghimera import CorpusEvidenceReader

# policy is a validated CorpusEvidenceConfig; corpus is the caller-owned store.
reader = CorpusEvidenceReader(policy, corpus)
bundle = await reader.read(intent)
for hit in bundle.hits:
    # These are exact stored spans, not current-site claims or reviewed answers.
    print(hit.passage.source_url, hit.passage.kind, hit.passage.text)
```

## Freshness and spend

`source_mode="retained_snapshot"`, `source_age="unknown"` and
`current_intent_verified=false` are explicit, validated fields. The existing
store records no acquisition time. Retrieval does not invent one from a query
time, filesystem timestamp, publication date or model statement. Callers wanting
current-site evidence must still use the normal source acquisition boundary.

The current query's encoding is audited by the corpus. Historical OCR/review/
browser observations remain within originals; they are not appended as current
run events or charged as new model calls. An oversized bundle refuses rather
than silently truncating provenance; its already-issued query remains audited.

## Configured research reuse

The `ghimera.research-reuse/1` policy is `research.retained_evidence` in the
ordinary collector recipe. See `examples/research-reuse.toml`; replace its
placeholder identities before use. Inject `retained_reader=reader` into
`Collector` or `Collector.from_toml`. Reader/store lifetime remains caller-owned.
Missing or mismatched bindings refuse before research model work; there is no
implicit corpus discovery, credential lookup or second crawler.

The normal research loop queries the original intent and then planned queries.
It supplies retained originals, not search snippets, to the current planner,
analyst and independent reviewer. Explicit unknown-age notices bind the whole
source representation and its text hash. The served-model prompt requires
reassessment for the new question, and asks for new sources when unknown-age
snapshots cannot support a time-sensitive answer. An old relevance verdict is
never taken as current question coverage. Only newly assessed, exactly cited and
independently reviewed coverage can finish a run.

With `assess_before_discovery=true`, sufficient retained text/PDF evidence can
answer without contacting its original hosts. Insufficient/empty/unavailable
retrieval continues the normal discovery and acquisition paths. Explicit source
seeds are still visited even if cached material covers the question. Disabling
early assessment still supplies originals but does not skip web discovery.
Local retrieval does not grant permission to contact source hosts; discovery
continues to enforce its own source scope.

Graph-aware planning keeps its published graph-view identity unchanged. With a
configured retained-evidence recipe, the actual planning call pins the combined
prompt as `<graph prompt revision>+retained-snapshots/1`; ledger replay checks
the same revision. Ordinary graph-only planning retains its original prompt pin.

`chimera.research-result/3` keeps `retrieval` beside the fresh `harvest`:

- The harvest's `documents` and `source_documents` contain only fresh originals.
  Its ledger contains only current acquisition, admission and model events. Its
  fetch/encoding counters do not relabel corpus reads as source HTTP work.
- `ghimera.research-retrieval/1` contains actual current query observations,
  separately configured query/character/snapshot/document bounds and admitted
  `ghimera.corpus-evidence/1` capsules. A successful encoding whose context
  delivery fails remains spent; cancellation preserves its actual observation.
- Historical local imports, page renders, OCR and reviews stay in each retained
  original. They are neither replayed nor charged as new collection work.
- `result.evidence_documents` joins fresh and retained originals by full
  representation identity. Different readings of identical URL/bytes remain
  distinct; citation text hash, reading basis and page indices remain checked.
- Completed-round checkpoints pin these admitted originals and retrieval spend.
  Resume does not repeat successful queries, including empty ones, or replace
  already admitted originals with a new corpus generation. Operation-level
  crash/uncertain-ack recovery remains I03, not a property of round checkpoints.

The query allowance is explicit and distinct from collection-scoring and
source-discovery allowances. Global research wall time still bounds everything.
Exhausting a local retrieval allowance does not forbid remaining source work.
Do not interpret cosine as calibrated answer confidence or independent review
as proof of freshness. Real-model multilingual/temporal quality still needs
acceptance; protocol fixtures establish wiring and provenance, not accuracy.

## Remaining required integration, not optional closure

1. Accept the retained-native/PDF graph follow-up described below through its
   focused and full gates. Image-region graph projection still requires I10;
   native/PDF graph support is not a substitute for it.
2. Extend answer/context citations to visual claims and image regions (I10).
   Retaining image passages is not yet visual-answer or visual-graph acceptance.
3. Add acquisition observations for future records before enabling age-bounded
   fresh-cache behavior. Old records remain unknown-age; never backfill invented
   timestamps. Hybrid/rerank and multilingual acceptance are separate I05 work.

Acceptance for this boundary uses real SQLite/FAISS persistence, fresh decode,
source/query/selection mutations, response limits and exact reviewed-PDF page
provenance, with scripted encoding/transcription replies. It is not evidence
that a served model answers correctly or that automatic research reuse is
deployed. The research-loop candidate also needs its own focused and full gates.

## Retained native/PDF graph follow-up

When the configured research graph is enabled, the same `GoalLoop` and
`SemanticStage` consume each distinct retrieved original once before planning.
There is no second graph, model selection or hidden crawler. Current semantic
extraction and configured independent verification consume the existing run
budget, preserve source-local identity and feed the ordinary graph-aware planner.
Repeated query hits do not repeat extraction; completed-round resume restores
the original graph and acknowledged semantic work rather than replaying it.

`chimera.harvest/2` carries `retained_sources` separately from fresh `documents`.
Each entry contains its complete original and a `ghimera.graph-retained-origin/1`
binding: corpus identity/recipe/generation, exact query bundle, whole original
digest and actual current query-call digest. Source age stays explicitly unknown.
The research-result validator binds those origins to its actual retrieval
capsules; changing a query capsule or original breaks validation.

Retained graph document nodes include this origin in their identity. Historical
PDF page readings, model calls and local/browser provenance remain attached to
the original; they are not relabelled as current imports, OCR or HTTP requests.
Fresh-only graph identities and harvest/1 serialization stay unchanged. New
semantic windows bind the exact native or reviewed-PDF reading, including page
references; two readings of identical source bytes do not overwrite one another.

Admission is a zero-source-byte `retained_source` ledger observation preceding
current semantic work. The versioned journal summary records retained document
origins separately from fresh counts. Full originals and graph evidence persist
through completed-round checkpoints and the existing acknowledged delivery
outbox, including after the corpus closes. Operation-level uncertain-ack recovery
and real-model graph accuracy remain separate required acceptance work.

Failed current semantic work records the exact retained origin, including when
the run budget expires between windows. A rejected independent review preserves
its current extractor/reviewer observations without publishing their proposed
claims. Recording a terminal refusal does not enable continuation: recovery
still requires the explicit semantic failure policy. Admission checks document
and snapshot limits before changing the graph.

Development verification: the nine retained graph/browser checks passed under
Python 3.11.16, including real SQLite/FAISS, native PDF/browser, journal,
checkpoint and outbox boundaries with scripted model replies. The affected
module/importer selection passed 336 tests, no failures or skips, under
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing this isolated candidate.
Ruff, formatting and strict mypy (164 source files) also passed. The combined
source now passed 1,105 tests without failures/skips under that same Python
3.11.16 interpreter; installed/public-artifact acceptance is recorded separately
in RELEASE_046.md. None of these checks measures real
model accuracy or proves the complete infrastructure PRD.

## Integration contract

Reuse belongs in `ResearchLoop`, assembled through `Collector`, not in a second
crawler or an application-specific wrapper. A run must bind the
caller-owned reader to its typed research configuration before any model spend.
The context remains an explicit snapshot capsule alongside the fresh harvest.

- **Accounting:** the corpus query encoder can differ from the collection
  scorer. Its actual call needs a separately bound retrieval observation and
  budget. Do not insert it into the existing scorer-only encoding ledger, or
  copy historical extraction, import, browser or OCR calls into that ledger.
  Failure and cancellation after a query must retain its actual observation.
- **Scope:** permission to read a configured local corpus is not permission to
  contact its source hosts. Apply research source policy separately to any new
  discovery/refetch. Local-input identities must not become fabricated URLs.
- **Assessment and answers:** each resumed/new intent receives its own assessment
  and independent review. Citations bind exact stored reading representations,
  not old relevance decisions or corpus-search snippets. When a fresh reading
  and a retained reading differ for the same URL/bytes, preserve their distinct
  identities instead of choosing one implicitly.
- **Checkpoint and graph:** persist the admitted capsule and current retrieval
  spend at a completed round boundary; resume that capsule without requerying.
  Graph projection needs an explicit retained-source provenance contract,
  preserving local/PDF/browser observations without describing them as a fresh
  retrieval. New semantic extraction and review remain current work.
- **Acceptance:** an intent must finish with exact cited retained originals,
  zero source fetches when those originals suffice, actual new assessment/review
  calls, and explicit uncertainty about source age. A missing/insufficient
  context must continue ordinary discovery, not declare the question answered.
  Repeat this witness after pause/resume, including a changed source reading,
  native/PDF/visual evidence, bounded-response omissions and model cancellation.
