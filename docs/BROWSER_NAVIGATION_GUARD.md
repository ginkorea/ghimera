# Caller-session redirect guard (development candidate)

The existing native download observer compares the download URL with the
frontier request. Chromium reports a redirected attachment's final URL instead,
so that observer misses the file. Inline PDFs also navigate to their final URL,
which the current exact-original inline binding rejects. Loosening that binding
after navigation would contact an unadmitted redirect before detecting it.

`BrowserNavigationGuard` is the new request-stage primitive for closing that
gap. It is not yet wired into `Collector`, `HumanBrowserRoute` or archive/graph
provenance; redirected-file support remains open until that integration passes.
No additional Collector config is accepted and silently ignored.

## Ownership and configuration

The application explicitly delegates exclusive main-frame navigation during
one operation. It supplies its actual caller-owned Patchright Page, the existing
HumanBrowserConfig, exact requested URL, run scope and a typed
`BrowserNavigationAdmission` collaborator. Its `admit(url)` checks the run's
robots/cadence/budget policy before any continuation. There is no independent
default permission, credential discovery, profile import or new HTTP client.

`BrowserNavigationConfig` (`ghimera.browser-navigation/1`) owns maximum redirects,
finite per-hop admission timeout, cleanup timeout and the explicit interception
ownership declaration. `examples/browser-navigation-guard.toml` is a non-active
component fragment, not a newly supported Collector field. The existing browser
recipe/identity is unchanged. Competing interception owners cannot be discovered
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
It is not yet embedded into the current source evidence. Replay rejects a
stripped/discontinuous chain, loops, changed final URL, changed effective policy
or browser scope, and a redirect count beyond the configured bound.

## Remaining integration (not optional for closure)

1. Give the existing FetchLadder a typed per-operation admission port. Reuse its
   robots/cadence owner and actual page/time/byte spend. Do not add another
   permission cache or callback to serialized FetchRequest records.
2. Resolve reentrant budgeting before wiring the port: the present `_execute`
   reserves browser output bytes before capture. A redirect needing a fresh
   robots request cannot wait for the reservation held by that same capture.
   Preserve concurrent byte safety without silently waiving robots or budgets.
   Likewise, do not nest a redirect's host slot inside the held original host's
   global slot; the run must transfer/release owned admission correctly.
3. Integrate navigation, mapped clicks/landing pages, inline final-URL fetch and
   download event correlation using the observed native chain, not an arbitrary
   final-URL match. Preserve actual bounded document bytes and assistance spend.
4. Carry/replay the chain through source documents, graph, journal and archives.
   Old omitted policy/evidence must preserve their published serialized identity.
5. Prove the full collector path over controlled native redirected PDF/DOCX/HTML,
   denied cross-origin hops, budget/cancellation, and representative publisher
   workflows. A primitive test is not end-to-end or publisher acceptance.

## Verification record

The final owning guard plus download/inline/navigation and package-boundary
selection passed **42 tests in 90.49 seconds**, zero failures/skips, under
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-browser-redirects-20261007/src/ghimera`. Sources stayed frozen
during that run. Ruff check/format passed (225 files); strict mypy passed
150 source files. The sample component config is separate from the current
Collector config and does not activate the guard implicitly.

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
