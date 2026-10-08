# Cross-run conditional source refresh

Development candidate, not yet a published release. The optional typed
`ghimera.source-refresh/1` section in `examples/source-refresh.toml` connects
the ordinary Collector to a private source-version store. No second crawler,
polling loop, scheduler or implicit source scope is introduced.

Specify exact target URLs, an absolute owner-private directory, explicit
bootstrap permission, validator age and record/store capacity. For the first
run, set `create_if_missing = true` only for a new directory whose parent
exists. Subsequent runs can set it false. An existing broken/empty directory
is not recreated. The store uses the existing no-symlink, owner-only SQLite
boundary with drained thread-local transactions. It can hold RSS/Atom/sitemaps
or other configured native HTTP targets; feed parsing remains independent.

Every conditional GET still contacts the source through ordinary scope,
robots, pacing, origin cooldown, DNS/TLS, transport and credential boundaries.
Native HTTP 200 versions append without overwriting older originals. Validator
reuse requires the same exact URL, provider, HTTP/network policy, configured
Direct/Tor transport, user agent, impersonation and explicitly bound source
credential identity. Secret values stay in memory: only an opaque representation
fingerprint is stored, never credential values or response cookies. Changed
credentials/configuration cannot receive an earlier validator or original.
Human/browser captures and transient challenge-clearance sessions are not
durable HTTP-cache versions.

An actual HTTP 304 reuses the selected original bytes; current request transport
and session evidence come from the 304, not the older network connection.
`ghimera.source-refresh-use/1` records the original version id/hash, policy,
original capture time and revalidation time in the page/document/graph/ledger.
The native 304 fetch records its actual transferred bytes; the separate policy
observation is zero-transfer and does not invent another request. Conditional
reuse is not proof of semantic accuracy or publisher-date freshness.

Expired validators trigger a full GET. `no-store`, `Vary: *`, access refusals
and other non-200/non-304 responses append content-free invalidations, so a
later run cannot resurrect an older cache entry. Historical originals remain
available for an explicit private-store audit. An unsolicited or invalidated
304 refuses; there is no silent stale fallback. Store failures, policy changes,
clock rollback and capacity exhaustion also refuse rather than downgrade to
a volatile cache or delete old versions. Capacity bounds serialized original
records, not total filesystem overhead or general archive retention.

Use `SourceRefreshStore.versions()` for an explicit full payload audit. Ordinary
lookup verifies private storage, the bounded chain's headers/tail/capacity and
the selected original's exact bytes. This is corruption detection, not an
authenticated log against an owner who rewrites every record and its metadata.
There is no retention rotation, source API/citation connector or unattended
connector deployment here; those remain in the full infrastructure tracker.

Required acceptance: actual local HTTP 200/304 across fresh processes;
changed originals and credentials; expiry, robots refusal before contact,
no-store invalidation, bounded capacity, corruption/clock/storage refusal;
Collector, graph, harvest/journal/corpus provenance readback and cancellation.
Controlled sources/model-protocol fixtures establish mechanism only. They do
not establish representative publisher, onion, multilingual or model quality.
