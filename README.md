# ghimera

Intent-driven web research: discover sources, collect native-language documents,
follow evidence gaps, and return a source-cited answer—or an explicit partial
result when the evidence or budget is insufficient.

**v0.3.0 consolidates the repository, distribution and import as `ghimera`.**
It succeeds the `go-spider` distribution and `chimera` implementation. It is not
backward-compatible with v0.1.0's `spider_core` API or `spider` CLI. Python
**3.11+** is required. Some planned browser/document adapters and public-corpus
acceptance are still in progress; see the limitations below.

ghimera is an independent library. Supply your own search provider,
self-hosted model services, extraction policies and graph profile.

## What is implemented

- **Goal and intent loops.** Collect from configured seeds with `GoalLoop`, or
  use `ResearchLoop` to plan questions, discover sources through an injected
  search provider, assess gaps, and draft/review an evidence-cited answer.
- **Local models first.** Configured, already-served self-hosted models supply
  planning, judging, answer generation, review and embeddings. Compatible HTTP
  interfaces are supported; no external LLM fallback, model weights or model
  server are included.
- **Bounded direct and Tor HTTP.** Native v3 onion collection and open-web
  requests through Tor share the same route policy. Public-network validation,
  pinned DNS for direct requests, per-hop redirect checks, robots policy,
  concurrency/rate limits and retries are accounted for before returning data.
  A failed Tor route never silently falls back to direct access.
- **Isolated JavaScript rendering.** Patchright runs in a network-isolated
  Linux worker; the parent fetch boundary handles its permitted HTTP resources,
  redirects and accounting. Browser binaries are explicitly configured and
  verified, not downloaded on import. Camoufox/nodriver adapters remain planned.
- **Native extraction.** Configured HTML fit-Markdown, adaptive locator
  profiles, language detection, DOCX tables and native PDF text preserve raw
  bytes beside extracted native-language text. Full PDF/OCR and Marker
  acceptance remain open.
- **Relevance and deduplication.** An injected self-hosted encoder scores
  native text/windows and observed links against pinned reference vectors.
  Keyword/semantic ranking, encoding budgets, canonical URL handling, SHA-256
  and configured near-duplicate grouping retain source-qualified evidence.
  Similarity is not a calibrated probability or a substitute for a verdict.
- **Graphs and audit records.** A configurable research graph starts with the
  intent. Typed harvests, receipts and ledger rows retain configuration,
  transport, model-call spend, omissions, verdicts, refusals and source hashes.
  Operational discovery traces are distinct from evidence-supported claims.

Every candidate reaching acceptance receives an accept/reject/hold verdict. A hold gets a
second model pass. An intent is marked answered only after the coverage,
citations, review and configured confidence checks pass; budget exhaustion is
not silently presented as success.

### Additions since go-spider 0.2.0

The current working branch also has explicit authorized source sessions,
configurable references/citing-source discovery, persistent publisher-locator
drift detection with generic recovery and a doctor, and offline model-based PDF
layout, tables, OCR and column-aware reading order, durable run journals, and
intent-based semantic scoring without a prebuilt reference-vector file, and a
configuration-driven `Collector` facade using actual adapters. These were not included
in the old `go-spider==0.2.0` wheel and are included in `ghimera==0.3.0`.
See [source sessions](docs/SOURCE_SESSIONS.md),
[references](docs/C3_REFERENCES.md), [locator health](docs/C2_HTML.md), and
[PDF configuration/acceptance](docs/C2_DOCUMENTS.md),
[run journals](docs/RUN_JOURNAL.md) and
[intent scoring](docs/C3_EMBEDDING_SCORING.md#intent-references-unreleased-source).
For the assembled intent-only API and full non-active template, see
[configured collector](docs/COLLECTOR.md) and `examples/collector.toml`.
The package also supports explicitly configured ordinary HTML search
alongside JSON, and complete research archives retain successful discovery
responses with query/fetch bindings. See [HTML search](docs/C3_SEARCH_HTML.md)
and [search evidence](docs/C3_SEARCH_EVIDENCE.md). Neither mode solves access
challenges, and a refusal is not a successful research result.
Representative-corpus accuracy and Marker acceptance remain open; passing a
controlled document check is not a universal quality claim.

### Unreleased additions after 0.3.0

The feature branch adds explicitly configured local challenge recovery with
private, origin-scoped clearance and guarded content verification; this is
not a universal CAPTCHA solver. See [challenge recovery](docs/CHALLENGES.md).
It also admits hash-pinned owned PDF/DOCX seeds before intent planning through
the same document pipeline. See [local inputs](docs/LOCAL_INPUTS.md).
Neither addition is in the immutable `ghimera==0.3.0` artifacts. Automatic
organizational entity extraction and graph-driven network expansion remain
planned, not claims of the existing research graph.

## Installation

Use a dedicated virtual environment:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install 'ghimera==0.3.0'
```

Install the adapters you intend to configure:

```bash
python -m pip install 'ghimera[html,documents,browser]==0.3.0'
```

The base package contains the typed core, HTTP/Tor transport, research/search
and self-hosted model/embedding clients. Extras add pinned HTML, document and
Patchright dependencies. They do **not** install an inference server, browser
binary, Tor daemon or PDF/OCR model artifacts. The isolated browser adapter
requires Linux, a compatible explicitly supplied Chromium binary and Bubblewrap;
other operating systems have not been accepted for that adapter.

## Configuration and API

Operational choices are typed, versioned configuration—not Python constants:
scope, budgets, endpoints, model identities/revisions, thresholds, private
worker directories, browser provenance and direct/Tor policy. Parse once with
`GhimeraConfig.from_toml(Path(...))`; inject the matching collaborators.

The [examples](https://github.com/ginkorea/ghimera/tree/v0.3.0/examples) are non-active templates. Replace invalid endpoints,
contact information, private paths and model identifiers; reference-vector
fixtures are **not** production relevance data. Adapter blocks belong in the
main configuration under their named keys, not as unrelated root settings.
Supply any model credential separately in memory, only to its authorized exact
endpoint; configuration is not credential or destination approval.

Download the starter configuration, or copy it from the repository:

```bash
curl --fail --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/ginkorea/ghimera/v0.3.0/examples/chimera.toml \
  --output chimera.toml
```

An entirely offline smoke example, using explicitly named test doubles:

```python
import asyncio
from pathlib import Path

from ghimera import GhimeraConfig, Goal, GoalLoop, Harvest, Scope
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder


async def main() -> None:
    config = GhimeraConfig.from_toml(Path("chimera.toml"))
    collector = GoalLoop(
        config=config,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = await collector.run(
        Goal(text="ports", seeds=("https://example.org/start",)),
        Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
    )
    # Reader revalidates retained sources, provenance and receipt accounting.
    restored = Harvest.model_validate_json(result.model_dump_json())
    print(restored.receipt.stop_reason)


asyncio.run(main())
```

This example makes no network or real model calls and proves no research
accuracy. For actual collection bind `CurlRoute`, configured extraction and
scoring adapters, and the self-hosted judge through their ports. For intent-only
research, inject those into `ResearchLoop` alongside `GroundedSearch`,
`IntentPlanner`, `ResearchAnalyst` and `AnswerReviewer`, then call
`run(ResearchRequest(intent="your research question"))`. `SearxSearch` is the
implemented search adapter.

### Configured intent API

The configured collector assembles real adapters, so applications need not manually
wire every port. Unlike the offline smoke above, this needs your configured
services and the adapted `examples/collector.toml` template:

```python
import asyncio
from pathlib import Path

from ghimera import Collector


async def main() -> None:
    collector = Collector.from_toml(Path("collector.toml"), max_config_bytes=100_000)
    result = await collector.run("Your research question")
    print(result.status)


asyncio.run(main())
```

Use the [collector guide](docs/COLLECTOR.md) to configure actual private models,
source routing, extraction, optional graph/journal and separately supplied
credentials. This API is included in `ghimera==0.3.0`, not the old `go-spider==0.2.0` wheel. The
lower-level APIs remain supported for custom providers and composition.

The package also supplies a configured intent command that retains the
full result, original documents and citations in a private, checksum-sealed archive:

```bash
python -m ghimera --job /absolute/path/collector-command.toml --max-job-bytes 100000
```

See the [command guide](docs/COLLECTOR_COMMAND.md) and
`examples/collector-command.toml`. Existing output identities are never overwritten;
partial results remain partial. This command is not in the published 0.2.0 wheel.

The following guides cover the existing lower-level wiring:

| Area | Guide |
|---|---|
| Intent, discovery, coverage and answer review | [Intent research](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C3_RESEARCH.md) |
| Model roles, credentials and native evidence context | [Self-hosted models](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C3_MODELS.md) |
| Reference vectors, encoding and frontier ranking | [Embedding scoring](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C3_EMBEDDING_SCORING.md) |
| Open web and native onion routing | [Tor policy](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/TOR.md) |
| Browser isolation, resources and redirects | [Browser rendering](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C1_BROWSER.md) |
| HTML extraction and adaptive locators | [HTML extraction](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C2_HTML.md) |
| DOCX/native PDF and offline artifacts | [Document extraction](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C2_DOCUMENTS.md) |
| Canonical and near-duplicate source evidence | [Deduplication](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C2_DEDUP.md) |
| Configurable research graphs | [Research graph](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/RESEARCH_GRAPH.md) |
| Architecture, contracts and completion tracker | [Specification](https://github.com/ginkorea/ghimera/blob/v0.3.0/docs/C0.md) |

## Scope and limitations

Robots are honored by default. An override requires a recorded, reasoned,
exact-host configuration decision; it does not disable login/paywall refusal.
Post-release source adds an opt-in local challenge gateway with bounded,
exact-origin clearance reuse; see [challenge recovery](docs/CHALLENGES.md).
That addition is not in the published 0.3.0 artifacts and does not guarantee
universal CAPTCHA solving or provide an authenticated-site bypass.
Tor routing is a transport capability, not a guarantee of anonymity or authority
to access a source.

Collection is not limited to anonymous access. Supply your own authorized
cookies or headers through explicitly configured source sessions, with exact
origin/path scope and no credential values in receipts. Browser resources use
the same parent-owned session boundary. See [authorized sessions](docs/SOURCE_SESSIONS.md).

Source/search requests run on the host executing the crawler. Model control is
a separate private-service boundary. Your application owns authorization,
deployment and storage; no external scheduler or registry is required to import
or use the library.

Configurable source expansion follows observed document URLs and discovers
candidate citing sources through your search provider. Depth, host policy and
budgets live in `[references]`, not in Python. Source hashes and native locators
are retained; a citing-source search hit is not proof that a citation exists.
See [reference expansion](docs/C3_REFERENCES.md).

For durable observations, configure `[journal]` and pass a unique `run_id`.
The collector persists JSONL events before acknowledgment and seals a completion
summary only after receipt reconciliation. Interrupted prefixes remain inspectable
without silently refetching sources. See [run journals](docs/RUN_JOURNAL.md).

Still required for the complete planned spider: the remaining browser adapters,
representative publisher/locator acceptance, full PDF/OCR and Marker validation,
real reference/cited-by adequacy, real served-model quality/admission and
calibrated decision policy and live runtime/egress acceptance.
The repository's detailed tracker retains those requirements; this release
does not erase them or describe fixture results as real-world model accuracy.

## Development

```bash
git clone https://github.com/ginkorea/ghimera.git
cd ghimera
uv sync --locked --extra html --extra documents --extra browser --python 3.11
# Explicit browser/isolation paths are required for the complete gate.
export CHIMERA_TEST_BROWSER=/absolute/path/to/compatible/chrome
export CHIMERA_TEST_ISOLATOR=/absolute/path/to/bwrap
bash scripts/gate.sh
uv build --no-sources
```

The gate checks the actual interpreter/import path, lockfile, Ruff, strict mypy
and the entire test suite. Browser tests refuse absent acceptance prerequisites
rather than pretending they ran. Evidence records distinguish protocol fixtures,
installed-artifact checks, public-corpus acceptance and production activation.

## Migration from go-spider

Install `ghimera==0.3.0` explicitly; this is a new distribution, not an in-place
rename of old PyPI releases. Use `from ghimera import Collector, GhimeraConfig`,
`ghimera.*` for submodules, and `ghimera` or `python -m ghimera` for the command.
The legacy root `from chimera import Collector, ChimeraConfig` and
`python -m chimera` forward to the same implementation. Old nested
`chimera.*` imports must migrate; there is no second implementation or import hook.
Existing versioned `chimera.*` configuration, graph, harvest, journal and result
schemas are retained so existing saved evidence does not change identity.

The v0.1.0 `spider_core` API and old `spider` command are not supplied; keep
`go-spider==0.1.0` while migrating those applications. The old cloud-client,
VPN-manager and implicit fallback design is not retained. Prototype source
remains in Git history.

See [CHANGELOG.md](https://github.com/ginkorea/ghimera/blob/v0.3.0/CHANGELOG.md) for release changes. Josh Gompert maintains
the project at [ginkorea/ghimera](https://github.com/ginkorea/ghimera).
Licensed under [MIT](https://github.com/ginkorea/ghimera/blob/v0.3.0/LICENSE), matching the existing PyPI licence declaration.
