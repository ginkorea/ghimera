# Configurable local challenge recovery

This is source added after the published ghimera 0.3.0 artifacts. It is not yet
a released upgrade or a claim of acceptance against production CAPTCHA sites.

## Implemented mechanism and explicit switch

The existing isolated Patchright renderer remains the ordinary JavaScript
adapter. For a public HTTPS source returning a detected challenge, configure
the optional `[challenges]` section from [the example](../examples/challenges.toml).
Omit that section to disable automatic recovery. Origins, provider revision,
cookie names, cache size/TTL, timeout and attempt budget are configuration—not
Python edits. Effective non-secret policy is retained in the run receipt.

`ChallengeConfig` owns validation. `CurlRoute.clear_challenge` is the recovery
port; `ChallengeSessions` implements the declared local gateway dialect. The guarded
fetch ladder invokes that port only after detecting a challenge, after the
normal source-scope/robots boundary. Its existing template remains final.
If the robots file itself is challenged, recovery reads that file first; any
resulting Disallow still stops collection. A challenge appearing only in the
rendered DOM can trigger one recovery/refetch; a second rendered challenge
remains terminal rather than creating a recursive solver loop.

The sequence is: normal fetch → detected challenge → local gateway
`request.get` → bounded in-memory clearance + matching User-Agent → normal
guarded source refetch → challenge check → ordinary extraction. Clearance
acquisition alone is not success. The provider's returned DOM is discarded;
only the refetched original response is document evidence. Gateway requests,
received bytes, latency and failures reconcile with the run's receipt/journal.
Internal gateway browser requests are not individually metered by Ghimera's
page budget; deploy a bounded gateway, not an unbounded crawling service.

Only configured cookie names are accepted. Cache scope is the exact scheme,
host and port, bounded by provider cookie expiry and configured TTL. No cookie
value, clearance token or gateway response body enters the evidence archive.
Every source use records non-secret policy/provider/expiry metadata. Cross-host
redirects cannot carry the cookie. Clearing is refused for source URLs with
configured account credentials; this adapter never sends those credentials to
a solver or combines its cookies with an entitled account's session.

## Gateway deployment is a separate network boundary

Use a locally installed, version-pinned FlareSolverr or Byparr service bound only to
loopback. The client refuses remote endpoints, redirects and unapproved provider
versions, and never inherits a proxy or credential. Ghimera does not install,
launch, modify or publicly expose that service. Pin its container image by
digest in your own deployment configuration; do not use `latest` for production.
Use `LOG_LEVEL=warn` or `error` and keep HTML logging disabled: the inspected
upstream implementation logs Turnstile tokens at INFO. Private client storage
does not redact an independently deployed gateway's logs.

Unlike the passive browser renderer, this gateway executes source scripts with
its own network access. `operator_managed_local_browser_gateway` declares that
difference explicitly. Its egress/firewall/proxy must block private networks,
metadata endpoints and unwanted hosts; the Ghimera source-origin allowlist is
not a firewall for a separate process's subresources or redirects. Run it on
the approved collection host. Never infer permission to route through a model
host merely because a local model endpoint is configured.

This adapter is direct-route only. Tor's existing request-specific circuits do
not preserve a separate browser's IP identity; combining them would fail or
leak the selected route. Challenge-enabled onion/Tor origins refuse at config
validation instead of silently switching to direct access. Tor/browser recovery
needs a separate route-preserving implementation and acceptance.

## Select a gateway through configuration

The original `ghimera.challenges/1` FlareSolverr configuration and serialized
digest remain unchanged. New `ghimera.challenges/2` policy requires an explicit
`wire_dialect`, with non-secret provider provenance in
`ghimera.challenge-evidence/2`. There is no automatic provider fallback.

| Provider | Configuration | Request dialect |
|---|---|---|
| FlareSolverr | [original example](../examples/challenges.toml) | `maxTimeout` in milliseconds; `returnOnlyCookies=true` |
| Byparr 2.1.0 | [Camoufox example](../examples/challenges-byparr.toml) | `max_timeout` in whole seconds |
| Byparr 3.0.4 | [newer example](../examples/challenges-byparr-modern.toml) | FlareSolverr-compatible millisecond wire; cookies-only requested |

The inspected [2.1.0 dependencies](https://github.com/ThePhaseless/Byparr/blob/v2.1.0/pyproject.toml)
use Camoufox; [3.0.4 dependencies](https://github.com/ThePhaseless/Byparr/blob/v3.0.4/pyproject.toml)
use a different Playwright-based stack. Browser identity is a deployed provider
property, not something the client infers from the provider name. Pin the image
digest and require its response version to match `provider_version`.
Byparr's response version comes from its deployment `VERSION` setting; the
version string alone is not cryptographic verification of its executable.

Older Byparr returns page HTML and ignores FlareSolverr's `returnOnlyCookies`.
Ghimera bounds that response and discards the DOM; large responses may refuse.
It sends the actual seconds field instead of letting an ignored field select
the provider's default timeout. Whole-second rounding is bounded by the local
deadline, but client cancellation does not prove the separate gateway stopped
its browser. Configure the provider's own resource limits and cleanup.
The newer Byparr wire treats numbers below 1000 as seconds, so Ghimera sends a
minimum of 1000 milliseconds while retaining the caller's shorter local deadline.
Byparr clearance requires a successful source status, valid configured cookies
and matching origin/version/User-Agent; a success envelope alone is insufficient.
`tabs_till_verify` is FlareSolverr-only and refused for Byparr.

Byparr is a separately deployed GPL-3.0 service; its code and browser binaries
are not bundled, imported or installed by Ghimera. Deployment/distribution must
retain the provider's own licence notices and obligations. The Python package
adds no solver dependency or paid API. Nodriver and audio extensions are not
silently added: a compatible driver is not a proven CAPTCHA solver.

## What upstream actually provides

Sources inspected 6 October 2026:

- [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) is the existing
  Chromium driver. Browser camouflage is not universal CAPTCHA-solving proof.
- [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) documents local
  browser challenge handling, returned cookies/User-Agent and optional
  `tabs_till_verify`. Its CAPTCHA-solvers section also explicitly warns that
  its general solvers do not work. This adapter does not promise universal
  Turnstile, reCAPTCHA or hCaptcha success.
- [Cloudflare clearance](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/)
  distinguishes visitor-bound clearance cookies from single-use Turnstile
  tokens. A token is not a cookie and is not reused across the crawler pool.
- [Buster](https://github.com/dessant/buster) is an audio-assistance extension,
  not a verified offline speech model or a service this package has integrated.
- [Camoufox](https://github.com/daijro/camoufox) is available through the explicit
  Byparr 2.x gateway integration, not as an interchangeable passive renderer.
- [nodriver](https://github.com/ultrafunkamsterdam/nodriver) remains an alternate
  browser candidate. Its licence obligations and equivalent network/resource
  behavior must be checked before integration; it is not silently vendored.

Challenges normally require the remote site's validation services. Hosting the
browser/solver locally avoids a paid solving API; it does not make the target
site or CAPTCHA validation work offline.

Login/paywall and robots refusals retain their own semantics. An embedded
CAPTCHA on a login or subscription wall does not turn it into a public source.
Unsupported challenges remain `challenge_not_solved`. Caller-supplied authorized
sessions remain available through [source sessions](SOURCE_SESSIONS.md).

## Acceptance status

Local HTTP integration exercises source challenge → provider wire → private
clearance → refetch, exact-origin reuse, expiry, failed/still-challenged results,
bounded oversized responses, provider/version validation, redirect refusal,
robots/entitlement separation and complete harvest/journal reconciliation.
Those checks prove the adapter, not upstream challenge-solving accuracy.
New Byparr checks cover both request dialects, provider/source-status validation,
legacy policy serialization and versioned harvest/journal replay. They use local
HTTP fixtures, not an installed Byparr browser or a remote challenge website.

The provider-extension full gate on 6 October 2026 used
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout's `src/ghimera`. Offline lock validation resolved 137 packages; lint
passed, all 125 checked files were formatted, and strict typing passed for 88
source files. The complete suite passed **457 tests, zero failed, zero skipped**
in 485.70 seconds. This verifies client integration and regressions, not the
independently deployed providers' accuracy on public CAPTCHA sites.

The complete `scripts/gate.sh` run on 6 October 2026 used
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing this
worktree's `src/ghimera`. Offline lock validation resolved 137 packages; lint
and formatting passed for 111 files, strict typing passed for 79 source files,
and the suite passed **378 tests, zero failed, zero skipped**, in 405.97 seconds.
The installed Chromium fixture ran with network isolation; fixture servers
were local. No live solver, remote challenge target or inference service was
used. An initial full run found a stale mutation-test anchor after a second
budget-reservation call was added; its witness was made context-specific,
not skipped or weakened, before this clean full rerun.

Still open: a deployed, egress-restricted real gateway, representative authorized
public challenge sites, more solver adapters,
human-assisted interaction, and same-browser handling where a clearance cookie
cannot be replayed by the ordinary HTTP client. These gaps must remain explicit.
