# Same-browser public-document acceptance

On 7 October 2026, an owned Chromium profile and explicitly selected CDP target
captured these public publisher pages through `HumanBrowserRoute` and the shared
`FetchLadder`, then extracted native text through the configured `HtmlExtractor`:

- <https://docs.python.org/3/library/graphlib.html>
- <https://docs.python.org/zh-tw/3/library/graphlib.html>

The executing interpreter was
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, importing
`/tmp/ghimera-ahmia-20261007/src/ghimera/__init__.py`. The browser was the installed
Chromium executable configured by the caller, using Patchright **1.63.0**.
The actual extraction recipe was
`scrapling@0.4.2+crawl4ai@0.9.4+lingua@2.1.1`.

## Retained engineering observations

| Native page | DOM bytes | Extracted characters | Detected language |
|---|---:|---:|---|
| English graphlib | 46331 | 15180 | en |
| Traditional Chinese graphlib | 47221 | 11819 | zh |

These values are local engineering observations, not governed asset identifiers
or a claim of representative multilingual accuracy. Native page evidence records
`acquisition=browser_dom` and `http_status=null`; it does not invent a network
status, original HTTP body, or isolated-renderer capture.

The ledger accounted for **3 fetches and 94069 bytes**: one cached robots
observation plus the two DOM captures. Operator-managed browser subresource
traffic is **not measured** by those counts. No model calls, search queries,
source credentials, accounts or platform services were used. Robots policy was
honored, with scope restricted to the publisher and the two selected top-level
documents. The selected target survived collection; only the harness that owned
the newly created browser context closed it afterwards.

Private paired page/extraction files, effective configuration and ledger were
retained under the task-owned `human-public-1` evidence directory. A fresh process
using the same Python **3.11.16** interpreter and owned import path revalidated
configuration/capture bindings, recomputed DOM digests, checked source hashes and
URLs, and reconciled the ledger byte totals. Both native texts retained
`TopologicalSorter`, `prepare`, `get_ready`, `done`, and `CycleError`.

| Original DOM | SHA-256 content hash |
|---|---|
| English | `d3ee868680fd48a78e818801a2784d1e596e5e5c492b0ddcacd8b743fa207d9f` |
| Traditional Chinese | `a56235c04e88290df376d570b9e4d5ba8ef80757f8573652bdf2605112aa5789` |

Hashes identify retained bytes, not catalogue records. The replay did not repeat
the browser fetch or make network/model calls.

## What this closes, and what it does not

This proves bounded real public-publisher DOM capture, native extraction and
fresh-process paired evidence readback on these pages. It does not establish
full `Collector.run` intent acceptance, successful human login or challenge
completion, entitlement checks, browser-native PDF downloads, verified browser
Tor routing, representative publisher coverage, independent model quality or
production delivery. Those remain separate requirements in [C0](C0.md).

No material was masked by a platform projection; no platform catalogue was
queried. The unvalidated requirements above are unresolved acceptance gaps, not
silently omitted successes.
