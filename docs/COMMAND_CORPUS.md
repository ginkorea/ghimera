# Configured collector corpus

`ghimera.collector-command/7` composes an existing command `/2`–`/6` with an
explicit native disk corpus. The inner command and its receipt formats are
unchanged. Plain commands `/1`–`/6` still have no additional corpus owner.

Run `ghimera-collect --job /absolute/job.toml --max-job-bytes <operator-bound>`.
The outer file contains `schema`, `action`, `[command]` (the original command),
and `[corpus]`. Corpus configuration is a separate TOML `CorpusConfig`; its
SHA256 is over the exact file bytes. Credentials remain in the inner command's
existing credential bindings, not either configuration file. The native
encoder factories use that explicit encoder credential. No discovery of
platform credentials, model downloads, or default corpus paths is added.

The binding requires its version, absolute `config_path`, `config_sha256`,
`mode`, and `append_result`. `mode = "open"` additionally requires the exact
native `corpus_id` and `generation`; the full native recipe is checked on open.
An active native writer or changed file pin, recipe, UUID, or generation refuses
before collection contact. `mode = "create"` exclusively creates fresh storage,
requires append and a fresh run, and cannot claim pre-existing discovery or
retained-reader identities. Populate it from ordinary collection, then configure
the existing corpus discovery/retained reader against the returned UUID and
recipe identity. Creation does not remove fresh storage on a later collection
failure; inspect that owned corpus explicitly.

The command owns and closes only the corpus it opens. Collection still uses the
existing Collector factory, exact request/output reservation, recovery store,
budgets, and cancellation. A service cannot accept `/7` as its inner command:
the service already owns its separately configured corpus. Library callers can
continue injecting a borrowed corpus into `execute`, which does not close it.

The `/7` result exposes the effective non-secret `corpus_config`, raw config
digest, UUID, generation, original command receipt, and `handoff` state.
`append_result = false` uses discovery/retained evidence without appending.
Append starts only after native archive readback confirms the complete receipt.
A suspended run reports `pending` and appends nothing. An append exception
reports `failed` with the complete original archive receipt (CLI exit 2), without
classifying collection as failed or repeating source/model work. Cancellation
also preserves any already completed archive.
Failure attribution contains only a bounded category (`refused`, `invalid`,
`io`, or `unexpected`), plus the native refusal code when present. Raw exception
messages are never returned.

For explicit retry use `action = "handoff"`, an open binding pinned to the
current original corpus generation, and the original inner **run** command and
request file. This route constructs no Collector and contacts no search/source/
research-model port. Native bounded archive reservation/readback, exact recipe
and request, sealed journal rows and native source descriptors, unresolved
query/model/source holds, and graph proof must all admit the original result.
Only the existing native corpus append runs; it can encode genuinely uncommitted
passages under its own bounds. Repeating an acknowledged append is native
deduplication, not another collection. An uncertain corpus encoding remains
subject to its native explicit encoding-recovery policy, not blind retry.

Configured query/model/source completion recovery remains limited to the
documented exact acknowledged boundaries and explicit serial recovery profile.
This composition does not repair arbitrary concurrent, mid-source, mid-reader,
discovery UNKNOWN, or graph interruptions.

The example is inert: replace all paths, pins, UUID/generation, bounds, and the
inner command with an admitted operator-owned configuration before invoking it.

## Candidate evidence

The dedicated subprocess fixtures exercise native corpus construction (no
injected corpus), discovery and retained query-ACK process death/restart,
archive-only append retry/deduplication, typed failure attribution, full reader
recipe drift, native writer refusal, and owned/borrowed close behavior. The
bounded final selection with existing service completed-output admission cases
passed 10 tests without skips in 173.71 seconds under Python 3.11.16 against the
isolated source tree. Strict mypy passed five changed source modules; Ruff and
format checks passed six source/test files. Separate legacy command/schema
witnesses also passed. These are local protocol/source acceptance fixtures,
not live model/site accuracy, installed-wheel acceptance, or a finished release.
