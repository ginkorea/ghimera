# Explicit HTTPS gateways for self-hosted model control

An approved self-hosted model or encoder may sit behind an HTTPS gateway with
globally routable DNS answers or explicitly authorized shared-space answers.
Select `chimera.model-service/3` or
`chimera.embedding-service/2` and declare a nested `gateway` with schema
`ghimera.self-hosted-gateway/1` and its exact HTTPS `origin`. The existing model
`/1` and `/2`, and embedding `/1`, keep their private/loopback policy and omit
`gateway` from serialized recipes. Those older schemas cannot acquire gateway
authority. Model `/3` supports the existing optional versioned generation
controls; it requires gateway approval whether generation controls are present
or absent. Gateway `address_scope` defaults to `global`; that default is omitted
from serialized recipes, preserving existing gateway bytes and identities.

```toml
schema = "chimera.model-service/3"
endpoint = "https://inference.example.invalid/v1/chat/completions"
allow_plaintext = false
allow_plaintext_credentials = false
# Set approved_addresses to this exact origin's actual resolved global IPs.
# Other existing model limits and identities are required as usual.
[gateway]
schema = "ghimera.self-hosted-gateway/1"
origin = "https://inference.example.invalid"
```

`examples/self-hosted-gateway.toml` contains complete nonactive fragments. Its
reserved `.invalid` hostname cannot provide an operational gateway. Configure
the authorized origin, real resolved pins, model/revision, authorization mode,
and byte/time bounds before use. Declaring this policy records an operator's
approval; it does not obtain approval, discover credentials, grant compute,
launch inference, or prove a served endpoint works.

Origins use canonical HTTPS spelling with a lowercase ASCII hostname, no path,
trailing slash, userinfo, query or fragment. Default HTTPS port 443 is omitted;
an explicitly approved nondefault port is retained. The model or embedding
endpoint must have the exact same origin and its native completion/embedding
path. Both plaintext flags must be false. By default, address pins are distinct
canonical globally routable unicast IPs. Private, loopback, metadata/special-use,
link-local, reserved, multicast, unspecified and IPv6 translation/tunnel
addresses are refused. A literal endpoint must itself match an approved pin.

An origin whose actual DNS answers use RFC 6598 shared address space requires
an explicit `address_scope = "global_or_shared"` in its nested gateway policy.
That choice admits the IPv4 range `100.64.0.0/10` in addition to the original
global unicast addresses. It does not admit private networks, loopback,
link-local, other reserved ranges, or known metadata/platform endpoints such
as `100.100.100.200` and `168.63.129.16`. The recipe must still pin the actual
resolved addresses individually. There is no shared-space wildcard, DNS
substitution, proxy rewrite, plaintext allowance or generic JSON relaxation.

```toml
[gateway]
schema = "ghimera.self-hosted-gateway/1"
origin = "https://inference.example.invalid"
address_scope = "global_or_shared"
```

The optional shared scope uses the same native model transport validation
path. No alternate resolver or transport is needed: model/embedding admission
validates the declared address scope, then the existing destination boundary
requires every real DNS answer to match an individual approved pin before Curl
receives that exact address. TLS continues to authenticate the original origin.

`PinnedModelHttp` specializes the existing bounded JSON transport's admission
boundary. It revalidates the concrete versioned model/embedding configuration,
resolves the actual configured hostname through the native resolver, and
requires every resolved address to match a declared pin before contact. Curl
receives the real selected scoped pin through `RESOLVE`; the URL keeps its
original hostname for TLS SNI and certificate validation. TLS peer and host
verification are enabled, HTTPS is the only allowed protocol, redirects are
not followed and any redirect response is refused. Proxy/netrc discovery
remains disabled. Request, response, header and timeout bounds remain owned by
the existing transport. DNS drift requires an explicit recipe update; it never
becomes an automatic wildcard or private-address mapping.

Credentials use the existing separately injected `SecretStr` input and exact
authorization mode. They are sent only in the configured origin's authorization
header and do not enter recipe or model-call evidence. Model and encoding call
evidence retain the complete service configuration, including gateway origin
and scoped pins. These inputs therefore participate in existing exact recipe,
request, retention and recovery bindings.

Generic private JSON delivery/search consumers retain their original admission
rules. Passing a gateway-bearing model configuration directly to
`PinnedJsonHttp` still refuses public and shared addresses. Gateway authority is
selected only through the native versioned model/embedding control boundary;
there is no generic public JSON fallback.

Focused contract tests inspect native curl configuration and bounded callbacks
without making network requests. They establish admission/refusal behavior,
not live TLS, model quality, broker acceptance or publication evidence.
