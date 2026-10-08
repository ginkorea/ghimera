# Ghimera 0.4.6 release acceptance

Status: RELEASE CANDIDATE, not yet published. Public 0.4.5 identities and
artifacts remain unchanged. This increment does not close the full
infrastructure PRD.

## Gated source

Source candidate `04d1ac34843c6f4216b3e8156a924fc3ab797133` combines retained
original context/research/native-PDF graph integration with passive source feeds.
Its frozen full `scripts/gate.sh` passed 1,105 tests in 1221.67 seconds, zero
failures/skips, exit zero. Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python` imported its isolated source tree.
Ruff/check/format (254 files), strict mypy (169 source files) and the unchanged
142-package offline lock also passed. No gated input changed during execution.

This release changes version metadata, its exact test assertion and documentation
only. Production modules and examples must remain byte-identical to that candidate.
Independent wheel installation and publication readback are separate checks.

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

Pending: offline build, exact tracked archive bytes, independent installed API,
feed-original/parser and retained native/PDF graph/journal/corpus/delivery
readback; new GitHub tag/ref verification; official PyPI upload and original
public artifact byte equality. No package is described as published before
these observations exist.

Release metadata tests passed five checks with no skips under the same Python
3.11.16 interpreter, importing the isolated release tree. The offline lock
validated its unchanged 142-package closure. A Git source/example comparison
against the complete gated candidate is empty; no production module or example
changed for release metadata.

## Remaining required scope

Operation-level crash/uncertain-call reconciliation, retained-source freshness
and reranking, semantic organization/identity quality, representative entitled
publisher and browser/onion workflows, visual citations/graph and PDF figure
crops, remote retention/delivery, unattended service/API, persistent connector
refresh and representative multilingual/model quality remain required.
Simplified Chinese OCR quality remains unaccepted. Existing protocol fixtures
and model cards do not substitute for recognition or research accuracy.
