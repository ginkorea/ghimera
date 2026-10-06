# Chimera

Goal-directed collection, repurposed from the owner's go-spider repository.
Package: `taipan-chimera`; import: `chimera`; Python **3.11+**.

Status: **standalone source candidate, not deployed**. Core collection, real
direct/Tor HTTP, configurable research graphs, the intent research loop and a
SearXNG HTTP adapter and configured self-hosted model clients are implemented.
Native HTML extraction with adaptive locators/language detection, DOCX tables,
native PDF text and canonical/near-duplicate grouping are implemented.
Isolated Patchright rendering is implemented as a configured HTTP-ladder
transformer; remaining browser adapters and public corpus acceptance stay open.
Full-PDF extraction, admitted real-model acceptance, TAIPAN integration and
full C0–C5 acceptance remain required.
See [Tor routing](docs/TOR.md) and [intent research](docs/C3_RESEARCH.md).
See [browser rendering](docs/C1_BROWSER.md) for network isolation and provenance.
See [model control and evidence context](docs/C3_MODELS.md) for model roles.
See [native HTML extraction](docs/C2_HTML.md) for the pinned parser extra.
See [offline document conversion](docs/C2_DOCUMENTS.md) for DOCX/native PDF and
the remaining full PDF/Marker acceptance.

Chimera remains its own repository. TAIPAN consumes a pinned release and wheel
digest, like judais-lobi. The existing repository remote is
`https://github.com/ginkorea/spider`; this lane does not create a remote, push,
publish a package or modify production services.

## Development

```bash
uv sync --locked --extra html --extra documents --extra browser --python 3.11
# Set CHIMERA_TEST_BROWSER and CHIMERA_TEST_ISOLATOR explicitly; see C1_BROWSER.md.
bash scripts/gate.sh
uv build --no-sources
```

Read [the specification and tracker](docs/C0.md) and the explicit
[configuration example](examples/chimera.toml) before extending the core.
Content identity and retained duplicate evidence are described in
[C2 deduplication](docs/C2_DEDUP.md).
Self-hosted models are injected through ports. A local API is normal;
external model fallback is refused. No model server or weights ship here.

The prototype's frontier and self-grading concepts are retained; its OpenAI
clients, VPN manager, hidden fallbacks, scripts and bytecode are removed on
this branch. Baseline `6a06245` stays in history and the dirty donor checkout
is untouched. This is not compatible with the prototype CLI/API.

The donor README declared MIT but its referenced LICENSE file is missing.
Retain that provenance; verify the owner's licence before public publication.
