# Durable native evidence corpus

Status: native corpus source at `7733285` and the subsequent automatic collector
handoff at `3b21a7a` passed their respective full gates. Both are unreleased. This
does not close the infrastructure PRD's language/model quality, operation
recovery, service, refresh or delivery-outbox requirements.

## Ownership and use

The corpus is a separate reusable component, not a replacement collector or
hidden model service. Install the `corpus` extra. An explicitly configured
`CorpusConfig` and two `EvidenceEncoder` ports bind passage and query encoding
to the same immutable model/revision/dimensions; their native prefixes may differ.
The real adapter is the existing `SelfHostedEncoder`. Credentials are memory-only
constructor inputs to that adapter, never stored in the corpus configuration.

```python
import tomllib
from pathlib import Path

from ghimera import CorpusConfig, EvidenceCorpus
from ghimera.embedding import SelfHostedEncoder

policy = CorpusConfig.model_validate(
    tomllib.loads(Path("corpus.toml").read_text(encoding="utf-8"))
)
corpus = EvidenceCorpus(
    policy,
    encoder=SelfHostedEncoder(policy.encoder),
    query_encoder=SelfHostedEncoder(policy.query_encoder),
    create=True,  # Only for a new directory; reopening never overwrites it.
)
try:
    receipt = await corpus.append(result.harvest)
    found = await corpus.search("What is known about these ports?", top_k=10)
    for hit in found.hits:
        original = corpus.document(hit.passage.document_id)
        # Retained native text, URL, raw digest, offsets and visual region anchors.
        print(hit.passage.source_url, hit.passage.text, hit.cosine)
finally:
    corpus.close()
```

The caller chooses and creates the parent directory. The non-active example
`examples/corpus.toml` creates no service and acquires no hardware. A passage
model/prefix or chunk recipe change requires a new corpus directory and an
explicit re-append from retained evidence; incompatible vectors never mix.
Capacity, transport, query-prefix, threshold and ANN tuning changes can reopen
the same corpus without re-embedding. Existing material must fit any newly
lowered capacity. Receipts bind the full effective configuration separately
from the immutable passage recipe, and retain the stable corpus identity.
Relocating the private directory does not itself change the recipe identity.

## Durable truth and indexing

### Corpus-backed research discovery

The unreleased `CorpusLeadSearch` implements the existing `GroundedSearch`
request port, preserving its final spend/ledger template. Configure a
`CorpusSearchConfig` (`ghimera.corpus-search/1`) using the opened store's
`identity`, `config.identity` and exact `config.query_encoder`; the non-active
`examples/corpus-search.toml` contains placeholders, not an admitted model.
Set that binding as the collector's `search` recipe and explicitly pass the
borrowed store as `Collector(config, corpus=store)`. A discovery strategy instead
uses provider-keyed `discovery_corpora`; ordinary CLI invocation does not open
an implicit store or discover credentials.

Query length, passage hits, result count, original/response bytes, title/snippet
length, cosine threshold and optional language filter are explicit policy.
These limits cannot widen the corpus's configured query bounds or lower its
minimum threshold. Empty `languages` means all retained language identifiers,
not validated multilingual quality. Cosine is not a probability.

The `ghimera.corpus-leads/1` response retains the actual native query, corpus
generation/configuration identity, exact encoding call, returned passage hits,
selected original documents, selected passage IDs and omission codes. A source
URL yields at most one lead, using its original title and native passage. Invalid
HTTP(S) URLs are not repaired by a model. Size, threshold, result-limit and
duplicate exclusions are observable; if even the bounded query observation
cannot fit, the attempt refuses rather than dropping provenance. Native original
readback runs on an owned worker connection; cancellation drains it before the
caller may close the store.

Archived research re-derives the lead projection from these originals and checks
the exact query/model binding. Strategy domain filtering is also replayed.
Search calls retain the existing global/provider query, byte and elapsed-time
accounting; the corpus independently retains its encoding audit. Encoding audit
is not a new claim of shared token/GPU-budget enforcement.

This deliberately supplies **source leads**, not answer citations: the ordinary
research pipeline still fetches the original URL, checks scope/entitlement and
builds new source-bound evidence. Direct cached-source answer reuse, freshness
policy, lexical/hybrid reranking and representative multilingual retrieval
quality remain open. A protocol fixture's Chinese passage returned for an
English query establishes provenance plumbing, not cross-language accuracy.

### Automatic handoff from configured collection

The unreleased `PersistentCollector` facade composes an existing configured
`Collector` with an explicitly created/reopened `EvidenceCorpus`:

```python
from ghimera import PersistentCollector, CorpusHandoffFailure

service = PersistentCollector(collector, corpus)
try:
    completed = await service.run("Find evidence about these ports")
except CorpusHandoffFailure as failed:
    # Preserve failed.result with the application's durable result archive.
    # After correcting the corpus/model issue, retry persistence alone:
    completed = await service.persist(failed.result)

print(completed.corpus.generation, completed.corpus.added_passages)
# completed.result is the unchanged original ResearchResult or Harvest.
```

`run`, `collect`, and `resume` acknowledge completion only after the corpus
append returns. The new outer `ghimera.persistent-collection/1` record binds the
exact harvest digest to its corpus receipt; it does not alter existing harvest,
research, journal or graph identities. Collection and corpus encoding spend
remain separately attributable. An unchanged retry is idempotent.

The facade borrows both components; the caller owns their configuration,
credentials, archive policy and closure. It refuses overlapping operations
instead of creating an unbounded implicit queue. Closed/replaced corpus storage
refuses before starting new source work. Research suspension retains the existing
checkpoint behavior; persistence happens when resumed research returns a result.
Cancellation during handoff raises `CorpusHandoffCancelled`, a `CancelledError`
subclass carrying the completed source result, after corpus cleanup has drained.
It must be retained by the caller just like an ordinary handoff failure.

This is automatic **completion-time** handoff, not an unattended service, a
per-source crash-durable frontier or a delivery outbox. A process crash can still
lose an unarchived exception result; operation recovery remains I03. Existing
configured journals and application-owned archives must not be discarded merely
because corpus indexing failed. Automatic collection does not yet consult the
corpus during research planning or answer composition.

SQLite owns accepted document snapshots including exact raw bytes, parser/source
provenance and retained relevant images. It also owns all native passage spans,
exact returned vectors, input-bound encoding observations, effective configuration
and operation states. Appending an unchanged document snapshot is idempotent.
An append first validates complete harvest/source bindings and configured
document/chunk/encoding bounds. Only after all required embeddings complete
does one transaction publish originals, passages, vectors and a new generation.
Failed or cancelled calls remain audited without publishing partial passages.

Native text is not translated or summarized before indexing. Relevant retained
image OCR and independently reviewed visual claims enter the same text index
with distinct `image_ocr`/`visual_claim` provenance and image-region anchors;
they do not pretend to have offsets in the parent page. Logos/rejected images
are not selected from transient observations. Parent document verdict language
is retained; it is not per-image language detection or script identification.

A lazy, compiled FAISS HNSW cosine view is rebuilt from durable vectors when a
process opens a generation or observes an append. Serialized native index files
are not treated as authoritative or deserialized from arbitrary input. Model,
source, span and call bindings are checked before a rebuilt index is used.
The index can be discarded and reconstructed without another embedding call.
HNSW is approximate, and similarity is not a calibrated probability. Empty or
below-threshold results remain empty. A language filter applies to bounded ANN
candidates, so it can return fewer than top-k; it is not exhaustive filtered
search. Measure candidate recall and startup/refresh costs on the intended
corpus before choosing operator limits or claiming large-scale throughput.

## Failure and lifecycle

The root and database must be owned, private and non-symlinked; replacement,
hard-linking or changed bytes refuse. A single append writer is excluded across
processes; independent readers may query the last committed generation while an
append is encoding. Model audit reservations are durably established before
calls. Capacity exhaustion refuses rather than truncating native evidence or
silently discarding old audit. Explicit export/rotation/retention remains an
operator responsibility pending I11.

An interrupted operation is recorded as pending, not automatically replayed.
Its audit distinguishes observed refused/cancelled calls from unavailable adapter
telemetry. Operation-level uncertain-call reconciliation is still I03. The
component refuses closure during active operations; it owns and drains an
off-loop native index build on cancellation. Embedding spend here has its own
explicit allowance and is not relabeled as the collector's already spent
frontier-scoring budget.

## Bounded verification

The test interpreter was Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-corpus-20261007/src/ghimera`; it imports no platform SDK, so
platform doctor/floor are not applicable. Actual installed backends were
faiss-cpu 1.15.1 and NumPy 2.4.6. The broader corpus/encoding/collector/execution
selection passed 74 tests in 64.70 seconds before the final identity, off-loop
reconstruction and recipe/config separation changes. After those changes, all
15 corpus witnesses passed in 13.92 seconds, no skips.

The full `scripts/gate.sh` at frozen `7733285` then returned **860 passed in
709.94 seconds**, no failures or skips, on Python 3.11.16 at that same
interpreter and corpus checkout. Ruff and formatting passed over 194 files;
strict mypy passed over 129 source files; the offline lock checked 142 packages.
The gate used explicitly configured installed Chromium, bubblewrap, Tesseract,
English traineddata and fixture font. This covers software contracts, not real
embedding accuracy or representative Pacific OCR. The subsequently added
`PersistentCollector` facade was not present in that frozen gate.

The facade and its corpus/collector/continuation importers subsequently returned
**49 passed in 71.73 seconds**, no failures or skips, under Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-automatic-corpus-20261007/src/ghimera`. Five new witnesses exercise
automatic configured research handoff, exact result/receipt bindings and fresh
corpus reopening; cancellation retaining completed source work and releasing the
facade; closed-store preflight before source/model calls; checkpoint resume
without source refetch; and persistence-only retry after a configured encoding
capacity refusal. All use credential-free local protocol fixtures, not actual
model-quality claims. Ruff/format passed over 196 files and strict mypy over
130 source files.

The first facade-only run returned four passes and one failed retrieval assertion:
the protocol fixture assigns orthogonal vectors to plural `ports` and the source's
singular `port`. The corrected witness queries retained native text and validates
each returned passage against its original; no threshold, source binding or
quality criterion was relaxed.

The full `scripts/gate.sh` at frozen `3b21a7a` subsequently returned **865 passed
in 734.20 seconds**, no failures or skips, on Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-automatic-corpus-20261007/src/ghimera`. Ruff/format and strict mypy
passed, and the offline lock checked 142 packages. This includes the automatic
handoff; the subsequent responsive-image candidate was not in that gate.
Versioned package publication and real model/language acceptance remain pending.

The responsive-image build at frozen `b887075` subsequently passed its full
`scripts/gate.sh`: **886 passed in 750.44 seconds**, no failures or skips, using
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-responsive-visuals-20261007/src/ghimera`. Ruff/format, strict mypy
and the offline lock check passed. Corpus-backed discovery was not in that gate.

The discovery candidate's first expanded selection returned **20 passed in
22.76 seconds**, no failures or skips, using that same Python interpreter and
`/tmp/ghimera-corpus-leads-20261007/src/ghimera`. Real SQLite/FAISS and
credential-free loopback HTTP fixtures cover native query/original readback,
wrong-corpus/bounds preflight, language-filtered empty observations, duplicate and
size omissions, tampered original/query/lead refusal, configured single-provider
and multi-provider research, and repeated cancellation draining an original-read
worker. The full `scripts/gate.sh` at frozen `0ab1443` subsequently returned
**906 passed in 785.14 seconds**, no failures or skips, on that same Python
3.11.16 interpreter and corpus-leads checkout. Ruff/format passed over 203 files,
strict mypy passed over 135 source files, and the offline lock checked 142
packages. Delivery-outbox changes were not in that gate. Versioned publication
and real language/model acceptance remain pending.

Witnesses include actual SQLite/compiled FAISS, fresh-process query and original
readback, idempotent appends, native Japanese span coverage, pending append
beside an independent committed-generation reader, failed/cancelled model audit,
capacity-before-spend refusal, incompatible model/recipe refusal, metadata and
vector corruption, operator tuning without re-embedding, and repeated cancellation
while a native build is draining. Selective visual intake ran actual local
Tesseract on a controlled raster and proved that its OCR passage/image anchors
are queryable while the logo is never downloaded or stored as an image.

One earlier combined run reported 73 passes and one failure in the new visual
witness: its 80-character native window recipe produced two native passages,
not the one assumed by the fixture assertion. The corrected witness requires
all three native/OCR passages and does not impose a semantic order on tied
protocol-fixture vectors. No representative model-quality claim follows from
the loopback encoder's deterministic replies. Source/tests/examples were frozen
during every reported run. Full-package gate and publication remain pending.

## Acceptance still required

Real SQLite, FAISS, fresh-process readback and the existing loopback model wire
can establish durability and source bindings, not multilingual model accuracy.
Simplified/Traditional Chinese, Japanese, Korean, Tagalog/Filipino and the other
Pacific priorities require representative native and cross-language retrieval
checks on the actually admitted encoder. The example's name is a placeholder,
not model admission. Unattended service wiring, hybrid/reranked context,
long-running refresh and scalable corpus-size acceptance remain open.
