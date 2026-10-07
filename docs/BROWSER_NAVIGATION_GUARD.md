# Caller-session redirects (integrated development candidate)

The existing native download observer compares the download URL with the
frontier request. Chromium reports a redirected attachment's final URL instead,
so that observer misses the file. Inline PDFs also navigate to their final URL,
which the current exact-original inline binding rejects. Loosening that binding
after navigation would contact an unadmitted redirect before detecting it.

The full `Collector` now consumes an explicit `human_browser.navigation` policy
through its existing FetchLadder and caller-bound Page session. Redirected HTML,
PDF/DOCX attachments, inline originals and mapped download landing pages retain
their native before-contact chain through extraction, citations, graph, journal
and archive readback. This is an integrated development candidate, not published
support. Representative publisher, Tor-browser and model-quality acceptance remain
separate requirements.

## Ownership and configuration

The application explicitly delegates exclusive main-frame navigation during
one operation. It supplies its actual caller-owned Patchright Page, the existing
HumanBrowserConfig, exact requested URL, run scope and a typed
`BrowserNavigationAdmission` collaborator. Its `admit(url)` checks the run's
robots/cadence/budget policy before any continuation. There is no independent
default permission, credential discovery, profile import or new HTTP client.

`BrowserNavigationConfig` (`ghimera.browser-navigation/1`) owns maximum redirects,
finite per-hop admission timeout, cleanup timeout and the explicit interception
ownership declaration. Merge `examples/browser-navigation-guard.toml` into a
complete `human_browser` recipe. It is an actual `[human_browser.navigation]`
table. Use `adapter = "patchright_page"` and inject the application-owned Page
through `BoundPageHumanSession` into Collector. CDP attachment mode rejects this
policy instead of ignoring it. Omission preserves the published recipe/evidence
serialized identities. Competing interception owners cannot be discovered
through CDP, so callers must actually delegate exclusive control; this is not a
claim that a declaration proves global browser ownership.

The guard validates the native target ID and main frame through its own CDP
session, then pauses Document requests at the request stage. The first main-frame
request must be the exact operation URL. Each redirect must continue the preceding
native request ID, frame and network chain. Loops, too many hops, changed scope,
unexpected competing main-frame navigation and malformed wire replies refuse.
Scope is checked both before and after the asynchronous admission call. A denied
or timed-out request is failed before contact. The browser's actual same-session
cookies remain inside the browser; the guard never reads/copies them.

Subframe/subresource traffic remains the caller's operator-managed browser
boundary. It is not counted as zero or claimed to be controlled by the main-frame
guard. Tor browser verification is still missing and this component refuses a
declared Tor policy rather than treating it as proof of routing. Challenge/login/
subscription interaction remains the existing explicit human-assistance port.

## Lifecycle and evidence

Enter the guard before goto/click and retain it through the actual final response
or download event. On a vendor navigation error, call `guard.check()` so the real
policy refusal is preserved. `guard.evidence(actual_final_url)` binds the observed
native chain to the two effective policy digests and exact target. Successful
normal context exit also checks failure, so catching a vendor error cannot turn
a denied navigation into successful admission.

Cancellation cancels owned admission tasks and fails paused requests before
detaching its own session. The borrowed browser, context, target and unrelated
tabs remain open. Callbacks have owned tasks, exceptions are consumed/sanitized,
and no provider exception string or request header is persisted. Cleanup is
bounded separately from admission; the guard is single-use.

`ghimera.browser-navigation-evidence/1` records only native URLs, request-chain
IDs, target, digests and before-contact admission. It does not invent HTTP
status/headers, retained file bytes, a completed download or robots results.
It is embedded into capture, document, graph, journal and archive evidence.
Replay rejects a
stripped/discontinuous chain, loops, changed final URL, changed effective policy
or browser scope, and a redirect count beyond the configured bound.

## Run-owned integration and human continuation

FetchLadder supplies ephemeral typed `BrowserOperation` ports, never callbacks or
credentials in serialized FetchRequest. Admission reuses the run's existing
robots, cadence, request and byte-budget owners. Release the previous host/global
slot before fetching fresh robots policy or acquiring the next slot. Reserve
output bytes only during actual bounded DOM/file reads, not while awaiting
redirect admission. This prevents reentrant one-slot/byte-budget deadlocks while
preserving concurrent byte safety and actual refusal/cancellation spend.

`ghimera.browser-source-action/1` records each known main-frame or explicit inline
GET allowance as a typed policy ledger row: an attempted action, not completion
or browser-wide traffic. A capture's aggregate fetch row records collector bytes,
not another request allowance. Harvest and journal replay share one counting
function. Replay requires prior known spend for every retained chain, including
failed assistance, and a separate inline reservation for every inline acquisition.

Native downloads correlate the same-Page event with the admitted final hop before
retaining bounded originals. Explicit click downloads retain both landing and
file chains. Inline originals keep the existing bounded second same-origin GET
with redirects refused; navigation is not represented as intercepted PDF bytes.

Before human assistance, detach this operation's interception and release its
source slot. Preserve the interstitial chain and actual DOM spend. After explicit
resume, admit a fresh collector navigation to the original source or mapped landing
URL, retaining its chain separately. The earlier chain does not attest to unknown
human requests. Challenge/login/subscription interaction is a human port, not a
solver or entitlement bypass. Browser flows depending on non-reloadable transient
page state still need representative acceptance; the controlled witness uses
browser-persisted completion state. Borrowed and unrelated tabs remain open.

Combined gate, independent installed-package acceptance, representative publisher
workflows and immutable publication remain required; I08 is not wholly closed.

## Verification record

The frozen full integration and affected browser/fetch/journal/continuation/package
selection passed **166 tests in 271.27 seconds**, zero failures/skips, under
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-browser-redirects-20261007/src/ghimera`. Strict mypy passed
**152 source files**. Native Chromium used owned loopback publishers and synthetic
session cookies; sources stayed frozen during the run.

Witnesses include actual redirected HTML/PDF/DOCX/inline parsing and citations;
graph/journal/archive readback; mapped redirected landing clicks; denied final
contact; fresh redirected-origin robots with one global slot and bounded bytes;
human continuation with separate chains; cancellation preserving spend and an
immediately reusable target/slots; mutations stripping provenance or known spend.
Search/model protocol fixtures are not real served-model or publisher-quality
evidence. Earlier fixture-only tuple and mutation-message failures were corrected
without weakening origin, byte or provenance checks.

### Earlier primitive evidence (not the full integration)

The final owning guard plus download/inline/navigation and package-boundary
selection passed **42 tests in 90.49 seconds**, zero failures/skips, under
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-browser-redirects-20261007/src/ghimera`. Sources stayed frozen
during that run. Ruff check/format passed (225 files); strict mypy passed
150 source files. At that earlier revision the sample component config was not
a Collector field; the integration above supersedes that limitation.

The native witnesses used installed Chromium and owned loopback sources:
302/307 chains into ordinary HTML, attachments and inline PDF; retained
attachment bytes matching the original PDF; synthetic same-session cookies;
scope/browser/robots/budget refusals before final contact; redirect limits and
loops; admitted and denied cross-origin navigation; exact-target refusal;
competing main-frame navigation; admission timeout and cancellation without
late contact; unchanged borrowed page and unrelated tab after cleanup. These
are controlled transport witnesses, not publisher, Tor or model-quality claims.

Earlier attempts are superseded: one native cancellation failure exposed an
ordering bug (stop-loading invalidated the paused request before fail-request),
corrected by failing paused requests first. One later cross-origin fixture
failed before navigation by treating a tuple-valued policy field as a list;
the fixture was corrected, without weakening origin admission. The final
42-pass selection includes both cases. The earlier 967-pass delivery full
gate does not cover this new guard; a combined full gate remains required
before a release.
