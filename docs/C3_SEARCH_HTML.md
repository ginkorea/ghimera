# Ordinary HTML search — candidate

Status: standalone source candidate; not published, deployed or a completed
real intent-to-reviewed-answer acceptance.

## Configuration, not fallback

The typed `chimera.searxng/2` recipe requires an explicit `response_format`
(`json` or `html`). Legacy `/1` remains JSON-only, omits the new field and
preserves its serialized identity. `examples/searxng-html.toml` is deliberately
non-routable. Put its values in the Collector's `[search]` section and configure
the existing `[extraction]` private paths, encoding and worker allowances.

`Collector` selects `SearxSearch` or `SearxHtmlSearch` before work. Both inherit
the final `GroundedSearch.discover` reservation/accounting contract and compose
one `SearxTransport`. JSON's `search-api/1` revision remains unchanged; HTML
records `search-html/1`. Neither adapter silently tries another mode or provider.
No operational endpoint, egress node or model is chosen in Python.

HTML mode requests ordinary `theme=simple` search without a JSON `format`
parameter. It uses the same credential-free source connector, guarded DNS,
verified TLS, direct/Tor selection, response bounds and refusal handling as
JSON. Search does not borrow source sessions or completion credentials.

## Passive parsing and retained evidence

Pinned Scrapling 0.4.2 parses the observed `#results #urls` envelope and each
`article.result` title link/content snippet. Navigation/cache links are not
search hits; missing result envelope or malformed selected result links refuse.
A recognized empty envelope can yield zero hits. The simple-theme provider
structure is an adapter invariant, not a general publisher selector or proof
against every future provider redesign.

The adapter composes the existing `PassiveWorker`: explicit input/output,
diagnostic, concurrency, timeout and cleanup limits; owner-private scratch;
credential-free child environment; timeout/cancellation kills and reaps its own
child. The parser never constructs a crawler, browser or model and its audit
guard refuses network/process I/O. It does not execute HTML scripts or load
subresources. That guard is not a claim of arbitrary-code OS containment.

Every successful search retains original page bytes, parsed hits and transport
through `SearchHistory` and `chimera.research-result/2`. Raw and typed digests
bind them to their accounted query. Parser failure/cancellation keeps bytes
already fetched in spend accounting. Search snippets remain discovery, never
answer citations. Completion requires native documents and a supported review.

The [SearXNG API documentation](https://docs.searxng.org/dev/search_api.html)
describes optionally enabled JSON formats; its upstream
[result template](https://github.com/searxng/searxng/blob/master/searx/templates/simple/results.html)
and [macros](https://github.com/searxng/searxng/blob/master/searx/templates/simple/macros.html)
are the source for this simple-theme structure (checked 6 October 2026).

## Checks and actual public trial

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16;
import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`; no TAIPAN import.

- Before implementation: 8 failed, 1 passed in 4.74 s on the new file.
- Initial HTML/JSON conformance: 17 passed in 9.83 s.
- Expanded changed-file/importer run: 68 passed in 56.93 s, with explicit local
  Chromium and bubblewrap inputs. An earlier run omitted that required browser
  setting and reported 1 failed, 67 passed; that is not counted as a green gate.
- Checks include native Chinese title/snippet, navigation exclusion, complete
  Collector composition and result replay, explicit modes/no JSON fallback,
  input/hit bounds, fetched-byte cancellation accounting, actual controlled
  SOCKS transport with no source DNS, and refused layouts/access barriers.
  Model responses in this composition are protocol fixtures, not model quality.
- Final full `scripts/gate.sh`: 358 passed, 0 failed, 0 skipped in 378.99 s,
  using the interpreter above. Offline lock check passed; Ruff passed;
  105 files already formatted; strict mypy passed over 74 source files.
  The provisioned local browser/bubblewrap inputs were explicit and platform
  credentials were removed from the environment. Source/tests stayed unchanged
  throughout the run. This closes the source gate, not public-corpus acceptance.

One actual ordinary HTML search on the laptop at
`https://search.inetol.net/search`, for a non-sensitive Python documentation
query, returned `search_unavailable` with 9,130 fetched bytes and no invented
hits. A single diagnostic request then retained HTTP 200 and the actual body:
it was a Substation proof-of-work security interstitial, not a results page.
Nothing followed, solved or executed that challenge. Private artifacts are
`gate-work/search-html-live-20261006/` and
`gate-work/search-html-inetol-probe-20261006/`.

The observed signature now receives the more precise `challenge_not_solved`
refusal before parsing: exact interstitial title plus its script-cookie marker.
The initial focused regression failed on the old generic classification.
Replaying the retained real body locally classifies it correctly, without a
new source request; ordinary reporting about the security mechanism remains
collectable. No cookies from the interstitial are reused.

These instances' refusal evidence does not establish successful public search,
retrieval adequacy, unattended access permission or full research accuracy.
A permitted reachable search service, representative publisher checks and the
full real-model/embedding research loop remain required acceptance work.
