# Configured discovery routing and stagnation recovery

Status: development source. `Collector` composes multiple concrete SearXNG/MCP/Ahmia
bindings through its existing research, fetch, extraction, scoring and archive
owners. It does not deploy a search engine, onion index or MCP server.

## One configuration boundary

Use the optional `ghimera.discovery/1` section instead of the single `[search]`
binding. Both together refuse; omitting discovery preserves legacy recipe and
result serialization. See [the non-active fragment](../examples/discovery.toml).
Each provider declares its ID, exact adapter recipe, supported source domains,
disclosure/retention acknowledgement and independent limits. Effective non-secret
recipes remain in the run receipt. Secrets stay in explicitly injected clients.

`target_domains` selects open-web, onion or explicitly permitted mixed discovery.
It is not inferred from the intent. Each returned hit is filtered to the provider's
intersection with those domains; original response bytes remain retained. Source
scope, robots, entitlement and Direct/Tor fetch policy still apply independently.
An onion search service is not a Tor fetcher, and onion leads do not enable Tor
implicitly or permit a direct fallback.

`query_disclosure = "planned_query"` explicitly permits that bound service to
receive planner-produced queries. Those queries can contain information from
the research context. Use trusted private discovery services for private research;
this version does not automatically redact queries or claim that public-provider
disclosure is harmless. `use_contract = "crawl_and_retain"` is the operator's
acknowledgement, not a legal finding or a way to change a provider's terms.
Display-only grounding is not admitted by this recipe. The existing Google
Search grounding restriction remains in [the design](GROUNDED_DISCOVERY.md).

For MCP, bind initialized clients by provider ID:

```python
collector = Collector(
    config,
    discovery_clients={"web": web_client, "onions": onion_client},
)
result = await collector.run("Find evidence answering the research question")
```

Clients use the existing `SessionMcpLeadClient` boundary. Missing, extra or
endpoint-mismatched clients refuse during assembly. Session lifecycle and
authentication remain the application's responsibility; there is no ambient
credential discovery. The ordinary CLI can compose SearXNG-only recipes, but
does not create MCP sessions. See [MCP composition](MCP_LEADS.md).

The unreleased corpus provider uses the same interface with an explicit
`ghimera.corpus-search/1` binding. Pass a borrowed corpus as
`Collector(config, corpus=store)` for a single search binding, or as
`discovery_corpora={"retained": store}` for that exact strategy provider ID.
Missing or extra corpus bindings refuse before model/source requests. See
[the corpus interface](EVIDENCE_CORPUS.md) and the non-active
[binding example](../examples/corpus-search.toml). The CLI does not implicitly
open a corpus. A corpus query contacts only its pinned private query encoder;
retained HTTP(S)/onion URLs remain source leads with the same domain and fetch
admission as external-provider leads. Native corpus response bytes survive
domain filtering, so excluded leads remain inspectable rather than disappearing.

## Shared abstractions and accounting

`DiscoveryProviders` is a stateless configured provider set. `BoundSearch` is a
`GroundedSearch`: it implements only `request`, retains the final `discover`
template and gives each configured binding a configuration-digest identity.
`SearchHistory` owns per-run routing, usage, concurrency and retained responses.
It never combines several network calls into one paid search observation.

Every actual provider call consumes a separate global query/fetch/byte/time
allowance. Provider ceilings additionally bound calls, response bytes, cumulative
bytes, result count, request timeouts and cumulative service-call seconds.
In-flight byte and time allowances are reserved before contact. Independent
queries share a configured provider semaphore. Completed calls release their
reservations; cancellation propagates and releases all owned reservations.
Service-call seconds are summed observed call latency, not GPU time or elapsed
run time. Deadline/cancellation overhead can exceed a timer slightly; measured
latency is retained, and a spent allowance admits no further calls.

Ordered fallback advances on failures or domain-filtered empty results, stopping
at a nonempty eligible lead set. Fan-out queries providers concurrently. Cold-start
fan-out applies during the first research round when explicitly selected. A
provider failure does not discard healthy neighboring results. All owned attempts
are awaited before an aggregate budget failure propagates. Legitimate empty
responses remain observations; errors do not become successful empty searches.

Consecutive provider failures open that run's configured circuit. Quota/circuit
holds have explicit policy rows naming the provider and limiting dimension.
There is no automatic circuit reset or hidden retry spend. New model-planned
queries may use eligible alternative providers within the remaining budgets.

## Deterministic escape from unproductive branches

Each completed research round retains `discovery_progress`: newly accepted unique
document digests and newly answered original questions. A document count is not
an accuracy score. Duplicate/rejected documents do not count; ungrounded answers
cannot pass the existing coverage validator.

After `stagnation_window` consecutive rounds below both configured progress
thresholds, routing rotates the configured provider order, up to
`max_strategy_changes`. The planner still sees unresolved questions, retained
documents and graph context. This changes the discovery source without inventing
URLs, changing the original intent or declaring a gap answered.

Completed-round continuation reconstructs usage/circuits from the original
ledger and routing from retained progress. It does not reset spent budgets or
blindly replay an uncertain interrupted call. A changed recipe changes the
binding/strategy identity and cannot be resumed as the original run. State is
isolated between runs; sharing a Collector does not share a circuit or budget.

## References and archive readers

Each citing-source provider attempt has its own source-derived `ReferenceQuery`
and retained search observation. Candidate proofs bind that actual provider,
query sequence, original response digest and returned hit. Duplicate leads may
share one frontier entry; all permitted provider observations remain retained.
The cited-by query budget caps actual provider requests, not a synthetic combined
query. Legacy single-provider duplicate-query refusal remains unchanged.

`ResearchResult` validates configured identities, every successful response's
ledger binding and per-provider call/byte/result ceilings. An unknown provider
or missing successful response refuses readback. The configured strategy identity
is distinct from each actual provider identity. Multi-provider results require
the existing retained-discovery result version; no legacy result can silently
claim this provenance.

## Verification and remaining acceptance

Focused checks exercise native Collector extraction/citation/archive readback,
actual routing continuation, fallback, independent calls, concurrent neighboring
failures, provider timeouts, cancellation cleanup, source-domain separation and
configuration refusal. Sources and search/model responses are controlled local
protocol fixtures, not real-provider or model-quality acceptance.

Development verification on 7 October 2026 UTC used
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing
`/tmp/chimera-c0-20261006/src/ghimera`. The full `scripts/gate.sh` run returned
**768 passed, 1 failed** in 661.95 seconds, with no skips reported; Ruff,
formatting, offline lock verification and strict mypy (108 source files) passed.
The failure was the Tor cleanup fixture constructing a Unix-socket scratch path
under a long `TMPDIR`; the production short-path guard correctly refused it.
The fixture now uses its explicitly configured short Tor scratch parent, without
weakening that guard. A subsequent run of discovery routing, search archive and
conformance, intent research, continuation, references, MCP leads and Tor transport
returned **99 passed** in 29.71 seconds with that same long `TMPDIR`, interpreter
and source tree. Ruff and formatting passed afterward. A corrected full-suite
run then returned **769 passed in 652.91 seconds**, no skips reported, with the
same interpreter and source tree; offline lock verification, Ruff, formatting
and strict mypy over 108 source files passed. That closes the routing checkpoint's
source gate, not a later adapter's gate or real-provider acceptance.

The owner selected self-hosted Ahmia for the onion-index integration. Its bounded
implementation and live acceptance requirements are tracked in
[Ahmia integration](AHMIA_INTEGRATION.md). Its read-only native index adapter is
now development source; provisioning and real-index acceptance remain separate.

Remaining broader requirements are unchanged: real allowed-provider federation,
representative onion-index health/coverage, minimized-query and language strategies,
dedicated corpus/vector adapters, provider weighting, richer retry/recovery policy,
and independent real-model complete-research acceptance. This initial concrete
router closes neither those requirements nor the original collector scope.
