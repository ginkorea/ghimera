# Discovery, corpus retrieval and caller browser candidate

This candidate extends the existing native collector owners. It does not start
an index service, browser, proxy or alternative crawler. Ahmia and MCP lead
compatibility remain unchanged; independently hosting Ahmia is deferred.

`CorpusSearchConfig.retrieval` and `CorpusEvidenceConfig.retrieval` accept the
explicit `ghimera.hybrid-retrieval/1` policy. The corpus reuses its pinned native
vector generation, reads exactly that generation's immutable passage identities,
and builds a bounded lexical view. Unicode word and script-bigram tokenization
preserves original passages; normalization occurs only in search representation.
BM25 candidates and vector candidates form a union. Weighted reciprocal-rank
fusion reranks that union; lexical candidates outside the approximate shortlist
receive an exact cosine against their retained vector. The original configured
minimum cosine and language admission still apply. Query receipts carry the
full fusion policy, candidate ranks, lexical scores and reproducible fusion
scores. Native offsets, visual/PDF reading basis and model encoding calls remain
with the existing corpus query. Capacity failures refuse before query inference.

No optional policy means the existing vector-only path and serialized policy
identities. This is deterministic rank fusion, not a claimed cross-encoder model.
The lexical view is rebuilt within explicit bounds, so representative corpus
scale and real cross-language quality remain acceptance work. Priority scripts
are not admitted by a tokenizer test or an embedding model's marketing.

`SourceFeedConfig.site_api` admits `json_api` as an explicit response dialect,
with exact origin bindings and per-site JSON paths. Result, reference, cited-by
and next-page mappings produce the existing source-feed entries and native
frontier links. References are declarations by the original response, not
independent proof that another paper supports a claim. Each mapped entry retains
its relationship and JSON pointer; replay reparses the exact retained original.
URL templates accept one percent-escaped observed identifier, never generated
model URLs. Duplicate JSON keys, ambiguous fields, nonfinite numbers and
structure/entry/output overruns refuse. JSON Feed and site JSON cannot silently
compete for the same parser. Fetch scope, robots, sessions, pacing, conditional
refresh, graph and source journals remain owned by the existing collector.

The Crossref fragment is a concrete metadata/reference mapping. Operators can
map another entitled API's cited-by endpoint and pagination fields through the
same boundary. API configuration is not permission to disclose credentials or
query private endpoints. Independent source fetching and semantic review still
decide whether declared references enter an answer.

`HumanBrowserConfig.pagination` supplies an exact landing URL, next-page URL
and selector. The caller-bound page navigates through the existing main-frame
admission, retains its landing DOM and native chain, then clicks the configured
selector under a new guard whose first request must match the requested source.
Both DOM reads enter the shared collector byte budget. Redirect denial precedes
contact; action mismatches refuse. Existing human assistance/session continuity
and native attachment/inline downloads keep their established owners.

`HumanBrowserConfig.tor_verification` is explicitly opt-in. Unverified Tor
declarations still refuse. Before contact, native Chromium command-line
observations must match one loopback SOCKS proxy, loopback proxy bypass disabled,
and direct DNS disabled except for that proxy. Alternate/direct proxy modes and
duplicate route flags refuse. A bounded HTTPS JSON document on the same target
must then report boolean `IsTor: true` and a public exit IP. Native network
metadata must bind that guarded request and an uncached JSON response;
redirects, disk/prefetch caches and service-worker replies refuse. Exact probe text,
digest, native chain and policy are retained with DOM/download/inline evidence.
The probe is an operator-approved external route assertion, not proof about
unmetered browser subresources. Its URL must be admitted by both browser and
collector scopes, and its source action and bytes use the existing budget.
The borrowed browser/profile/session is never modified or closed.

Configuration examples: `hybrid-retrieval.toml`, `source-api-crossref.toml`,
`browser-pagination.toml` and `browser-tor.toml`. Their operational values are
examples, not deployment defaults. Missing runtime paths, browser/network
admission and model-quality evidence are separate from source implementation.

## Acceptance record

Measured interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16.
Import: `/tmp/ghimera-discovery-20261008/src/ghimera/__init__.py` via absolute
`PYTHONPATH`; platform SDK absent intentionally. No shared environment installs.
Tests use owned real-disk temporary state and the explicitly supplied Chromium
executable and bwrap inputs. Native loopback/Chromium execution requires scoped
test escalation because the sandbox prohibits socket creation.

The native robots-aware public direct transport retrieved
`https://api.crossref.org/works/10.1038/nphys1170` with HTTP 200 under that exact
interpreter and source import. The retained original is 4,918 bytes with SHA-256
`921770fcba28cbfe695a01a42af0cd9df9a59ed07842e665e16e455983aa456f`.
The original metadata title is “Measured measurement.” Configured parsing
produced 11 entries, including 10 declared references, and 10 admitted HTTP(S)
links with no configured link-cap omissions. Exact-source replay passed.
This acceptance retained the original, native page, source-feed evidence and
robots/fetch ledger in an owned acceptance directory. It did not contact those
reference targets, infer their entailment or call a model.

Final bounded test results are recorded below before commit. Fixtures cannot
establish real publisher entitlement, a working Tor exit, onion investigation,
multilingual semantic quality or full I14 completion. The first broad owning
gate reached its explicit time limit without a terminal summary; it is not a
pass. Split reruns use the supplied OCR inputs rather than treating missing
fixture environment variables as source failures.

The final split owning gates passed under the measured Python 3.11.16
interpreter and absolute candidate `PYTHONPATH`: 95 corpus/API/feed tests in
111.42 seconds and 64 native browser tests in 119.16 seconds, with no failures
or skips. The subsequent final query-admission and safe-metadata guards passed
their 15-test hybrid/pagination selection in 10.32 seconds, with no failures or
skips. Ruff and strict mypy passed for all 19 changed/new production modules.
These are scoped candidate checks, not the combined repository/release gate.

The native fixtures demonstrate generation-bound SQLite/FAISS retrieval,
retained evidence reader reuse, exact API reference/cited-by/next-page fetching
through the public Collector, graph/journal/source readback, real Chromium
pagination with and without caller assistance, existing native downloads and
inline documents, guarded redirect denial, and refusal of a direct browser
before any attempted Tor probe/source contact. Native-script lexical cases
include separate Simplified/Traditional Chinese, Japanese, Korean and Thai;
fixture vectors do not establish cross-language model quality.

Still required for I05/I08/I13/I14 acceptance: independently reviewed real
multilingual retrieval/reranking and omissions, representative publisher
pagination/entitled sessions, a live admitted Tor browser/onion investigation,
real cited-by/source refresh traversal and unattended connector/runtime
admission. No Tor process/index/model was deployed, no private entitlement was
invented, and no representative-language or full end-to-end claim follows from
these source checks. Root integration owns the combined gate and artifact
publication decision.
