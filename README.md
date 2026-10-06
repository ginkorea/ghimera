# Chimera

Goal-directed collection, repurposed from the owner's go-spider repository.
Package: `taipan-chimera`; import: `chimera`; Python **3.11+**.

Status: **C0 source candidate only**. Real fetch/extraction routes, served-model
bindings and TAIPAN integration follow in C1–C5; none are deployed by this lane.

Chimera remains its own repository. TAIPAN consumes a pinned release and wheel
digest, like judais-lobi. g39 hosting is the working assumption; this lane does
not create a remote, publish a package or modify production services.

## Development

```bash
uv sync --locked --python 3.11
bash scripts/gate.sh
uv build --no-sources
```

Read [the specification and tracker](docs/C0.md) and the explicit
[configuration example](examples/chimera.toml) before extending the core.
Self-hosted models are injected through ports. A local API is normal;
external model fallback is refused. No model server or weights ship here.

The prototype's frontier and self-grading concepts are retained; its OpenAI
clients, VPN manager, hidden fallbacks, scripts and bytecode are removed on
this branch. Baseline `6a06245` stays in history and the dirty donor checkout
is untouched. This is not compatible with the prototype CLI/API.

The donor README declared MIT but its referenced LICENSE file is missing.
Retain that provenance; verify the owner's licence before public publication.
