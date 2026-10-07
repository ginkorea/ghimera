# Infrastructure readiness review — 7 October 2026

This is the baseline assessment before the unreleased concurrent collection,
native corpus, automatic handoff and responsive-image candidates. Its original
missing-feature list below is historical, not a current implementation inventory.
Use [PRD_INFRASTRUCTURE.md](PRD_INFRASTRUCTURE.md),
[EVIDENCE_CORPUS.md](EVIDENCE_CORPUS.md), [VISUALS.md](VISUALS.md) and
[CONCURRENT_COLLECTION.md](CONCURRENT_COLLECTION.md) for current candidate
coverage and still-open quality/service requirements.

## Intended product and verdict

Ghimera is a standalone, intent-driven research collector: discover permitted
open-web and onion sources, fetch native documents as a human's explicitly
authorized proxy, assess relevance, preserve evidence, expand a configurable
graph and deliver cited findings. It is not merely a URL downloader, and it must
not call a partial or unsupported investigation complete.

The current release candidate has substantial collection and evidence machinery.
It is **not yet an accepted unattended research service** for the owner's
multilingual organizational-network use case. Source gates, a successful English
research diagnostic and bounded public-browser captures prove different things;
none closes the missing functional/operational requirements below.

This is an assessment and bounded implementation plan, not implementation of
these proposed components or authorization to deploy downstream integration.

## Built versus missing

| Area | Current source | Remaining capability or acceptance |
|---|---|---|
| Intent entry | Concrete `Collector.run`, planner, questions, rounds, source-bound answers and partial outcomes | Representative full-workflow quality and usable long-running service interface |
| Discovery | SearXNG, caller-owned MCP sessions, provider fan-out/fallback and stagnation switching; read-only Ahmia adapter | Operating onion index and MCP host; multilingual/minimized queries; corpus retrieval adapter; real dead-end recovery |
| HTTP/Tor | Guarded native HTTP and v3 onion transport; open web through Tor; scope, redirects, robots, conditional fetch cache, request isolation | Admission/health on the actual deployed runtime and representative complete onion investigations |
| Browser access | Isolated Patchright rendering, explicitly bound entitled source sessions, same-target Chromium assistance and terminal continuation | Verified browser Tor egress/subresources, native downloads, configured pagination/interaction workflows and real entitled-source acceptance |
| Challenges | Configured local FlareSolverr/Byparr clients and origin-private clearance | Installed providers and real authorized-site acceptance; route-preserving browser/Tor recovery. No universal CAPTCHA guarantee |
| Documents | Native HTML, DOCX/PDF, offline Docling layout/tables/OCR, immutable originals and extraction evidence | Representative non-Latin scans, tables and charts; diagram topology; Marker if it materially improves the measured corpus |
| Visuals | Selective accepted-HTML raster enrichment, bounded offline OCR, script-specific Pacific configuration, source/region provenance, optional reviewed local vision; logos/rejected images not retained | Representative infographic accuracy, standalone images, PDF figure crops, graph/answer-region integration and durable accepted-image indexing |
| Relevance | Native-text embeddings and source-bound score/call observations; explicit document judge | Persistent chunk vectors, multilingual retrieval/context ranking and calibrated acceptance policies |
| Knowledge graph | Configurable ontology; durable validated graph batches; semantic proposals, review/quarantine, graph-aware identity/dispute questions | Reliable real-model extraction; cross-source alias resolution/merge/split; temporal entity identity; chart relationships |
| Recovery | Journals, complete sealed archives, completed-round suspend/resume with original budgets | Durable per-operation recovery, reconciliation of uncertain calls and export acknowledgements; cross-run research reuse |
| Throughput | Concurrent discovery; transport slot limits and origin spacing | Collection pipeline currently processes frontier documents serially; bounded asynchronous fetch/extract/encode/review queues and pooled workers |
| Delivery/operations | Local receipts, ledger, trace graph and output directory | Durable delivery outbox, queryable stores, periodic collection service, runtime manifests, storage headroom/retention and operational metrics |

The primary implementation tracker remains [C0](C0.md). Several historical
documents describe the state at their own candidate revision; they are not a
substitute for reading current source.

## Are relevant results vectorized?

**They are embedded for scoring, not yet persisted as a searchable corpus.**
`EmbeddingScorer.rank` encodes selected native-text windows and observed
URL/anchor text. Intent mode encodes the original question once; pinned mode
uses the caller's model-compatible reference bundle. The ledger retains
similarities, window offsets/hashes, coverage omissions and encoding call
provenance. Intent-reference vectors survive replay. Source-window and link
vectors are local intermediates, not a retained vector index.

The current implementation does not index every relevant result's complete
native text or provide a cross-run nearest-neighbor query service. It also uses
bounded keyword/native-span context selection rather than an implemented semantic
context reranker. Neither a score nor the saved graph is a vector database.

Add a neutral `EmbeddingSink` and `EvidenceRetriever` boundary, not a second
encoder in the crawler. Preserve source-window vectors when compatible with
the indexing recipe; embed additional accepted-document chunks asynchronously
only when required by explicit coverage policy. Chunk identity must bind source
and native-text hashes, offsets, chunker revision, model/revision, dimensions
and input recipe. A sink acknowledges durable batches before they count indexed.
Changed model/chunker recipes create new index generations, not mixed vectors.
Retain originals even when the index is absent or rebuilding.

Combine lexical and dense retrieval, then optional reranking over native evidence
to assemble question-specific context. Multilingual performance is an admitted
model/evaluation property, not guaranteed by the encoder interface. Query
translation/expansion should preserve the original question and be configured;
do not replace native text or pretend English-only acceptance proves other
languages. A downstream platform may own its production indices without making
the standalone library depend on that platform.

## What will actually stop an investigation?

1. **No adequate discovery source.** An adapter without a populated, maintained
   index returns no useful leads. Ahmia does not supply exhaustive onion coverage.
   Dead services, stale observations and login-only forums remain explicit gaps.
   Broad search snippets are not retained source evidence.
2. **Access or browser mismatch.** Expired entitlement, session discontinuity,
   a JavaScript POST/pagination workflow, unresolved challenges, a download in
   the browser, or an unavailable selected Tor route can block acquisition.
   Fix required workflows or request human assistance; do not silently switch
   identities, weaken source scope or leak Tor traffic onto the direct route.
3. **Model or context failure.** No compatible served model, insufficient
   input/output allowance, malformed replies, unsupported review findings or
   inadequate source coverage prevent a supported result. Current real Chinese
   organizational reviews have failed despite valid JSON; this is a quality
   problem, not solved by another parser or a wider confidence threshold.
4. **Wrong text selection or document interpretation.** An English question
   can miss the relevant Chinese spans; OCR may recover words while missing
   chart arrows, tables or multi-column order. A native quotation match proves
   the quote exists, not that the inferred reporting relationship is correct.
5. **No resolved identity.** Source-local mentions are not canonical people or
   organizations. Similar names, aliases, offices/officeholders and dated
   reorganizations must remain unresolved or disputed until evidence resolves
   them; otherwise graph expansion searches the wrong entity.
6. **Operational interruption.** The single-document loop waits on a slow stage;
   a crash after a checkpoint may leave uncertain calls that refuse replay;
   disk exhaustion or downstream outage may strand results. Saved JSON alone
   is not an always-on, recoverable collection service.
7. **Provider contracts.** A discovery backend must permit automated frontier
   use and retention. Google's Gemini Search grounding terms effective
   23 March 2026 prohibit using Grounded Result links as crawl destinations;
   wrapping those results in MCP does not alter that contract. Use a compatible
   search/index provider. See the [official terms](https://ai.google.dev/gemini-api/terms#grounding-with-google-search),
   inspected again on 7 October 2026, and [grounded discovery](GROUNDED_DISCOVERY.md).

## Bounded close plan

| Priority | Deliverable | Live exit condition |
|---|---|---|
| P0 | Real end-to-end reference investigations and reliable semantic extraction/context selection | An English intent, native Chinese organization PDF plus follow-up sources, and an onion investigation finish with inspected original documents, supported claims and explicit gaps; no fixture/model-memory evidence substitution |
| P0 | Concurrent stage controller and durable operation frontier | A deliberately slow page/reviewer does not stall unrelated eligible collection; cancellation/restart reconciles actual spend, exclusive ownership and committed evidence |
| P0 | Durable accepted-document storage and vector/lexical retrieval | A fresh process retrieves an accepted native passage with exact source identity; mixed model generations refuse; an index outage never loses source originals |
| P0 | Outbox and storage lifecycle | Downstream unavailable/retry/duplicate submission all preserve one acknowledged output identity; originals are removed locally only after confirmed durability and configured retention |
| P1 | Browser-native downloads, explicit interaction plans and Tor browser runtime | Actual PDF download, pagination, authorized human-session continuation and complete onion/clearnet-through-Tor workflows preserve route/source/capture evidence |
| P1 | Entity resolution and temporal graph persistence | Same entity across sources/languages can be resolved with evidence, while a homonym or disputed reorganization stays separate and merge/split is reversible |
| P1 | Maintainable deployment and operator interface | Pinned service/runtime recipe, configuration validation, health checks, run/status/pause/resume/cancel, structured metrics and actionable refusal reasons work after process restart |
| P2 | Additional source connectors and refreshed corpus | Sitemap/RSS/site APIs, archive/citation sources, scheduled recrawl and dedup across runs added only where they improve a measured investigation |

Independent ports should reuse the existing `FetchLadder`, `RunBudget`,
`GroundedSearch`, source/citation readers and graph owner. Proposed new owners
are `OperationStore`/`FrontierStore`, stage queues, `EmbeddingSink`,
`EvidenceRetriever` and `DeliveryOutbox`. Domain/publisher workflows and backend
adapters implement narrow interfaces; a new framework must not duplicate their
policy or state. Configuration owns deployment choices, worker concurrency,
endpoints, storage, model/index recipes, retry and retention policy.

The external Ahmia MCP service owns discovery. Ghimera on approved egress hosts
owns collection and evidence. Downstream ingestion owns platform permissions,
ontology mapping, publication and production retrieval. That future integration
remains design-only: see [Ahmia architecture](AHMIA_INTEGRATION.md).

## Evidence and confidence limits

The [English full-Collector diagnostic](C3_LIVE_RESEARCH.md) succeeded, but used
a secondary document and same-model review. The [Chinese organization trials](ORGANIZATION_GROUNDED_EVIDENCE.md)
include complete refusals even after structurally valid review responses; they
do not establish accepted organizational extraction. The
[public-browser evidence](C1_HUMAN_PUBLIC_EVIDENCE.md) proves capture/extraction
on its selected pages, not arbitrary publisher, login, Tor or research quality.

These are engineering records, not governed catalogue reports. No material was
masked by a platform projection in this review, and no platform catalogue was
queried. Unresolved capabilities and unaccepted deployment/quality cases are
explicitly listed above. Green source tests must not erase them.
