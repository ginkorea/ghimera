# Changelog

## Unreleased

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
