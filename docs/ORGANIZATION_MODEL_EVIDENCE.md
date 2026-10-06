# Native organization extraction: real-model evidence

Date: 6 October 2026. Status: **not accepted for organization-graph accuracy**.
These bounded checks used actual model responses, not fixture proposals.

## Retained source and recipe

The source is the [official Chinese constitutional PDF](https://download.12371.cn/wenjian/2022/11/1/djcbesddz.pdf).
Original bytes/MIME and production native extraction are documented in
[C2_DOCUMENT_DOWNLOAD_EVIDENCE.md](C2_DOCUMENT_DOWNLOAD_EVIDENCE.md).
It is constitutional text, not a visual organization chart. Native extraction
retains 24,400 characters over 47 pages, without translation or OCR.

All four trials selected `Qwen/Qwen3-30B-A3B-GPTQ-Int4`, pinned to
`9b534e4318b7ebc3c961a839f13eb18b1833f441`, through a broker-admitted,
single-GPU self-hosted model session. The recipe selected an 8,192-token
context, 4,096 output tokens, temperature zero, schema-constrained JSON,
3,000-character native windows, at most 16 mentions/16 relations per window,
and ten calls/windows. Only the first window was attempted in each trial;
failure stopped the stage. Source browsing happened on the collector host,
not the model node. Startup/queue time is excluded from the timings below.

The worker was `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16,
importing this checkout's `src/ghimera`. Credentials stayed in memory/private
stdin and were not retained with the evidence. Each consumer was released;
each trial's newly created startup job was cancelled and observed terminal.

## Observed results

| Trial | Explicit profile | Stage seconds | Result |
| --- | --- | ---: | --- |
| 01 | original `/1` | 60.702 | HTTP 200; proposal refused; `model_unavailable` |
| 02 | original `/1` | 58.463 | Correct model, finish `stop`; proposal root invariant refused |
| 03 | mention keys `/2` | 49.717 | Structurally valid 8 mentions/5 relations; source-bound projection refused |
| 04 | mention keys `/2` | 55.992 | Structurally valid 8 mentions/7 relations; 7 requested native occurrences did not resolve |

These timings are individual diagnostic measurements, not throughput or
accuracy benchmarks. No trial produced a successful semantic window or
accepted entity/relation claims. Every summary reports zero covered characters,
24,400 unread characters and one charged semantic call. Existing source/intent
graph traces replayed; that is **not** semantic graph acceptance.

Trial 02 failed the proposal's unique-mention-key/closed-endpoint invariant.
The diagnostic does not distinguish duplicate keys from unlisted endpoints.
Trials 03/04 selected the explicitly versioned mention-key prompt without
relaxing validators. Both returned syntactically valid proposals. Trial 04
reported zero wrong citation IDs, unknown roles/rules or exceeded list limits,
but seven selected occurrences failed native matching. This count alone cannot
distinguish absent surface text from an incorrect zero-based occurrence index.
No normalization, guessed offset, alias merge or model-response repair was
used to make these responses pass.

## Evidence retention

The operator retains private, ignored `gate-work/organization-semantic-live-01`
through `-04` directories with effective recipes, ledgers, graph traces and
summaries. Trials 02–04 additionally retain non-secret response-shape counts;
raw provider prose and credentials are not copied into those shape records.

| Artifact | SHA-256 |
| --- | --- |
| Trial 03 shape | `1191c41ad4e3804f8597637a2a3fe00bf63590153f6896fb1b80937bd36ffb31` |
| Trial 03 summary | `6cdfb3ec629dc22e5442fd9559c0f8e9f026e564671a66e14dd1892c115ed6e5` |
| Trial 04 shape | `f7540b889b344e323ec2a1683bbe829b50694aad5250905707e2074345d0da79` |
| Trial 04 summary | `caef0f224bc4a7e9ec78d5344340def08aa6f6a6689c6c176b7ad30b4d6f4f87` |

The unchanged version-1 serialization and explicitly selected version-2
profile passed the complete package gate: **472 passed, zero failed, zero
skipped**, 500.74 seconds. Offline lock validation resolved 137 packages;
lint passed, all 127 checked files were formatted and strict typing passed
for 89 source files. Browser/protocol fixtures remained local and isolated.
These checks establish implementation invariants, not real-model quality.

## Next acceptance boundary

Distinguish missing native surface strings from incorrect occurrence indices
in a subsequent bounded diagnostic before choosing a remedy. Preserve native
text and failure records; do not silently normalize model text into evidence.
Real entity/relationship accuracy, entailment, coverage, alias/temporal
resolution and visual-chart handling remain open in
[ORGANIZATION_RESEARCH.md](ORGANIZATION_RESEARCH.md).
