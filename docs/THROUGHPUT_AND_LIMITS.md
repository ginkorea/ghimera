# Throughput, run limits and causes (0.4.12)

Status: **prepared for 0.4.12**, not yet published. Evidence is recorded in
[release acceptance](RELEASE_0412.md).

0.4.12 changes how fast a page is read, what ends a run, and what a refusal
says. Recipes written for 0.4.11 parse and serialize unchanged unless noted.

## Run limits are off by default

A self-hosted deployment is not a metered API. These run limits are now
optional and **absent means no limit**:

| Field | Where |
|---|---|
| `page_budget`, `byte_budget`, `judge_budget`, `wall_seconds` | run recipe |
| `max_rounds`, `query_budget` | `[research]` |
| `encoding_call_budget`, `encoding_char_budget` | `[scoring]` |

Omit a field, or write `"unbounded"`, `0` or `null`, for no limit. A configured
positive number remains an exact ceiling enforced at its existing spend
boundary. `RunBudget` keeps accounting every fetch, byte, judge and encoder call
on the receipt whether or not a limit is configured, so a host can show spend.
An unbounded `wall_seconds` is represented internally as one year of remaining
time, so per-call deadlines stay finite numbers.

`BUDGET_EXHAUSTED` is raised only for a limit a recipe configures, as
`BudgetExhausted`, naming the limit with used and limit, for example
`run budget page_budget exhausted: used 2 of limit 2`. Only such a run limit,
or an expired configured wall clock, ends collection.

What ends a run without limits is the mission's own stopping rules: goal
satisfied, every question answered, collection saturation
(`saturation_window`/`saturation_min_new`), an empty frontier, cancellation or a
contract failure. Research without `max_rounds` also stops when
`saturation_rounds` consecutive rounds (default **2**) retain no new evidence;
an explicit `max_rounds` keeps the original behaviour.

## One page never ends a round

A refusal that belongs to one page refuses that page, records a ledger row with
its `detail`, and the round continues:

- a reader's own deadline (`extraction.timeout_seconds`) or output bound;
- an encoder or model that stays unavailable after its retries;
- an overlong link text (below).

`ADAPTER_CONTRACT` (a broken invariant in the core or an injected adapter)
still fails the collection.

### Per-text encoder bounds cut, never refuse

Each link is scored on its observed `url\nanchor`. If that text exceeds the
encoder's `max_text_chars`/`max_input_chars` (less `text_prefix`), it is cut:
the URL keeps its scheme, host and path (query and fragment dropped) and the
anchor is truncated to the remaining room. `LinkSimilarity.omitted_chars`
records the characters cut. A whitespace-only scoring window is skipped and
counted as omitted. A page's encoder batches are sent concurrently, each at the
service's `max_batch_texts`; every call made is recorded.

### Transient model refusals retry

`model_retry` (`ghimera.model-retry/1`, default 180 s total waiting, 1 s
doubling to 30 s) retries refusals that a shared service gives while reloading
or overloaded: HTTP 408, 409, 425, 429, 500, 502, 503, 504, or a dropped
connection. Each try is its own accounted call and ledger row. Wrong models,
invalid envelopes and contract faults are never retried. Encoder batches,
legacy document verdicts and research planner/analyst calls retry; calls bound
by durable `model_work` keep their single recorded intent and are not retried
here. When the retries are exhausted the page is skipped with
`model_unavailable` and its detail; in-flight sources are never cancelled.

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

The crawl4ai content filter, locator profiles, generic recovery, rendering and
document parsers are unchanged and still read every page the fast pass declines.

Concurrent collection remains [`execution`](CONCURRENT_COLLECTION.md); configure
it for concurrent fetch, extraction and scoring of admitted leads, bounded by
the HTTP scheduler's per-origin politeness and `global_concurrency`.

## Refusal causes

Every `GhimeraRefused` carries `detail`: one bounded sentence (400 characters,
single line) saying what was checked and what was found. Code that supplies no
sentence gets the refusing module, function and line. Details never contain
credentials, secrets or page bodies. Refusal ledger rows, verdict/model failure
rows and encoder failure rows carry `detail`.

Examples: `journal directory runs has mode 755; group/other access must be 0`,
`html page of 9437184 bytes exceeds max_input_bytes=8388608`,
`rich reader: no title on a 2048-char page`, `encoder answered HTTP 409`,
`link depth 3 exceeds max_depth=2`.

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
accident; its retained proof keeps the provider's original title.
