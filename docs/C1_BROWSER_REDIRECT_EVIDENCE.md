# Per-hop browser redirects — 6 October 2026

Status: **local source and installed-wheel candidate**, not pushed, published,
deployed, or full spider acceptance. Source commit
`c4b8552d457bed957eef16bc8476b05d24aa1c50`, branch
`gompert/chimera-c1-browser-redirects-20261006`, based on `5da5133`.
These are local, ungoverned engineering measurements, not TAIPAN run reports.
No governed catalogue material was queried or withheld.

## Implementation and test-first evidence

The initial real-browser redirect test failed: a redirected script never made
its article ready and rendering refused on deadline. The parent previously
followed the redirect invisibly, then refused replaying the final bytes at the
original URL. Ordinary high-level browser routing also cannot be relied on for
every redirect URL; the implementation now uses Chromium's CDP Fetch domain.

`FetchLadder.resource_fetcher` exposes a single-hop port over the same bounded
HTTP mechanism used for document requests. Each browser redirect is locally
fulfilled separately. The parent verifies the preceding retained Location,
scope, resource kind, configured redirect cap, loops and transport transitions;
each new source request also checks robots and spends the original run's budget.
No browser-owned network continuation or direct fallback is used.

The change also closes a real HTTP header gap: the earlier curl header allowlist
dropped CSP/CORS before rendering. The shared response boundary now retains
allowlisted response policies and charset metadata, preserves repeated CSP,
and excludes cookies and authentication. Network request classification is
joined to the corresponding paused request before applying resource-kind policy,
so an early CDP XHR label does not misclassify a fetch request.

Controlled browser checks cover:

- redirected script and JSON; final `Response.url`; raw bytes for every hop;
- relative ES-module imports resolved from the final module directory;
- cross-origin redirect with CORS allowed and denied by the actual browser;
- robots-denied and out-of-scope destinations never contacted;
- redirect cap and loop refusal before additional destination I/O;
- open-web and valid-v3-onion redirect chains through a real local SOCKS server,
  with a DNS collaborator that refuses every local DNS invocation;
- real HTTP CSP retention, repeated CSP enforcement, and credential exclusion;
- JSON chain tamper rejection and successful-hop policy validation;
- the existing timeout/cancellation, original/DOM binding, resource cap,
  extraction/harvest, POST/socket, and HTTP accounting regressions.

The lower-level interceptor observes an implicit Chromium favicon attempt that
the older routing path omitted. It records a named `other` refusal before parent
I/O rather than pretending no resource was attempted. Existing assertions were
updated to distinguish retained successful content from these refused attempts.

## Full source gate

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
`taipan` resolves none; platform doctor/floor are not applicable to this standalone
package. Source stayed unchanged during each gate.

First complete gate: **186 passed, 1 failed**. The failure was the mutation
test's old scope-guard anchor matching both the outer fetch check and the new
shared hop check. Its anchor was made specific to the outer check; the witness
still disables that guard and requires its behavioral test to fail. All **14
contract mutation checks passed** on the source interpreter above.

Final complete `scripts/gate.sh`: **187 passed, 0 failed, 0 skipped in 199.70 s**
on that same source interpreter. Ruff/format passed for 60 source/test files;
strict mypy passed for 44 source files; offline lock verification resolved 137
packages. Gate handle was terminal with exit 0 before source commit/build.

```bash
CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh
```

## Built artifacts and installed-wheel readback

Built offline from the source commit above:

- `dist/c1-browser-redirects/taipan_chimera-0.1.0-py3-none-any.whl`, SHA-256:
  `f46e055d5d88bc9964a6480b65b73e11b8a41b6c25234463a24624849b7318ce`
- `dist/c1-browser-redirects/taipan_chimera-0.1.0.tar.gz`, SHA-256:
  `a51b1560fb5c2b08a3cf4b0c152a50eb99678ed7758c1e9c48231230d16c5dbd`

Installed into the existing task-owned, noneditable offline browser/HTML acceptance
environment, replacing only its earlier task wheel. `uv pip check` reported 103
compatible packages. No shared environment, provisioned browser artifact or
production service was modified.

Actual execution used `/tmp/chimera-c1-browser-wheel-20261006/bin/python`, Python
**3.11.16**, from `/tmp` with `-I`, no `PYTHONPATH` and no TAIPAN credential.
Import resolved to
`/tmp/chimera-c1-browser-wheel-20261006/lib64/python3.11/site-packages/chimera/__init__.py`,
not the source checkout; `taipan` resolved none. Platform doctor/floor are not
applicable. The local fixture script is
`gate-work/accept-browser-redirect-wheel.py` (an untracked operator fixture).

Installed readback on that interpreter reported:

- actual parent HTTP requests (`fetches`): **6**, including robots and initial HTML;
- retained decoded content (`bytes_read`): **336**, reconciled to ledger rows;
- retained resource statuses (`hop_statuses`): **302, 200, 307, 200**;
- final JSON `Response.url` visible in the real rendered article;
- original HTML unchanged; Page JSON roundtrip passed;
- distinct parent/child network namespace identities; owned scratch empty after cleanup.

Content SHA-256 values from that same installed readback:

- original HTML: `5d8a109dce3a8798ba2c714fc26cd04bf423f01defd0841967795eccbc1edbec`;
- DOM: `fcc9ca677ef5ec50ae3e0e70beeab615c1d9a2a669eb49742064a6f3d145096b`;
- provisioned Chromium executable:
  `0b20b130e7edd9dd51873be867761295fe0cfad490c2b9a64f95bd3cfc08fa71`.

The DOM includes the fixture's ephemeral response URL, so its digest is evidence
of that run, not a hard-coded expectation for another port. This is actual browser
and installed-library execution over controlled local HTTP, not a publisher
accuracy/speed benchmark or public Tor/anonymity acceptance.

## Unresolved full-scope requirements

Camoufox/nodriver fallback, public browser/Tor and full publisher acceptance,
full model-based PDF layout/OCR and Marker, archived locator relocation evidence,
real encoder/scorer and served-model acceptance, one-hop references, governed
TAIPAN integration and C5 runtime/egress/hardened admission remain open. This
increment closes subsidiary redirect handling, not the full go-spider objective.
