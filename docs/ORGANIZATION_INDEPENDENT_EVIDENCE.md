# Native organization extraction with an independent reviewer

Observed 6 October 2026 (Hawaii), completed 7 October UTC. Trial 10 is a
real-model, two-phase diagnostic. It passes source/protocol replay but **does
not pass organization-role or relationship-quality acceptance**. Neither
model agreement nor a high model score establishes a factual graph.

## Source, recipe and execution boundary

The retained [official Chinese constitutional PDF](https://download.12371.cn/wenjian/2022/11/1/djcbesddz.pdf)
has SHA-256 `0b75acd4280e0b331ac479996411b0dd652350f8fce903d117fcb942028167cc`.
Its production native extraction contains 24,400 characters over 47 pages.
This is constitutional text, not an organization chart. No translation, OCR,
native-text normalization, guessed offsets or repaired model proposals were
used. Earlier trials remain recorded in
[ORGANIZATION_MODEL_EVIDENCE.md](ORGANIZATION_MODEL_EVIDENCE.md).

The source revision was `327637bb8883efd3957c67e2159feba696f80078`.
The worker used `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16,
importing that checkout's `src/ghimera`. The admission client used
`/home/gompert/.venvs/taipan/bin/python`, Python 3.13.14, with an editable SDK
from its inspected source tree. Model catalogue entries and advertised
revisions were read before admission; this does not cryptographically inspect
the actual weight files.

| Phase | Pinned served model | Revision | Explicit generation option |
| --- | --- | --- | --- |
| Extraction | `Qwen/Qwen3-30B-A3B-GPTQ-Int4` | `9b534e4318b7ebc3c961a839f13eb18b1833f441` | thinking disabled |
| Review | `openai/gpt-oss-20b` | `6cee5e81ee83917806bbde320786a8fb61efebee` | low reasoning effort |

The recipe selected `ghimera.semantics/4`, the defined-ontology profile,
organization/position roles, and `reports_to`, `member_of`, `elects`, `directs`
with explicit definitions. Limits were 1,500 native characters per window,
17 windows/calls per phase, eight proposed mentions/relations per window,
8,192 model context tokens, 3,072 output tokens, temperature zero, a 90-second
request deadline and 900-second phase budget. The graph threshold was 0.8;
these are recorded operational choices, not new Python defaults.

Extraction finished and released its one broker-admitted test GPU before
review admission. Both consumers were released and their newly created
startup jobs were explicitly observed cancelled. Source material stayed on
the collector host; model nodes performed inference only. Credentials remained
private and are absent from the retained evidence.

The reviewer consumed the retained original proposals, not new extractor
calls. Its endpoint and the owned graph sink were rebound in a recorded
phase transition. The semantic policy and original extractor call evidence
were unchanged. This is **not** acceptance of a single live `Collector` run,
a complete harvest/journal, simultaneous model deployment, or restart-safe
in-flight external-call recovery.

## Observed accounting

| Measurement | Observed result |
| --- | ---: |
| Charged extraction calls | 17 |
| Structurally valid proposals | 16 |
| Proposed mentions / relationships in valid proposals | 113 / 63 |
| Mentions failing deterministic native occurrence matching | 26 |
| Charged review calls | 16 |
| Successfully reviewed/projected windows | 15 |
| Native characters in those windows | 21,400 |
| Retained mention observations | 54: 45 organization, nine position |
| Quarantined mentions / relationships in projected windows | 51 / 48 |
| Projected semantic relationships | 12 |
| Threshold-held edges | 0 |
| Reviewer coverage assessments | 12 incomplete, three adequate |
| Extraction / review phase seconds | 811.440 / 292.054 |

Counts of retained mentions are occurrences, not distinct resolved entities.
The 12 edges comprise eight `directs`, two `member_of`, one `elects` and one
`reports_to`; all remain `model_asserted`. The 54/51 and 12/48 splits cover
only the 15 projected windows, not the failed review window. These timings are
single diagnostic observations excluding startup/queue time, not a throughput
benchmark. No measured precision, recall or probability calibration follows
from these counts.

The review at native offsets 9,000–10,500 refused its response contract despite
HTTP 200. Its actual call records the refused outcome; the original extractor
call separately remains successful. The extraction at 16,500–18,000 also
refused, leaving 3,000 characters without a successfully reviewed projection
across those two windows. Failure records were not overwritten by retry.

The diagnostic recorder initially lost call metadata for the refused extractor
window when validation failed after a returned proposal. Its exact cause and
response cannot be reconstructed. The ignored diagnostic helper now stores
the original return/call before validation for future trials; this does not
retroactively recover this trial's missing evidence. Production `SemanticStage`
already retains the call before proposal validation and was not patched here.

## Quality failures that remain visible

- At offsets 0–1,500, the reviewer rejected `中国共产党`, claiming its exact
  text was absent, even though deterministic native matching found it. The
  graph correctly honored the configured review refusal, but the reviewer
  made a false-negative source judgment.
- At offsets 3,000–4,500, both models accepted `社会主义市场经济体制` and
  `中国特色社会主义` as organizations. The review reasons merely establish
  that the strings occur, not that they satisfy the institutional-role
  definition. Exact text and independent agreement therefore remain
  insufficient for role accuracy.
- Other retained proposals label `党的纪律` as an organization and
  `中央书记处` / `党支部` as positions. These contradict the configured
  distinction between institutions, offices and abstract concepts.
- Reviewer-supported edges include organizations allegedly directing the
  generic `党的基层组织` in the 18,000–19,500 window. The presence of both
  strings does not establish that particular target/direction. They are not
  promoted as verified hierarchy or corroborated facts.
- The same concept can be rejected in one window and accepted in another;
  all extractor mention confidence values were 1.0. That is neither calibrated
  confidence nor a reliable rule for graph admission.

The mechanisms successfully preserve originals, quarantine neighbors and
replay acknowledged projections. They do not solve correlated model error,
role identification, entailment, omissions, alias identity or temporal
hierarchy. A stricter, explicitly versioned role/entailment review contract
and repeatable native-source rejection cases are the next quality boundary;
silently rewriting these results or weakening validators is not a fix.

## Retention and replay

Private, ignored evidence is under
`gate-work/organization-independent-extract-10` and
`gate-work/organization-independent-review-10`. These directories contain
effective configurations, original proposals, the phase transition, ledger,
graph batches and summaries. They contain no credential values. The worker
verified bound review/projection rows, serialized native reprojection and
graph-batch node/edge identity replay before writing its successful summary.
An additional fresh-process, read-only replay with the same Python 3.11.16
interpreter verified all 15 retained windows, 32 ledger rows and 15 graph
batches (57 nodes, 67 total edges). Those totals include research-trace and
mention edges, not 67 organizational relationships. An initial diagnostic
used a wrong field name and failed; its corrected sandboxed asynchronous
read stalled and was terminated. The bounded native-thread replay then passed.
Neither unsuccessful diagnostic is counted as acceptance or a source defect.

| Artifact | SHA-256 |
| --- | --- |
| Extraction summary | `0b986be529bbc7ca2aad8084dbe2f1bc060c06104d92d5312dc100b5bfb1cd91` |
| Original proposal packets | `9d1a631e272fe7aba4099ffec619cbe49ef85a3144b4e22b4ee7dd0ed3e5307b` |
| Review summary | `688e9fad04480716a0c0fc2351d4f3f01b94ccea618ff9108bc3a7be3d1f00a1` |
| Review ledger | `db3baf74cbca809e2c232e59c5b52873ef13da582eef63397a8b5874bc632ffc` |
| Graph snapshot | `0c82d34dae5bb5d64543b013a66cc55db70bdc8e9e96bafee41a47ff5328af23` |
| Phase transition | `ee3a1c3ddf50f73b0d3c09bd6d9d4ffb653a397507d8ae2cf919ac2ae73895da` |

No production source changed for this diagnostic. The earlier 509-test source
gate remains an implementation check, not acceptance of this model recipe.
The feature branch is still unreleased. Live CAPTCHA acceptance, chart/OCR
topology, alias/temporal resolution, multi-run graph expansion and downstream
graph publication remain separate requirements.
