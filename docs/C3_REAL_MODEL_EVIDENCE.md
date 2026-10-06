# C3 real self-hosted model trial — 6 October 2026

This is a bounded engineering trial, **not** representative quality acceptance,
a completed end-to-end spider run, or a published release. All measurements
below came from `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**,
importing `/tmp/chimera-c0-20261006/src/chimera/__init__.py`. That standalone
environment imports no platform SDK. An external private harness supplied a
consumer-scoped completion capability in memory, separately from source fetching.

## Actual inputs and boundaries

The concrete HTTP and HTML adapters fetched these public pages on the laptop,
retaining original bytes, extracted native text, hashes and transport/extraction
observations before using a GPU:

| Source | Raw bytes | Extracted characters | Role |
| --- | ---: | ---: | --- |
| `https://docs.python.org/3/library/graphlib.html` | 40,908 | 14,474 | Relevant English document |
| `https://docs.python.org/zh-tw/3/library/graphlib.html` | 41,640 | 11,039 | Relevant Traditional Chinese document |
| `https://docs.python.org/3/library/random.html` | 115,780 | 38,752 | Unrelated control |

The intent asked how `graphlib.TopologicalSorter` prepares and advances a
dependency graph and reports cycles. One supplied question was preserved exactly.
The actual served model was `openai/gpt-oss-20b`; the harness selected the
published revision `6cee5e81ee83917806bbde320786a8fb61efebee`. Compatible completion
responses were checked against the configured served-model name. Revision
binding is the harness's responsibility, not independently attested by the
compatible completion envelope.

One broker-managed GPU startup was requested, not a manually selected device.
The server context limit was 8,192 tokens; the trial used temperature 0,
2,048 maximum output tokens and explicit input/wire ceilings. Evidence selection
allowed two documents, 12,000 total native characters, 3,000-character windows
and one window per document. These were trial configuration, not source constants.
The same startup served the follow-up trials; no second startup was requested.
Every trial consumer was released. After the final trial, the original startup
job `job-045dafe7c40a` was cancelled through the broker and observed `cancelled`.

## Real failures drove the repairs

1. The initial JSON-object trial completed planning and document judgments, but
   assessment failed typed validation. A held document's second look previously
   reused the identical small excerpt.
2. JSON-Schema output made assessment structurally valid, but the model shortened
   and rewrote the source quotations while copying hashes and offsets. Two
   returned quotations were not native substrings. `CitationValidator` refused
   them; this refusal was not relaxed or replaced by fuzzy matching.
3. The source repair makes an explicit second look expand the original native
   span up to the operator's total context ceiling. The configured
   `template_ids` wire mode lets the model select provided citation IDs; the
   client restores exact native text, bounds and hashes from that call's context.
   Invented or out-of-context IDs still refuse. Public application citations,
   assessments, drafts and result archives retain their existing types.

## Final live trial

All ten calls returned typed results. Their observed task outcomes were:

| Task | Observed result | Client elapsed seconds |
| --- | --- | ---: |
| Plan | Supplied question preserved | 3.623 |
| English first look | Hold: cycle detail outside excerpt | 12.708 |
| English expanded look | Accept | 10.025 |
| Chinese first look | Hold: cycle detail outside excerpt | 7.883 |
| Chinese expanded look | Accept | 7.775 |
| Unrelated `random` document | Reject | 4.251 |
| Assessment | Answered coverage; native citations validated | 15.606 |
| Draft | Native citations validated | 13.099 |
| Review | Supported claim; exact draft digest preserved | 20.711 |
| Collection grade | **Not satisfied**: incorrectly demanded an answer draft | 9.870 |

Reported usage across those calls was **34,732 total tokens**. Summed client
call elapsed time was **105.551 seconds**, not GPU compute time, startup time,
throughput or billing. Every value is an ungoverned engineering measurement from
the standalone interpreter named above, not a platform run-report statistic.

Assessment and draft support included retained English characters 3,000–6,000
and Chinese characters 0–3,000. The actual source and extracted-text digests,
selected bounds, request/response digests, usage and omissions are in the private
trial artifacts. Successful native-span checks prove citation integrity, not
semantic entailment of every sentence. Review used the same model as the author
and cannot establish independent correctness.

Private artifacts are under
`gate-work/real-model-trial-20261006-citation-ids/`; the source preparation is
under `gate-work/real-model-trial-20261006c/`. They are ignored, owner-private
working records, not shipped corpus or credentials. The trial invoked individual
model ports with a fixed relevant-document set; it did not test autonomous search
selection, frontier scoring, reference expansion or research-loop stopping.

## Follow-up: evidence-only collection grading

The original grade above is preserved as a failure observation. A bounded repair
clarifies that grading assesses retained evidence sufficiency, not an answer
draft. Inputs declare `grading_basis = "retained_evidence"`; each call records
`chimera-collection-grade/2`. Other task prompt revisions and public result
types are unchanged. Expected outcomes were used only by the private test
harness, never supplied to the model.

The follow-up reused the same actually fetched raw/native documents and their
previous real-model document judgments. No source pages were refetched or
replaced with fixture text. It used the same published model pin, a separately
admitted broker startup, temperature 0, 2,048 maximum output tokens, two documents,
12,000 total context characters and one 6,000-character window per document.
The context window size is trial configuration, not a production constant.

All observations below came from `/tmp/chimera-c0-20261006/.venv/bin/python`,
Python **3.11.16**, importing the owned source tree named at the start of this
record. These are ungoverned engineering observations, not calibrated accuracy.

| Case | Actual retained sources | Observed `satisfied` | Client elapsed seconds |
| --- | --- | --- | ---: |
| Original graphlib intent | English and Traditional Chinese graphlib docs | `true` | 25.092 |
| Original graphlib intent, irrelevant control | English random docs | `false` | 4.306 |
| Original graphlib intent, empty control | None | `false` | 3.413 |
| Exact Napoleon birth-date request, memory control | Graphlib docs, no biographical source | `false` | 5.403 |

The positive reason identified `prepare`, `get_ready`, `done`, and cycle
reporting in retained documentation. The negative reasons identified the actual
absence of relevant evidence rather than demanding a draft. The four outcomes
matched the predeclared controls. This does not prove coverage across arbitrary
intents/languages, semantic correctness of every reason, or a calibrated
confidence score; the model returned confidence 1.0 on several cases and that
number is not an independently measured probability.

A separate readback with the same standalone Python **3.11.16** interpreter
recomputed original raw hashes and validated both selected native citations
against the retained documents. Each selected span was characters 0–6,000;
their actual text contains `prepare`, `get_ready`, `done`, and `CycleError`.
These integrity checks corroborate the bounded positive control, not general
language understanding or entailment accuracy.

Private artifacts are under `gate-work/real-model-trial-grade-20261006/`, with
their exact source-preparation and previous-result digests. The consumer was
released. Its own startup job `job-3838601f5c5c` was cancelled through the native
broker and separately observed `cancelled`; no other job or service was changed.
All source requests stayed on the laptop. The standalone library received only
a scoped invocation capability in memory, never the general platform credential.

## Remaining acceptance

- Measure evidence-only grading across a representative intent/language corpus
  with independent judgments; the bounded failure/repair above is not calibration.
- Run the full intent research loop with real search and a pinned semantic
  encoder, over a representative source/language corpus rather than three docs.
- Establish independent reviewer/judge binding and calibrated decision policy;
  successful same-model review is only a diagnostic.
- Complete the original publisher/locator, browser, PDF/OCR/Marker, checkpoint
  resume and runtime/egress acceptance. This trial does not erase those gates.

No source credential, general platform token or invocation capability appears in
this record. No source login, challenge, CAPTCHA or paywall bypass was attempted.

## Candidate regression gate

The sequential full `scripts/gate.sh` ran against unchanged production/test
source with `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**,
and the owned import path given above: **329 passed, 0 failed, 0 skipped**, in
**363.32 seconds**. Ruff lint/format passed for **100 files**; strict typing
passed for **71 source files**; the offline lock check resolved **137 packages**.
Local protocol/browser fixtures are not additional real-model accuracy evidence.

The added regressions cover expanded second-look native spans, exact citation-ID
restoration, unknown IDs in assessment and drafting, strict wire schema shape,
context round-trip/spoof refusal and the unchanged compatibility default.
The two original repair regressions were observed red before the corresponding
production fixes. A separate readback of the actual trial artifacts validated
two assessment citations and two draft citations against retained raw/native
documents, and confirmed review binding to the exact draft digest, using the
same standalone Python **3.11.16** interpreter.

Reproduction (no platform credentials or jobs):

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/path/to/installed/chrome \
  CHIMERA_TEST_ISOLATOR=/path/to/bwrap \
  CHIMERA_GATE_CACHE=/path/to/owned/offline-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```

Browser, isolation and cache paths are operator inputs. This gate requires the
declared dependencies and local fixture socket/isolation permissions; it never
restarts the model trial or treats a sandbox refusal as a product regression.

The evidence-only grader follow-up subsequently passed the same sequential
full `scripts/gate.sh` against unchanged production/test source with
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**: **330 passed,
0 failed, 0 skipped**, in **364.69 seconds**. Ruff lint/format passed for
**100 files**, strict typing for **71 source files**, and the offline lock
check resolved **137 packages**. The original bounded grade-contract regression
was observed red before the fix in both citation wire modes, then green.
This gate and the four live controls are separate evidence; neither claims
that every originally planned spider acceptance requirement is complete.
