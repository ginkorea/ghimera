# Owned local document seeds

Status: unreleased source after 0.3.0. The configured intent API and command can
admit explicitly pinned PDF/DOCX files before the first research plan. They use
the same extraction, scoring, judging, deduplication, graph and archive path as
collected documents; there is no second document-research implementation.

## Configuration

Add a `[local_inputs]` block to your collector configuration. The values below
are examples, not active paths or recommended universal resource limits:

```toml
[local_inputs]
schema = "ghimera.local-inputs/1"
allowed_roots = ["/absolute/chosen-volume/owned-documents"]
max_files_per_run = 4
max_input_bytes = 20000000
max_total_bytes = 40000000
```

Also configure `[document_extraction]` as described in
[the document guide](C2_DOCUMENTS.md), including the chosen private worker,
offline artifacts and parser limits. The local per-file allowance must not
exceed the parser's input allowance. Local reads also consume the run's shared
byte allowance and deadline; subsequent extraction, encoding and model calls
consume their existing budgets. File reads are not HTTP requests and do not
increment the fetch count.

The application explicitly chooses absolute, non-root input directories and
separately controls access to them. A seed must be a regular, nonempty file
under one of those directories. Symlink files and symlink directory components
refuse. The reader opens components without following symlinks, limits its read,
checks the file did not change during that read and verifies the requested hash.
Admission does not make a document relevant or automatically accepted.

## Request and API

Obtain a SHA-256 for the exact original file, for example:

```bash
sha256sum /absolute/chosen-volume/owned-documents/organization.pdf
```

Add `local_documents` to the existing JSON research request. Replace the path
and placeholder hash; the all-zero hash is not a usable document pin:

```json
{
  "intent": "Identify the organizational relationships supported by this document",
  "local_documents": [
    {
      "path": "/absolute/chosen-volume/owned-documents/organization.pdf",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "content_type": "application/pdf"
    }
  ]
}
```

Use that request with the existing [intent command](COLLECTOR_COMMAND.md), or
construct its typed equivalent:

```python
from pathlib import Path

from ghimera import LocalDocumentSeed
from ghimera.research_types import ResearchRequest

request = ResearchRequest(
    intent="Identify the organizational relationships supported by this document",
    local_documents=(
        LocalDocumentSeed(
            path=Path("/absolute/chosen-volume/owned-documents/organization.pdf"),
            sha256="0000000000000000000000000000000000000000000000000000000000000000",
            content_type="application/pdf",
        ),
    ),
)
# With your configured Collector and actual model/search services:
# result = await collector.run(request, run_id="organization-001")
```

DOCX uses MIME
`application/vnd.openxmlformats-officedocument.wordprocessingml.document`.
There is no directory scan, implicit upload, format sniffing fallback or model
download. Missing policy, out-of-root paths and excess requested file counts
refuse before collection. Actual read/hash failures are recorded before any
planning or discovery. Cancellation drains the bounded reader owned by the run
and accounts bytes already read; it does not leave an unaccounted background
file reader.

## Evidence and privacy

Source identity is `urn:ghimera:local:<sha256>`, not a fake web URL. Different
original bytes produce a different identity. Retained provenance binds byte
count, original hash, MIME, effective input-policy digest and reader revision.
The private result archive includes the original bytes, native text, parser
provenance, verdict and matching `local_input` ledger observation. Graph and
journal readers validate the same policy and source bindings.

Input filenames and paths are not placed in source evidence, graph nodes or
ledger records. The effective configuration still contains the explicitly
chosen input roots, and the supplied request itself contains private file paths;
protect those files as well as the result archive. Content and question text can
themselves be sensitive. Admission does not imply permission to share either.

Native quotations cite the content-addressed document with native offsets and
source hashes. Uploading a chart does not prove its arrows or hierarchy were
correctly interpreted. Links retained from a local document are observations;
local admission does not automatically queue them. The planner can use the
seed's native evidence to formulate queries through the existing search path.

## Acceptance boundary

Bounded checks exercise actual offline Docling PDF/DOCX conversion, native
citations, graph/journal readback, byte/hash/path refusal, cancellation and
per-run quotas. A command composition check uses an actual PDF and concrete
collector/archive with controlled local model/search protocol fixtures.
These checks do not establish entity completeness, diagram interpretation,
Chinese organization accuracy or independent model entailment quality.

The full `scripts/gate.sh` run on 6 October 2026 used this worktree's
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing
`/tmp/chimera-c0-20261006/src/ghimera`. Offline lock validation resolved 137
packages; lint and formatting passed for 114 files, strict typing passed for
81 source files, and **387 tests passed, zero failed, zero skipped**, in
425.50 seconds. This included the existing isolated browser checks and the
actual local PDF/DOCX conversions; inference/search responses in composition
tests were local protocol fixtures. No live challenge gateway, remote crawl,
credential or GPU job was used.

See [organizational research](ORGANIZATION_RESEARCH.md) for the remaining
semantic extraction, identity resolution and graph-driven continuation work.
The immutable published 0.3.0 artifacts do not contain this local intake change.
