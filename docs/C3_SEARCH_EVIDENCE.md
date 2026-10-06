# Retained discovery evidence — candidate

Status: standalone source candidate, not a deployed or published release.

## Ownership and guarantees

`SearchHistory` composes the existing final `GroundedSearch.discover` template
with a single research run's budget and ledger. Providers remain interchangeable
ports; no search service, endpoint or credential is selected in source code.
History is captured before downstream work and is isolated between concurrent
runs. Failed requests still consume their accounting budget and have refusal
rows; they do not manufacture a successful payload.

`chimera.research-result/2` retains successful responses and requires exactly one
observation per successful search fetch. Readback checks raw SHA/bytes, typed
response digest including parsed hits, query and question IDs, provider revision,
transport, ledger sequence and configured query/hit ceilings. Citing-source
decisions additionally require an actual hit observed after the source-derived
query and before its decision. Search snippets are still discovery leads, not
native answer evidence. Legacy `/1` remains readable with its original identity
and without claiming these new guarantees.

The complete-result archive's existing private permissions, byte allowance,
checksum readback and receipt-last publication apply to these payloads. This
change does not implement resume: interrupted/unsealed journals still lack
complete search payloads and cannot be treated as completed archives.

## Regression evidence

Measured using `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16,
importing `/tmp/chimera-c0-20261006/src/chimera/__init__.py`; no TAIPAN import.

- Before implementation, the new archive checks failed: 10 failed in 0.84 s.
- Targeted archive/research/reference checks: 34 passed in 4.81 s.
- After concurrent-run and citing-hit checks, changed modules plus importers:
  67 passed in 20.25 s. Selection: `test_search_archive`, `test_intent_research`,
  `test_reference_expansion`, `test_cited_by_expansion`, `test_collector_command`
  and `test_search_conformance`.
- Delayed async completion, a failed query and concurrent separate histories
  exercise real async scheduling, not just a count of two synchronous doubles.
- Tampering with raw bytes, query, provider, sequence, parsed hit, dropped or
  duplicated observations and citing-hit anchor/snippet all refuse.
- Strict mypy passed over 72 source files; Ruff passed.
- Full `scripts/gate.sh`: 343 passed, 0 failed, 0 skipped in 364.65 s,
  using the interpreter above. Offline lock check passed, Ruff passed,
  102 files formatted, strict mypy passed over 72 source files. Browser fixture
  inputs were the explicitly provisioned Chromium binary and `/usr/bin/bwrap`;
  platform credentials were removed from the process environment.

These are protocol/regression fixtures, not retrieval accuracy or model quality.
An initial sandboxed run stalled in a graph-persistence fixture; its exact owned
process was terminated and the bounded suites above rerun with required fixture
permissions. That incomplete run is not counted as a pass.

## Public-provider trial — 6 October 2026

The laptop made at most two credential-free public SearXNG requests using the
actual guarded connector, for a non-sensitive Python `graphlib` documentation
query. Operational endpoints came from a private trial recipe, not package
defaults. Both configured services (`https://priv.au/search` and
`https://search.ctq.ro/search`) returned `search_unavailable`; no successful
payload or result was invented. Each failure retained its actual query,
transport, spent response bytes and latency in the ledger. No authentication,
challenge solver, redirect or network-policy bypass was attempted.

Private local artifact: `gate-work/search-live-20261006/search-results.json`.
The artifact records the actual interpreter and both outcomes. The public
[instance registry](https://searx.space/) guided endpoint selection but its
availability listing does not establish JSON API availability. Successful
public-provider discovery was still open at this first trial; the refusal trial
does not close it. A later successful configured-provider and actual native
collection trial is recorded in [C3_LIVE_DISCOVERY.md](C3_LIVE_DISCOVERY.md).
A complete real intent-to-reviewed-answer run remains open.

A separate, bounded ordinary-HTML observation on the same two configured
services returned HTTP 302 to `/captcha` and HTTP 418 with a script-cookie
challenge, respectively. The connector did not follow or solve either. Raw
public responses and status/transport are retained privately in
`gate-work/search-html-probe-20261006/`. This means these particular services
cannot supply unattended acceptance under the current access boundary; it is
not evidence that HTML parsing alone will fix them. The documented support for
ordinary HTML versus optionally disabled JSON can inform a separately configured
adapter, but no implicit fallback or challenge solver is introduced here.
