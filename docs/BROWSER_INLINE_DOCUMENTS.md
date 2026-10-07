# Inline documents in the caller's browser

Status: 0.4.3 source full-gated, with installed-wheel readback and a bounded
public sample capture; publication is recorded separately in
[release acceptance](RELEASE_043.md). The immutable 0.4.2 release includes native
downloads, not this inline-response path.

## Configuration and ownership

An inline PDF can open Chromium's viewer without a native download event. The
collector does not treat that viewer's DOM as original PDF bytes. An explicit
`human_browser.downloads.inline` policy enables one additional same-origin
browser GET when navigation returned an admitted inline document. See the
non-active [fragment](../examples/browser-inline-documents.toml). Its formats
must also appear in `navigation_content_types`; omission preserves published
download-policy serialization and identities.

Use the existing caller-managed `patchright_page` configuration and supply the
actual Page to `BoundPageHumanSession`. Origins, paths, session/target identity,
deadlines and file-byte limits stay with their existing typed owners. There is
no endpoint discovery, cookie export, hidden request client or global browser
download-setting change. The existing CDP DOM adapter remains unchanged.

Navigation is followed by a separate browser fetch only for an admitted MIME
type. It is not an interception of navigation bytes: the second response may
have changed, and its actual bytes/response observations are what is retained.
That request uses the caller's existing same-origin browser session, with
redirects refused. A redirect refusal does not try another session or route.
Ordinary HTML keeps its normal DOM path, with no extra file request.

The caller controls browser egress, CSP, extensions, credentials, entitlement,
network quota and storage. A CSP/network/session refusal is not authorization
to bypass the site. This path neither verifies browser Tor routing nor admits
cross-origin or redirected file workflows. Those remain explicit requirements.

## Bounded original bytes and lifecycle

The in-browser reader retains at most the admitted file limit plus one byte
for detecting overflow. The driver reply is size-checked before base64 decoding;
all bytes actually returned to the collector, including that overflow probe,
are charged. An incomplete, oversized or incorrect-format body refuses rather
than becoming a partial parsed document. Actual PDF/DOCX byte admission precedes
the existing bounded native document worker.

Browser buffering, navigation traffic and stream chunks not copied into the
collector are not represented as metered bytes. The evidence explicitly reports
browser network totals as unknown, not zero. Operators must budget the navigation
and the one additional request in the browser's own network boundary.

Each capture has an owned AbortController in the isolated page world. Its finite
timer and cancellation cleanup abort only that operation. No browser, context,
caller tab or unrelated download is closed. Cancellation of a Python driver
call is not assumed to cancel JavaScript; cleanup explicitly cancels the owned
request. Aborting a stream may reject its later `cancel` call, so that cleanup
does not overwrite an already observed body or refusal.

## Evidence and integration

`ghimera.browser-response-evidence/1` with acquisition `browser_response` is
distinct from both a DOM capture and `browser_download`. It records the actual
navigation/body-response content types, successful status observations, exact
request/final/document URLs, caller session/target and effective policy digest,
driver/browser/adapter revisions, retained original hash/size and collector
byte count. It does not contain cookies, bearer headers or credentials. The
existing Page/ledger wrapper remains a browser acquisition, not an invented
standalone HTTP-client fetch; observed status lives in the browser evidence.

The existing FetchLadder, native parser, scorer, reviewer, source graph, journal
and research archive consume it. Replay checks original bytes, MIME observation,
effective policy, exact source/target and receipt spend. Removing the policy,
changing a response header/status, changing the original or stripping provenance
must fail readback rather than make the record look like ordinary HTTP.

## Native observations — 7 October 2026

A credential-free controlled Chromium primitive read the exact original inline
PDF through its own page: one navigation plus one body GET, no download event.
It passed one check in 3.36 seconds on Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing the owned
`/tmp/ghimera-delivery-outbox-20261007` checkout. This primitive is not production
or publisher acceptance.

The first production module run passed three cases and failed its oversize
case. A repeated diagnostic run passed five cases before the final cleanup
correction; it is not claimed as a stable full gate. The implementation now
prevents owned-stream abort cleanup from replacing the computed bounded result,
and returns explicit request-failure replies without fabricated HTTP statuses.

The combined changed-browser/download/navigation/human-command regressions
passed **63 tests in 101.99 seconds**, no failures/skips, on that same Python
3.11.16 interpreter importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. Source/tests/examples were
frozen. Ruff passed; strict mypy passed over 146 source files. Real Chromium
and native PDF parsing proved original-byte collection, source-matched citations,
graph identity, journal and research archive round trips; an in-browser
controlled caller-session action stayed in the selected session. Incorrect
media, overflow, in-flight cancellation and a refused redirect preserved caller
tabs. The redirect's target received no request. Planner/judge/reviewer replies
were fixtures, not model-quality evidence.

The subsequent final policy/provenance checks passed **12 tests in 30.76
seconds**, with no failures or skips, on the same Python 3.11.16 interpreter and
owned source import above. They include the non-active inline example and
refusal of altered original bytes, response status, response header, document
URL, spend and stripped inline policy. The frozen versioned source `5c07057`
then passed the full package gate: **950 tests in 869.41 seconds**, with no
failures or skips, on that same Python 3.11.16 interpreter and source import.
Ruff/format passed; strict mypy passed 146 source files. The separately installed
wheel reopened all three native browser archives and their exact citations,
originals, actual response/policy and graph bindings without source/model calls.

The installed candidate also captured the public W3C sample
<https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf> through
the normal FetchLadder, honoring robots. It returned `browser_response`, 13,264
original bytes, SHA-256
`3df79d34abbca99308e79cb94461c1893582604d68329a41fd4bec1885e6adb4`.
The ledger reconciled two charged fetch operations and 17,108 collector bytes;
browser navigation/subresource traffic remains unknown, not part of that total.
The interpreter was the independently installed Python 3.11.16 wheel environment
under the owned `ghimera-043-release-R1PMMC` operator directory. It used no
credentials, assistance or inference. A public sample is not a representative
publisher, entitled-session or research-quality benchmark.

Representative entitled publishers,
onion-browser networking, pagination, cross-origin/redirected files and native
Pacific OCR/research quality remain open in the infrastructure PRD.
