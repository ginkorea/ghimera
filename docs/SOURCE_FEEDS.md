# Native source-feed collection

Development candidate. RSS 2.0, Atom, Sitemap 0.9 URL sets/indexes and JSON
Feed 1/1.1 enter through the ordinary collector's scoped fetch and extraction
path. There is no second HTTP client, scheduler, implicit hostname allowance,
authentication bypass, feed poller or private service. The existing fetch
ladder owns robots, per-origin spacing, Direct/Tor routing, redirects, response
limits and conditional revalidation. Its conditional cache is run-local,
not cross-process incremental refresh.

Merge `examples/source-feeds.toml` under `source_feeds` in your normal recipe;
replace worker/storage paths with owned locations. Add the matching feed MIME
types to the request scope (and `research.content_types` for research runs),
and supply feed URLs as ordinary seeds. Every discovered article, child sitemap
or admitted attachment must still pass the normal scope, depth, frontier-score,
network and document-verdict boundaries. One feed declaration cannot grant
permission to crawl its outgoing hosts. Sitemap indexes discover child manifests
incrementally through the same frontier, not a synchronous recursive tree walk.

The pinned `ghimera.source-feeds/1` recipe sets admitted formats and MIME types,
optional attachment discovery, worker concurrency/time/output limits, XML node
and depth limits, entry/field/text limits and maximum distinct outgoing links.
Too-large or malformed manifests explicitly refuse; no partial parser result
is represented as a complete feed. Entries beyond the outgoing-link allowance
remain in the source reading and the exact omitted-link count is observable.

The owned passive worker parses UTF-8 source bytes only and does not execute
HTML, load external XML entities, contact URLs, or read models. DTD/entity
declarations are refused before XML parsing. RSS `guid` is a URL only when it
declares/defaults to a permalink and no `link` is present. Atom alternate links
and inherited `xml:base` are preserved. Enclosures/JSON attachments require the
explicit flag. XML/JSON content types do not admit arbitrary site API payloads.

`ghimera.source-feed-evidence/1` retains original-source identity, effective
parser policy, native format, title/language declarations, item ids/dates,
observed URLs and exact extracted entry text. Decode replays against the original
bytes; invented article URLs, dates, text or a changed policy cannot pass as
the same source. Publisher dates are not acquisition time and item ids are not
globally resolved entities. Mixed-language feed readings use `und`; declared
language is preserved separately, not passed off as measured language ID.
Collected linked documents use their ordinary native extraction/language stage.

An accepted feed is evidence of what the publisher's manifest declared, not
proof that a linked article was visited or that its synopsis is true. Statements
about linked documents require their own retained originals and citations.
Graph, journal, source corpus and delivery reuse the existing native document
boundary. Fresh feed parsing has its own exact extraction ledger observation;
historical corpus reuse does not pretend to parse/refetch the old manifest.

Required acceptance: native RSS/Atom/sitemap/JSON fixtures through the public
collector, scope/robots refusal before linked contact, child-index traversal,
conditional 304 readback, exact source/text/link/policy mutation rejection and
worker cancellation/limits. Representative publisher and onion feed workflows,
pagination/site-specific APIs, persistent conditional state, cross-run refresh
and unattended connector scheduling remain I13 work; this initial parser does
not close those requirements or claim real-model answer quality.
