# Sources-of-sources

Status: standalone source candidate. Controlled collaborators exercise frontier
and budget behavior; real Docling exercises native document links. This is not
yet representative publisher or served-model quality acceptance.

## Configuration and ownership

`examples/intent-research.toml` contains `chimera.references/1`. Omit the section
to disable special reference expansion. `follow_document_references` and
`discover_cited_by` independently control the two paths. `max_hops` is operator
configuration, not a fixed one-hop implementation ceiling. Ordinary navigation
still has its independent `Scope.max_depth`.

One `CollectionSession` owns a `ReferenceBook` across all research rounds.
Parent, queued-candidate, extra-host and cited-by query budgets never reset on a
follow-up round. Document links and citing-source candidates share queued and
per-parent caps. Search, fetching, scoring and model calls spend the same run
budgets as ordinary collection. Additional hosts count against the research
discovery host ceiling on subsequent rounds as well.

`outside_scope = "refuse"` keeps references inside the current exact-host scope.
`observed_public` can add only an observed candidate host, within configured
limits. Reference and research deny lists, configured-only research scope,
ports, MIME types, normal network address checks, robots and transport policy
remain in force. Reference expansion grants no new credential or session.

## Native document references

The Docling worker retains URL text spans and actual Docling hyperlink layout
indices, bound to native text/layout. Normalized links retain the original
observation and base URL. Only accepted, retained source evidence may seed
reference expansion; rejected documents cannot do so. The configured scorer
ranks actual observed links before they enter the shared priority frontier.
Special reference targets do not bypass disabled reference policy by falling
through to ordinary navigation.

## Citing-source discovery

After collection, the intent loop forms a configured query from the accepted
document's native title and source URL. The template permits only plain
`{title}` and `{url}` substitutions. Queries are reserved once per source
revision before provider I/O, and run at configured search concurrency.

The actual provider's hits—not model-proposed URLs—become candidates, scored
against the native parent context. New candidates can be collected in the
remaining round quantum. They undergo the ordinary extractor and document
judge. Analyst answers still require citations into retained native text.

A hit means **candidate citing source**, never a verified bibliographic link.
The ledger records provider/revision, exact query, response digest, candidate
title/snippet, parent raw/text hashes, decision score, depth and outcome. The
digest identifies the response; it does not embed its raw bytes or establish
that the target page really cites the parent. Bibliographic verification is a
separate content-evidence judgment.

## Saved-result checks and remaining acceptance

The harvest reader validates native reference locators against retained source
text/layout. Search decisions bind their prior source-derived query and
successful provider fetch digest. It rechecks configured depth, thresholds,
deny lists and shared queue/parent/query caps. Decisions explain disabled,
out-of-scope, below-score, already-seen, depth-limit and budget-limit outcomes.

Representative real references, redirects/navigation ancestry and calibrated
model judgments still need acceptance. No controlled-fixture result establishes
publisher coverage, scholarly citation recall or research-answer accuracy.
