# MCP discovery leads

Development source now composes an explicitly bound MCP client into `Collector`.
This is a discovery adapter, not an MCP server, model backend, tool autodiscovery
or credential store. It calls the configured tool using each research query;
returned URLs enter the existing scoped, budgeted collection loop. No initial
source URL is required.

## Default wire

The defaults match `web_search`:

```json
{
  "isError": false,
  "structuredContent": {
    "data": {
      "query": "the submitted query",
      "answer": "provider synthesis, not collected evidence",
      "queries_used": ["the provider's actual search"],
      "notes": [],
      "leads": [
        {"url": "https://example.org/report", "title": "Report", "snippet": "Lead only"}
      ]
    },
    "handling_summary": {},
    "result_ref": null
  }
}
```

The array is `structuredContent.data.leads`, **not** `structuredContent.leads`.
When structured content is absent, one MCP text block containing the same JSON
envelope is accepted. Generated `answer` text is not mined for URLs or promoted
to native document evidence. Extra fields, including provider provenance and
the handling envelope, remain in the bounded raw discovery observation.

An MCP error, malformed/missing lead array, mismatched echoed query, or an empty
response saying the provider did not search is a failure, not a successful
search with no hits. Legitimate empty arrays remain valid. Scope and Tor routing
are enforced by the existing collection boundary, not granted by an MCP result.

## Configuration and an existing SDK session

Replace the search section of a complete Collector configuration:

```toml
[search]
schema = "ghimera.mcp-leads/1"
endpoint = "https://mcp.example.org/mcp"
# Defaults: tool_name="web_search", query_argument="query", limit_argument="limit"
# results_path=["data", "leads"], url_field="url", title_field="title",
# snippet_field="snippet"
```

Use the official MCP SDK in the hosting application; Ghimera itself does not
need a new SDK dependency. Inside that application's already initialized,
authenticated `ClientSession` context:

```python
from pathlib import Path
from ghimera import Collector
from ghimera.mcp_leads import SessionMcpLeadClient

client = SessionMcpLeadClient(session, endpoint="https://mcp.example.org/mcp")
collector = Collector.from_toml(
    Path("collector.toml"), max_config_bytes=65536, mcp_client=client
)
result = await collector.run("Find evidence about the organization")
```

The caller owns session establishment, authentication, verified transport,
redirect policy, receive-size limits and teardown. No credential is read from
the environment, stored in configuration or copied to source requests. Ghimera
adds a per-call deadline, serialized response byte cap, search/fetch budgets,
ledger and retained response digest. Missing or endpoint-mismatched clients
refuse before model/source work. The generic CLI does not create MCP sessions;
use the embedding API until an explicit CLI transport binding is implemented.

## Onion discovery

The same adapter can call an application-owned `onion_search` MCP tool. Configure
its tool name and result path; return observed HTTP(S) onion URLs, not addresses
invented by a model. Configure the collector's existing verified Tor policy and
source scope separately. A failed Tor path never falls back to direct access.

Recommended follow-on: an Ahmia-based operator-owned index plus a searchable
directory of sources successfully observed in earlier research. Ahmia publishes
its [search service](https://github.com/ahmia/ahmia-site),
[index](https://github.com/ahmia/ahmia-index) and
[crawler](https://github.com/ahmia/ahmia-crawler). Its hosted service's
[terms, updated 25 September 2026](https://www.ahmia.fi/terms/), prohibit scraping
without permission; no public-site scraping adapter is implemented here.
An own index still needs seed acquisition and maintenance: installing the code
does not provide Ahmia's existing corpus.

Initial provider federation and deterministic stagnation switching now compose
through [the configured routing boundary](DISCOVERY_ROUTING.md). An onion-index MCP
server remains follow-on work, not a feature implied by this client adapter.
See [discovery design](GROUNDED_DISCOVERY.md). Provider-specific result-use terms
still apply through MCP; Google-grounded results do not become unrestricted
crawl seeds by changing transport.

## Verification boundary

Tests use the inspected `web_search` envelope, both MCP encodings, explicit
tool arguments, configurable mappings, byte/error handling, borrowed SDK-shaped
sessions, and a complete Collector run over local search/model/source protocol
fixtures with paired result readback. This is wire/composition evidence, not a
claim of live platform authentication, hosted search coverage or model accuracy.

Combined development source passed `scripts/gate.sh` on 7 October 2026 UTC:
**739 passed, exit 0**, in 629.45 seconds; Ruff, formatting and strict mypy
(105 source files) passed. The interpreter was Python 3.11.16 and imported this
worktree's `src/ghimera`, not a sibling installation. Browser fixtures used
installed Chromium 151.0.7922.34; search/model/source services were controlled
loopback fixtures. No hosted search, real account or external inference was used.
This records source verification, not publication or platform activation.
