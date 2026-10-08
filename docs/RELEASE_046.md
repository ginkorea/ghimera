# Ghimera 0.4.6 release acceptance

Status: **PUBLISHED AND ORIGINAL ARTIFACTS VERIFIED**. Public 0.4.5 identities
and artifacts remain unchanged. The original official 0.4.6 files matched the
independently validated local artifacts exactly. This increment does not close
the full infrastructure PRD.

## Gated source

Source candidate `04d1ac34843c6f4216b3e8156a924fc3ab797133` combines retained
original context/research/native-PDF graph integration with passive source feeds.
Its frozen full `scripts/gate.sh` passed 1,105 tests in 1221.67 seconds, zero
failures/skips, exit zero. Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python` imported its isolated source tree.
Ruff/check/format (254 files), strict mypy (169 source files) and the unchanged
142-package offline lock also passed. No gated input changed during execution.

Release commit `528ade0bee693c33dff0c8405c9d7d589373fcd8` changes version
metadata, its exact test assertion and documentation only. Production modules
and examples were verified byte-identical to that candidate. This evidence
follow-up does not move the immutable tag or replace any published artifact.

## Real public-source observations

The normal robots-aware fetch/parser path captured
`https://blog.python.org/rss.xml`, application/xml: 25,093 bytes, 50 RSS entries,
50 selected links and no omissions. Original SHA-256:
`d4ef47aded86d7c08afbf3210a2221b2099a92d3681d26373bdbc26279959c3c`.
An immediate conditional request returned 304 and reused those exact bytes;
accounted bytes remained 46,121 including robots. No linked article or model
service was contacted.

The same source passed through an explicitly configured, already-running local
Tor SOCKS listener. Retained transport evidence identifies the Tor route,
remote-pinned target DNS and `tor-authenticated-stream/1`; original bytes/hash
matched direct capture. This is native HTTP open-web-over-Tor acceptance, not
an anonymity guarantee, verified browser subresources or an onion investigation.
The first attempted legacy Python URL returned 404 and was correctly refused;
it was not counted as a successful source capture.

## Artifact acceptance and publication

Release metadata tests passed five checks with no skips under the same Python
3.11.16 interpreter, importing the isolated release tree. The offline lock
validated its unchanged 142-package closure. A Git source/example comparison
against the complete gated candidate is empty; no production module or example
changed for release metadata.

- Offline wheel built from its source archive. Inspection matched all 169
  tracked wheel package members and 428 tracked source-archive members, with
  complete package closure and no untracked archive content. Both Twine checks
  passed under the private Python 3.11.16 publication environment.
- The exact wheel installed into a fresh Python 3.11.16 virtual environment,
  `wheel-046-env/bin/python`, and resolved its installed site-packages, not an
  editable checkout. The standalone API and both command entry points passed.
- That installed interpreter re-parsed both retained public RSS observations,
  including the 304-backed original. It used its installed passive worker,
  preserved all 50 entries/links, exact reading and raw-source hash, and made
  zero new source or model calls.
- Installed retained-native and reviewed-PDF fixtures passed exact original/
  citation/planning bindings, durable graph replay, journal-summary/2,
  SQLite/FAISS corpus passage readback, destination acknowledgement and independent
  result decode. The fixtures retained seven and six graph nodes respectively
  and made zero source/model calls. Scripted model observations establish
  persistence/protocol compatibility, not real-model accuracy.
- GitHub main was atomically fast-forwarded to the exact release commit.
  Annotated tag `v0.4.6`, object `dc7630860f81de3cf52726a9dbf2b1dedafbe568`,
  peels to that commit; both public refs were independently verified.
- Only the two hash-checked artifacts were uploaded to
  `https://upload.pypi.org/legacy/` with the existing named publication profile.
  TLS verification and redirect refusal were retained; credentials stayed in
  memory without logging or copying. Official unauthenticated metadata and
  both original artifact files were read back and matched exactly, with no yanks.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `ghimera-0.4.6-py3-none-any.whl` | 389864 | `36f6bdddcae3596ea84f160775a11a19793eae5981e931c16cf0aa06ca7f68ef` |
| `ghimera-0.4.6.tar.gz` | 1120285 | `9b5159525ab0356b8f37e066ac13e4d18983cf4b9f11f5334a022d61d14b54f8` |

This is standalone package publication, not an unattended service deployment.

## Remaining required scope

Operation-level crash/uncertain-call reconciliation, retained-source freshness
and reranking, semantic organization/identity quality, representative entitled
publisher and browser/onion workflows, visual citations/graph and PDF figure
crops, remote retention/delivery, unattended service/API, persistent connector
refresh and representative multilingual/model quality remain required.
Simplified Chinese OCR quality remains unaccepted. Existing protocol fixtures
and model cards do not substitute for recognition or research accuracy.
