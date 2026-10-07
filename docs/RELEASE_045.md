# Ghimera 0.4.5 release acceptance

Status: **PUBLISHED AND ORIGINAL ARTIFACTS VERIFIED**. Existing published
identities remain unchanged. Public PyPI metadata and both original 0.4.5
artifact files were read without credentials, with TLS verification and
redirects refused, and matched the independently validated local files exactly.

## Scope and source

Production source candidate `e0696bd802a6b542460eeb72de133509b0b3e704` contains
the configured PDF page transcription/review integration, corpus provenance and
graph/planning provenance. Its full combined gate passed: 1,031 tests in
1085.10 seconds, zero failures/skips, exit zero. Ruff, formatting (238 files),
strict mypy (158 source files) and offline lock validation (142 packages) passed.
Python 3.11.16 imported the isolated candidate, and all gated source stayed
frozen through termination. The graph/journal/resume/native-input selection passed 109
tests, no failures/skips, under Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing the isolated candidate.
That selection is not a substitute for the combined gate.

Release commit `0d994e783a4351008273baa29daf8f14341194d7` changes only metadata,
its exact version assertion and documentation from that candidate. Production
modules and examples are byte-identical to the combined-gated source. This
post-publication evidence update does not change the immutable tag or artifacts.

## Completed artifact acceptance

- Metadata tests: five passed, no skips, using Python 3.11.16 at
  `/tmp/chimera-c0-20261006/.venv/bin/python` with the isolated release source.
  The unchanged 142-package offline lock validated.
- Offline wheel built from the source archive; archive inspection matched
  tracked bytes for 158 wheel source members and 406 source-archive members.
  Complete tracked Python-module closure was verified; both Twine checks passed.
- A fresh independent wheel environment, Python 3.11.16 at
  `wheel-045-env/bin/python`, imported its installed site-packages, not an
  editable checkout. Both public CLIs and the standalone API passed.
- A controlled native PDF passed fresh installed rendering pixel equality,
  preserved original/native/generated reading checks, exact page citations,
  durable semantic graph replay and SQLite/FAISS corpus passage readback.
  Transcription/review and embedding replies were scripted fixtures, not live
  model-quality evidence. No external model was called.
- That same installed interpreter replayed five guarded browser archives and
  three legacy native browser archives with original, parser, citation and
  graph bindings intact. The initial operator invocation supplied a result file
  rather than its owning archive directory; correcting the invocation passed
  without changing package code.
- GitHub main was atomically fast-forwarded to the release commit. Annotated
  tag `v0.4.5`, object `b5f4a12293211dafd04361209c6e721b0ed36840`, peels to
  that exact commit; both remote refs were independently read back.
- Only the two validated artifacts were uploaded to
  `https://upload.pypi.org/legacy/`. The initial explicit-URL invocation bypassed
  Twine's configured credentials and exited before uploading either artifact.
  The successful attempt used the existing named profile, asserted its exact
  destination in memory, verified TLS and refused upload redirects. Credentials
  were not printed or copied.
- Official public metadata identified exactly these two non-yanked files;
  original public file bytes matched their local counterparts exactly:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `ghimera-0.4.5-py3-none-any.whl` | 367530 | `c24005e50a117dd773d7d633f960b28a4e0d1f4635354aa3b723b86c341e3bf5` |
| `ghimera-0.4.5.tar.gz` | 1079288 | `80f6d1ed86a1e5d083374080b8bfa0dec44a82706e20c6a620cc1fd370b37340` |

This is standalone package publication, not a platform deployment or a claim
that the complete infrastructure PRD has passed representative acceptance.

## Quality and full-goal gaps

Scripted completions exercise accounting, rejection, source binding and durable
readback. They do not prove Chinese recognition accuracy. Simplified Chinese
still has a wrong character in the existing native OCR control; real admitted
Qwen transcription and independent review have not been exercised. No model
weights, private service or hardware allocation is installed by this release.

The full infrastructure PRD remains open: operation-level crash reconciliation,
cached evidence answer reuse/freshness/reranking, semantic organization and
identity quality, representative browser/Tor/publisher workflows, visual graph
integration, remote delivery/retention, unattended service/API, incremental
connectors and representative multilingual/onion acceptance. An OCR integration
release does not close those requirements.
