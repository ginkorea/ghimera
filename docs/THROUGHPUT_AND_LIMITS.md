# Throughput, input bounds and kept results (0.4.12)

Status: **prepared for 0.4.12**, not yet published. Evidence is recorded in
[release acceptance](RELEASE_0412.md).

0.4.12 changes what ends a run, what a bound does, what happens to a page a
reader or judge cannot handle, how fast a page is read, and what a refusal says.

## There are no run ceilings

The core runs on the operator's own hardware. A hard question may take hours,
and a limit with no threat behind it is friction. The spend ceilings are gone:

| Removed key | Where |
|---|---|
| `page_budget`, `byte_budget`, `judge_budget`, `wall_seconds` | run recipe |
| `max_rounds`, `query_budget` | `[research]` |
| `encoding_call_budget`, `encoding_char_budget` | `[scoring]` |
| `max_calls`, `byte_budget`, `call_seconds_budget` | each `[[discovery.providers]]` |
| `cited_by_query_budget` | `[references]` |
| `max_calls_per_run` | `[semantics]`, `[semantics.verification]` |
| `max_proposal_calls`, `max_review_calls` | `[identity_automation]` |
| `max_calls` | `[research.reranking]` (`max_pairs`/`max_input_chars` now bound one call) |
| `max_files_per_run`, `max_total_bytes` | `[local_inputs]` |
| `max_input_bytes`, `max_unanswered_calls` | `[model_work]` |
| `max_total_result_bytes` | `[model_work.results]`, `[scoring.run_encoding_recovery]` |

A recipe that still carries one is refused at load with one sentence:

```
recipe key 'page_budget' is refused: the core no longer has run ceilings (removed in 0.4.12); delete the key
```

Nested keys are named with their section (`'research.max_rounds'`,
`'scoring.encoding_call_budget'`, `'discovery provider byte_budget'`,
`'references.cited_by_query_budget'`, `'semantics.max_calls_per_run'`,
`'semantics.verification.max_calls_per_run'`,
`'identity_automation.max_proposal_calls'`, `'research.reranking.max_calls'`,
`'local_inputs.max_total_bytes'`, `'model_work.max_unanswered_calls'`,
`'model_work.results.max_total_result_bytes'`,
`'run_encoding_recovery.max_total_result_bytes'`). A per-run count of calls,
queries, files or retained bytes is a spend ceiling by another name; only the
bound of ONE input remains. `model_work.max_input_bytes` bounded nothing that
was stored (the intent keeps a digest and size); each model service's own
`max_input_chars`/`max_request_bytes` bounds what is sent. Unanswered model
calls stay recorded and held for reconciliation without a count ceiling. `BudgetExhausted`, `RunBudget`, the
`budget_exhausted` refusal code and the `budget_exhausted` stop reason no longer
exist.

A run ends only on the mission's own rules:

- the goal is satisfied (grade) or every question is answered (research);
- collection saturation: `saturation_window` fetches retaining fewer than
  `saturation_min_new` new documents;
- research saturation: `saturation_rounds` consecutive rounds (default **2**)
  retaining no new evidence;
- an empty frontier, cancellation, or a contract failure.

A `stop` ledger row with reason `failed` carries `detail` naming what failed
(for example `adapter_contract at https://example.org/a: adapter returned an
unbound link`).

Per-call deadlines are the services' own finite timeouts
(`request_timeout_seconds`, `extraction.timeout_seconds`, `browser.timeout_seconds`,
each model, encoder and discovery provider `timeout_seconds`); no run clock
shortens them.

### Spend is still counted

`RunSpend` (`ghimera.spend`) counts fetches, bytes, judge calls, encoder calls
and characters, search calls and rerank calls before each call, so receipts,
rounds and ledger rows show what a run used. Rounds keep `language_queries` and
`pages_by_language`. Nothing compares a count with a limit. A restored run adds
its downtime to `elapsed_seconds`; that is reporting, not a clock that stops
anything.

## Results are not thrown away

- **Rich-tier extraction failure.** When the rich HTML reader refuses a page
  (no title, a parse failure, an invalid worker reply, locator drift, its
  deadline), the page is read again in-process as plain body text (lxml,
  `script`/`style`/`noscript`/`template` stripped) and **kept**. Its evidence
  says `tier = "fallback_plain"`, `parser_revision` starts with
  `ghimera.fallback-plain/1:`, and `fallback_refusal`, `fallback_detail` and
  `fallback_attempts` keep the failed rich-tier attempt. A ledger `policy` row
  `extraction_fallback_plain` names the refusal it replaced. No fast-reader
  recipe is needed for this.
- **No text at all.** Only a page from which no text can be read is refused.
  With a run journal configured its fetched bytes are kept at
  `<journal directory>/.refused-pages/<run id>/<sha256>` (mode 0600), and the
  refusal detail names that path.
- **Judge outage.** A served judge that refuses (`model_unavailable` after its
  retries, an input bound, a deadline) never drops a page or stops a round: the
  page is kept **unjudged** (`verdict.kind = "unjudged"`, reason
  `unjudged: judge refused ...`) and the `served_judge_failed` refusal row stays
  in the ledger. A grade the judge cannot give is skipped. Only a contract
  failure (`adapter_contract`, ledger/graph sink or contract, research contract)
  fails the run.

## Input bounds: cut, or refuse that one input

An input bound protects a reader from one malformed, hostile or never-ending
input. It is never a spend limit, and it never ends a round. Where the material
can be cut and the rest still used, it is cut and the omission recorded. Where
cutting is impossible or unsafe, that one page, image, file or call is refused
with `BOUND_EXCEEDED`; its `detail` names the bound, used and limit and says
`an input bound, not a spend limit`, for example
`input bound visuals.max_pixels exceeded: 2.25e+08 over limit 4e+07; an input bound, not a spend limit`.

### Cut (the rest is used, the omission recorded)

| Bound | What happens | Recorded as |
|---|---|---|
| `extraction.max_input_bytes` (HTML reader) | the first `max_input_bytes` of the page (and of its rendered DOM) are read; evidence still names the whole page's digest | `ExtractionEvidence.omitted_bytes` |
| `extraction.max_text_chars`, `max_links` (fast and plain readers) | text cut; first links kept | `omitted_text_chars`, `omitted_links` |
| encoder `max_text_chars`/`max_input_chars` on a link text | URL head kept, anchor truncated | `LinkSimilarity.omitted_chars` |
| evidence context `max_chars`, `max_documents` | the windows that fit are taken, required citations first; the rest are left out | `ContextDocument.omitted_chars`, `omitted_documents` |
| `dedup.max_text_chars` | the exact-text digest covers the whole text; the near-duplicate simhash reads the first `max_text_chars` | `ContentFingerprint.omitted_chars` |
| `dedup.max_index_documents` | the document is kept, only not indexed for later near-duplicate matching | `ContentIndex.omitted_documents` |
| `identity_automation.max_context_chars` | pairs are dropped until the request fits (refused only when no pair fits) | `omitted_pairs` |

### Refuse that one input (`BOUND_EXCEEDED`)

| Bound | Why it is not cut |
|---|---|
| `http.max_response_bytes` | a cut binary or compressed body is not a document; set it above the reader bound so the reader cuts |
| `browser.max_input_bytes`, `max_protocol_bytes`, `max_resources`, `max_rendered_bytes`, `timeout_seconds` | an isolated renderer that overflows or never answers is not a page |
| browser download and inline `max_file_bytes` | a partial file is not a document |
| `human_browser.max_dom_bytes`, capture bytes, Tor probe bytes, capture deadline | an incomplete capture is not the page the person saw |
| `extraction.timeout_seconds` and the per-page/persistent reader deadlines | a reader that never answers (an HTML page then falls back to plain text, above) |
| `visuals.max_image_bytes`, `visuals.max_pixels`, the decoder's bomb limit | an image bomb; the decoder worker exits with its bound status and names the bound; that image is refused, the page and round continue |
| `local_inputs.max_input_bytes` | a local file is never read in part |
| model, visual-model and transcription `max_input_chars`/`max_request_bytes`; encoder batch and request bounds | a cut prompt changes what is asked |
| `model_work.results.max_result_bytes`, `run_encoding_recovery.max_result_bytes` | one retained answer must fit a journal record |
| `pdf_transcription.max_document_text_chars` | transcription evidence binds every reviewed page |
| `retained_evidence.max_source_documents`, `max_snapshot_bytes`; retained-evidence query bounds | a recovery snapshot must fit its journal |
| intent encoder `max_text_chars`/`max_input_chars` | an intent is never truncated |
| `research.reranking.max_pairs`, `max_input_chars` (of one rerank call) | a cut candidate set would change what is ranked |

## Transient model refusals retry

`model_retry` (`ghimera.model-retry/1`, default 180 s total waiting, 1 s
doubling to 30 s) retries refusals that a shared service gives while reloading
or overloaded: HTTP 408, 409, 425, 429, 500, 502, 503, 504, or a dropped
connection. Each try is its own counted call and ledger row. Wrong models,
invalid envelopes and contract faults are never retried. Encoder batches,
legacy document verdicts and research planner/analyst calls retry; calls bound
by durable `model_work` keep their single recorded intent and are not retried
here. When the retries are exhausted the page is kept unjudged (above).

## Reading a page in milliseconds

Per-page subprocess parsing costs several seconds of interpreter start and
vendor import, whatever the page size. Two opt-in extraction keys remove it; see
[`examples/fast-reading.toml`](../examples/fast-reading.toml):

- `[extraction.fast_reader]` (`ghimera.fast-reader/1`) reads a page in-process
  with lxml: `script`/`style`/`noscript`/`template` and the configured
  `excluded_tags` are dropped, body text is read under `generic_body_selectors`
  with block boundaries as line breaks, links keep their anchors (first
  `max_links`; further distinct hrefs are counted in `omitted_links`), text over
  `max_text_chars` is cut and counted in `omitted_text_chars`, and language is
  detected with the same lingua policy. Pages without a title or with less than
  `min_text_chars` of body text, and hosts with a locator profile, go to the rich
  tier. Evidence names the tier: `parser_revision` starts with
  `ghimera.fast-reader/1:`; its `raw_markdown_sha256` digests the unfiltered text.
- `worker_mode = "persistent"` keeps up to `max_workers` rich-tier interpreters
  that import scrapling/crawl4ai/lingua once and serve many pages over
  length-prefixed pipes. A worker past `timeout_seconds`, over its output bound
  or out of protocol is killed and replaced. `HtmlExtractor.close()` (or
  interpreter exit) kills workers and removes their package copies.

Concurrent collection remains [`execution`](CONCURRENT_COLLECTION.md); configure
it for concurrent fetch, extraction and scoring of admitted leads, bounded by
the HTTP scheduler's per-origin politeness and `global_concurrency`.

## Refusal causes

Every `GhimeraRefused` carries `detail`: one bounded sentence (400 characters,
single line) saying what was checked and what was found. Code that supplies no
sentence gets the refusing module, function and line. Details never contain
credentials, secrets or page bodies. Refusal, verdict, model failure, encoder
failure, visual refusal and failed stop rows carry `detail`.

Examples: `journal directory runs has mode 755; group/other access must be 0`,
`input bound http.max_response_bytes exceeded: 65536 over limit 65536; an input bound, not a spend limit`,
`rich reader: no title on a 2048-char page`, `encoder answered HTTP 409`,
`link depth 3 exceeds max_depth=2`.

## Compatibility

- Configuration keeps `chimera.config/1`: only a recipe that carries a removed
  key is refused, by name. Bumping the identity would have refused every recipe.
- Removing `max_calls_per_run` changes the semantic policy digests and the
  semantic request wire of every semantics profile; retained semantic answers
  from an earlier release do not replay.
- Receipts, ledgers and extraction/dedup evidence keep their schema identities;
  `omitted_bytes`, `omitted_chars`, `tier` and the `fallback_*` fields are
  optional and omitted when unset.
- The refusal vocabulary replaced `budget_exhausted` with `bound_exceeded`, and
  the stop reason `budget_exhausted` is gone. A journal, receipt or checkpoint
  written by an earlier release that recorded either value, or whose effective
  recipe carries a removed key, no longer restores: rerun it.

## Run identifiers

A run id is one path segment: it starts with an ASCII letter or digit and uses
only letters, digits, `.`, `_` and `-` (`ghimera.identifiers.RUN_ID_PATTERN`).
Dotted ids such as `ghimera.0123456789abcdef01234567-r1` are accepted by the
journal, graph, archive and continuation stores. A leading `.`, `/` and other
separators remain outside the alphabet.

## Preferred languages

`ResearchRequest.languages` (language codes such as `zh`, `en`, `zh-Hant`) asks
for sources in each language. The planning request carries them and the plan
prompt asks for at least one query per question in each language, in that
language's own words, with `SearchQuery.language` set. Discovery then adds a
language-routed copy of the question's first query (or its text) for every
(question, language) pair the plan left uncovered; it never translates. SearXNG
sends a query's language instead of the provider default. Each `ResearchRound`
records `language_queries` and `pages_by_language` (new evidence documents by
detected language). Without `languages`, plans and identities are unchanged.

## Search leads and `min_link_score`

Primary search hits are queued as unscored seeds at the top of the frontier;
`min_link_score` does not apply to them. It applies to links found **on**
fetched pages and to cited-by search candidates admitted as references. A
cited-by lead whose title is empty or only names its host is scored on its URL
path words and snippet instead, so it is not ranked below a well-titled lead by
accident; its retained proof keeps the provider's original title. Cited-by
expansion is bounded by `max_parents`, `max_queued_per_run` and
`max_candidates_per_parent`; there is no cited-by query ceiling.
