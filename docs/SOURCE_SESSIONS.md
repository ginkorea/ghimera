# Authorized source sessions

The collector can act on a caller's existing, authorized access. It need not
limit itself to anonymous public responses. Explicit sessions supply cookies,
authorization headers or declared custom `x-*` credential headers to collected
sources. They do not solve challenges, acquire an entitlement, bypass a login
or silently borrow a personal browser profile.

## Policy and credentials are separate

`SourceSessionPolicy` is the versioned `chimera.source-session/1` boundary.
`examples/source-session.toml` is a deliberately non-routable example. Its
non-secret policy is included in the main configuration's `source_sessions`
tuple and the effective run receipt. It defines a named session, exact origin
(scheme, host and port), permitted path prefixes and credential header names.
No token, cookie value, password or credential-file contents belongs in TOML.

Your application supplies `SourceCredentials` separately to `CurlRoute`:

```python
from pydantic import SecretStr
from ghimera.http import CurlRoute
from ghimera.source_sessions import SourceCredentials

# config contains the validated publisher-subscription session policy.
# session_cookie comes from your application's own authorized secret store.
route = CurlRoute(
    config,
    source_credentials={
        "publisher-subscription": SourceCredentials(
            headers=(("cookie", SecretStr(session_cookie)),)
        )
    },
)
```

Every configured session must have its exact binding before collection starts.
Missing/extra bindings or mismatched header names refuse before source I/O.
Distinct nonoverlapping path scopes may use different credentials on one origin;
ambiguous overlaps refuse at configuration parse. The package does not infer
credentials from the environment, `.netrc`, a browser profile or a user home.

## Exact-destination enforcement

HTTPS is required unless `allow_http` is explicitly enabled for that exact
origin. Plain HTTP can expose credentials; this switch requires the caller's
appropriate authority and transport decision. An authenticated HTTP onion
source still requires the configured Tor transport and valid onion address.
Sessions never grant new crawl scope, network destinations or Tor bypass.

Paths match complete prefixes, not string lookalikes. Dot traversal, encoded
separators and ambiguous repeated percent decoding do not receive credentials.
Each redirect hop independently selects its matching session. A different
scheme, host, port or out-of-prefix path does not inherit a prior credential.
The ordinary scope, DNS, TLS, robots, byte/time and response checks still apply.

Browser scripts/resources go through the same parent-owned HTTP boundary, so
authorized resources can render without exposing credential values to the
isolated worker. The browser itself has no imported cookie jar/profile. This
does not yet claim support for interactive sign-in, JavaScript cookie access,
POST-based workflows or automatic session renewal.

The discovery-service client deliberately does not inherit source sessions.
Private model-control authentication already has its own separate boundary.
Source, search and model identities are not interchangeable.

## Evidence and failures

`chimera.source-session-use/1` records selected session ID, request URL, origin,
header names and policy digest, never credential values. It travels with the
source fetch ledger, retained document and authorized browser resource. Saved
harvest readers bind selections back to effective policy and source URLs.
Selection metadata is not proof that authentication or the network request
succeeded. Credentials remain in memory; malformed header values refuse
without including them in error messages.

An expired/refused session follows ordinary 401/403 failure behavior without
retrying a challenge, widening scope or substituting another identity. A login
or subscription wall requires a valid authorized/entitled session or another
source. The caller owns lawful access and secret rotation.

Controlled local HTTP and isolated-browser checks establish request behavior,
not acceptance against a real publisher's account or subscription service.
Exact environment and full-gate results: `SOURCE_SESSIONS_EVIDENCE.md`.
