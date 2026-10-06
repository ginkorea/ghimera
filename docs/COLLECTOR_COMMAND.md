# Intent command and complete-result archive

Status: included in the ghimera 0.3.0 release line. The command uses the concrete configured `Collector`,
not the demo doubles. It does not start services, acquire GPUs, download models,
discover platform credentials or bypass a source's access controls.

## Run an intent

Install the extras and prepare the running private model/search services described
in [COLLECTOR.md](COLLECTOR.md). Copy `examples/collector-command.toml`,
`examples/research-request.json` and the collector recipe. Put your question in
the request's `intent`; `seeds` are optional known document URLs. Edit the command
with absolute paths, a fresh run ID, a fresh output directory, and explicit read
and output allowances. The output's parent must already exist on your chosen
storage volume. Do not put secret values in any of these files.

```bash
python -m ghimera --job /absolute/path/collector-command.toml --max-job-bytes 100000
```

`chimera.collector-command/1` is a typed, frozen contract. Unknown fields/newer
versions, relative configured paths, special input files, oversized reads and
invalid requests refuse before discovery. `max-job-bytes` bounds the outer TOML
read; `max_input_bytes` bounds each referenced configuration, request, credential
binding and vector file. `max_result_bytes` bounds the full serialized result.
These are caller limits, not promises of available disk space. No implicit output,
model, source policy, resource reservation or timeout is chosen by the command.

The existing configuration owns all network/model/politeness/budget decisions.
The command validates the request through that collector before reserving output.
It then exclusively creates an owner-private output directory, before planning
or paid work. Existing directories are not reused or overwritten. A failed or
interrupted command may leave an incomplete directory and any configured graph
and journal; preserve them, use a new identity for a new run. Automatic resumption
of a previous frontier remains incomplete.

## Explicit credentials

For services configured with bearer authorization, optionally set
`bindings_path` to a JSON `chimera.command-credentials/1` file. It names environment
variables, **not credentials**. Only those named variables are read; there is no
ambient platform discovery, cloud fallback or token search. The non-active
`examples/credential-bindings.json` illustrates completion, embedding and source
cookie inputs; remove unused entries. Every completion key is bound to its exact
configured endpoint. Embedding keys have their separate input. Source keys must
match configured session IDs/header names and remain scoped by the existing
origin/path/session policy. Source keys never become search or model credentials.
Private-address, TLS/plaintext and redirect policy stays with each transport.

Empty/missing/control-character credentials refuse before collection. Resolved
values are `SecretStr` inputs, not receipt fields or displayed command arguments.
The command never echoes parser/validation exception messages, input values,
question text, bodies, endpoints or tokens. Stdout is a small archive receipt,
not the research answer. Preserve the private result for authorized consumers.

## Complete artifacts and reading

The output directory is `0700`; `result.json` and `receipt.json` are `0600`.
`result.json` is the full `chimera.research-result/2`: original bytes, native text,
retained duplicate occurrences, source and extraction provenance, verdicts,
effective configuration, ledger, graph snapshot, questions, rounds, draft/review
and exact native-text citations. Successful discovery responses also retain raw
bytes, parsed hits, query/question IDs, transport and their ledger bindings.
It is not just a journal summary or search snippet. These responses count toward
the serialized-result byte allowance; callers must budget archive space as well
as fetch bytes. Legacy `/1` archives remain readable without a raw-discovery
retention guarantee. Failed search requests retain their refusal observations,
not fabricated successful responses.

Files are fsynced and published without overwrite. The receipt is written last,
with run ID, result byte count/SHA-256, status and document count. No receipt means
no complete archive, even if a result file exists. The reader requires an
owner-private, non-symlink directory and owner-private regular single-link files,
enforces its byte allowance, verifies the hash/count/status and revalidates the
entire research result. Failed and partial outcomes are retained honestly.

```python
from pathlib import Path
from ghimera.result_archive import ResearchResultArchive

result = ResearchResultArchive.read(
    Path("/absolute/path/results/research-001"), max_bytes=50_000_000
)
print(result.status)
```

A checksum is not a signature or proof of publisher/model truth. A sealed archive
does not upgrade a partial result or establish real-world model accuracy.

Exit codes: `0` = stored answered result; `1` = stored partial/failed result;
`2` = named refusal or input/storage failure; `130` = keyboard interruption.
Named diagnostic lines omit raw exception values. Missing artifacts, interruptions
or unsealed output do not mean the research finished.

## Acceptance boundary

Tests exercise concrete HTTP/search/extraction/embedding/completion adapters on
controlled servers and native result persistence/replay. Their model replies are
protocol fixtures; this is not calibration or a real LLM quality measurement.
Representative publishers, real-model decisions, Camoufox/Marker, runtime/egress
acceptance and the platform seam remain in the original completion tracker.
Exact gate/artifact evidence is in
[COLLECTOR_COMMAND_EVIDENCE.md](COLLECTOR_COMMAND_EVIDENCE.md).
