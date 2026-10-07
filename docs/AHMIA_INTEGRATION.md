# Ahmia-backed onion discovery

Status: selected by the owner; implementation and deployment pending. This is
an addition to the complete collector scope, not a substitute for its remaining
browser, document, research-quality or runtime acceptance.

## What is free and what is not supplied

Ahmia publishes its [crawler](https://github.com/ahmia/ahmia-crawler),
[index](https://github.com/ahmia/ahmia-index) and
[search site](https://github.com/ahmia/ahmia-site) under BSD-3-Clause. Software
licensing is distinct from operating costs, dependencies and rights to a corpus.
Installing those repositories does not supply the hosted service's collected
index. An operator-owned deployment needs its own permitted seed sources, Tor
connectivity, storage and index maintenance.

The public service's [terms, updated 25 September 2026](https://ahmia.fi/terms/),
require permission for scraping. This integration targets an operator-owned
Ahmia index, not an automated scraper of the hosted website. Other software's
licenses and source access/retention permissions still need their own review.

## Native composition

The first implementation is a read-only lead provider over an explicitly bound
private Ahmia Elasticsearch index. It uses the existing `GroundedSearch` final
accounting template and `DiscoveryProviders` routing, not another crawler,
frontier, research loop or model registry. A separate application tool can expose
the same lead service as `onion_search`, returning the existing MCP `data.leads`
envelope. MCP sessions and authentication remain application-owned.

The upstream [mapping](https://github.com/ahmia/ahmia-index/blob/master/mappings_tor.json)
defines `url`, `title`, `h1`, `meta`, `content`, `domain`, `content_type`,
`updated_on` and `is_banned`. Query original fields with bounded JSON queries;
do not submit model-generated Elasticsearch query syntax. Treat ranking scores
as retrieval scores, not calibrated probabilities. Do not assume the mapping's
English analyzers establish multilingual retrieval quality.

Only validated observed v3 onion HTTP(S) URLs enter this provider's frontier.
Record index identity/revision, native response bytes and query provenance.
Retain source observation age, exclude banned entries, and distinguish partial
or timed-out index searches from legitimate empty results. Index excerpts are
discovery context, not collected source documents. The collector separately
fetches source bytes through its existing verified Tor, scope and entitlement
boundaries before claims can cite them.

## Configuration and ownership

A typed, versioned Ahmia binding must declare the exact index/search endpoint,
approved destinations, authorization mode, index identity/revision, query fields,
age policy, response/request limits and timeout. No endpoint, credential, index,
hosted onion address or operator threshold belongs in Python constants.
Credentials are explicitly injected, omitted from recipes and logs, and never
borrowed from source sessions. TLS verification remains on; redirects and
ambient proxies must not forward private index credentials elsewhere.

Keep index control traffic separate from source Tor traffic. Endpoint and DNS
validation happen before contact. Missing configuration or client credentials
refuse before research starts. Effective non-secret recipes bind the receipt and
continuation identity, while quotas belong to the run. An index failure must not
become a false empty search or a claim of complete dark-web coverage.

## Bounded implementation tracker

- [ ] Inspect and pin the upstream mapping/search dialect used by the adapter.
- [ ] Add the typed Ahmia binding and concrete bounded private index client.
- [ ] Compose it through Collector, native research, retained discovery and
      archive/continuation readers; reuse per-provider and global accounting.
- [ ] Provide an application-callable lead envelope compatible with
      `onion_search`, without implicitly deploying an MCP server.
- [ ] Exercise schema/URL validation, partial search failures, byte/time limits,
      cancellation, credential isolation and restart/resume quota preservation.
- [ ] Run the corrected complete package gate before calling the candidate green.
- [ ] Provision a pinned operator-owned index/crawler with explicit storage,
      permitted seeds and maintenance configuration; do not modify a shared
      deployment or download a corpus implicitly.
- [ ] Validate a real cold start and a stalled-branch recovery, retaining native
      documents, citations and graph output. Report index coverage and observation
      freshness, not exhaustive dark-web coverage.

Initial wire fixtures can prove bounded transport and composition, but not that
an index exists, contains relevant documents, or provides adequate language and
topic coverage. Publication and platform activation require separate evidence.
