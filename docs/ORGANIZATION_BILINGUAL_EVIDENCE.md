# Native institutional ontology diagnostic: trial 12

Observed 7 October 2026 UTC against feature-branch revision `3669af9`.
This is a bounded real-model diagnostic, **not quality acceptance**, a full
live Collector run, an organization chart, or a completed organizational graph.

## Source, policy and execution

The retained [official Chinese constitutional PDF](https://download.12371.cn/wenjian/2022/11/1/djcbesddz.pdf)
and its native extraction are unchanged. Source content hash:
`0b75acd4280e0b331ac479996411b0dd652350f8fce903d117fcb942028167cc`.
The parsed text contains 24,400 characters. Selected windows were 0–1,500,
3,000–4,500 and 15,000–16,500, not a representative sample. The last contains
an explicit congress-to-committee election passage, supplying a positive
relationship case absent from trial 11's selected windows.

The diagnostic explicitly selected bilingual institutional/office definitions
and directional relation definitions through the existing semantics/4 policy.
It retained dimensioned verification/2 and distinct model identities. Definitions
exclude abstract doctrines/economic systems and distinguish offices from their
holders or institutions; they do not blacklist particular Chinese strings.
Because these definitions and the intent changed, **three new extractor calls**
were made. Old proposals were not relabeled under the new policy.

Current governed catalogue reads returned these active, unwithheld entries:

- Extractor: `model.Qwen--Qwen3-30B-A3B-GPTQ-Int4`,
  `Qwen/Qwen3-30B-A3B-GPTQ-Int4` at
  `9b534e4318b7ebc3c961a839f13eb18b1833f441`, thinking disabled.
- Reviewer: `model.openai--gpt-oss-20b`, `openai/gpt-oss-20b` at
  `6cee5e81ee83917806bbde320786a8fb61efebee`, medium reasoning.

Both phases used explicitly configured 16,384 context tokens, 6,144 output
tokens, temperature zero, a 120-second request limit and a 600-second phase
budget. Model names/revisions were checked again before admission. Advertised
revision matching does not cryptographically inspect the actual weight files.
Compared with trial 11, ontology, intent, extraction calls, one selected window,
reasoning and output/request budgets changed together: this is **not an isolated
ablation** attributing an improvement to one change.

The worker used `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16,
importing this checkout's `src/ghimera`. Admission used
`/home/gompert/.venvs/taipan/bin/python`, Python 3.13.14, importing the inspected
editable SDK. Offline validation ran before each admission. Source material
stayed on the laptop; nodes inferred only. There was at most one admitted test
GPU at a time: extractor startup `job-2b2f94eaefdf` became ready, its consumer
was released and the job was observed cancelled before reviewer startup
`job-5b2876ad8df2` was admitted. Its consumer was subsequently released and that
job was also observed cancelled. Credentials/capabilities were not retained.

## Observed result and remaining failures

These are ungoverned working diagnostic fields, not calibrated accuracy or a
published platform analysis. Retained mentions count source-local occurrences,
not resolved entities.

| Field | Result |
| --- | ---: |
| `semantic_calls` / structurally valid proposals | 3 / 3 |
| Proposed mentions / relationships | 16 / 9 |
| `review_calls` / successful reviewed windows | 3 / 2 |
| `reviewed_chars` | 3,000 |
| `accepted_mentions` / `quarantined_mentions` | 1 / 7 |
| `projected_relations` / `quarantined_relations` | 0 / 4 |
| Extractor / reviewer phase seconds, excluding startup | 106.532 / 218.575 |

- Opening window: the extractor proposed eight mentions, seven absent from
  that source window. The reviewer now accepted the present `中国共产党`,
  unlike trials 10–11, and rejected the absent titles. Deterministic native-span
  checks quarantined those seven and all four dependent relationships. The
  new recipe did not solve extraction hallucination.
- Economic passage: extraction returned empty lists instead of the earlier
  two abstract-concept false positives. But `中国共产党` is actually present
  in this passage, so an empty proposal is an omission, not complete correct
  extraction. The reviewer marked coverage incomplete, without providing a
  source-bound list of the allegedly missing institutions/positions. Its vague
  coverage reason must not be promoted into new entities.
- Election passage: the extractor proposed the expected directional election
  assertion among five relationships, but also a generic institutional class,
  an institution typed as an office, and a title that does not match its native
  line wrapping. The review call ended `model_unavailable` at 120.016 seconds,
  with no HTTP status, usage or returned review. No projection was accepted for
  this window. A request deadline is the observed boundary; the retained data
  does not establish upstream token truncation or the server's exact cause.
- The successful opening review still marked all four null-date validity
  checks unsupported because no dates were supplied. That repeats the prior
  conflation of unknown dates with an unsupported date assertion. Original
  judgments remain unchanged; no relation is retroactively admitted.
- All proposed mention/relationship confidence values remain 1.0. These are
  not calibrated probabilities or useful accuracy estimates.

Successful review prompt/output tokens were 4,450/3,843 and 3,552/496. New
extraction usage was 3,408/1,357, 3,420/13 and 3,446/1,502. The final review's
usage is unknown; it is not counted as zero spent tokens. Its failure is retained
separately from its successful original extraction call.

Fresh-process read-only replay with the same owned Python 3.11.16 interpreter
reconstructed two projections and verified six ledger rows, three graph batches,
four nodes and two total research/mention trace edges. These are **zero
organizational relationships**. Retained source/proposal/config/ledger/graph
file hashes were unchanged. The first sandboxed asynchronous replay stalled
and was stopped; only the completed bounded native-thread replay is acceptance
evidence for storage/reconstruction. No model call was repeated for replay.

**Decision:** keep the recipe diagnostic-only. The present-name judgment improved
and the earlier concept false positives disappeared in this small sample, but
hallucination, omission, office/institution typing, date-check semantics and
positive relationship acceptance remain unresolved. Next work must improve the
source-grounding/coverage contract and separately test positive relationship
review within a suitable explicit generation/deadline budget. Do not silently
increase production defaults, rewrite failed judgments, lower thresholds, or
declare an empty/all-rejected response complete.

## Retained artifacts and unchanged release status

Private ignored inputs/helpers are `gate-work/organization-bilingual-recipe-12.json`,
`gate-work/organization-independent-live.py` and
`gate-work/organization-bilingual-replay-12.py`. Evidence roots are
`gate-work/organization-bilingual-extract-12` and
`gate-work/organization-bilingual-review-12`. Previous trials were not overwritten.

| Artifact | SHA-256 |
| --- | --- |
| Explicit recipe | `edab020140580544f9c6db3dfd435cd40206475d07de5b81b710658c5d57d202` |
| New proposal packets | `93b6790bbf0214e791f11573df5885d8e7fe7632d7d623b07509e39d887a3a1f` |
| Extraction summary | `7af742d48cd4747ba1db4df16c3897d0ca83a629bee460cff3fee1aad8f99918` |
| Review summary | `bb0ae0dd281b2f1c06e58eaa89ca21b7bd0913f0a42f28a4b19750fa40edcb02` |
| Review ledger | `1169626091bff02845a0314d54b3fad848dc931651f650b9cf4a34bf509da6ef` |
| Graph snapshot | `2fb9a329c5ae6a3db96bb24fc2af0b5a9533f0b6f09bc663a0373e49ad87ad0c` |
| Phase transition | `74755919efb85fdf12819a465b9249606ddd9183318c67eb954784b1a97b9983` |

No production source/configuration, release tag, published package or pool service
changed. The previous full source gate is not a fresh gate for this diagnostic
and is not model-quality evidence. Catalogue entries were unwithheld; real
relationship quality, representative chart/OCR acceptance, alias/temporal
resolution, persistent expansion and downstream publication remain open.
