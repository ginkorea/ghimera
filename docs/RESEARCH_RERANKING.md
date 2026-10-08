# Run-bound learned corpus retrieval

Opt in with the typed `research.reranking` policy shown in
`examples/research-reranking.toml`. It requires the original durable run journal,
native model-work result retention and shared judge allowance. The selected
native corpus must enable its own encoding recovery. Its exact reader/search
binding pins the corpus configuration, including the independently configured
offline CPU reranker and artifact/runtime recipe. There is no new store or
transport and no download, remote code, GPU allocation or probability conversion.

Both native corpus discovery and retained research use the existing corpus
transaction and immutable generation to build the full RRF candidate union.
Before score contact, native model work reserves one shared judge call and the
explicit rerank call/pair/input-character allowance. The original intent records
the exact native request, corpus recipe, generation, RRF evidence and original
passage/text hashes. The original ACK retains finite complete CPU scores.
Original reservations, including UNKNOWN outcomes, remain charged on readback
and restore. Native source exclusions and coverage precede top-k selection.

`validate_rerank_rows` revalidates the original committed prefix before contact
or replay. Its retained-request work is bounded by the original model input and
result limits; the prefix is bounded by the native journal's record count and
byte capacity. It scans that finite prefix for each original rerank intent to
check its ACK, so worst-case work grows with rerank intents times journal rows.
It does not scan corpus history, reopen arbitrary files, or maintain a second
unchecked incremental accounting cache.

`RerankDecision` requires either explicit fresh work with an operation key or
explicit local replay with that same key and the original intent sequence.
Reusing a fresh key never authorizes a second invocation; any unresolved rerank
holds new score contacts. Acknowledged replay requires the original committed
run prefix, exact corpus/recipe/query/generation/candidates and retained return.
It reuses the corpus encoding ACK and does not re-encode, prepare a backend or
score again. A changed binding, malformed result or unknown outcome refuses.
CPU evidence is distinct from HTTP request/response or token-spend evidence.

Native research creates fresh decisions for its planned discovery operations
and retained queries. Successful restored retained snapshots are not repeated.
Unconfigured legacy recipes retain their original serialization and behavior;
direct unbound learned `CorpusLeadSearch.request` still refuses.

This increment does **not** extend automatic recovery to arbitrary process death
inside discovery or retained retrieval. Such cuts remain held: a score ACK alone
cannot restore the driver cursor or surrounding query/fetch effects. Explicit
original-ACK replay is available only with its original run and allowances.
Contract tests use controlled scores, not a substitute model. Real multilingual
ranking quality, original quality failures and native real-source acceptance
remain separate pending evidence; source completion is not a release claim.
