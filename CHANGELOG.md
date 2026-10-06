# Changelog

## Unreleased

- Opt-in local FlareSolverr challenge recovery alongside the isolated Patchright
  renderer. Versioned configuration declares exact source origins, provider
  revision, clearance-cookie names, request/response limits, retry budget and
  private cache lifetime. Recovery acquires only clearance metadata/cookies,
  then verifies content through the ordinary guarded source fetch.
- Private origin-scoped clearance and matching User-Agent reuse, with expiry,
  cross-origin isolation and non-secret harvest/journal provenance. Challenge
  work consumes the shared request, byte, deadline and politeness budget.
- Explicit direct-route, public-source gateway boundary; account credentials
  are never forwarded or merged. Unresolved challenges, robots denial and
  login/paywall walls remain refusals. Real gateway/site acceptance and
  route-preserving Tor recovery remain open; this is not universal CAPTCHA
  solving and is not included in the immutable 0.3.0 artifacts.

## 0.3.0 — 2026-10-06

- Consolidated project naming as `ghimera`: distribution, Python package,
  command and GitHub repository. The primary configuration class is
  `GhimeraConfig`. A narrow legacy `chimera` root facade and module command
  reuse the same implementation; nested imports migrate to `ghimera.*`.
  Existing `chimera.*` data schemas, prompt revisions and saved-result
  identities are unchanged. The old `go-spider` releases remain unchanged.

- Concrete live intent research acceptance joined real discovery, laptop
  HTTP/HTML extraction, a pinned semantic encoder, self-hosted model judgment,
  answer/review, incremental graph and sealed archive readback. This English,
  same-model-review diagnostic does not establish representative accuracy or
  close the remaining browser/publisher/document/runtime acceptance gates.

- Explicit SearXNG ordinary-HTML search beside unchanged JSON mode. Version-2
  search configuration selects the dialect; the Collector assembles the owning
  adapter. The HTML adapter parses observed simple-theme results with pinned
  Scrapling in the existing bounded passive worker, shares direct/Tor accounting,
  and refuses unknown layouts or access barriers without format fallback.

- Completed intent results now retain successful search responses, exact queries,
  parsed hits and transport in `chimera.research-result/2`. Readback binds each
  response to its accounted fetch and refuses missing, substituted or duplicate
  discovery evidence. Citing-source candidates must be actual observed hits.
  Legacy `/1` results remain readable without claiming raw-search retention;
  snippets still cannot serve as answer citations.

- Collection grading now explicitly evaluates retained evidence sufficiency,
  not the presence of an answer draft, with a distinct recorded prompt revision.
  A real self-hosted-model follow-up accepted sufficient native documentation
  and rejected irrelevant, absent, and model-memory-only evidence. These bounded
  controls do not establish representative accuracy or independent calibration.

- Explicit `citation_format = "template_ids"` for assessment and drafting:
  the model selects supplied context IDs and the client restores exact native
  quotations, offsets and hashes. Unknown/out-of-context IDs refuse without
  similarity repair; full-citation mode remains the compatibility default.
  Document judges' second looks now expand native context to the configured
  character ceiling while retaining the first span. A real self-hosted-model
  trial confirms these paths; its original grader failure, subsequent repair,
  and independent-evaluation gaps remain recorded rather than being described
  as complete acceptance.

- An explicit configuration-driven intent command (`python -m chimera`) with
  bounded input reads, separately named environment credential bindings and
  private no-overwrite complete-result archives. Originals, citations, graph,
  ledger and model/extraction provenance survive revalidated checksum readback.
  Partial outcomes and interrupted/unsealed archives never become answered runs.

- A configuration-driven `Collector` facade assembles actual search, HTTP,
  HTML/document, optional browser, embedding and completion adapters. It supports
  intent research and seeded collection with fresh per-run state, retains the
  effective search recipe, and offers an explicitly bounded TOML read. Graph,
  journal and source-session settings use their existing owning contracts.
  Controlled-server composition acceptance is not real-model accuracy.

- Explicit intent-reference semantic scoring alongside unchanged pinned-shelf
  scoring. The original intent's embedding spends the shared run budget once;
  prepared vectors, call linkage, failure/cancellation and concurrent run
  isolation are auditable through harvest and journal readers.

- Configured owner-private per-run JSONL observations, fsync-before-ack storage,
  hash-chain replay checks, completion summaries and read-only run inspection.
  Interrupted runs remain explicitly unsealed; no automatic refetch or resume.

- Configurable single generic HTML reparse over retained source bytes, without
  a second fetch or renewed deadline. Success, refusal and cancellation attempts
  retain typed source/configuration-bound provenance and round-trip ledger checks.

- Persistent exact-publisher/profile locator health with a configured miss bar,
  generic extraction recovery, restart-safe/concurrent state, read-only doctor
  output and source/config-bound harvest ledger findings. No publisher-redesign
  accuracy claim follows from the regression fixtures.

- Explicit offline PDF layout/table/OCR recipe, pinned worker dependencies and
  artifacts, configurable column-aware reading order and reproducible local PDF
  acceptance with source-bound receipts. Representative-corpus and Marker
  acceptance remain open.

- Authorized source sessions with caller-supplied cookies/headers, exact origin
  and path scope, protected browser resource support, non-secret audit metadata,
  and separation from discovery/model-control credentials.
- Configurable document-reference depth and citing-source discovery through the
  injected search provider, using native parent context and the existing scorer.
- Source-bound reference decisions and native Docling URL/hyperlink locators;
  saved-harvest validation of source hashes, query/provider binding and budgets.
- Shared reference/discovery host, parent, per-parent candidate and query limits
  across follow-up rounds, with versioned configuration and an example.

## 0.2.0 — 2026-10-06

Rebuild of go-spider as a typed, configured standalone library. Distribution
identity stays `go-spider`; the implementation package is `chimera`.

### Added

- Goal collection and intent-driven planning/discovery/coverage/answer-review
  orchestration with exact native-text citations and explicit partial results.
- Direct/Tor HTTP, native v3 onion support, bounded robots/redirect/retry/cache
  policies and source-network separation from private model-control traffic.
- Isolated Patchright rendering with parent-owned HTTP resource/redirect handling.
- Native HTML fit-Markdown/adaptive locator extraction and language detection;
  offline DOCX tables and native PDF text extraction with retained layout.
- Self-hosted completion and embedding clients with declared model binding,
  bounded native evidence, token/encoding observations and recorded failure.
- Reference-vector relevance scoring, semantic/keyword link ranking, canonical
  and near-duplicate grouping with independently retained source occurrences.
- Configurable research graph, immutable typed harvest/ledger/receipt contracts,
  conformance tests, deliberate mutation witnesses and full package gate.
- Public README, package metadata, MIT licence text and migration guidance.

### Breaking changes

- Requires Python 3.11 or newer; v0.1.0 required 3.10.
- `chimera` replaces the prototype `spider_core` imports. No compatibility shim
  or legacy `spider` command is included.
- No cloud LLM client, automatic model-server launch, VPN manager or silent
  external-model/direct-network fallback.
- Operational policy is explicit validated configuration and injected ports.

### Not claimed complete

Public publisher/locator and real-model quality acceptance; full PDF/OCR/Marker;
Camoufox/nodriver; calibrated judge policy; one-hop references/cited-by expansion;
production runtime/egress acceptance. Publication
is not deployment or proof of completion of those planned features.

## 0.1.0

Original prototype release with `spider_core` and the `spider` CLI. Existing
users may pin that version while migrating to the explicit v0.2.0 library API.
