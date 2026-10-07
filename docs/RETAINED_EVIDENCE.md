# Retained evidence context and research reuse

Development candidate, not included in the immutable 0.4.5 artifacts. This
implements the direct-context boundary of I05, not full research-loop reuse,
freshness, reranking or representative multilingual quality acceptance.

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

## Required next integration, not optional closure

1. Add an explicit research reuse policy and retained-source capsule to the run
   result/checkpoint. Keep fresh `Harvest` and historical originals distinct;
   neither their provenance nor their budgets may masquerade as the other.
2. Run relevance/evidence assessment against the **new intent**, retaining actual
   current model calls. The original corpus acceptance is not this assessment.
3. Bind cited answers and graph nodes to the retained source representation,
   including reviewed-PDF page/model records and image regions. Preserve native
   wire identities and do not reinterpret old extraction under today's recipe.
4. Pause/resume and cancellation must preserve admitted snapshots, current spend
   and query observations without a duplicate source fetch or fabricated model
   call. New graph processing must remain current work, not recycled assertions.
5. Add acquisition observations for future records before enabling age-bounded
   fresh-cache behavior. Old records remain unknown-age; never backfill invented
   timestamps. Hybrid/rerank and multilingual acceptance are separate I05 work.

Acceptance for this boundary uses real SQLite/FAISS persistence, fresh decode,
source/query/selection mutations, response limits and exact reviewed-PDF page
provenance, with scripted encoding/transcription replies. It is not evidence
that a served model answers correctly or that automatic research reuse is live.

## Integration contract

The next implementation belongs in `ResearchLoop`, assembled through `Collector`,
not in a second crawler or an application-specific wrapper. A run must bind the
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
