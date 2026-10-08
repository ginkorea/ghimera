# Configured collector

Status: included in the ghimera 0.3.0 release line; deployment/quality acceptance
is separate. `Collector` assembles the existing concrete adapters
from one validated `GhimeraConfig`. It does not supply a model server, download
weights, discover credentials or require an external registry or scheduler.

## Intent to evidence

Copy `examples/collector.toml` and replace its non-active endpoints, model
identities/revisions, contact address and private worker paths. Install the
selected extras into your own environment. The example enables HTML extraction
and original-intent embedding: no reference-vector file or fake scorer is needed.
Supply an already-served private embedding endpoint and completion services for
the planner, judge, analyst and reviewer. With `require_distinct_reviewer=true`,
the reviewer must declare a different model identity/revision from the analyst.
Different declarations alone do not prove independent weights or model quality.

```python
import asyncio
from pathlib import Path

from ghimera import Collector
from ghimera.research_types import ResearchResult


async def main() -> None:
    collector = Collector.from_toml(
        Path("collector.toml"),
        max_config_bytes=100_000,  # application's explicit configuration-read bound
    )
    result = await collector.run("Find the evidence needed to answer my question")
    validated = ResearchResult.model_validate_json(result.model_dump_json())
    print(validated.status)  # answered, partial or failed: inspect evidence/review too


asyncio.run(main())
```

This example uses actual configured adapters, not the smoke-test doubles. It
requires your configured services to be running. Construction makes no outbound
source, search or model request. Local adapter/artifact checks may refuse before
collection. Configuration is never permission to access a source or send secrets.

`Collector.run` accepts an intent string or a typed `ResearchRequest`. It plans,
discovers sources through SearXNG, collects native documents, evaluates coverage
and evidence gaps, and drafts/reviews an answer through the existing research
loop. Exact citation validation checks retained native text, not translated or
invented search snippets. `answered` requires that loop's coverage, citations,
review and confidence checks; a finished call or sealed journal alone is not an
answered question or proof of factual accuracy.

For known seeds use `await collector.collect(goal, scope)`, with typed `Goal`
and `Scope`. Each call creates fresh run budgets, frontier, document index and
intent-reference state. Original-intent embedding is charged once per run when
needed, not once per collector instance. An intent too large for the configured
encoder/run allowance is refused before discovery instead of silently truncated.

## One configuration owner

Required sections are `[http]`, `[research]`, `[search]`, `[models]`, `[scoring]`
and `[extraction]`, alongside the core budget/politeness settings. The SearXNG
recipe is now retained under `[search]` in every run's effective non-secret
configuration. A supplied search adapter cannot claim a different recipe.
Existing low-level `SearxConfig` imports from `ghimera.searxng` remain supported;
omitted search sections preserve the old serialized config shape.

Search response dialect is a configuration choice. Legacy `chimera.searxng/1`
remains JSON-only with unchanged serialization. `chimera.searxng/2` requires
`response_format = "json"` or `"html"`; the facade selects the matching concrete
adapter before work. The non-active `examples/searxng-html.toml` shows ordinary
simple-theme search: it omits the JSON format parameter and uses observed result
links/snippets. HTML mode needs the pinned `html` extra and reuses `[extraction]`
for its private bounded parser worker, input/output limits and encoding. It
does not borrow source cookies, execute page scripts, solve challenges or retry
another format. Missing result envelopes or malformed selected result links
refuse rather than inventing hits. See [HTML search](C3_SEARCH_HTML.md).

MCP discovery is supported in development source with `[search] schema =
"ghimera.mcp-leads/1"` and an explicit `mcp_client` passed to `Collector` or
`Collector.from_toml`. Default tool/field mappings match the `web_search` MCP
envelope. The application owns the initialized session and credentials;
Ghimera retains discovery provenance and fetches source URLs independently.
See [MCP lead configuration](MCP_LEADS.md).

All endpoints, thresholds, paths, language choices, timing and resource limits
are configuration. `max_config_bytes` bounds one read before TOML parsing. The
immutable effective configuration is available as `collector.config` and in the
harvest receipt. Lower-level typed ports remain available for different search
providers or custom composition; the facade does not replace their invariants.

Additional supported configuration is explicit, not automatically loaded from
other files:

| Capability | Configuration and guide |
|---|---|
| PDF and DOCX | `[document_extraction]`; [documents](C2_DOCUMENTS.md) |
| Owned PDF/DOCX seeds before planning (unreleased) | `[local_inputs]` and request `local_documents`; [local inputs](LOCAL_INPUTS.md) |
| Isolated Patchright rendering | `[browser]`; [browser](C1_BROWSER.md) |
| Same-browser human assistance (unreleased) | `[human_browser]` plus explicit `human_assistant` application port; [human browser](HUMAN_BROWSER.md) |
| Native onion/open-web Tor routing | `[transport]`; [routing](TOR.md) |
| Authorized source cookies/headers | `[[source_sessions]]`; [sessions](SOURCE_SESSIONS.md) |
| Reference/citing-source expansion | `[references]`; [references](C3_REFERENCES.md) |
| Persistent locator health and generic reparse | `[extraction.locator_drift]` and `[extraction.recovery]`; [HTML](C2_HTML.md) |
| Incremental graph | `[graph]`, `[[graph.roles]]`, `[[graph.relations]]`; [graph](RESEARCH_GRAPH.md) |
| Native entity/relation extraction (unreleased) | `[semantics]` with the configured semantic graph; [semantic extraction](SEMANTIC_EXTRACTION.md) |
| Graph-aware follow-up planning (unreleased) | `[research.graph_context]` over acknowledged semantic observations; [graph planning](GRAPH_PLANNING.md) |
| Durable run observations | `[journal]`; [journal](RUN_JOURNAL.md) |
| Completed-round restart (unreleased) | `[continuation]`; [library](CONTINUATION.md) and [resumable command](COMMAND_CONTINUATION.md) |

The graph example includes question/query roles and relations needed by intent
research. Enable graph/journal by configuration and pass a new, safe `run_id` to
`run` or `collect`. Private paths are chosen by the application, never implicitly
placed on a root/home volume. Existing run identities are not overwritten.
Journals retain observations and summaries; preserve the returned harvest/result
for the original document bodies and final answer. Unreleased source now supports
explicit completed-round suspension/restart with the same run identity and
cumulative budget. Automatic reconciliation of uncertain in-flight calls remains
incomplete; it is not inferred from an unsealed journal.

For a command-line run that preserves the complete result and original document
bodies, see [COLLECTOR_COMMAND.md](COLLECTOR_COMMAND.md). It uses this same concrete
assembly and explicitly configured inputs; it does not activate model services.

## Credentials are separate

Constructor/from-TOML keyword inputs accept `model_credentials` (a mapping from
exact completion endpoint to `pydantic.SecretStr`), a separate
`encoder_credential`, and `source_credentials` (session IDs mapped to typed
`SourceCredentials`). Values do not belong in TOML, examples, reports or command
arguments. No credential is discovered from ambient platform configuration.
Each configured authorization mode must match its supplied credential. TLS and
explicit private-address/plaintext controls stay with the model client; source
credentials stay within their exact origin/path/session scope. Search does not
borrow source-session credentials. Direct/Tor source routing does not route
private model-control calls through Tor.

`source_resolver` is an optional typed transport dependency, useful for explicit
deployment DNS or controlled acceptance. It cannot disable the existing address,
port, redirect or source-session validation.

Unreleased source also accepts optional `model_http` in `Collector`,
`Collector.from_toml` and `SelfHostedModels.from_config`. This narrow mapping
uses role keys `planner`, `analyst`, `reviewer`, `judge`, not endpoint keys:
roles sharing an endpoint may still have different immutable service policies.
Each value implements `ghimera.model_http.ModelHttpPort` and must expose the
exact configured policy for that role. Unknown roles or policy drift refuse
at construction. Omitted roles retain their existing native `PinnedModelHttp`.
Injected transports own credentials; a `model_credentials` entry targeting
any injected role's endpoint is refused, including endpoints shared by roles.
The application owns the injected transport's lifecycle. Its wrapper can
delegate unchanged requests to `PinnedModelHttp`; native prompt construction,
response validation, evidence, accounting and recovery remain in Ghimera.
Injection adds no TOML fields or serialized configuration and supplies no
lease renewal, serving lifecycle, retry or UNKNOWN reconciliation policy.

Unreleased `human_assistant` is a narrow application-owned interaction port, not
a cookie import or credential-discovery route. The explicit `[human_browser]`
recipe binds one dedicated local Chromium target. Collection after assistance
continues in that same browser session and retains observed DOM as a distinct
source kind through extraction, graph, private archive and completed-round
resume. Selected browser origins never silently retry in the HTTP client's
different session. Browser subresource traffic remains operator-managed and
unmetered by the run; see [the boundary](HUMAN_BROWSER.md).

## Current evidence and remaining scope

The composed path is exercised with actual HTTP, SearXNG, HTML worker,
embedding/completion clients, disk graph and disk journal against controlled
local servers. Their model responses are protocol fixtures, not a real LLM
evaluation or independent-judge measurement. See [evidence](COLLECTOR_EVIDENCE.md).

This facade supports HTML/XHTML and, when explicitly configured, PDF/DOCX.
Other advertised/requested MIME types refuse before outbound work rather than
pretending an extractor exists. Plain text and additional formats are not yet
assembled. Browser/document/Tor/session adapters have their separate tests;
that does not establish representative public-corpus acceptance for their full
composition here. Camoufox/optional nodriver, Marker, representative multilingual
document/publisher quality, real served-model calibration, fine-grained in-flight
recovery and runtime/egress acceptance remain in the original completion tracker. The
command/archive are included in 0.3.0; the separately documented local-file
intake, challenge recovery, semantic graph/planning and completed-round
continuation additions remain unreleased source.
