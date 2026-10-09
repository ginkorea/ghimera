# ghimera

Intent-driven web research with your own local model services: discover sources,
collect native-language evidence, follow gaps, and return a source-cited answer
or an explicit partial result when evidence or budget is insufficient.

ghimera is an independent Python library and command-line application. You own
the model services, search providers, authorized source sessions, networking and
storage. It does not launch models, discover credentials, allocate compute or
silently fall back to an external LLM.

**Release 0.4.11.** This increment adds opt-in recovery, identity, judgment and
retrieval capabilities described [below](#0411-capabilities). Its frozen source
gate and installed-package observations are recorded in
[release acceptance](docs/RELEASE_0411.md); publication readback is a separate
step. These checks do not establish live multilingual model quality, complete
publisher coverage or recovery from every interruption.

## Installation

Python **3.11+** is required. Use a dedicated environment:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install 'ghimera==0.4.11'
```

Install the adapters you intend to configure:

```bash
python -m pip install 'ghimera[html,documents,browser]==0.4.11'
```

The base package supplies typed collection/research contracts, HTTP/Tor
transport, search and self-hosted model/embedding clients. Extras add HTML,
document and Patchright dependencies. They do **not** install an inference
server, browser binary, Tor daemon or PDF/OCR model artifacts. The isolated
browser adapter requires Linux, an explicitly supplied compatible Chromium
binary and Bubblewrap; other operating systems have not been accepted for it.

## Configure and run an intent

Start with the [0.4.11 examples](https://github.com/ginkorea/ghimera/tree/v0.4.11/examples),
especially `collector.toml`. Templates are non-active: replace invalid endpoints,
private paths, model identifiers and contact details. Use examples matching your
installed version; a different source checkout does not update an installed wheel.

Configure already-served private completion and embedding services, a search
provider, source scope, extraction policies and explicit budgets. Endpoints,
model revisions, language artifacts, thresholds, direct/Tor routes and storage
belong in typed, versioned configuration. Reference-vector fixtures are not
production relevance data. See the [collector guide](docs/COLLECTOR.md).

`Collector` assembles the native adapters and runs the full intent workflow:

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

### Check a recipe offline

Before running research, validate your copied `collector.toml` without contacting
sources or model services:

```python
from pathlib import Path

from ghimera import GhimeraConfig

config = GhimeraConfig.from_toml(Path("collector.toml"), max_bytes=100_000)
print(config.schema_version)
```

This checks configuration, not service availability or research quality. The
intent example above needs the services and credentials you configured.

Planning, discovery, collection, assessment, answer generation and independent
review share the configured allowances. A result is answered only after native
coverage, citation, review and confidence checks pass. Budget exhaustion is not
success; retained model assertions are not independent corroboration.

Supply model and source credentials separately through the documented inputs,
not secret values in TOML. Credentials are scoped to their exact configured
service or source session. Neither configuration nor a model response grants
access to a destination. Lower-level `GoalLoop` and `ResearchLoop` remain
available for explicitly injected providers and custom composition.

### Command and private result archive

Adapt the published `collector-command.toml`, `research-request.json` and
collector recipe. Select a fresh run identity and output directory whose parent
already exists on your storage volume:

```bash
python -m ghimera --job /absolute/path/collector-command.toml --max-job-bytes 100000
```

The `ghimera` executable runs the same command. It retains the full typed result,
original documents, native text, provenance, observations and citations in a
private checksum-sealed archive. Stdout is a small receipt, not the answer.
Existing output identities are never overwritten; partial results stay partial.
Optional credential bindings name environment variables, not credential values.
See [command configuration and readback](docs/COLLECTOR_COMMAND.md).

## Authorized sources and discovery routes

Collection is not restricted to anonymous sources. Your authorized cookies or
headers can be supplied through [source sessions](docs/SOURCE_SESSIONS.md),
with exact origin/path scope. Login, paywall and challenge refusals are not
permission to bypass controls. Robots are honored by default; an override needs
an explicit, reasoned exact-host decision and does not disable access refusal.

- **Direct HTTP:** public-network admission, pinned DNS, per-hop redirect
  checks, robots, pacing and byte/time budgets apply at the native fetch boundary.
- **Tor and onion:** explicitly select the Tor route for public web or native
  v3 onion sources. A failed Tor route never silently falls back to direct.
  Private model control uses its separate service boundary. See [Tor policy](docs/TOR.md).
- **Browser and human assistance:** isolated Patchright rendering routes admitted
  resources through the parent fetch boundary. Alternatively, explicitly bind
  your authorized dedicated Chromium session or caller-owned page and complete
  normal login/challenge interaction there. Native capture resumes in that same
  session; there is no personal-profile copying or anonymous fallback. The
  [interactive command](docs/TERMINAL_ASSISTANCE.md) accepts request-bound
  resume/decline input, never passwords, cookies or MFA values in the terminal.
- **MCP:** borrow an explicitly initialized, caller-bound MCP session for leads;
  tool names and result mappings are configured. Source originals still require
  their own admitted fetch. No platform registry or implicit session is needed.
  See [MCP leads](docs/MCP_LEADS.md).
- **Ahmia and other discovery providers:** use an explicitly configured
  operator-owned Ahmia-compatible index, SearXNG, or configured web/onion/MCP
  providers. Fan-out, ordered fallback, switching and per-provider budgets are
  explicit. No onion-index service is bundled. See
  [discovery routing](docs/DISCOVERY_ROUTING.md) and [Ahmia integration](docs/AHMIA_INTEGRATION.md).

Separately deployed [FlareSolverr/Byparr challenge gateways](docs/CHALLENGES.md)
support bounded, origin-scoped recovery; they are not universal CAPTCHA solvers.
Transport support, including verified browser-over-Tor capture, is not an
anonymity guarantee or source-access authority.

## Native evidence, retrieval and graphs

- **Documents:** HTML/Markdown, native PDF and DOCX, hash-pinned owned-file seeds,
  configured feeds and source APIs enter the native extraction path. Browser
  downloads and inline bodies retain their acquisition provenance. References,
  next-page links and citing-source discovery are bounded frontier work; a search
  hit does not prove a citation exists. See [documents](docs/C2_DOCUMENTS.md),
  [local inputs](docs/LOCAL_INPUTS.md), [browser downloads](docs/BROWSER_DOWNLOADS.md)
  and [references](docs/C3_REFERENCES.md).
- **OCR and images:** configured offline language-routed OCR, PDF layout/tables
  and column-aware reading order preserve originals and native locators.
  Selective image admission filters logos before download; accepted images and
  PDF figure crops retain OCR regions and optional separately reviewed vision
  claims. Full scanned-PDF model readings remain identified as generated, not
  native text. See [visuals](docs/VISUALS.md) and [PDF transcription](docs/PDF_TRANSCRIPTION.md).
- **Source-aware vectors:** the explicitly owned disk corpus keeps originals,
  chunks, text/OCR/visual provenance and model-bound vectors. Configured hybrid
  lexical/vector retrieval returns source-qualified leads. A retained reader can
  supply original evidence to current assessment without refetching it or
  treating old judgments as new. Source age is not inferred; similarity is not
  calibrated confidence. See [corpus storage](docs/EVIDENCE_CORPUS.md),
  [corpus discovery](docs/GROUNDED_DISCOVERY.md) and [retained evidence](docs/RETAINED_EVIDENCE.md).
- **Graphs and citations:** configured semantic extraction and separate review
  preserve source-local entity/relationship assertions, quoted spans, dates and
  omissions. Graph-aware planning can pursue evidence gaps; reversible dated
  identity decisions do not merge away source originals. Answers retain native
  citation bounds and evidence basis. See [semantic verification](docs/SEMANTIC_VERIFICATION.md),
  [graph planning](docs/GRAPH_PLANNING.md) and [identity planning](docs/IDENTITY_PLANNING.md).
- **Durable observations:** native journals, source-work records and optional
  conditional refresh preserve original spend, uncertainty and source versions.
  A receipt proves the documented storage/readback checks, not publisher truth
  or model accuracy. See [journals](docs/RUN_JOURNAL.md),
  [source work](docs/SOURCE_WORK.md) and [source refresh](docs/SOURCE_REFRESH.md).

Corpus use is explicit: library callers supply the native corpus/reader;
the configured service can own it. The published standalone collection command
does not itself create or bind a disk corpus. `PersistentCollector` composes
collection with acknowledged corpus handoff; a failed handoff retains completed
work for persistence-only retry rather than another crawl.

## Unattended service, delivery and continuation

Adapt the published `collection-service.toml`: choose private storage, bounded
jobs/connections, the collector recipe and the named bearer-credential input.
Create optional corpus/outbox/destination stores through their native APIs before
startup. First startup explicitly creates the service store:

```bash
ghimera-service --config /absolute/path/collection-service.toml \
  --max-config-bytes 1000000 --create-service-store
```

Reopen the same store without `--create-service-store`. Every route requires the
configured bearer: run submission/status, pause/resume/cancel, health, manifests
and optional corpus query/delivery routes. The included HTTP server is loopback
only; off-host exposure needs your authenticated TLS boundary and supervisor.
Unattended service does not inherit interactive terminal assistance.

Pause takes effect at a completed checkpoint quantum, not at an arbitrary
in-flight call. Native [completed-round continuation](docs/CONTINUATION.md)
preserves the original run, frontier, graph acknowledgement and cumulative
budgets. Durable job/archive receipts and archive-only handoff retries prevent
an acknowledged completed result from becoming a fresh collection.

An explicit outbox and `ghimera-delivery` worker support background delivery.
The configured remote adapter uses guarded PUT and exact readback; destination
readback precedes acknowledgement and optional local-payload pruning. Retention,
audit rotation and optional compaction are bounded configuration. A local
destination is not off-host backup. See the [service guide](docs/OPERATIONS_CANDIDATE.md)
and [delivery lifecycle](docs/DELIVERY_OUTBOX.md). Destination durability, outage/
scale behavior and deployed off-host acceptance remain operator requirements.

## 0.4.11 capabilities

These opt-in capabilities extend the immutable 0.4.10 package:

- [Research recovery](docs/RESEARCH_RECOVERY.md) and [service restart admission](docs/SERVICE_RECOVERY.md)
  restore exact acknowledged research-model boundaries with original identities,
  output reservation and budgets. Retained ACKs replay locally; unknown outcomes
  remain held and charged. Recovery does not promise immediate pause.
- Explicit [model](docs/MODEL_UNKNOWN_RECONCILIATION.md) and
  [encoding](docs/ENCODING_RECOVERY.md) decisions can authorize one separately
  charged bounded attempt without erasing or claiming to resolve the original
  uncertain outcome. They are not automatic retries or budget resets.
- [Completed-source recovery](docs/SOURCE_COMPLETION_RECOVERY.md) can adopt an
  atomic serial source acknowledgement, preserving the original cursor, dedup,
  frontier, references, graph and spend. Selected unsupported overlap refuses;
  ordinary concurrent collection is unchanged.
- [Source-processing recovery](docs/SOURCE_PROCESSING_RECOVERY.md) restores
  supported parser, scoring and verdict acknowledgements through the native
  command and authenticated service. Later semantic, visual, identity and
  frontier interruptions remain explicit holds, not automatic retries.
- [Run-bound learned retrieval](docs/RESEARCH_RERANKING.md),
  [identity proposal/review](docs/IDENTITY_AUTOMATION.md),
  [contribution-aware document judgment](docs/DOCUMENT_JUDGMENT.md) and
  [intent-ranked native windows](docs/SEMANTIC_WINDOW_SELECTION.md) have explicit
  recipes and audit boundaries. They do not establish real ranking, identity or
  organizational-research accuracy.
- [Compact native quote review](docs/SEMANTIC_GROUNDING.md) reduces unused quote
  metadata while preserving the original source and complete replay checks.
  Smaller requests are not evidence that a model gives correct answers.

Arbitrary mid-source, concurrent, discovery/retained-reader or graph interruptions
are not transparently adopted. Unknown calls, torn tails, changed source/corpus/
recipe/model identities and active writers remain guarded holds unless a
specifically supported native decision or acknowledged boundary admits them.

## Acceptance limits and further reading

Real served-model quality, calibrated decisions, multilingual omission/citation
adequacy, organizational relationships and reference/cited-by coverage remain
open acceptance work. Native-script retention is not a language-quality claim:
Simplified Chinese scanned-PDF quality remains unaccepted. OCR text is not a
diagram's arrows, and model agreement is not corroboration.

Marker validation, alternate passive browser adapters, representative entitled
publisher/locator behavior and model-driven onion investigation remain separate
requirements. Byparr's Camoufox-backed gateway is not a passive Camoufox renderer.
Controlled protocol fixtures, installed-wheel checks and bounded real public
captures establish different things; none substitutes for those requirements.

Use the [core completion tracker](docs/PRD_INFRASTRUCTURE.md) for current gaps,
[architecture](docs/C0.md) for the original contracts, and [CHANGELOG](CHANGELOG.md)
for release history. The [v0.4.11 tree](https://github.com/ginkorea/ghimera/tree/v0.4.11)
is the matching source reference for this package version; later development
recipes may require a newer release.

## Migration

Install `ghimera==0.4.10` explicitly when migrating from `go-spider`. Use
`from ghimera import Collector, GhimeraConfig`, `ghimera.*` submodules and
`ghimera` or `python -m ghimera`. The legacy root `chimera` import and
`python -m chimera` forward to the same implementation; old nested `chimera.*`
imports must migrate. Existing versioned `chimera.*` evidence/configuration
schemas retain their identities. The old `spider_core` API and `spider` CLI are
not supplied; prototype releases remain in Git history.

Maintained by Josh Gompert at [ginkorea/ghimera](https://github.com/ginkorea/ghimera).
Licensed under [MIT](LICENSE).
