# Native HTML extraction

Status: standalone candidate, not deployed. This implements the HTML portion
of C2; it does not close PDF/DOCX conversion or the full spider acceptance.

## Configuration and ownership

Install the pinned `html` extra. It contains Scrapling **0.4.2**, Crawl4AI
**0.9.4** and Lingua **2.1.1**, with transitives in the lockfile. Crawl4AI 0.9.0
requires lxml 5.3 while Scrapling 0.4.2 requires lxml >=6.0.2; 0.9.4's declared
range permits the compatible locked lxml. Neither library is vendored or
replaced with a name-only adapter.

`chimera.extraction/1` is parsed at the main configuration's `extraction` field.
`examples/extraction.toml` supplies an explicit, non-active policy. Worker and
locator directories, exact-host CSS profiles, language candidates, pruning,
concurrency and byte/character/time bounds are operator configuration.
`HtmlExtractor.validate_config` refuses a binding different from the run's
effective policy before collection starts. No library uses its default home
directory or personal browser profile.

The parent owns a bounded semaphore and child lifecycle. Parsing receives
already-fetched `Page` bytes; it cannot bypass `FetchLadder`, robots, source
scope or Tor policy by performing a second crawl. It never constructs
`AsyncWebCrawler`, a browser, an LLM filter or a model service. Source raw bytes
remain unchanged beside the derived text and metadata.

## Vendor boundary

Crawl4AI imports its broader optional API stack eagerly. Those imports occur
only in a child process, never the research/controller interpreter. The child
gets an explicit credential-free environment, owned cache directory and an
audit guard refusing network initiation and subprocess creation. This guard is
defense in depth, not an OS sandbox or anonymity guarantee. C5 still owns the
runtime's OS-level egress controls.

The adapter uses actual Scrapling selectors and persistent adaptive SQLite
profiles, scoped to an exact host and the full profile digest. Direct matches
are saved; missing matches may relocate above the configured score floor.
Each direct/relocated/missing field is recorded. A missing profile body uses
the configured generic article/main/body selectors and records the fallback.
It does not silently represent a relocated field as an exact CSS match.

### Persistent locator drift

The example config includes `locator_drift` (`chimera.locator-drift-policy/1`):
`consecutive_miss_limit=3` and an explicit SQLite contention timeout. A completed
publisher-profile attempt with any missing configured field counts once; a
successful direct/relocated attempt resets the streak. Errors without observed
selector misses do not count as CSS drift. Failed extractions that did observe
misses still update health. In-flight completions are serialized by short SQLite
transactions, not by holding a lock over fetching or parsing.

At the bar, the exact host/profile/policy switches to generic extraction on
subsequent attempts. It never stops collecting solely because of CSS drift.
The latch survives controller restarts and stays visible until that exact
profile is changed or explicitly reset with `LocatorHealthStore.reset(profile)`.
An already admitted attempt may finish; it cannot silently clear the latch.
No state crosses hosts or profiles. The owner-only state database is separate
from Scrapling's adaptive storage and refuses foreign, shared or corrupt files.

`locator_health` is retained in extraction evidence and validated against the
harvest's effective policy. A drifted extraction adds a `policy` ledger row
with reason `locator_drift`. Inspect every configured profile without changing
its state using:

```bash
python -m chimera.locator_health --config /path/to/extraction.toml
```

The doctor emits JSON and exits 1 for a drifted profile, 0 for healthy profiles.
It does not expose source bodies or cookies. Missing optional drift policy in
an existing configuration preserves its original serialization and digest;
enabling/changing policy is explicit configuration, not a release-time literal.

Configured boilerplate tags are removed without running scripts. The actual
`DefaultMarkdownGenerator` and `PruningContentFilterLXML` produce fit Markdown
from that retained fragment; tables and native prose are exercised. Vendor
"Error ..." outputs are refused, not treated as evidence. Markdown generation
is not a translation or an abstractive summary.

The untyped Crawl4AI calls have minimal reviewed local type declarations under
`typing/crawl4ai`, for exactly these pinned parser interfaces. All serialized
worker results undergo local Pydantic validation and source/text/config binding.
Scrapling and Lingua use their shipped typed interfaces; Lingua's Rust enum is
resolved through checked attributes, not Python Enum subscription.

Primary interfaces:
[Scrapling selectors](https://scrapling.readthedocs.io/en/latest/api-reference/selector.html),
[adaptive storage/relocation](https://scrapling.readthedocs.io/en/v0.4/parsing/adaptive.html),
[Crawl4AI Markdown and pruning](https://docs.crawl4ai.com/core/markdown-generation/).

## Native text and provenance

Title, author/byline, date and canonical-URL declarations are extracted as
source metadata, not verified publisher claims. Relative document links are
resolved against the fetched final URL; non-HTTP schemes, URL credentials and
control characters are refused. These links are candidates, not permission to
fetch outside the run's scope. Canonical declarations do not yet authorize
cross-host identity merges.

Lingua operates offline on a bounded native-text sample among the configured
languages. The HTML language hint is recorded separately and cannot override
the detector. Insufficient sample/confidence/margin yields `und`. Its score is
not presented as a calibrated probability or proof that other languages were
excluded. Native text is never translated to obtain a language label.

`chimera.extraction-evidence/1` records source URL/bytes hash, native-text hash,
effective-policy digest, parser revisions, encoding/replacement count,
selection/profile/locator outcomes, declared/detected-language disagreement,
sample and confidence shape, raw-Markdown hash and omitted link count.
The harvest preserves this record and an extraction ledger event. Deserialized
documents and harvests revalidate native text, raw bytes and configuration.

Input/output/diagnostic pipes and text/links are bounded; excessive or empty
output refuses instead of silently truncating evidence. Timeouts include
waiting for a worker slot. Cancellation/timeout kills and reaps the exact owned
child and releases its slot; no broad process-name kill is used.

## Remaining C2 and full-goal work

- Docling native/model PDF and DOCX, explicit offline model-artifact admission,
  canonical/near-duplicate policy and retained occurrences now have dedicated
  implementations/evidence; see C2_DOCUMENTS.md and C2_DEDUP.md. Marker math
  fallback and representative document quality remain required.
- Persistent locator drift and its doctor are implemented. The PRD's archived
  real-publisher-pair acceptance remains required; fixture redesigns and streak
  tests do not establish its 90% relocation bar.
- Browser/Tor full-route acceptance, real encoder/scoring, TAIPAN integration and
  real served-model intent research still require their own evidence.
