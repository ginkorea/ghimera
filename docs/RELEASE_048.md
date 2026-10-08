# Ghimera 0.4.8 release acceptance

Status: **PUBLISHED AND ORIGINAL ARTIFACTS VERIFIED**.
This increment does not close the complete infrastructure PRD. Previous public
release identities remain immutable and are not rebuilt, retagged or replaced.

## Exact source gate

Source commit `19e99ceda1520e475b298fd506834bfcb15197ff`, tree
`e30ac983bbc772da69f174e17b3c6e0dca12acef`, combines cross-run source refresh
and repeated graph-cancellation acknowledgement ownership. The frozen full
`scripts/gate.sh` completed with 1,217 passed in 1376.95 seconds, no failures or
skips. Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python` imported
`/tmp/ghimera-recovery-release-20261008/src/ghimera/__init__.py`. The offline
lock check, Ruff, formatting and strict typing also passed. Staged tree equality,
absence of unstaged changes and diff checks were verified before committing.

These are controlled native/protocol tests, not a measurement of served-model
quality, publisher coverage, multilingual accuracy or unattended deployment.

The release metadata changes only the version, its assertion, lock self-version
and documentation. Production source and examples remain byte-identical to the
gated source commit. Five metadata checks passed without skips under the same
Python 3.11.16 interpreter/source binding; the offline lock check resolved the
same 142 packages using that explicit interpreter.

## Independent artifact acceptance and publication

- The source archive was built offline, then the wheel was built explicitly
  from that archive using Python 3.11.16. Complete inspection compared all 176
  wheel package members and 448 tracked source-archive members against the exact
  release Git blobs, checked metadata/license, and rejected untracked or missing
  contents. Both Twine checks passed under the private Python 3.11.16 tool runtime.
- Release commit `d734ad007cd0d1d5802b1ac4ebeb32e06b3c5fe2` contains only
  release metadata and documentation above the gated source. The real wheel
  installed offline into a new private Python 3.11.16 environment,
  `wheel-refresh-env/bin/python`, resolving its own site-packages. Existing
  third-party dependencies were copied from the previously independent installed
  environment; old project packages and editable hooks were excluded. This is
  not admission of every optional extra. Both installed CLI entry points passed.
- Actual loopback RSS collection across fresh child processes passed native 200,
  real conditional 304, changed-original preservation, no-store tombstone and
  subsequent full GET, native feed readback, private store modes and byte
  accounting. Five target contacts produced four immutable version/invalidation
  records. Robots denied the cached target before another target contact. No
  source outside the controlled loopback server or model was contacted.
- The first operator refresh probe incorrectly used an IP-literal crawl host
  and refused before target contact. Correcting only the probe to use a declared
  DNS alias pinned to the loopback server resolved it; production scope validation
  and accepted artifact bytes were not changed.
- Installed native graph cancellation waited through repeated caller cancellation
  after a real file commit. Its live view retained the initial node until the
  acknowledgement was released, then matched fresh disk replay with two nodes.
  No external source/model call or semantic-quality measurement occurred.
- GitHub main atomically fast-forwarded to the release commit together with
  annotated `v0.4.8`, object `a7ae86085a86508f7c7f00f49a9d059903197a8e`.
  Both remote refs and the tag's peeled release commit were independently read
  back. This evidence-only follow-up does not move that tag.
- Exactly the two accepted hash-pinned artifacts were uploaded to the official
  PyPI destination using the existing named profile. TLS was verified, redirects
  refused, and credentials were not logged or copied. Official metadata and both
  original public files were independently read back: no yanks and exact local
  byte equality.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `ghimera-0.4.8-py3-none-any.whl` | 412237 | `4c30ea62d781f4cc6d968e28b74443a8c5376f67b334665abcd609d59208c376` |
| `ghimera-0.4.8.tar.gz` | 1173153 | `75c17857d5be45899280b4f1af7976e8c1300745c3d5b4bbf2cb650b47974b76` |

These controlled checks establish package/lifecycle behavior, not representative
publisher/onion coverage, multilingual semantic accuracy or unattended deployment.

## Capability increment and remaining scope

`ghimera.source-refresh/1` configures exact URLs, bootstrap, private storage,
age and capacities. Conditional reuse retains the exact original/request
partition and current transport evidence; failures refuse rather than serve
stale content, delete history or downgrade persistence. See SOURCE_REFRESH.md.

Native graph append/replay use the existing drained worker boundary. Caller
cancellation does not release ownership before a pending graph acknowledgement
is checked and applied. This is not general uncertain graph/model reconciliation.

The complete I01-I14 tracker remains open, including interrupted whole-research
adoption, durable uncertain model invocation and spend reconciliation, reversible
temporal entity resolution, unattended service/API and scale, remote delivery,
representative publishers/browser-onion workflows and actual multilingual model
quality. Simplified Chinese OCR still fails its controlled full-transcription
check. Qwen-VL is a candidate for the existing explicit transcription interface;
no actual Qwen run or Chinese quality acceptance is claimed.
