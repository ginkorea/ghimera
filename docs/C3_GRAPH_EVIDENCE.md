# Configurable graph core — source evidence, 6 October 2026

Tested source: `917febe12d83c910ec9ecc1a6a1e7c9b6d74c808`, branch
`gompert/chimera-c3-graph-20261006` in `/tmp/chimera-c0-20261006`.
The donor checkout was not edited. This candidate is not merged, tagged,
published or deployed.

## Complete package gate

Command: `CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh`.
Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
The environment resolves no TAIPAN SDK; this is the standalone core and performs
no governed platform calls.

- Offline lock: 23 packages resolved, unchanged.
- Ruff lint: pass; formatting: 22 files pass.
- Strict mypy: 15 production modules pass.
- Complete tests: **69 passed, 0 failed, 0 skipped**, in 22.39 seconds.

Graph-specific witnesses cover both injected memory and real directory sinks,
graph-before-fetch order, configuration-only vocabulary changes, frozen wire
roundtrip, idempotence, restart replay, altered configuration/corrupt journal
refusal, endpoint/citation/threshold refusal, incorrect acknowledgment, resource
bounds, concurrent observations and cancellation during in-flight persistence.
Citation witnesses re-compute edge IDs for malformed claims, so an identity
check alone cannot make them pass. Existing HTTP fixtures exercise real curl
against owned loopback servers; judge/encoder/extraction remain explicit doubles.

Sandbox-only execution stalled at thread-backed filesystem I/O and was stopped
by its exact 30-second timeout (exit 124). The same owned tests passed outside
that sandbox; no network except the existing private loopback HTTP fixtures,
credentials or live-service changes were involved.

## Candidate artifacts and isolated wheel

Offline build: `uv --cache-dir /tmp/chimera-c0-uv-cache build --offline
--no-sources --out-dir dist/c3-graph`.

| Artifact | SHA-256 |
|---|---|
| `dist/c3-graph/taipan_chimera-0.1.0-py3-none-any.whl` | `725abe15fb1c792d3592d343ab35588033537dbb0934c1c99d5c62c4795cb313` |
| `dist/c3-graph/taipan_chimera-0.1.0.tar.gz` | `dc05dd9848a66f1dec49f48e26ae23ec1c6a91b7f5b91572c8f1480debe79515` |

These are unpublished candidate artifacts built from the tested source above;
later evidence-document commits do not retroactively change their content.

Fresh offline-installed wheel environment:
`/tmp/chimera-c3-wheel-check-20261006/bin/python`, Python **3.11.16**.
Import came from that environment's `lib64/python3.11/site-packages/chimera`,
not the checkout. From `/tmp`, with PYTHONPATH and TAIPAN credentials unset,
an observed fixture route required a durable initial graph before fetch. The
seeded loop returned a JSON-roundtrippable harvest; a new graph instance replayed
the real private journal and matched its complete snapshot/checkpoint.
`wheel_graph_roundtrip PASS`; journal `/tmp/chimera-c3-wheel-2utsa7ax`.
The judge was explicitly `test_double`: no real-model acceptance is claimed.

## Remaining objective

See `RESEARCH_GRAPH.md` for graph-specific gaps. The full spider still requires
browser/adaptive routes, native structured document extraction, served-model
scoring/research, intent-only grounded search and gap-driven cited answers,
bounded background graph writer/deadlines, exact TAIPAN graph/SDK/MCP integration,
and Edge/live publisher acceptance. This partial C3 evidence does not close C3
or the full spider goal. Owner additions are recorded on the isolated TAIPAN
PRD branch `codex/chimera-owner-additions-20261006` at `e6870090`.
