# Binary-PDF download admission evidence

Status: unreleased source candidate. This closes explicit MIME admission, not
the complete organizational research workflow or predictable parser latency.

## Real primary source

On 6 October 2026 the laptop's native `CurlRoute`/`FetchLadder` retrieved
[the official Chinese constitution PDF](https://download.12371.cn/wenjian/2022/11/1/djcbesddz.pdf)
with system DNS, verified TLS, guarded redirects and honored robots. No fixture
resolver, account session, model call or GPU was used. The diagnostic explicitly
allowed `application/pdf` and `application/octet-stream` in its source scope.
The publisher returned status **200**, declared **`application/octet-stream`**
and **520,791 bytes** beginning with `%PDF-`. The retained bytes have SHA-256
`0b75acd4280e0b331ac479996411b0dd652350f8fce903d117fcb942028167cc`.
The run charged **two fetches**, robots plus source, and took **15.593748 seconds**
on `/tmp/chimera-c0-20261006/.venv/bin/python` **3.11.16** importing
`/tmp/chimera-c0-20261006/src/ghimera/__init__.py`. TAIPAN resolves none in this
standalone environment; its doctor/floor checks are not applicable.

The old parser returned `content_type_unwanted`. A `.pdf` suffix was not enough
to bypass that boundary; the new behavior instead requires a version-2 recipe,
an explicit selected binary MIME type, original PDF header and actual parsing.
Neither `Page.content_type` nor original bytes were rewritten.

Private local observations (not committed source data):

- `gate-work/organization-pdf-live-mime-01/`: original `source.bin`, `page.json`,
  native fetch ledger and summary.
- `gate-work/organization-pdf-native-acceptance-01/` and `-02/`: identical
  effective parser recipes from the two failed direct acceptance attempts.
- `gate-work/organization-pdf-worker-diagnostic-01/`: bounded diagnostic worker
  output, diagnostic stream and summary.

These are local evidence paths, not a shipped corpus or a durable external
artifact registry. The constitutional text is not a visual organization chart.

## Native conversion and unresolved timing

The actual offline pinned worker produced **47 pages**, **24,400 native text
characters**, language **`zh`**, and version-2 parse/media records binding the
same source hash and original MIME. Reader validation confirmed exact native
terms `中国共产党`, `中央委员会` and `中央军事委员会`. These observations used the
same Python **3.11.16** and owned source above, with Docling **2.134.0**,
Docling-core **2.99.0** and Lingua **2.1.1**. No OCR or translation was applied.

However, the two direct acceptance-harness runs each reached their configured
**120-second** parser deadline. The diagnostic worker completed in **4.825744
seconds**; separate task-observed runs through the unchanged production
`DocumentExtractor` also returned the full parse. This inconsistency is **not
explained or fixed**, and the diagnostic is not substituted for stable end-to-end
acceptance. No higher deadline, automatic retry or performance claim was added
to conceal it. Preserve these failures when investigating the worker/harness
wait state. The configured resource bounds remain enforced.

The text checks establish neither entity completeness, hierarchy accuracy,
diagram topology nor independent source entailment. The remaining organizational
acceptance is described in [ORGANIZATION_RESEARCH.md](ORGANIZATION_RESEARCH.md).

## Regression and compatibility boundary

The initial tests-first check refused to collect because the media-policy module
did not yet exist. The implementation then passed **11 checks, no failures or
skips**, on the same owned Python **3.11.16**. The checks use actual Docling on
controlled PDF bytes, not a converter-shaped double; their collection judge and
one source route are explicitly doubles. They are contract checks, not model
accuracy or live browser/solver acceptance.

Checks reject unconfigured generic MIME types, HTML/executable/padded-header or
ZIP content, legacy recipe expansion, altered source/policy digests and forged
resolution methods. The original URL can end in `.docx` while the resolved
actual bytes are PDF: filenames do not select the parser. Existing `/1`
configuration and receipt serialization omit the absent media field; the
published native recipe's digest is covered by its existing regression check.
The composed Collector checks its research MIME admission before any I/O.

The importer check passed **51 tests, zero failed, zero skipped**, in
**104.46 seconds** on the same owned Python **3.11.16**. The full unchanged
candidate's `scripts/gate.sh` then passed **469 tests, zero failed, zero skipped**,
in **499.43 seconds**, using `/tmp/chimera-c0-20261006/.venv/bin/python`
**3.11.16** importing `/tmp/chimera-c0-20261006/src/ghimera/__init__.py`.
The offline lock resolved **137 packages**; lint and formatting passed for
**127 files**, strict typing for **89 source files**. Local browser/network
fixtures were exercised; they are not live CAPTCHA/provider acceptance.
Nothing in this evidence declares a new package release, deployment, general
PDF quality acceptance or universal challenge bypass.
