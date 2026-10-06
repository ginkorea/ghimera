# C1 HTTP evidence — partial lane, not full spider acceptance

Test environment: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16,
importing `/tmp/chimera-c0-20261006/src/chimera/__init__.py`. No TAIPAN SDK is
installed or needed for these package/network tests. No governed model queried.

The complete package gate passed: **60 passed / 0 failed / 0 skipped in 22.13 s**,
Ruff/format passed, strict mypy passed over 13 source modules, offline lock check
passed (23 packages). Re-run:

```bash
cd /tmp/chimera-c0-20261006
CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh
```

The gate needs permission to open its controlled loopback-only fixture server.
The initial sandboxed run refused socket creation; the real-server tests then
proceeded through an approved escalation, without weakening that sandbox.
The tests-first run failed before NetworkPolicy existed. During implementation
the compressed-body test caught curl_cffi's callback return convention: the route
now uses CURL_WRITEFUNC_ERROR and checks the callback state before returning.

Runtime pins checked against public PyPI and installed in the private environment:
curl_cffi 0.16.3, Protego 0.7.0, Pydantic 2.13.5; transitive artifacts are locked.
Production transport narrows the partially annotated vendor lifecycle through a
small Protocol, with no Any propagation, cast or type suppression.

Real libcurl acceptance uses an owned HTTPServer listening on 127.0.0.1 with a
fresh random port. The declared fixture DNS name is pinned to that exact loopback
address; fixture exceptions cannot appear in public network policy. Covered:

- honest UA, no cookie/auth propagation, inherited proxy disabled and pinned DNS;
- robots deny/wildcard/longest allow, scoped audited override, config binding;
- checked redirects, off-scope refusal before I/O, bounded redirect loops;
- 5xx retry accounting, no 4xx retry/escalation, terminal challenge/login/paywall;
- decoded compressed-body cap, conditional 304 exact representation reuse;
- host/global scheduling and spacing, byte/page reservations, cancellation and
  partial-transfer timeout spend; metadata headers bounded at the C callback;
- shared route conformance includes CurlRoute; isolated base mutations turn the
  witnesses red (no production/shared source changed by mutation probes).

These are local behavior measurements, not live-publisher success rates or model
accuracy. Browser routes, adaptive extraction/PDF conversion, intent-first search,
served scoring/answer synthesis, incremental graph and TAIPAN/Edge acceptance
remain required implementation. No publication, deployment or live config change.

Configuration locations: `examples/chimera.toml [http.robots]`; typed validation
in `config.RobotsPolicy`; enforcement in `Politeness.permits`; terminal barriers
in `http.page_barrier`. Override does not authorize login/paywall/CAPTCHA bypass.

## Build and noneditable wheel acceptance

Tested source commit: `b4aba5b525167c7b9752388ac1aa26a78119df7d` on
`gompert/chimera-c1-http-20261006` (the evidence wording above is corrected
afterward; production code is unchanged). C0's artifacts remain untouched.

Built with `uv build --offline --no-sources --out-dir dist/c1-http`:

- wheel SHA-256 `e8daf350699554b8ffddbb729d5a31828bf05e5747d087e9fe8b2da10f0eaff0`;
- source archive SHA-256 `706b3b6366f633c800287f3b001dc74177400e9a56b446c1f4a0cb0d821f2727`.

`/tmp/chimera-c1-wheel-check-20261006/bin/python` (3.11.16) imports the installed
package from its `lib64/python3.11/site-packages/chimera/__init__.py`, with
PYTHONPATH and TAIPAN credential variables unset. Offline dependency resolution
needed uncached registry metadata; the same pinned dependencies were installed
from public PyPI into this private venv only. The installed wheel then performed
a real loopback HTTP goal run: two requests (robots + report), one retained
document, a page-budget stop, and harvest JSON reader/writer roundtrip PASS.
Extractor and judge remained explicitly test doubles; no model/extraction
accuracy or live-publisher acceptance follows. Artifacts predate this evidence
append and are unpublished local candidates, not a release.
