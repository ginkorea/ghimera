# Interactive human-proxy command

Development source now binds the existing human browser-assistance port from
the CLI. This is the same Collector, same selected browser target and same
source evidence/archive path, not a second crawler or an automated login solver.

## Configure and run

1. Prepare a dedicated, caller-managed Chromium browser and choose its exact
   local control endpoint and target. Configure eligible origins/paths,
   acquisition route and assistance reasons under the collector recipe's
   `[human_browser]`. See [browser policy](HUMAN_BROWSER.md) and its example.
   This command does not discover browser profiles, launch a default personal
   browser, copy cookies or choose the first open tab.
2. Copy [collector-interactive.toml](../examples/collector-interactive.toml).
   Set the absolute recipe/request/output paths, new run identity and explicit
   byte limits. Choose presentation input limits in `[human_assistance]`.
3. Run from an interactive POSIX terminal, with stdin and stderr attached:

```bash
python -m ghimera --job /absolute/path/collector-interactive.toml --max-job-bytes 100000
```

The command uses `ghimera.collector-command/3` with an explicit versioned
`ghimera.terminal-assistance/1` policy. Legacy `/1` and `/2` remain unchanged and
cannot silently opt into prompting. The ordinary optional `/3` `[execution]`
block supports the existing run/resume behavior when the collector has durable
continuation enabled. This is completed-round restart, not retry of an uncertain
interrupted interaction.

If the selected source shows a configured challenge, login or subscription
barrier, the command reports its reason, sanitized source path, target/session,
capture identity, deadline and immutable request digest to stderr. Act normally
in that same browser using your own entitlement, then enter one of:

```text
resume <the displayed full request digest>
decline <the displayed full request digest>
```

A response applies only to that exact capture/attempt/source/policy. Stale or
unbound responses cannot resume another request. Invalid responses consume the
configured attempt allowance; oversized responses fail within the input byte
cap. No passwords, cookies, MFA answers or challenge tokens are requested in the
terminal. Query strings, fragments and userinfo are omitted from displayed
URLs; JSON escapes terminal controls and directional characters.

## Lifecycle and evidence

The adapter implements `HumanAssistant`. It serializes human prompts only,
not the collector's unrelated asynchronous work. Waiting for that prompt lock
counts against the browser request's existing deadline. Selector-based reads
do not block the event loop or create an uncancellable `input()` thread.
Cancellation and timeout remove only this adapter's reader; it never closes
the user's descriptors or changes terminal settings. The hosting application
must reserve those terminal streams for this adapter rather than attach another
reader to the same descriptor. A noninteractive terminal or missing browser
binding refuses before network/model work and output reservation.

After `resume`, the browser adapter recaptures the source and reruns all ordinary
scope, barrier, type, extraction, scoring and evidence checks. Resume is neither
an accept verdict nor permission to broaden scope. If the barrier remains,
another attempt is bounded by the existing browser policy. Decline/timeout and
failed assistance remain recorded failures; there is no HTTP fallback that
substitutes another session.

Stdout remains the small completion/checkpoint receipt. Native document content,
browser DOM provenance, assistance observations, citations, graph and ledger stay
in the owner-private complete-result archive. The collector still does not
pretend DOM capture observed HTTP status/headers or all browser subresource bytes.

## Verification and remaining scope

The complete `scripts/gate.sh` passed on 7 October 2026 UTC: **753 passed,
exit 0**, in **662.37 seconds**, with no failures or skips reported. Ruff,
formatting and strict mypy also passed (106 source files). The interpreter was
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, importing
`/tmp/chimera-c0-20261006/src/ghimera/__init__.py`. Browser checks used installed
Chromium **151.0.7922.34** and `/usr/bin/bwrap`. This is local engineering
verification, not a governed analysis result or live publisher/model acceptance.

The gate used the explicit large-volume workspace
`/home/gompert/data/ghimera-test-kfsm5U/full-terminal-2`. Its configurable
`GHIMERA_GATE_WORK` now owns the default type/lint caches and child temporary
directory, avoiding reliance on shared `/tmp` quota. Operator overrides remain
supported; production paths and collector behavior are unchanged. The earlier
attempt stopped at a lint error before running the suite; it is not counted as
a pass. The final run used the corrected source without concurrent source/test
edits.

Focused checks used actual PTYs and an installed Chromium against controlled
local sources. They exercise resume/decline, stale request binding, deadline,
cancellation/reader cleanup, malformed/oversized input, immutable legacy command
serialization, command preflight and browser-to-native-extraction/archive
readback. Self-hosted search/model replies in these checks are protocol fixtures,
not real-model quality evidence or an entitled-publisher acceptance.

The initial terminal adapter requires a POSIX selector-capable loop. Applications
on other event loops may bind their own `HumanAssistant` through the library.
Verified Tor-browser capture, real-publisher acceptance, browser downloads,
alternate passive drivers and broader collector acceptance remain open. Existing
direct/Tor HTTP collection is a separate working mechanism. See the original
[completion tracker](C0.md); this CLI addition does not narrow its scope.
