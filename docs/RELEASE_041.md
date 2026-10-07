# ghimera 0.4.1 release acceptance

Status: **SOURCE AND INSTALLED ARTIFACT VERIFIED**, not yet published. Package
metadata and the lock name 0.4.1. The combined full gate and independent wheel
checks passed; public publication/readback are still required. Existing 0.4.0
artifacts/tags remain immutable.

## Scope

- Configurable concurrent source/stage execution and backpressure.
- Explicit Pacific scanned-PDF OCR engine/pack recipe; the controlled Simplified
  Chinese title-recognition failure remains recorded, not declared fixed.
- Owner-private native evidence corpus, actual SQLite/compiled FAISS retrieval,
  exact source/model audit and automatically composed completed-result handoff.
- Passive responsive infographic intake tied to retained source markup and
  explicit image policy; decorative admission still precedes downloading.
- Configured corpus-backed discovery through the existing search interface,
  retaining original documents, native passages and actual query observations.
- Durable completed-result outbox, bounded concurrent idempotent delivery,
  readback-before-acknowledgement and explicitly gated local-payload pruning.

All operational values remain typed configuration or injected collaborators.
No external service, model, credentials, source entitlement or hardware is
implicitly created. The generic destination port includes a real local durable
implementation, not a claim of off-host publication.

## Evidence available before the combined candidate gate

The frozen discovery line `0ab1443` passed its complete `scripts/gate.sh`:
906 passed in 785.14 seconds, no failures or skips. Interpreter:
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16; source import:
`/tmp/ghimera-corpus-leads-20261007/src/ghimera`. Ruff/format passed over 203
files, strict mypy over 135 source files, and the offline lock checked 142
packages. Delivery changes and the version bump were not in that gate.

The affected outbox/storage/corpus/collector/continuation selection returned
89 passed in 104.96 seconds, no failures or skips, on the same interpreter
importing `/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. It used real
private SQLite/FAISS, a fresh-process dispatcher and credential-free loopback
model/search/source fixtures. Its first expanded run failed one OCR fixture
because its invocation omitted the installed Tesseract path; that failed run
is retained in [delivery evidence](DELIVERY_OUTBOX.md), not relabeled green.

The final delivery/private-storage module selection returned 21 passed in 9.68
seconds on that same Python 3.11.16 interpreter and delivery-outbox checkout,
no failures or skips, including the destination-loss pruning refusal. The final
versioned source still needs its combined gate. Do not infer real Pacific
OCR/semantic/retrieval accuracy from deterministic fixture replies.

## Combined candidate and independent artifact observations

The complete gate at `348a522` returned **926 passed, one failed in 813.85
seconds**, no skips, on Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. Ruff/format and strict mypy
passed. The failure was the release metadata witness still asserting 0.4.0
against the declared 0.4.1 candidate. Its assertion is corrected to the exact
new release; no production behavior or other acceptance assertion changed.
A new combined gate is required before publication.

The first built candidate wheel/source archive passed Twine metadata checks
and archive-member inspection (no private stores, weights, environment or Git
metadata). The exact wheel installed with `images,corpus` extras into the
task-owned release `wheel-env`; its Python 3.11.16 import resolves installed
`site-packages/ghimera`, not a source/editable path. These initial build checks
are not a full gate, public readback or multilingual quality acceptance.

Independent installed-wheel acceptance passed under Python 3.11.16 at
`/home/gompert/data/workspace/TAIPAN/.codex-tmp/ghimera-041-release-Rr9eq6/wheel-env/bin/python`.
The import resolved its `lib64/python3.11/site-packages/ghimera` tree with no
`PYTHONPATH` or editable checkout. The installed CLI ran; a sealed research
fixture retained its one document and cited claims; two original corpus
documents reopened with their exact native identities, using the actual pinned
compiled FAISS backend admission. A completed result was queued, reopened,
dispatched to the real local SQLite destination, read back and explicitly
pruned only after acknowledgement. The destination copy survived pruning;
reenqueueing preserved the acknowledged deduplication tombstone. No model
endpoint, credentials or production service was used. These are controlled
artifact/durability checks, not representative model quality.

The first sandbox smoke was interrupted after making its private fixture
stores. A native repetition refused because those stores already existed;
they were retained. The successful bounded native run used fresh explicitly
named fixture stores, not destructive cleanup or weaker storage admission.
Its result SHA-256 was
`359c6c5e856ebcbdbc01f9590f089845849c97bdaf9267775df2627a2cc24991`.
The initial wheel SHA-256 was
`bfcbe84e62fbfe7dc500dee062440ae38d9d2aac924177b3bdcf75cba0556506`;
the initial source archive SHA-256 was
`bddc60189ff10a2f80fd04ddccfa93337349327ad64a885d95abc6578f17719b`.
The final source archive must be rebuilt after adding this evidence, and the
final wheel's equality/acceptance checked before publication.

## Publication requirements

The corrected frozen source `7de2be3ae85b3923fe7fac277e2f759e807f7645`
passed the entire `scripts/gate.sh`: **927 passed in 796.73 seconds**, exit zero,
no failures/skips. Python 3.11.16:
`/tmp/chimera-c0-20261006/.venv/bin/python`; import:
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. Ruff/format passed across
214 files; strict mypy passed across 144 source files; offline lock checked
142 packages. No source/tests/examples/metadata changed during the run. This
evidence update changes documentation only. Final archive inspection must verify
tracked contents against the tagged checkout and unchanged production modules
against that gated source. The wheel must remain byte-identical to the
independently accepted wheel above.

The pre-publication read of GitHub found main still at `3e193f55` and no
v0.4.1 tag; official PyPI's version-specific metadata returned HTTP 404.
Those checks are not publication evidence.

Commit the exact candidate, run `scripts/gate.sh` with the declared installed
fixtures, then build a wheel/source archive. Inspect metadata/contents and
install the exact wheel without an editable import path in an isolated owned
environment. Exercise the public API and command, native corpus readback and
delivery/restart behavior. Record hashes, push a new immutable tag, upload those
same files, and compare downloaded public bytes. Publication remains unproven
until those observations exist.

## Remaining infrastructure work

The [PRD tracker](PRD_INFRASTRUCTURE.md) remains authoritative. Operation-level
crash frontier, direct cached-source answer reuse/hybrid ranking, entity identity
resolution, browser/download/Tor representative acceptance, maintained Ahmia
service deployment, PDF-figure/visual-answer/graph integration, automatic
retention/compaction and remote delivery, unattended service/health/API,
connectors/refresh, and representative multilingual research quality are not
closed by this incremental release. Native-script originals and evidence offsets
remain mandatory; translation is not a substitute for stage-specific quality.
