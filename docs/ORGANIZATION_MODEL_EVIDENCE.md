# Native organization extraction: real-model evidence

Date: 6 October 2026. Status: **not accepted for organization-graph accuracy**.
These bounded checks used actual model responses, not fixture proposals.

## Retained source and recipe

The source is the [official Chinese constitutional PDF](https://download.12371.cn/wenjian/2022/11/1/djcbesddz.pdf).
Original bytes/MIME and production native extraction are documented in
[C2_DOCUMENT_DOWNLOAD_EVIDENCE.md](C2_DOCUMENT_DOWNLOAD_EVIDENCE.md).
It is constitutional text, not a visual organization chart. Native extraction
retains 24,400 characters over 47 pages, without translation or OCR.

Trials 01–06 selected `Qwen/Qwen3-30B-A3B-GPTQ-Int4`, pinned to
`9b534e4318b7ebc3c961a839f13eb18b1833f441`. Trial 07 selected
`openai/gpt-oss-20b`, pinned to `6cee5e81ee83917806bbde320786a8fb61efebee`.
Both used a broker-admitted, single-GPU self-hosted model session.
The recipe selected an 8,192-token
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
| 05 | mention keys `/2` | 40.622 | Structurally valid 6 mentions/4 relations; 3 absent surfaces and 2 out-of-range occurrence indices |
| 06 | native spans `/3` | 31.892 | Structurally valid 8 mentions/no relations; occurrence indices resolved where text existed, but 3 surfaces were absent |
| 07 | native spans `/3`, second family | 93.939 | Correct model, finish `length`; output budget exhausted and no final text; `model_unavailable` |

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

Trial 05's finer diagnostic distinguished the causes: the model returned
occurrence indices `[0,1,2,3,4,5]` as if they were mention-list positions,
although indices must count each exact surface separately. Its proposal also
named a committee and secretary-general not present in the selected window.
The country name appeared only across retained line breaks, not as the model's
joined surface string. Trial 06 explicitly selected the native-span `/3`
profile: all eight proposed occurrence indices were zero, with no out-of-range
indices, but three proposed surfaces were absent. It also mislabeled concepts
and a phrase fragment as organizations. These observations do not establish
entity-role accuracy or a statistically demonstrated prompt improvement.

Trial 07 used the independently pinned second model family, not an external
model API. The response reported 3,839 prompt tokens and 4,096 completion tokens,
finish `length`, and null final content. No proposal could be evaluated. This
is an output-budget/admission failure, not a measured entity-accuracy result.

## Evidence retention

The operator retains private, ignored `gate-work/organization-semantic-live-01`
through `-07` directories with effective recipes, ledgers, graph traces and
summaries. Trials 02–07 additionally retain non-secret response-shape counts;
raw provider prose and credentials are not copied into those shape records.
Trials 05/06 retain the parsed proposals separately for source comparison;
those contain no request headers, credentials or model-call capability.

| Artifact | SHA-256 |
| --- | --- |
| Trial 03 shape | `1191c41ad4e3804f8597637a2a3fe00bf63590153f6896fb1b80937bd36ffb31` |
| Trial 03 summary | `6cdfb3ec629dc22e5442fd9559c0f8e9f026e564671a66e14dd1892c115ed6e5` |
| Trial 04 shape | `f7540b889b344e323ec2a1683bbe829b50694aad5250905707e2074345d0da79` |
| Trial 04 summary | `caef0f224bc4a7e9ec78d5344340def08aa6f6a6689c6c176b7ad30b4d6f4f87` |
| Trial 05 shape | `aabf34afcf51fac49ca69d34b3a1084fe3e43713cd6d6b645890beb6c0870a5a` |
| Trial 05 summary | `663773ec7ebf2f33bb037f8950ead5a0bcc73c1065bf36676e9c24498d040208` |
| Trial 06 shape | `8db06f84863d561717ef57532dd8fe47781602a4f4e059c473971e3abb824180` |
| Trial 06 summary | `06f9e27f5787dcddf62fe791f44ddd72b8c141bd5e754b5cf6ae0a3a3ed5801b` |
| Trial 07 shape | `706613aed02ec2d4aa57766d793769651a88b22470c173475b41ce25553fe6b7` |
| Trial 07 summary | `64b589d3843dbe70299d79a19971ebfbc38b1ed7b97d5e42c8e6960059a206dc` |

The unchanged version-1 serialization and explicitly selected version-2/3
profiles passed the complete package gate: **475 passed, zero failed, zero
skipped**, 502.80 seconds, on the Python 3.11.16 interpreter above.
Offline lock validation resolved 137 packages;
lint passed, all 127 checked files were formatted and strict typing passed
for 89 source files. Browser/protocol fixtures remained local and isolated.
These checks establish implementation invariants, not real-model quality.

## Next acceptance boundary

The native-surface/index distinction is now observed. Next, make local-model
generation/reasoning controls explicit and versioned, and evaluate real entity
roles and source-grounded proposals. Preserve native text and failure records;
do not silently normalize model text into evidence or accept concepts as actors.
Real entity/relationship accuracy, entailment, coverage, alias/temporal
resolution and visual-chart handling remain open in
[ORGANIZATION_RESEARCH.md](ORGANIZATION_RESEARCH.md).
