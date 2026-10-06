# C2 HTML candidate evidence

Status: standalone source and installed-wheel acceptance, not published or
deployed. Source revision: `947fc7e` on
`gompert/chimera-c2-extraction-20261006`.

## Gate and interpreter

The full package gate ran against
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, resolving
`chimera` to `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
This standalone environment resolves no TAIPAN SDK; platform doctor/floor
checks do not apply. No platform credentials or GPU calls were used.

Command: `env CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh`.
Result on that interpreter: **135 passed, 0 failed, 0 skipped**, 66.01 seconds;
strict typecheck covered 31 production modules, Ruff covered 43 files, and
the lock resolved 117 packages. Fixture results are not a multilingual
accuracy benchmark or the PRD's publisher-redesign acceptance corpus.

The gate's first full run found an existing HTTP timing witness sensitive to
server arrival jitter. Its test now observes actual client politeness-slot
admission, retaining the spacing and parallel-duration assertions. The
mutation witness still requires removing the client delay to fail. No
production delay or networking policy was weakened.

## Packaged artifacts and fresh installation

Build: `env UV_CACHE_DIR=/tmp/chimera-c0-uv-cache uv build --offline --no-sources --out-dir dist/c2-html`.

| artifact | SHA-256 |
|---|---|
| `taipan_chimera-0.1.0-py3-none-any.whl` | `ce98a4328d7c757bcf4d443816a9c11f1ca2255d4a418b52b9c2a569bca5a4da` |
| `taipan_chimera-0.1.0.tar.gz` | `17ba31204a91a2833dbd5905e77152c76afe593f0d502b9d7a432dcb40b6822d` |

The fresh owned environment is `/tmp/chimera-c2-html-wheel-20261006`.
An index-resolving offline install of the wheel's `html` extra initially
refused a cached Lingua dependency. Installation using the exact lock's URLs
with `uv sync --offline --locked --extra html --no-dev` succeeded; installing
the built wheel with `uv pip install --offline --no-deps` then replaced the
editable package. Dependencies stayed pinned; no registry fallback was needed.

Acceptance ran from `/tmp` with `PYTHONPATH` unset using
`/tmp/chimera-c2-html-wheel-20261006/bin/python`, Python **3.11.16**. The
import resolved to
`/tmp/chimera-c2-html-wheel-20261006/lib64/python3.11/site-packages/chimera/__init__.py`,
not the source checkout. A native English fixture retained its title, prose,
language and document link and validated source/text hashes through the
actual child parser. Parser revision:
`scrapling@0.4.2+crawl4ai@0.9.4+lingua@2.1.1`.

## Real public-page acceptance

Using that same installed-wheel interpreter, `CurlRoute` + `FetchLadder`
retrieved the public Tor Project blog root and followed one URL actually
returned by its extracted links. Scope was exactly `blog.torproject.org`,
HTTPS port 443, depth 1; budgets were 5 requests, 2 MiB and 90 seconds.
TLS verification and `robots=honor` remained enabled. No credentials, login,
challenge-solving, browser or external model were used. This run used the
direct connector: it is not evidence of browser/Tor leak protection.

| retained source | raw bytes | native extracted characters | language |
|---|---:|---:|---|
| `https://blog.torproject.org/` | 16,288 | 268 | `en` |
| `https://blog.torproject.org/arti_2_7_0_released/` | 11,404 | 1,740 | `en` |

On `/tmp/chimera-c2-html-wheel-20261006/bin/python` 3.11.16, the second
page's fetch plus extraction took **4.525 seconds**. Total accounted work
was **3 requests / 27,746 bytes**, including robots. This is one functional
measurement, not a throughput benchmark or hardware-independent speed claim.
The article title was `Arti 2.7.0 released | Tor Project`; its extracted
body supplied 7 safe link candidates. The index supplied 4 same-host candidates.

Source/text bindings:

| source | raw SHA-256 | native-text SHA-256 |
|---|---|---|
| blog root | `171d8b6467344a74deedf14c3d5bf981ad33a304577a06abf682085372c62e0a` | `86b4876be79a11bb35854b813ef02c98a06c10c76820e654fca283dab2de7da4` |
| discovered article | `18301db90105fd2f87d620f50bf022e9238bcfb64f54e2801d75667c80fbe5f2` | `fd75589925dec848a8840381f3622ccd23bbcbd2ec27db2a261abfbc9af4cf18` |

Owned parser state: `/tmp/chimera-wheel-public-t_a_6e8a`. Documents were
checked in memory; this is not a governed published corpus or durable final
research answer. Two initial acceptance harness invocations refused before
source I/O because required budget-clock/scope-depth constructor arguments
were omitted; the corrected invocation supplied both. No package workaround
or source modification was needed.

## Still required for the full goal

PDF/DOCX conversion and Marker fallback, canonical/near-dedup policy,
drift diagnostics and archived-publisher-pair acceptance, the browser ladder
with complete Tor-route enforcement, real encoder/scoring, real served-model
intent research, TAIPAN's governed seam and C5 live acceptance remain open.
The passive parser audit guard is not an OS sandbox or an anonymity claim.
