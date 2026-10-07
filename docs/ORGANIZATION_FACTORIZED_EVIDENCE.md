# Dimensioned native-document review: trial 11

Observed 7 October 2026 UTC (6 October in Hawaii), against source revision
`c477a9f3d6f1a7195b71b9e34a566ad2cbced668`. This bounded real-model diagnostic
validates the new review protocol and replay. It **fails entity-quality
acceptance**. Explicit review dimensions did not remove the earlier role
classification errors or a false-negative native-source judgment.

## Source, configuration and execution

The [official Chinese constitutional PDF](https://download.12371.cn/wenjian/2022/11/1/djcbesddz.pdf),
native extraction and original proposals from
[trial 10](ORGANIZATION_INDEPENDENT_EVIDENCE.md) were reused unchanged. This
is constitutional text, not an organization chart. Its content hash is
`0b75acd4280e0b331ac479996411b0dd652350f8fce903d117fcb942028167cc`;
the original proposal-packet hash remains
`9d1a631e272fe7aba4099ffec619cbe49ef85a3144b4e22b4ee7dd0ed3e5307b`.
No new extractor call, translation, text normalization or proposal repair was
performed. Windows 0–1,500, 3,000–4,500 and 18,000–19,500 native characters
were deliberately selected failure cases, not a representative sample.

The current governed entry `model.openai--gpt-oss-20b` returned active,
unwithheld `openai/gpt-oss-20b` revision
`6cee5e81ee83917806bbde320786a8fb61efebee` before admission. The broker
reported 0.4.12 build `08d2bccd8f529dc4311da479a1d39898eb5170fa` and offered
the selected `taipan/vllm` runtime. Advertised revision matching is not
cryptographic inspection of the actual weight files.

An explicit diagnostic transition selected verification/2, three review calls,
low reasoning effort, 16,384 admitted context tokens, 4,096 output tokens,
temperature zero, a 90-second request deadline and a 600-second phase budget.
Extractor service, ontology definitions, proposal limits and original calls
remained unchanged. Reviewer/judge endpoint, graph sink and wall budget were
rebound and recorded. These are diagnostic configuration, not Python defaults
or an activated production recipe.

The worker used `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16,
importing this checkout's `src/ghimera`. SDK admission used
`/home/gompert/.venvs/taipan/bin/python`, Python 3.13.14, importing the inspected
editable SDK. Local validation completed before the one-GPU admission. Returned
startup job `job-1af5258b1802` was observed loading, then the consumer became
ready. Afterward its consumer was released and that newly created job was
explicitly observed `cancelled`; the task-owned loopback forward was stopped.
Source files stayed on the collection laptop, model nodes inferred only, and
credentials were not retained.

## Observed result, not calibrated accuracy

| Working diagnostic field | Result |
| --- | ---: |
| `cases` / `successful_windows` | 3 / 3 |
| `review_calls` / `new_extractor_calls` | 3 / 0 |
| Protocol/response failures | 0 |
| `accepted_mentions` / `quarantined_mentions` | 3 / 19 |
| `projected_relations` / `quarantined_relations` | 0 / 14 |
| Phase seconds, excluding admission/startup | 87.344 |
| Coverage judgments | All three incomplete |

All responses supplied the dimensions and consistent aggregate verdicts.
Their actual call evidence retains request/response hashes, model/service,
successful HTTP/contract outcomes and usage. Prompt/output tokens were
4,277/1,050; 3,771/866; and 4,195/1,857. These fit the explicit allowances:
the quality failures are not explained by truncation or missing final answers.
These are ungoverned working diagnostic figures, not a published benchmark or
a governed analysis report. The three retained mentions are occurrences, not
three resolved/validated organizations, and include the two errors below.

Fresh-process read-only replay on the same Python 3.11.16 interpreter verified
three native projections, six ledger rows and three graph batches: six nodes
and four total research/mention trace edges, no organizational relationships.

## Failures and decision

- In 0–1,500, the reviewer marked `中国共产党` unsupported on both dimensions,
  saying the string was absent. Fresh reconstruction of the selected source
  context proved it is present in its native quote. This is a false-negative
  judgment, not a context omission.
- In 3,000–4,500, it retained `中国共产党` but again called
  `社会主义市场经济体制` and `中国特色社会主义` specifically named
  institutional bodies on both dimensions. Their presence does not satisfy
  the configured organization definition: the trial-10 role error remains.
- In 18,000–19,500, all mentions and six relationships were rejected; five
  relationships admitted by the previous reviewer were no longer projected.
  This removes the questionable generic hierarchy, but proves no relationship
  recall: this selected set has no independently established positive control.
- All original relationships had null validity dates, but the reviewer marked
  validity unsupported along with failed endpoints/predicates. It conflates
  unsupported relationships with unsupported date assertions. Its original
  judgments and aggregate rejections were not rewritten.

**Decision:** do not activate this recipe as a validated organization extractor.
A dimensioned schema makes errors accountable; it is not a factual judge.
Do not count all-rejected windows as completeness, tune a probability threshold
to these three cases, blacklist these Chinese strings in generic code, or
rewrite the original judgments into passing data.

Next quality acceptance must separately exercise native-role understanding,
positive and negative source-supported relationships, and omissions, with an
appropriate pinned reviewer/recipe. Model or contextual-input changes must be
explicit and compared against these retained failures. Alias/temporal
resolution, persistent multi-run expansion, chart topology and downstream
publication remain separate implementation/acceptance requirements. No full
live `Collector` composition or completed organizational network is claimed.

## Retained evidence

Private ignored evidence is `gate-work/organization-factorized-review-11`;
recipe and worker are `gate-work/organization-factorized-recipe.json` and
`gate-work/organization-factorized-live.py`. Original trials were not overwritten.

| Artifact | SHA-256 |
| --- | --- |
| Explicit recipe | `6fcaffe61f58cd801a8bcda2573cee2637dfbec7938b68c02b52bedf2c375447` |
| Summary | `6342de18d831ca56e639ef76cb506ea250dc55704808c86e26cd423b98883f75` |
| Original proposals and new reviews | `05a64dab21fb1fd229fd43128adc0d7c699eea6de7b8fb262456fdf47e18da40` |
| Ledger | `1e2e4ad4e6037d1183b97bf356487008c23830f20bdeef3c2b0880b1f9b90d94` |
| Graph | `6dfcc44d14ace5d0158c5ea40f6d02e3a7de6446f76af9fb9a81bb8cccdaf5ac` |
| Phase transition | `b899bb98f0de0cb09b8fa1178cdd9c11d0541b0e7c542a1ea969cb10d6fed12f` |

No production source changed for this trial. The prior complete 535-test source
gate proves implementation/compatibility, not this recipe's quality. The branch
remains unreleased; no PyPI artifact or production configuration changed.
The inspected model entry withheld no catalogue material. Quality acceptance
and the untested workflow requirements remain unresolved.
