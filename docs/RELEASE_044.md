# Ghimera 0.4.4 release acceptance

Status: **PUBLISHED; ORIGINAL PUBLIC ARTIFACT BYTES VERIFIED**.
No existing tag or public artifact is changed.

## Scope and complete gate

The release adds explicit caller-Page guarded redirect admission, full-collector
budget/robots/cadence integration, native chain replay and human continuation;
configured delivery-worker lifecycle and readback-gated cleanup; and the explicit
Chinese RapidOCR example. See BROWSER_NAVIGATION_GUARD.md, DELIVERY_OUTBOX.md and
PACIFIC_PDF_CANDIDATE.md. Source is not deployed service or measured model quality.

Frozen source `73129ea1e4c22da76f95b1430a77db9040237874` passed the complete
`scripts/gate.sh`: **999 passed in 980.63 seconds**, zero failures/skips, exit zero.
Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python` imported
`/tmp/ghimera-browser-redirects-20261007/src/ghimera`; no platform SDK resolves.
Offline lock resolved 142 packages; Ruff check and formatting passed 228 files;
strict mypy passed 152 source files. All gated inputs stayed frozen. Operator
records are retained under `ghimera-guarded-collection-3Jm4VF/full-gate`.

After that gate, only release metadata, its exact-version assertion and
documentation change. Production code, all other tests and examples must remain
byte-identical to the gated source, verified before the release commit.

The release metadata/README checks passed five tests in 1.46 seconds under the
same Python 3.11.16 source interpreter, zero failures/skips. Offline lock check
passed. The only test change is the exact release-version assertion; production
source and examples match `73129ea` unchanged.

## Independent development artifact acceptance

A private development wheel from the gated source was installed offline into
`ghimera-guarded-collection-3Jm4VF/wheel-env` under Python 3.11.16. Imports came
from installed site-packages, not an editable checkout. Both CLIs and the public
API passed; five guarded native archives and three legacy archives replayed
original hashes, actual parser evidence, citations, graph and source chains.
Fixture search/model replies are not real-model accuracy evidence.

The same installed candidate captured W3C's public dummy PDF through actual
Chromium and FetchLadder with robots honored, normal TLS and no credentials,
login, assistance or inference. Original bytes: 13,264; SHA-256
`3df79d34abbca99308e79cb94461c1893582604d68329a41fd4bec1885e6adb4`.
Three known attempts and 17,108 collector bytes reconcile with the ledger.
The single native hop retained the exact requested/final URL. Browser-wide
unknown traffic is not represented as zero. This is one public sample, not
representative redirected publishers, entitled accounts or Tor acceptance.

The final 0.4.4 wheel/sdist still require tracked-byte archive inspection, metadata
validation, independent installed acceptance, an immutable new tag and official
PyPI publication followed by TLS-verified, redirect-refusing original artifact
readback and byte equality. The private candidate's 0.4.3 filenames must NEVER
be uploaded or substituted for public 0.4.3.

## Remaining full-goal requirements

The infrastructure PRD remains active: operation-level crash recovery, cached
answer reuse/reranking, reliable multilingual organization extraction, reversible
entity identity, pagination/publisher/Tor workflows, PDF figures and visual graph/
answer integration, remote delivery/retention and unattended service deployment,
incremental connectors and representative multilingual/onion model-quality
acceptance. Required-term Chinese OCR success is not full transcription support;
Qwen-VL remains a researched candidate, not an installed or accepted recognizer.

## Recorded publication

GitHub main fast-forwarded from `b7aefd1` to release source
`8cc8e980db7f1e2efd0542fae9893b1e6b91a301`. New annotated tag `v0.4.4` is
`5ead79b67217ae3e8d7587472b646471c2299a7d`, peeling to that release source.
Independent remote reads confirmed both. The owner's dirty checkout and its
local main were not changed; all previous published identities stayed fixed.

Final wheel and sdist were built offline from clean committed release source.
Tracked-byte inspection compared 152 wheel source members and 392 sdist members;
no untracked content, private stores, weights or Git metadata entered either.
Twine 6.2.0 metadata validation passed under the private tool Python 3.11.16.
The final wheel was installed offline into the independent wheel environment
above and passed API/both CLIs plus all five guarded and three legacy native
archive readbacks again, with imports from installed site-packages.

| Original public artifact | Bytes | SHA-256 |
|---|---:|---|
| `ghimera-0.4.4-py3-none-any.whl` | 353,208 | `84576c90d5d04ea6d6c060801ef7933c143e766259c0ca330ca83131e2ff8c2f` |
| `ghimera-0.4.4.tar.gz` | 1,056,036 | `6a1a3967bf60917df9d23237ee23e1fef60cc263aefc20bff0d5b5f68891c0ed` |

Only those exact files were uploaded through the named official PyPI profile.
Unauthenticated metadata readback at `pypi.org` and original file readback at
`files.pythonhosted.org`, normal TLS and redirects refused, proved exact identity,
sizes, SHA-256, no yanks and byte equality. The readback used private tool Python
3.11.16 and retained its script under `ghimera-guarded-collection-3Jm4VF`.
Public release: https://pypi.org/project/ghimera/0.4.4/.

This publication record is a later documentation-only commit. It does not
rewrite the immutable release tag, files or their earlier acceptance record.
