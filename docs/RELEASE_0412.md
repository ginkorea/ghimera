# Ghimera 0.4.12 release acceptance

Status: PREPARED, NOT PUBLISHED. No tag, wheel or upload exists for 0.4.12.
Source checks below are development evidence, not the frozen `scripts/gate.sh`
gate that earlier releases record; that gate (with the browser, isolator, OCR
and font fixtures) and installed-wheel acceptance remain to be run before
publication.

## Changes under acceptance

See [throughput and limits](THROUGHPUT_AND_LIMITS.md): run limits off by
default with spend still accounted, page-local refusals, cut-not-refuse
encoder texts, transient model/encoder retry, the in-process first-pass reader
and persistent rich-tier workers, concurrent per-page encoder batches, refusal
`detail` sentences, dotted run ids, preferred research languages and fair
scoring of bare-host cited-by leads.

Deliberate contract changes:

- `page_budget`, `byte_budget`, `judge_budget`, `wall_seconds`, research
  `max_rounds`/`query_budget` and scoring encoding budgets are typed
  `int | None` (`float | None`); a recipe value of `0` now means no limit
  instead of refusing at parse time.
- `MODEL_UNAVAILABLE` on one page no longer stops collection as `failed`.
- The planner response schema gains the optional `SearchQuery.language`
  (its schema fingerprint is re-pinned in the test suite). Recovering a 0.4.11
  plan-phase call under 0.4.12 was not tested and may be refused as a changed
  request.

## Source tests

Interpreter: `/home/gompert/data/workspace/ghimera/.venv/bin/python`, CPython
3.11.17, environment from `uv sync --python python3.11 --all-extras` against the
committed `uv.lock`. The suite was run from a detached worktree of the release
candidate with `PYTHONPATH=src` and without the gate's browser/OCR fixture
variables.

| Tree | Passed | Failed |
|---|---|---|
| 0.4.11 (`6b1bae9`) | 1,947 | 124 |
| 0.4.12 candidate (`c94ea2e`) | 1,996 | 124 |

The 124 failures are the identical set on both trees: browser, human-browser,
navigation, redirect, download, visual and OCR tests that require the fixture
executables `scripts/gate.sh` names (`CHIMERA_TEST_BROWSER`,
`CHIMERA_TEST_ISOLATOR`, `GHIMERA_TEST_TESSERACT`, ...). No 0.4.12 change
introduced a failure. Ruff check, Ruff format and `mypy --strict` pass;
`uv lock --check` passes.

## Measurements

Shared development host, CPython 3.11.17, with other suites running (load
average 8 to 22), so absolute numbers are noisy:

| Workload | 0.4.11 per-page worker | 0.4.12 persistent worker | 0.4.12 fast reader |
|---|---|---|---|
| 1.1 MB HTML page, first read | 8.4 s | 7.3 s (cold start) | 0.14-0.18 s (language model load) |
| same page, warm | 8.5 s | 2.4 s | 0.07-0.09 s |
| 30 article pages concurrently | 52.3 s | 5.3 s | 0.24 s |

The median in-process time for the 1.1 MB fixture is about 70 ms on an idle
CPU; `test_one_megabyte_page_reads_in_under_100_ms` asserts the best of three
reads under 100 ms and can fail on a saturated host.

A page with 50 long outbound links scored with an encoder bound of
`max_text_chars = 256`, `max_input_chars = 2048`: 0.4.11 refused the page's
scoring as `budget_exhausted` with no detail after 3 serial calls; 0.4.12
scored all 50 links in 10 concurrent calls (0.03-0.05 s against a 20 ms
fixture encoder), with up to 201 characters cut from one link text and
recorded as `omitted_chars`.

## Remaining acceptance

The frozen gate with fixtures, installed-wheel acceptance, publication and live
served-model behaviour (real encoder 409s, real page mixes) are not established
by this record.
