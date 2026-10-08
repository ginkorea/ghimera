# Opt-in offline learned reranking

`ghimera.corpus/2` adds an independently configurable local cross-encoder stage
after the complete bounded vector/BM25 union. Native weighted reciprocal rank
fusion remains `ghimera.retrieval-evidence/1`; it is not learned relevance.
Corpus `/1` still uses its original selection, serialized fields, digests and
immutable passage-vector recipe. Changing query-only reranking policy does not
require re-encoding immutable stored passages.

## Operator boundary

Load `examples/offline-reranking.toml` with
`OfflineRerankingConfig.model_validate(tomllib.loads(...))`. The example is inert:
replace every artifact digest, immutable model revision, installed runtime
version and absolute path with reviewed local choices. Declare the complete
model/tokenizer inventory. Only canonical relative data files are accepted;
extra files/directories, symlinks, executable model code, missing files, changed
digests and excess artifact bytes refuse. Transformers weight loading requires
safetensors; the ONNX recipe requires a standalone captured model.

The Transformers `/1` adapter requires a separately provisioned Python environment with
the exact declared PyTorch and Transformers versions. Ghimera does not install
dependencies, download weights, consult a model hub or run model-provided code.
The supported runtime is `transformers_sequence_classification/1`, CPU only,
with one raw relevance logit per query/document pair. A logit may be negative;
it is **not** a probability, acceptance score, entailment or calibrated quality
claim. Model compatibility and suitability remain the operator's responsibility.

All model, tokenizer, runtime, pair/token/batch/thread/concurrency, byte and time
bounds are part of the typed policy and its observable non-secret identity.
The worker uses an owned private working directory, a digest-verified copied
artifact snapshot, local-files-only loading, `trust_remote_code=False` and an
explicit offline environment without inherited authorization/proxy variables.
This is an offline model-loading boundary, not a security sandbox for compromised
Python dependencies or protection against a malicious local filesystem owner.
CPU and artifact-memory use are bounded by the declared workload/artifact caps;
this slice does not impose an OS-level RSS limit.

### Optional ONNX CPU recipe

`ghimera.offline-reranking/2` with `onnx_sequence_classification/1` selects the
explicit nested `ghimera.onnx-reranking-runtime/1` policy instead of Torch or
Transformers. The old `/1` fields, serialized bytes and semantics are unchanged;
mixing Torch fields, runtime names or schemas refuses. The safe template is
`examples/offline-reranking-onnx.toml`. Operator-supplied versions are checked
exactly for ONNX, ONNX Runtime, tokenizers and NumPy; no dependency is installed
by Ghimera. ONNX's official protobuf reader/checker owns graph parsing.

The complete inventory is exactly the declared `.onnx` model and tokenizer JSON.
Only their digest-verified captured bytes are used. Before session loading,
bounded inspection of every graph protobuf message rejects external tensor data,
including tensors in nested graph attributes, functions and sparse initializers.
External `.data` files are not supported. The model is passed to ONNX Runtime
as bytes, with only `CPUExecutionProvider`, explicit thread/optimization/memory
options and fallback disabled. No custom-op libraries or model code are loaded.

Declare exact model tensor names and their tokenizer sources (`ids`,
`attention_mask`, optional `type_ids`), and the one logit output. This recipe
requires int64 rank-two inputs with dynamic batch/sequence axes and one float32
`[dynamic_batch, 1]` output. Missing/extra keys, static input axes, alternative
dtypes, multi-head outputs and provider expansion refuse. Actual returned array
dtype, complete batch length, single-logit shape and finite values are checked
again. The backend never invents scores or falls back to another recipe.

Tokenizers loads only the captured local JSON. Truncation is explicitly disabled
on each pair encoding; all complete token lengths are admitted before prediction.
Longest-in-batch padding uses the declared direction, token/id and type id. The
padding token must resolve to its declared vocabulary ID; no implicit token
truncation or length rounding is allowed. The tokenizer's native pair processor
and special tokens are retained as part of its artifact identity.

## Native API

`EvidenceCorpus(..., reranker=port)` accepts the narrow `PassageReranker` protocol
(`config`, async `prepare()`, async `score(RerankRequest) -> RerankScores`). Its
policy must equal the corpus's exact configured `reranking` policy. An injected
port is an implementation boundary, not proof of trained-model behavior. Omit
the port to select `OfflineCrossEncoder` automatically when opening corpus `/2`.
Corpus `/1` refuses injected learned ports.

Select corpus `/2` explicitly when validating the corpus configuration:

```python
configured = CorpusConfig.model_validate({
    **legacy_config.model_dump(),
    "schema": "ghimera.corpus/2",
    "reranking": offline_policy,
})
# Keep explicitly configured document/query encoding ports separate.
store = EvidenceCorpus(configured, encoder=document_encoder,
                       query_encoder=query_encoder)
result = await store.search(query_text, top_k=chosen_top_k,
                            languages=chosen_languages,
                            retrieval=hybrid_policy)
```

Supply the explicit native `HybridRetrievalConfig` on every learned query.
Missing hybrid policy and failed runtime admission refuse before query encoding.
The default adapter rechecks a freshly captured artifact/runtime snapshot on each
query's preparation and again when scoring; it does not cache admission of
mutable paths. This intentionally loads the local model in two finite child
invocations per nonempty query rather than silently trusting stale admission.
The embedding/query encoder remains its own explicit port and transport policy;
offline reranking does not make a remotely configured encoder offline.

The result is `ghimera.corpus-query/2`, with separate RRF and
`ghimera.reranking-evidence/1` records. The learned request binds the exact corpus
configuration, generation, query, complete RRF-union passage IDs, original
document identities, full passage hashes, exact text hashes, languages and
cosines. Every candidate is scored exactly once. Missing, duplicate, foreign,
non-finite or excessive-token returns refuse; no silent RRF fallback occurs.

Before any prediction, tokenize every complete pair with special tokens and
`truncation=False` (Transformers) or `no_truncation()` (tokenizers). An excessive
pair refuses the entire query, not a shortened
reading. Inference uses explicit bounded batches, also without truncation.
Candidates outside `max_pairs` or aggregate `max_input_chars` refuse instead of
silently narrowing the candidate pool. These actual-pool checks follow query
encoding because the pool is not known earlier; that encoding remains charged
even if learned admission subsequently fails.

Selection sorts by descending raw logit with passage-ID tie breaking. It records
an exhaustive decision for every candidate: cosine floor, language mismatch,
per-original-document passage cap, original-document cap, top-k or selected.
Original-source exclusion/coverage accounting happens before top-k and never
rewrites native passages. Coverage here is source diversity within the admitted
candidate pool, not proof that all relevant sources were retrieved. Reader
original-byte/response bounds still apply and have their own omission evidence.

## Supported scope and open paths

Independent corpus searches, reopened stores and `CorpusEvidenceReader` retain
the versioned learned evidence and original-source bindings. Existing reader
policy pins the exact corpus configuration identity. The learned runtime uses
the existing bounded `PassiveWorker`; there is no second durable store and no
embedding-recovery reinterpretation. Each successful nonempty query runs fresh
local inference; no learned-return adoption or retry is fabricated.

Native research reuse currently refuses a learned corpus before model contact:
run-bound rerank reservations, retained ACK adoption and ledger replay remain a
separate implementation slice. Corpus discovery used by research also refuses
learned policies, and research retrieval reports refuse learned snapshots rather
than validating them as unreserved historical model work.
The current service corpus-query request does not
carry the required hybrid policy, so service learned queries are not supported
by this slice. Unsupported legacy query calls refuse rather than bypassing the
selected learned policy. GPU, multi-label/probability heads, implicit truncation,
hub downloads and remote/custom model code are unsupported.

The focused contract/backend tests use controlled encoders, controlled logits,
synthetic data-file inventories and a mocked optional runtime. They exercise
native source storage, evidence validation, actual artifact capture and recipe
arguments, but do not establish real trained-model loading, learned ranking
quality, multilingual quality or end-to-end research acceptance.

Source verification for this slice used Python 3.11.16 with the isolated native
source directory explicitly on `PYTHONPATH`; the SDK, PyTorch and Transformers
were absent. The two new test modules plus narrowly selected legacy schema,
RRF, encoding reconciliation, research-original and corpus-discovery regressions
passed **61 tests in 9.75 seconds**, with no skips. Ruff check/format passed for
the 12 owned Python files; strict mypy with silent traversal of unchanged imports
passed for the 10 owned native source files. This was not a full gate or an
installed-package/trained-model acceptance run.

The optional ONNX followup additionally passed 78 focused tests with no skips
on the same Python 3.11.16 source-test interpreter (ONNX/ORT/Torch/Transformers
absent), in 10.94 seconds. Ruff check/format passed for its five owned Python
files; strict mypy passed for all 11 native reranking/corpus/research source
importers. That includes both unchanged model-facing fingerprints and a real child
refusing unavailable exact runtime versions. A separately invoked finite helper,
`tests/onnx_reranking_contract.py --output-directory /explicit/fresh/owned/path`,
passed three actual CPU/child fixture cases in an owned Python 3.11.16 environment
with ONNX 1.20.1, ORT 1.23.2, tokenizers 0.23.2 and NumPy 2.4.6: untrained tiny
graph inference/complete pair lengths, real-protobuf external-data refusal, and
changed-artifact refusal after prior successful admission. These are tensor and
lifecycle guarantees, not trained ranking or multilingual quality acceptance.
