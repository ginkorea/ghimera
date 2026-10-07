# Ghimera 0.4.5 release acceptance

Status: **RELEASE PREPARATION; NOT PUBLISHED**. Existing published identities
remain unchanged. Public PyPI metadata was read without credentials, with TLS
verification and redirects refused: 0.4.4 is current and 0.4.5 does not exist.

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

Release preparation changes only metadata, its exact version assertion and
documentation. Production modules and examples must remain byte-identical to
the combined-gated source. No merge, tag, package upload or platform deployment
is implied by preparation.

## Required artifact acceptance

After the combined source gate terminates successfully: verify metadata and
lock consistency; inspect wheel/sdist membership against tracked source bytes;
install the exact candidate independently; verify public API, CLIs, original
PDF/reading, citation, graph and corpus replay. Then create a new immutable tag,
publish the exact artifacts and read their original public bytes back over
TLS with redirects refused. Record hashes, sizes and observed revisions here.

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
