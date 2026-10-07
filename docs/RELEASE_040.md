# ghimera 0.4.0 release acceptance

Status: release candidate; final combined source gate and independent wheel checks green.
No publication is implied by this document until its terminal evidence is added.

## Scope

The Ahmia development line includes all commits of the discovery/reference
expansion line. This release brings both into the standalone `ghimera`
distribution: MCP and routed discovery, the configured Ahmia index adapter,
same-browser human assistance, local PDF/DOCX intake, explicit challenge-service
dialects, completed-round resume, and source-bound semantic/graph research.
It also adds configured origin jitter/cooldowns and selective image evidence,
offline Pacific-language OCR and optional separately reviewed local vision.
See [changelog](../CHANGELOG.md) for the versioned additions.

Legacy data schemas and prompt identities are preserved. Existing 0.3.0
artifacts and tags are immutable. Python remains **3.11+**. External model,
Tor/browser and Ahmia services are operator-provided, not implicitly deployed.

## Gates and artifacts

The earlier complete `scripts/gate.sh` ran on frozen source/tests at `9f104ef`
using `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, importing
`/tmp/ghimera-ahmia-20261007/src/ghimera/__init__.py`. Offline lock checked
137 packages; Ruff/format passed over 171 files; strict mypy passed over
113 source files. The entire pytest suite returned **794 passed in 677.03
seconds**, no failures or skips reported. Failed earlier gates remain documented
in [Ahmia integration](AHMIA_INTEGRATION.md), not relabeled green.

The final combined gate ran on frozen source/tests/examples at `586bb6b`, using
that same Python **3.11.16** interpreter and importing
`/tmp/ghimera-cadence-20261007/src/ghimera/__init__.py`. Offline lock checked
137 packages, Ruff/format passed over 181 files, strict mypy passed over
121 source files, and the full suite returned **825 passed in 688.75 seconds**,
no failures or skips reported. The preceding combined run had 822 passes and
one stale pacing mutation anchor; the revised mutation still proves that removing
origin spacing fails its witness. No failed run is relabeled as green.

The unpublished wheel built from that source passed strict Twine metadata and
content checks. It was installed with its pinned `images` extra in a new Python
3.11.16 environment at `/tmp/ghimera-040-pacific-release-VsN1Rj/wheel-env`,
without `PYTHONPATH`. Public imports, the legacy alias, CLI help, fresh-process
sealed archive readback and OCR execution through the installed package passed.
Both Chinese scripts, Japanese, Korean and Tagalog generated fixtures returned
native OCR regions. These are packaging and execution observations, not
representative infographic or model accuracy. See
[Pacific execution evidence](VISUAL_LANGUAGE_ACCEPTANCE.md).

Before publication, build a wheel/source archive, validate their metadata and
contents, and install the exact wheel into an isolated environment without an
editable source path. Verify public imports, command help and configuration
examples, then retain artifact hashes. Confirm upload by downloading the public
artifacts and comparing their bytes with the tested files.

## Acceptance limits

The combined source gate is not proof of exhaustive collection, representative
research accuracy or an operating Ahmia index. Bounded real public-browser
evidence is recorded [separately](C1_HUMAN_PUBLIC_EVIDENCE.md). Real semantic
organization-model refusals and remaining collector requirements remain in
[C0](C0.md) and their detailed evidence documents. Downstream platform ingestion
and the separately hosted Ahmia MCP architecture remain design-only.

The [infrastructure review](INFRASTRUCTURE_REVIEW.md) distinguishes native
embedding-based relevance from persistent corpus indexing, and describes the
remaining functional, operational and quality requirements.
