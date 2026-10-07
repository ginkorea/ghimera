# Durable native evidence corpus

Status: source candidate; bounded contract checks passed, full gate pending. This
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
not model admission. Automatic collector/service wiring, hybrid/reranked context,
long-running refresh and scalable corpus-size acceptance remain open.
