# ghimera

Intent-driven web research: discover sources, collect native-language documents,
follow evidence gaps, and return a source-cited answer—or an explicit partial
result when the evidence or budget is insufficient.

**v0.4.4 adds guarded browser redirects and a background delivery worker.**

Version 0.4.4 adds explicitly configured before-contact navigation admission
through the caller-owned browser session. Redirected pages, downloads and inline
originals retain their source chains through parsing, citations, graph and
archives. A configurable background worker retries durable delivery without
refetching sources. See [guarded collection](docs/BROWSER_NAVIGATION_GUARD.md),
[delivery lifecycle](docs/DELIVERY_OUTBOX.md) and
[release acceptance](docs/RELEASE_044.md) for evidence and remaining limits.
The repository, distribution and import use `ghimera`.
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
- **MCP discovery.** Borrow an explicitly bound MCP
  session to obtain source leads; default mappings support the `web_search`
  envelope. Other tools and result paths are configurable. See
  [MCP leads](docs/MCP_LEADS.md).
- **Configurable discovery routing.** Combine explicit
  open-web and onion lead providers, with per-provider budgets, concurrent
  fan-out or ordered fallback, and bounded switching when research stalls.
  Continuation retains spent budgets and each provider's original response.
  See [discovery routing](docs/DISCOVERY_ROUTING.md). An onion-index service
  is not bundled.
- **Ahmia index leads.** Query an explicitly configured,
  operator-owned index, or expose its lead payload through an application-owned
  `onion_search` MCP tool. Retain index provenance and fetch original documents
  separately through Tor. See [Ahmia integration](docs/AHMIA_INTEGRATION.md).
- **Human-proxy browser interaction.** An explicitly
  configured CLI can pause while you act in the selected browser, then resume
  native capture with request-bound terminal input. See
  [interactive collection](docs/TERMINAL_ASSISTANCE.md).
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
  verified, not downloaded on import. Alternate passive renderers remain planned;
  separately deployed challenge gateways are explicitly supported.
- **Native extraction.** Configured HTML fit-Markdown, adaptive locator
  profiles, language detection, DOCX tables and native PDF text preserve raw
  bytes beside extracted native-language text. Full PDF/OCR and Marker
  acceptance remain open.
- **Selective visual evidence.** Configured infographic admission filters logos
  before download, bounds raster decoding and runs offline language-routed OCR.
  Optional local vision interpretation requires a separate image-bound review.
  Accepted originals and OCR regions remain beside native text; rejected images
  are not retained or vectorized. See [visuals and Pacific OCR](docs/VISUALS.md).
- **Configurable browsing cadence.** Nonnegative jitter adds to origin/robots
  spacing, while `429`/configured throttle responses and `Retry-After` impose
  shared-origin cooldown without blocking unrelated origins. See
  [browsing cadence](docs/BROWSING_CADENCE.md).
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

### Additions in 0.4.0

This release adds explicitly configured local challenge recovery with
FlareSolverr or Byparr (including its Camoufox-backed 2.x wire),
private, origin-scoped clearance and guarded content verification; this is
not a universal CAPTCHA solver. See [challenge recovery](docs/CHALLENGES.md).
An explicit dedicated Chromium session can also use an application-supplied
human assistance port: finish ordinary login or a challenge in that browser,
then continue collection in the same session. DOM acquisition remains distinct
from HTTP responses throughout extraction, graph evidence, private archives and
completed-round resume. Browser egress is operator-managed; verified browser
Tor routing and representative-publisher acceptance remain open. A bounded
English/Traditional Chinese public-document capture is recorded in
[publisher evidence](docs/C1_HUMAN_PUBLIC_EVIDENCE.md). See
[human-assisted collection](docs/HUMAN_BROWSER.md).
Version 0.4.2 also captures actual PDF/DOCX downloads through
the caller's selected `BoundPageHumanSession`, injected into `Collector`.
An explicit format policy lets scored native links supply previously unknown
file URLs; the collector does not need a hard-coded attachment list. Original
file bytes enter the same extraction, citation, graph and archive path.
Caller-owned tabs remain open, and unknown browser-network usage is not
represented as zero. The 0.4.3 inline-body path makes one separate,
configured same-origin browser GET; it does not claim navigation bytes were
intercepted. Redirected files and verified entitled-publisher/Tor compatibility
remain separate acceptance work. See
[browser downloads and configuration](docs/BROWSER_DOWNLOADS.md).
It also admits hash-pinned owned PDF/DOCX seeds before intent planning through
the same document pipeline. See [local inputs](docs/LOCAL_INPUTS.md).
Configured [semantic extraction](docs/SEMANTIC_EXTRACTION.md) now produces
native entity/relationship observations as source-local model assertions.
Explicit [identity-aware planning](docs/IDENTITY_PLANNING.md) can turn repeated
names, asserted aliases and potentially competing dated claims into source-bound
follow-up research questions without merging the original nodes.
These additions were not in the immutable `ghimera==0.3.0` artifacts. Alias
resolution, graph-driven network expansion and organizational accuracy
acceptance remain open, not claims of the existing research graph.

## Installation

The 0.4.1 source line adds [bounded parallel collection](docs/CONCURRENT_COLLECTION.md)
and a [durable native evidence corpus](docs/EVIDENCE_CORPUS.md). The corpus keeps
accepted originals, text/OCR/visual provenance and model-bound vectors for later
similarity queries. These additions are not in the immutable 0.4.0 wheel;
representative Pacific-language quality and the full infrastructure tracker
remain explicit acceptance work.

An explicitly composed `PersistentCollector` automatically appends completed
collection/research evidence to that corpus and returns its acknowledgement
beside the unchanged original result. Failed handoffs retain completed source
work for persistence-only retry; operation-level crash recovery and an unattended
service are still separate requirements. See the corpus documentation for usage.

This source line can also use an explicitly bound corpus as a discovery
provider, alone or beside configured web/MCP/onion providers. It preserves native
query and source observations; returned matches are leads that still go through
ordinary collection and citation checks. Cross-language quality and direct
cached-source answer reuse remain separate acceptance work.

An explicit [delivery outbox](docs/DELIVERY_OUTBOX.md) can queue complete results
before returning and dispatch them concurrently when its configured destination
is available. Destination readback precedes acknowledgement and any explicit
local-payload pruning. The included local durable destination is not off-host
backup; remote adapters and unattended service deployment remain open.

Version 0.4.4 includes a configurable `DeliveryWorker` and
`ghimera-delivery` command for background retries, JSONL health and optional
readback-confirmed outbox cleanup. Previous release artifacts remain immutable.
See [delivery lifecycle and configuration](docs/DELIVERY_OUTBOX.md).

Use a dedicated virtual environment:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install 'ghimera==0.4.4'
```

Install the adapters you intend to configure:

```bash
python -m pip install 'ghimera[html,documents,browser]==0.4.4'
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

The [examples](https://github.com/ginkorea/ghimera/tree/v0.4.0/examples) are non-active templates. Replace invalid endpoints,
contact information, private paths and model identifiers; reference-vector
fixtures are **not** production relevance data. Adapter blocks belong in the
main configuration under their named keys, not as unrelated root settings.
Supply any model credential separately in memory, only to its authorized exact
endpoint; configuration is not credential or destination approval.

Download the starter configuration, or copy it from the repository:

```bash
curl --fail --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/ginkorea/ghimera/v0.4.0/examples/chimera.toml \
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

The defined-ontology profile provides separately configured semantic
verification, quarantined proposals and source-bound coverage gaps that can
motivate follow-up discovery. See [semantic verification](docs/SEMANTIC_VERIFICATION.md).
Model agreement is not corroboration; real organizational extraction quality
remains an open acceptance item.
An explicit `ghimera.semantic-verification/2` recipe additionally separates
named-instance/type checks from relationship entailment, direction and dates.
Contradictory summaries refuse instead of overriding a failed dimension;
the original review recipe remains unchanged. See the non-active
[dimensioned example](examples/semantics-factorized.toml).
An explicit version-4 [batched review profile](docs/SEMANTIC_BATCHING.md)
preserves the complete original proposal while limiting assessments per answer
and retaining a separate coverage call. Its bounds and budgets are configured;
it does not claim to fix a model that returns no final answer.

The semantic extraction stage can also feed a configured, bounded
graph view into follow-up research planning. Queries retain references to the
observed entities/relations and omissions remain explicit; see
[graph-aware planning](docs/GRAPH_PLANNING.md). Explicit completed-round
[suspend/resume](docs/CONTINUATION.md) preserves evidence, acknowledged graph,
pending frontier and cumulative budgets across processes, through the library
and a [versioned resumable command](docs/COMMAND_CONTINUATION.md). Alias resolution,
fine-grained interrupted-call reconciliation and real organizational-network
quality acceptance remain open.
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
