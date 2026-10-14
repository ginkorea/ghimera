# Ghimera 0.4.12 release acceptance

Status: PREPARED, NOT PUBLISHED. No tag or upload exists for 0.4.12.

**Acceptance is one live collection on TAIPAN** (owner, 14 Oct 2026): a wheel
built from this branch's HEAD runs on the Edge node against real sources and
served models. The full sharded source suite was deliberately not run for this
release; the targeted tests below cover the changed areas.

## What changed

See [throughput, input bounds and kept results](THROUGHPUT_AND_LIMITS.md).

- **No run ceilings.** Removed keys, each refused at load by name with
  `recipe key '<section.key>' is refused: the core no longer has run ceilings
  (removed in 0.4.12); delete the key`:
  - run recipe: `page_budget`, `byte_budget`, `judge_budget`, `wall_seconds`
  - `[research]`: `max_rounds`, `query_budget`
  - `[research.reranking]`: `max_calls` (`max_pairs`/`max_input_chars` now bound one call)
  - `[scoring]`: `encoding_call_budget`, `encoding_char_budget`
  - `[scoring.run_encoding_recovery]`: `max_total_result_bytes`
  - `[[discovery.providers]]`: `max_calls`, `byte_budget`, `call_seconds_budget`
  - `[references]`: `cited_by_query_budget`
  - `[semantics]`, `[semantics.verification]`: `max_calls_per_run`
  - `[identity_automation]`: `max_proposal_calls`, `max_review_calls`
  - `[local_inputs]`: `max_files_per_run`, `max_total_bytes`
  - `[model_work]`: `max_input_bytes`, `max_unanswered_calls`
  - `[model_work.results]`: `max_total_result_bytes`
- `RunBudget` → `RunSpend` (`ghimera.spend`), counting only. `BudgetExhausted`,
  `RefusalCode.BUDGET_EXHAUSTED` and the `budget_exhausted` stop reason are
  gone; `BOUND_EXCEEDED` refuses one input over an input bound that cannot be
  cut. Pages over `extraction.max_input_bytes` are cut (`omitted_bytes`).
- **Results are kept.** A rich-tier extraction failure keeps the page as
  plain text (`tier = "fallback_plain"`); a page with no text is refused and
  its bytes kept under `<journal>/.refused-pages/<run id>/`. A judge outage
  keeps the page unjudged and the round continues. A `failed` stop row carries
  what failed.

## Source checks (development evidence)

Interpreter `/home/gompert/data/workspace/ghimera/.venv/bin/python`, CPython
3.11.17, with the gate's fixture variables (browser, isolator, Tesseract,
tessdata, font) set. `ruff check`, `ruff format --check` and `mypy --strict`
(`src/ghimera`, `src/chimera`) pass; the package imports.

Targeted tests for the changed areas pass: `test_run_spend.py` (refused keys,
spend without limits, a run continuing past the old page budget, the 12 MiB
page read as its first 8 MiB, a real decompression bomb refused as an input
bound, an image-bomb page refused while the round continues),
`test_keep_results.py` (plain fallback, no-text refusal with archived bytes,
judge outage keeps pages, failed stop detail), and the updated suites for
extraction, extraction recovery, fetch routes, discovery routing, content
dedup, evidence context, encoders, semantics, reranking, identity automation,
model work, research recovery and continuation.

Known reds not caused by this change (identical at `c94ea2e`): inline
browser-document and navigation-guard redirect tests on this host's browser
fixture.

## Open

- The live TAIPAN collection is the acceptance; it is not recorded here yet.
- Still in the recipe vocabulary and not removed: `retry_budget` (per-request
  retries), `references.max_parents`/`max_queued_per_run`, corpus
  `encoding_recovery.max_calls`/`max_input_chars`/`max_stored_bytes`,
  `semantics.failure.max_failed_windows_per_run`,
  `challenges.max_attempts_per_run`, journal byte/record bounds.
- With identical bytes at several URLs and `max_depth >= 1`, a real collection
  was reported to build an invalid harvest (parse-attempt/duplicate-ledger
  validation); earlier tests hid it with `page_budget = 1`. Not investigated.
- Journals, receipts and checkpoints from earlier releases that recorded
  `budget_exhausted` or carry a removed key do not restore. Semantic policy
  digests and request wires changed (removed `max_calls_per_run`).
