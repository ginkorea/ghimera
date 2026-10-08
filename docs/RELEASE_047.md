# Ghimera 0.4.7 release acceptance

Status: **PUBLISHED AND ORIGINAL ARTIFACTS VERIFIED**.
Public 0.4.6 identities remain immutable. Both original official 0.4.7 artifacts
matched the independently accepted local files byte for byte. This increment
does not close the complete infrastructure PRD or claim an unattended deployment.

## Gated source

Source commit `9078cc4d8a8feb71821498a3bd47bbbd8bbccae1`, tree
`c9de7fa174a7f0ccf13fd0fbcfefb71635e16603`, combines web/local capture, retained
admission, durable web intents and atomic pending local batches. The exact frozen
full `scripts/gate.sh` returned 1,186 passed in 1433.41 seconds, zero failures
or skips. Python 3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python` imported
`/tmp/ghimera-recovery-release-20261008/src/ghimera/__init__.py`. Offline lock
(142 packages), Ruff, formatting (263 files) and strict typing (173 source files)
also passed. The gated tree was verified unchanged before committing it.

Release commit `83cce0ff475cae9f2c84a36ee0d6988026d6aa10` changes only the version,
its assertion and documentation. Production source and examples were verified
unchanged from the complete gated source. Its five metadata checks passed with
no skips under the same Python 3.11.16 interpreter. This evidence follow-up does
not move the immutable release tag or rebuild a published artifact.

## Independent artifact acceptance and publication

- The wheel was built offline from its source archive. Complete inspection
  compared 173 wheel package members and 441 tracked source-archive members,
  checked source-byte equality and rejected untracked archive content. Both
  Twine checks passed under the private Python 3.11.16 publication environment.
- The real wheel installed into an independent Python 3.11.16 environment,
  `wheel-recovery-env/bin/python`, resolving its own installed site-packages,
  never an editable checkout. The standalone API and both CLI entry points
  passed; the downstream platform SDK was absent by design.
- Native local capture parsed two owned DOCX fixtures, retaining 73,068 bytes
  and exact original/result bindings. Fresh-process readback preserved the
  immutable original store. The resume check deliberately refused its first
  parser, captured a quiescent checkpoint, removed only that pinned generated
  input, and used a fresh process to parse the pending second document without
  reopening the handled file. Actual byte spend and terminal operation states
  survived. These controlled English fixtures used explicit judge/scorer doubles,
  not a live model or a measurement of research accuracy.
- Both retained public RSS observations, including the 304-backed original,
  reproduced 50 entries/links, exact text/language and original-source hash
  through the installed native worker, with zero new source or model calls.
- Retained-native and reviewed-PDF fixtures passed original/citation/planning
  bindings, graph replay (seven and six nodes), journal-summary/2, SQLite/FAISS
  passage readback, destination acknowledgement and exact result decoding.
  No source, encoder, transcriber or reviewer was called by this readback.
- One operator resume probe initially checked a checkpoint after `finish`
  closed the session; moving that check before finish and preparing fresh inputs
  resolved it without changing package source. The first restricted-shell graph
  readback timed out at 180 seconds. Approved native local-only execution passed
  the same fixture readback; neither failed attempt is counted as acceptance.
- GitHub main was atomically fast-forwarded to the exact release commit.
  Annotated `v0.4.7`, object `456eca738341b27f99e979ed0cde89d4234787f1`,
  peels to that commit; both remote refs were independently read back.
- Only the two hash-pinned accepted artifacts were uploaded to the exact official
  `https://upload.pypi.org/legacy/` destination using the existing named profile.
  TLS was verified, redirects refused, and credentials remained in memory without
  logging or copying. Official public metadata and both original files were
  independently read back: exact local-byte equality and no yanks.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `ghimera-0.4.7-py3-none-any.whl` | 404376 | `3937fef2f1b932d45a90ec0703bce4f3611c329942c47052fcafc677d69b8b1c` |
| `ghimera-0.4.7.tar.gz` | 1157990 | `35136187607bd37d60dd922f9a9d321307256aa33007e5a2097d7bdda4250fe4` |

This is independent package publication, not an unattended service deployment.

## Capability increment

Explicit source-work configuration adds a run-owned private durable operation
store beside the existing journal. Fresh web and owned local acquisition retain
exact request pins, original bytes and acknowledged processing results. Retained
corpus admission records its historical original capsule before current graph or
model work; it does not fabricate a fresh fetch or replay old spend.

The optional frontier captures web intents and complete caller local batches
before first source I/O. Local batches commit atomically; private paths do not
become public provenance or web authority. Quiescent continuation checks exact
pending order/pins and reads only never-started local files. Lost acknowledgement
remains unresolved rather than becoming a false zero-byte success or an implicit
retry. Repeated cancellation drains the bounded owned local read.

These are recovery primitives, not automatic whole-research crash adoption.
Unknown model/graph acknowledgements, research control state before a completed
round, retained-reader reservation and full spend reconciliation remain required.

## Chinese OCR evidence

The existing pinned RapidOCR control remains unvalidated for full transcription.
On unchanged Simplified Chinese raster-PDF bytes, explicitly configured scales
2 and 4 produce the same output as prior scale3: `部门` is read as `部内` even
though `台湾` and `码头` are recovered. The PDF digest remains
`eedb338d1d2733bdb51e4f6af6d13a78512d315cb02ca82a837a9d748c9b92dc`; the pinned
artifact-manifest digest remains
`b5870ec8ca436990007331073af336da957fc1ddde966f0e4dbb826349580fef`.

Actual runs used Python 3.11.16 in the private document worker with the exact
candidate import, two declared CPU threads and one parser slot. They took 36.907
and 39.310 seconds including worker startup. Both extracted texts, effective
recipes and parser receipts were retained. No weights, GPU or model service was
used. This rules out the tested scale-only repair; it is not a representative
language accuracy or throughput claim. Qwen-VL is a candidate for the already
published configurable transcription boundary, not an accepted replacement.

## Remaining complete infrastructure scope

The complete I01–I14 tracker remains authoritative. Required remaining work
includes uncertain-call and research recovery, unattended service/API and scale,
freshness/hybrid reranking, validated semantic organization extraction, reversible
temporal identity resolution, representative entitled publishers and browser/onion
workflows, PDF figure crops and visual graph/answer integration, remote delivery
and retention, persistent feed/API/citation refresh, and actual multilingual
served-model quality. The separately hosted Ahmia index remains owner-deferred.

An incremental package release does not close those requirements. Offline
judge/scorer/model fixtures establish protocol behavior, not real-model accuracy.
