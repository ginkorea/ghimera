# ghimera 0.4.0 release acceptance

Status: release candidate; full combined gate and package acceptance pending.
No publication is implied by this document until its terminal evidence is added.

## Scope

The Ahmia development line includes all commits of the discovery/reference
expansion line. This release brings both into the standalone `ghimera`
distribution: MCP and routed discovery, the configured Ahmia index adapter,
same-browser human assistance, local PDF/DOCX intake, explicit challenge-service
dialects, completed-round resume, and source-bound semantic/graph research.
See [changelog](../CHANGELOG.md) for the versioned additions.

Legacy data schemas and prompt identities are preserved. Existing 0.3.0
artifacts and tags are immutable. Python remains **3.11+**. External model,
Tor/browser and Ahmia services are operator-provided, not implicitly deployed.

## Gates and artifacts

The corrected complete `scripts/gate.sh` must run on frozen source/tests with
the executing interpreter and import path recorded. It includes offline lock,
Ruff, format, strict mypy and the entire pytest suite. Failed earlier gates
remain documented in [Ahmia integration](AHMIA_INTEGRATION.md).

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
