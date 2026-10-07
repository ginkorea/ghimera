# Same-session browser document downloads

Status: published in 0.4.2 with a combined gate, native controlled acceptance
and byte-equal public artifact readback; see [release acceptance](RELEASE_042.md).

The 0.4.4 release line adds explicit before-contact redirect
admission and final-event correlation. It is not in the immutable 0.4.3 package;
the complete gate passed; final artifact status is in RELEASE_044.md.
see [guarded collection](BROWSER_NAVIGATION_GUARD.md) for configuration and its
controlled acceptance, separate from representative publisher workflows.

The authorized-browser port can return either a DOM snapshot or a document
download. Both go through the existing FetchLadder, document extraction,
relevance, native evidence graph, journal and result archive. There is no second
crawler or ungoverned file importer. A file is not represented as PDF-viewer
HTML or as an invented HTTP response.

## Configuration

The optional `human_browser.downloads` fragment is a versioned policy. Omission
preserves the existing browser recipe's serialized bytes and digest. Each action
names an exact source URL, a navigation URL, an optional exact locator, and the
expected PDF or DOCX format. A null locator means direct attachment navigation;
a locator means navigate to the configured page and perform one download action.
URLs must fit the existing exact-origin/path policy and the run's source scope.
The fetch owner checks robots policy for both the file and the landing page.
Nothing discovers credentials, browser profiles or unrelated tabs.

For initially unknown attachment URLs, opt into `navigation_content_types` in
that same policy; see `examples/browser-navigation-downloads.toml`. Explicit
actions may be omitted. URLs come from the existing configured discovery and
scored native-link frontier, not a separate URL list. Ordinary HTML is captured
once through the same DOM path, without waiting for a nonexistent file event.
If navigation actually produces an exact-page/URL download event, the bounded
file stream is inspected against only the admitted formats. URL suffixes,
suggested filenames and a model's guess cannot admit file bytes. Empty/duplicate
format selections and a policy containing neither actions nor formats refuse
at configuration time. Omitted navigation formats preserve the explicit-action
recipe's serialized identity.

Downloads use `adapter = "patchright_page"`. The application supplies its
actual Patchright `Page` to `BoundPageHumanSession`, then injects that session
into `Collector(..., human_browser_session=session)`. It does not configure a
debugger endpoint in this mode. The adapter checks the actual target before
every capture. Missing injection refuses before work; the CLI cannot invent a
live application-owned page.

The application supplies the dedicated caller-managed browser and target.
It must enable downloads and own the browser's egress controls and download
storage quota. Ghimera does not change global browser download behavior, import
cookies into another HTTP client, or close the context. These controls are not
an excuse to label browser-network bytes as zero: downloaded browser bytes and
subresource counts remain explicitly unknown in the evidence.

Why a separate page binding: native acceptance showed that the existing
`connect_over_cdp(no_defaults=True)` attachment did not receive download events.
Enabling downloads through that attachment would mutate caller-managed global
settings. CDP mode remains the unchanged DOM adapter and rejects download
configuration; it is not advertised as a working download path. The bound
page uses the caller's original driver/download state and works in the same
session in which a human interacts with the page.

## Transfer and admission

The public Python browser API's `save_as` writes the complete file. To avoid
an unbounded collector copy, the isolated pinned Patchright 1.63.0 adapter uses
that version's `saveAsStream` protocol and validates every reply before reading
the next bounded chunk. This private vendor boundary is intentional, confined
to one module, and needs native acceptance before a driver-version upgrade.
It is not a generic promise about other Playwright versions.

The collector reserves one byte to detect oversized input, charges it on
refusal, and refuses rather than truncating and parsing a partial document.
Retained memory is bounded by the file policy and current run allowance. No
collector staging path is derived from the browser's suggested filename.
The browser's separately downloaded temporary artifact is operator-owned;
this mechanism does not claim it is bounded by the collector's stream limit.
On success, refusal or cancellation, cleanup targets only the artifact from
this action. The bound-page adapter detaches its own metadata session, not the
caller's driver connection, browser, context or tab.

PDF header and DOCX archive-member observations are format admission, not proof
of a valid document. The existing pinned document worker still checks parsing,
archive expansion, pages and resource bounds. Original bytes and native text
remain unchanged. OCR/language quality is independent of download transport.

## Provenance and remaining acceptance

`ghimera.browser-download-evidence/1` records the requested/download URL,
observed landing page (when present), selected session/target, effective policy,
browser/driver revisions, actual retained file hash/size, consumed DOM/file
bytes and any resumed human assistance. HTTP status, response headers and
verified Tor routing are not fabricated. The same evidence travels with
source documents, graph nodes and ledger records; paired archive/journal
readers reject content mismatch and provenance stripping.

The initial action mapping is exact: a download event from another URL cannot
be mistaken for the requested file. Blob downloads and redirect/locator
workflows needing a different observed file URL require a separately explicit
binding; they are not silently accepted. Pagination, verified Tor-browser
egress, arbitrary publisher interactions, operation-level crash recovery and
representative entitled-source quality remain open in the infrastructure PRD.

The controlled native evidence below establishes download/parse/readback
behavior. The combined gate is recorded below. Controlled files do not
establish Pacific OCR accuracy or real publisher compatibility.

## Next workflow boundary

An actual browser download event is required. Direct navigation to an inline
PDF may open Chromium's PDF viewer instead of emitting such an event; this
candidate does not substitute the viewer's DOM for original PDF bytes. A
configured download button may work when it produces the exact admitted file
event, but unknown viewer controls are not clicked speculatively.

Scored native HTML links now feed initially unknown navigation downloads.
The retained parent extraction includes the link, and discovery/retrieval graph
edges bind the parent document hash to the child's source and captured file.
Operational policy supplies allowed formats, origins, path limits and budgets;
the source-derived action is run state, not a mutation of that policy. A model
may prioritize an observed attachment but may not invent a source URL or locator.
Autonomous discovery of publisher-specific download controls, inline PDFs,
redirected file URLs and pagination remains separate work.

Its acceptance must include an initially unknown PDF link, an inline PDF, a
publisher link that redirects to a file, a same-session entitled attachment,
pagination with a repeated next link, an irrelevant attachment, and cancellation
after an action has started. Each case must either preserve original bytes and
replayable evidence or return a specific actionable refusal/partial result.
Fixtures establish lifecycle and identity contracts; independently inspected
publisher sources establish compatibility. Human intervention must resume the
same task/session rather than create a second crawler or reset spent budgets.

The inline-document source candidate now has a separate bounded same-session
response path; see [inline documents](BROWSER_INLINE_DOCUMENTS.md). It records
an actual second browser response, not a native download event or PDF-viewer
HTML. This addition is not retroactively part of the published 0.4.2 artifacts.

## Controlled native evidence — 7 October 2026

Download and existing human-browser/command regressions: 46 passed in 94.60
seconds, no failures or skips. Interpreter:
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. Installed Chromium and
the real Docling native-document worker exercised PDF attachment navigation,
DOCX link activation, cited answers, graph/journal/archive round trips,
oversized/invalid formats, hostile suggested filenames, landing-page robots,
human assistance and cancellation while awaiting assistance. The public
Collector also used the injected page with actual credential-free local
search/embedding/chat protocol fixtures. Those replies are fixtures, not LLM
quality evidence.

The first native run failed because the fixture browser did not explicitly
enable downloads. The repeated CDP attachment still failed after downloads
were enabled by its owner; it did not observe file events. The bound-page
adapter passed without changing the browser's global settings. Failed evidence
was retained rather than converting refused transfers into successful results.

The final frozen source/tests/examples passed the complete `scripts/gate.sh`:
**938 passed in 854.83 seconds**, no failures or skips. Interpreter:
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. Ruff check and formatting
passed; strict mypy passed across 145 source files. The locked offline
dependency check passed. Gate working directory:
`/home/gompert/data/workspace/TAIPAN/.codex-tmp/ghimera-browser-downloads-gate1`.
Documentation clarifications did not modify source/tests/examples during this
run. This includes actual in-flight download cancellation (not just cancellation
while awaiting human assistance), preserved caller tabs, the shared browser
adapter ordering contract, and the final config example. It does not close
the independent publisher, autonomous discovery, inline-document, Tor-browser
or multilingual-quality acceptance requirements above. No publication follows
from this gate alone.

The subsequent navigation-format extension passed **53 browser regressions in
116.73 seconds**, no failures or skips, on the same Python 3.11.16 interpreter
and owned checkout above. It includes actual initially unknown PDF/DOCX downloads
and a native HTML parent flowing through the scored frontier into downloaded
PDF parsing and graph/harvest readback. Strict mypy passed all 145 source files.
This is not a new full-package gate. The final example and stronger graph-edge
assertions passed the bounded native module recheck: **5 passed in 17.74 seconds**,
no failures or skips, on the same interpreter/checkout. The graph assertions
prove the discovery edge reaches the retained parent version and the retrieval
edge binds the child's source to its exact browser capture. Ruff and formatting
passed. No merge or publication is claimed.
The initial extension check exposed a missing run ID in its graph-enabled test
harness; that check did not reach collection. The corrected passing run supplied
the required identity rather than weakening graph behavior.

The combined versioned candidate `9f0c174` subsequently passed the full
`scripts/gate.sh`: **943 passed in 845.28 seconds**, zero failures/skips, under
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. Source/tests/examples were
frozen throughout; Ruff/format and strict mypy passed. This supersedes the
extension's missing combined gate, not its still-open publisher/quality rows.
