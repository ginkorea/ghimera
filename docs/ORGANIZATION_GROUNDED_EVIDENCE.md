# Grounded review diagnostics: trials 13 and 14

Observed 7 October 2026 UTC against development source `e39487f`.
These are failed, bounded real-model diagnostics, not organizational extraction
accuracy acceptance, a complete Collector run, publication or deployment.

## Unchanged source and original proposals

Both trials reused the native Chinese constitutional PDF and trial-12 proposals
described in [the earlier evidence](ORGANIZATION_BILINGUAL_EVIDENCE.md).
The source content hash remains
`0b75acd4280e0b331ac479996411b0dd652350f8fce903d117fcb942028167cc`;
the original proposal-packet hash remains
`93b6790bbf0214e791f11573df5885d8e7fe7632d7d623b07509e39d887a3a1f`.
Original intent, institutional/office definitions, directional relation
definitions, thresholds, source spelling, line wrapping and assertions did not
change. No new extractor calls were made and no old judgment was rewritten.

The explicit reviewer transition selected verification/3 with at most four
concrete omission findings, neutral unasserted dates, 6,144 output tokens and
a 180-second client request deadline. Trial 13 selected the election passage
15,000–16,500 first, followed by the opening passage 0–1,500, with two review
calls and a 480-second phase budget. Trial 14 selected only the same unchanged
election proposal, one review call, a 240-second phase budget, and explicitly
changed reasoning from medium to low. These are diagnostic policies, not
increased production defaults or an isolated deadline ablation.

A fresh governed catalogue read returned `model.openai--gpt-oss-20b` as active
and unwithheld, model `openai/gpt-oss-20b`, revision
`6cee5e81ee83917806bbde320786a8fb61efebee`. Admission checked that pin again.
Workers used `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16,
importing this checkout's `src/ghimera`; admission used the inspected SDK through
`/home/gompert/.venvs/taipan/bin/python`, Python 3.13.14. Offline preparation
validated native source/proposal bindings before admission. Collection stayed
on the laptop; model nodes inferred only.

At most one test GPU was admitted at a time. Trial-13 startup
`job-3a9175da9ba0` became ready, its consumer was released, and the job was
observed cancelled before trial-14 startup `job-dd5aac67ebe3` was admitted.
The latter consumer was also released and its job observed cancelled.
No host, runtime, shared service or production model configuration changed.

## Actual failures, not rejected factual claims

These fields are ungoverned working diagnostics. They do not estimate model
accuracy, calibrated confidence, entity completeness or an exhaustive graph.

| Trial/window | HTTP status | `latency_seconds` | Outcome |
| --- | ---: | ---: | --- |
| 13 / election | 500 | 120.553 | `model_unavailable`; no review returned |
| 13 / opening | 200 | 117.250 | `semantic_extraction_failed` in post-response review validation |
| 14 / election | 200 | 46.280 | `model_unavailable`; final `content` was null |

The first failure occurred before the configured 180-second client deadline.
The inspected managed-inference forwarding source defaults to 120 seconds;
this is consistent with the timing, but does not establish the exact deployed
server revision or prove its error cause. Trial 13 did not retain raw responses,
so its 105-byte HTTP error body and the opening response's exact rejected field
cannot be reconstructed from hashes alone. Neither failed request says that
the source's explicit congress-to-committee election is unsupported.

Trial 13's final diagnostic validation also found a harness defect: when every
review failed, the private harness retained review rows without corresponding
extraction observations. It exited nonzero and did not produce a final graph
snapshot or summary. Those original failed artifacts remain unchanged. The
harness was corrected for trial 14 to pair failed reviews with unchanged
extractor provenance, clearly marked as retained observations, not new calls.
This was not a production ledger relaxation.

Trial 14 privately retained only the bounded HTTP response, never request
headers, credentials or capabilities. Its returned envelope had one choice,
matching model, `finish_reason=stop`, null final-answer `content`, no refusal,
and nonempty reasoning. Usage was 5,183 prompt tokens and 1,890 completion
tokens, total 7,073. Intermediate reasoning was not substituted for a final
review. No relationship or mention was accepted, and no unsupported judgment
was fabricated. Exit zero means the diagnostic collected and replayed its
failure correctly; it is not a quality pass.

Fresh-process read-only replay using that same Python 3.11.16 interpreter
verified trial 14's two ledger rows and two durable graph batches, with three
research/document trace nodes and one trace edge, zero semantic windows and
zero organizational relationships. Source, original proposals, configuration,
ledger and graph hashes remained unchanged.

## Actionable, non-secret completion diagnostics

Following this observation, development source adds the optional typed
`completion` field to model-call evidence. Its contract is
`ghimera.completion-shape/1`: returned choice count, model-match boolean,
bounded finish-reason vocabulary, missing/empty/present final-content state,
and refusal/reasoning presence. No response text, reasoning, provider error
prose, returned foreign model identity or credential is retained in this field.
Unparsed/error envelopes have no invented shape; ambiguous choice sets have
no invented single-choice state. Legacy calls without the field serialize
unchanged. This client-owned telemetry is removed from model output schemas.

Parsing the unchanged trial-14 response through the new boundary produced:

```json
{"schema":"ghimera.completion-shape/1","choices":1,"model_matches":true,"finish_reason":"stop","final_content":"missing","refusal_present":false,"reasoning_present":true}
```

The raw-response hash matched its original receipt before this projection.
This is derived diagnostics over retained evidence, not a fresh model call or
an alteration of the original receipt. It does not repair the serving defect,
invent a final answer, switch models or introduce automatic retry/fallback.

The diagnostics field remains validated and serialized in evidence but is
excluded from generated model-facing JSON schemas. The first full gate found
one legacy schema-fingerprint regression: 607 passed and one failed in 530.35
seconds, using the owned Python 3.11.16 interpreter above. The fix preserves
that original fingerprint; the frozen assertion was not updated or removed.
The bounded follow-up covering completion diagnostics, served-model behavior,
grounded reviews and semantic factorization passed 97 tests, zero failed and
zero skipped, in 13.02 seconds on that same interpreter and checkout. These
were local protocol fixtures, not new model requests or CAPTCHA acceptance.

The subsequent complete `scripts/gate.sh` run passed 608 tests, zero failed
and zero skipped, in 529.33 seconds using
`/tmp/chimera-c0-20261006/.venv/bin/python` (Python 3.11.16), importing this
checkout's `src/ghimera`. Offline lock validation resolved 137 packages;
lint passed, 138 files were formatted, and strict typing passed for 94 source
files. The installed Chromium fixture used the configured network isolator;
protocol servers were local and ambient credentials were unset. This gate
verifies source compatibility and regression behavior, not live model quality,
remote CAPTCHA-solving success, publication or deployment.

**Decision:** keep organizational quality acceptance open. Next work must address
the serving final-answer contract and bound review workload to the actual
forwarding window, then verify positive relationships and concrete omissions.
The full organizational workflow, chart/OCR acceptance, identity decisions and
persistent multi-run expansion remain open as tracked in
[ORGANIZATION_RESEARCH.md](ORGANIZATION_RESEARCH.md).

## Retained artifacts

Private ignored recipes are `gate-work/organization-grounded-recipe-13.json`
and `gate-work/organization-grounded-recipe-14.json`; evidence roots are
`gate-work/organization-grounded-review-13` and
`gate-work/organization-grounded-review-14`. No secret or raw model response
is committed to the repository.

| Artifact | SHA-256 |
| --- | --- |
| Trial-13 recipe | `fadfb4a49d51e5583b18de38e2073d4f90aaea20b66612473b4dfd32eb426678` |
| Trial-13 cases | `6698253a90e9c0567b7d01a63505815686200df3c20a8a2a18a233a57dbc73de` |
| Trial-13 original incomplete ledger | `09609f835d427805556f0b30e52cd8d113596972fc9f0a383f26e437fb069f80` |
| Trial-14 recipe | `16e5b989accb2ba9a3350eada2882c9f3a1a02e16d964baf4d8efe1ded33f4e8` |
| Trial-14 cases | `6bb79f5ef16ad2c436195003fd77ae91f506d46f9c92f14c7cf9631269024a55` |
| Trial-14 summary | `7612b070979c4dd7ba846f22cc8ec11db22e5760598199180c498cab68cef0fe` |
| Trial-14 ledger | `aa796e1d8b750bf28e2da603f48c07d626299163dce08fc712b5992f17eb0194` |
| Trial-14 graph | `a10b9cd09e382c26d068f65b4646aadd785063f28a2a332050db53d894947bd5` |
| Trial-14 private response container | `e540b40530b994fd128a9157fb36c20dd00369b35c0238c118e0738e6bb9f8d1` |
| Trial-14 raw HTTP response | `e7e76b96ffbe3be741281e06f191986a82abf42d3e9e63ad7ad3d9704d139f4a` |

## Trial 15: complete-batch diagnostic

Observed 7 October 2026 UTC against development source `b2fcc4a6`.
This is a failed bounded real-model diagnostic, not a complete Collector run,
organizational accuracy acceptance, publication or deployment.

The same source and proposal hashes above were verified unchanged. This
diagnostic selected only the retained election passage 15,000–16,500, with
all eight original mentions and five original relationships. No new extraction
was performed, no original assertions were changed, and no failure was retried.
The explicit verification/4 policy selected at most four mentions or two
relationships per call, plus a final coverage-only call: six calls required
and checked against the remaining allowance before any review. Each actual
call reserved its own budget. The configured allowance was six reviews/six
judges, a 600-second worker wall budget, low reasoning, 2,048 output tokens
and a 100-second request deadline. This is a changed diagnostic policy, not
a controlled ablation establishing which setting caused the result.

A fresh governed catalogue read and admission rechecked the same active,
unwithheld GPT-oss model/revision documented above. The broker's read-only
health response reported version 0.4.12; this does not establish the deployed
forwarder's exact source revision. Worker and admission interpreters/import
roots were the same Python 3.11.16/3.13.14 environments documented above.
Collection stayed on the laptop. One test GPU was admitted; consumer
`0303228457a34623bde780a6c69dc07d` was released and the session's own startup
`job-6f80f090b062` observed cancelled. No pool, service, model or runtime
configuration changed.

| Actual selection | Latency, seconds | Prompt/completion tokens | Observation |
| --- | ---: | ---: | --- |
| Mentions m1–m4 | 16.708 | 5,274 / 499 | HTTP 200, one matching-model choice, stop, nonempty final answer; structurally valid review |
| Mentions m5–m8 | 16.773 | 5,274 / 627 | Same completion shape; final JSON failed the explicit review-dimension consistency validator |

The second answer marked m7's overall verdict `supported`, its named-entity
check `supported`, but its required role check `unsupported`. The unchanged
`GroundedSemanticReview` validator requires the aggregate to match every
dimension. Parsing the retained final JSON reproduced exactly that validation
error. The current adapter records such a validation failure as
`model_unavailable`; that code does not mean this HTTP service was down or
that final-answer content was absent. Both calls retained non-secret completion
shape, usage, latency, hashes and their original selected keys.

The first answer's structural validity also does not prove factual quality:
its written reasons treated Central Committee and National Congress references
as generic and demanded an explicit existence claim. That reasoning needs
comparison against the configured institutional definitions and native passage;
it must not be accepted as evidence of correct entity rejection merely because
the JSON validated. Neither changing those judgments nor ignoring a failed
dimension would be an acceptable way to make the diagnostic green.

The complete review stopped after two actual calls. No relationship or
coverage call ran, no assembled review was invented, and no entity or
relationship claims were projected. The worker finished in 33.619 seconds
and replayed its failure; exit zero means diagnostic collection succeeded,
not model quality acceptance. A separate read-only process verified three
ledger rows and two durable graph batches (three research/document trace nodes,
one trace edge, zero semantic windows), with retained file hashes unchanged.
Its initial sandboxed invocation timed out awaiting the asyncio thread result;
the identical read-only replay completed outside that sandbox under explicit
native approval. It made no network/model calls and changed no evidence.

**Decision:** final-answer delivery improved in this particular bounded batch,
but complete review and organizational quality remain open. The next semantic
acceptance work must address role-specific review reasoning and consistent
aggregate verdicts, then demonstrate supported relationships and concrete
omissions. Preserve the full source/proposal, strict dimensions and actual-call
evidence; do not substitute reasoning, manufacture support or silently retry.
This result does not close any real-provider CAPTCHA acceptance gap.

Private ignored recipe: `gate-work/organization-batched-recipe-15.json`.
Evidence root: `gate-work/organization-batched-review-15`. Only the bounded
response containers were retained there; no request headers or credentials
are committed.

| Artifact | SHA-256 |
| --- | --- |
| Trial-15 recipe | `f644cc33bade8aec82ea9377c878af91bc912852696edbdf182ea61919bc51f0` |
| Trial-15 cases | `8456c1f9a2c72e401a83161cfe137595e004d73cf024beb457e5c92160a7c756` |
| Trial-15 summary | `491b4566ef70b1c098748796680d44860d82f7d1b0ac229567fa4345c1eecd25` |
| Trial-15 ledger | `2b977f856caba5a8874d3c8d08acd9dff9722b8f7f423ab5d547ce99771eed19` |
| Trial-15 graph | `c907060e6fbf4651da6d0c0c917013408a81efff40eff016fd5b47a1b0298b7d` |
| Trial-15 response container 1 | `ddc567a4a55908667651a299e115102fc1d7b746acd8d9582fa6422e4d7af097` |
| Trial-15 response container 2 | `f533f8c8a147efec89120e0d8e18f8bcb5285c03049a1c3b8d18bc6d865260b7` |

## Trial 16: assigned-role review diagnostic

Observed 7 October 2026 UTC against full-gated source `fa95756`.
The explicit `assigned_role_checks` option selected review prompt revision 5;
all source bytes, original proposals, ontology definitions, thresholds, model
pin, generation settings, partition limits and budgets stayed as in trial 15.
The source/proposal hashes above were rechecked unchanged. No extraction or
automatic retry ran. This is a bounded review diagnostic, not an actual full
Collector run or a validated organization graph.

The source gate used the owned Python 3.11.16 interpreter/import root above:
631 passed, zero failed or skipped in 534.41 seconds, with lint/formatting and
strict typing green. The live worker used that same interpreter; admission used
the inspected Python 3.13.14 SDK/import root documented above. A fresh governed
read and admission rechecked the same active, unwithheld model/revision. Broker
health reported 0.4.12 at build `08d2bccd8f529dc4311da479a1d39898eb5170fa`;
no service or model configuration changed. One test GPU was admitted, consumer
`6a247787cfb34b7191635f77f5bad0cb` released, and its own startup
`job-5a839a186d7a` observed cancelled.

| Actual selection | Latency, seconds | Prompt/completion tokens | Observation |
| --- | ---: | ---: | --- |
| Mentions m1–m4 | 22.308 | 5,511 / 791 | Structurally valid dimensional review |
| Mentions m5–m8 | 24.443 | 5,511 / 797 | Structurally valid dimensional review |
| Relationships 0–1 | 14.832 | 5,491 / 498 | Final JSON failed relation-dimension consistency |

All three returned HTTP 200, one matching-model choice, stop and nonempty final
content. Both mention batches supplied all original selected keys with consistent
dimensions. This does not establish entity accuracy: the reasons accepted the
generic party-organization phrase as a specific body and a secretariat as a
position, requiring further native-source/type evaluation. Increased support
counts are not themselves an improvement in quality.

For both selected relationships, original `valid_from` and `valid_to` are null.
The returned JSON nevertheless set `validity.asserted=true`, with an ambiguous
date assessment, while setting the overall relation verdict to supported.
Read-only parsing reproduced `relation summary must match asserted review
dimensions`. Even a consistent ambiguous verdict would not repair the false
asserted-date flag: the existing grounded validator separately compares it to
the unchanged original dates. The answer also described composition-based
direction as an implication, not proof of the configured directing predicate;
structural repair alone would not prove correct entailment.

The complete review stopped after three actual calls in 61.792 seconds. No
remaining relationship or coverage call was made and no assembled review or
semantic graph claim was fabricated. Fresh-process read-only replay verified
four ledger rows and two durable trace batches: three research/document nodes,
one trace edge, zero semantic windows. Retained file hashes remained unchanged.
Diagnostic exit zero records this failure correctly; it is not quality success.

**Next bounded action:** constrain the deterministic date-assertion input state
in model-facing schemas, keyed to each selected original relationship, without
inventing or changing dates. Preserve old prompt/schema behavior through an
explicit versioned profile. Continue to assess entailment/direction independently,
and do not coerce contradictory verdicts, weaken native validators or substitute
reasoning for final evidence. Complete review and real organizational quality
remain open, as do actual CAPTCHA-provider and the broader capability acceptance.

Private ignored recipe: `gate-work/organization-assigned-role-recipe-16.json`.
Evidence root: `gate-work/organization-assigned-role-review-16`.

| Artifact | SHA-256 |
| --- | --- |
| Trial-16 recipe | `d23753eda5a681daf43e47a42347f7261fd243abc91e07323da28df2577b6da7` |
| Trial-16 cases | `8456c1f9a2c72e401a83161cfe137595e004d73cf024beb457e5c92160a7c756` |
| Trial-16 summary | `c6e5bee529730f6157c703dc6ea5ca8792079dca2063b8953f02f89ad70006e2` |
| Trial-16 ledger | `20b9a12db74b90a2d9ac999523294db0f83a0afeff01b87a47309dd9f9815c2d` |
| Trial-16 graph | `bf51d2fc78056657236e7316234b3ce50bddc0537cf88835181c5429d366b884` |
| Trial-16 response container 1 | `bebef4458760735a5552e0c22d4065a4214685415eb8d9054507a1b26c77d999` |
| Trial-16 response container 2 | `5f42312775b906d2ff0993e126da34352451a24de1ba2c24a174d1e29a0748e2` |
| Trial-16 response container 3 | `0ebc13fa22ff4e1e5611b3d9c54fcd91ffeaf2a1345496405eeedd4c2b74cd11` |
