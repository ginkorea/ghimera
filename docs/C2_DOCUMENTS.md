# Offline document conversion

Status: standalone candidate. Actual Docling DOCX conversion, structured tables
and native PDF text are implemented and exercised. These do not close the
PRD's full model-based PDF layout/table/OCR or Marker acceptance.

## Ownership and configuration

`chimera.document-extraction/1`, parsed once at the main configuration's
`document_extraction` field, supplies the absolute worker interpreter and
private scratch path, concurrency/deadlines (including cleanup), byte/text/page
and ZIP expansion limits, CPU threads, language candidates and PDF pipeline.
`examples/documents-native.toml` is an explicit non-active native-text baseline.
Its operator must supply a private interpreter and scratch directory; no shared
runtime or personal cache is changed. The TOML omits `artifacts_directory` in
native mode because TOML has no null; the model supplies only that absence.

The pinned `documents` extra uses `docling-slim[convert-core,format-docx,format-pdf]`
**2.134.0**, `docling-core` **2.99.0** and Lingua **2.1.1**. Format extras do
not install Torch/CUDA or a model server. Full `standard` PDF processing needs
a separate compatible document-worker runtime with Docling's local model
dependencies and explicitly admitted, offline artifact files. A model directory
is not downloaded on first use: every file must match the configured relative
path, size and SHA-256 manifest, with no undeclared files or symlinks. Missing
artifacts refuse before collection, not downgrade to native PDF text.

`DocumentExtractor` implements the existing `Extractor` port;
`DocumentExtractionSuite` composes HTML and document extractors by MIME type.
Neither refetches a source, follows a reference, loads a browser or reaches a
model service. The existing ladder owns scope, robots, Tor and source I/O.
All originals remain bytes in the harvest; language detection does not translate
them. References are link candidates, not scope-expansion permission.

## Worker and failure boundary

HTML and binary parsing share `PassiveWorker`, which owns its subprocess,
bounded pipes, worker slots and cleanup. Child startup is shielded from losing
the process handle when a deadline fires. Cancellation/timeout aborts any open
input writer, kills exactly the owned child, drains remaining pipes without
retaining their bytes and reaps it under the configured cleanup deadline.
There is no process-name kill and no unlimited wait on an orphaned pipe.

The explicit child environment excludes ambient credentials/proxies, disables
GPU visibility and model downloading, and bounds CPU-library threads. The
passive audit guard refuses network initiation and subprocess construction
before vendor imports. This remains defense in depth, not an OS sandbox or
proof that a browser is anonymous. C5 still owns OS-level runtime controls.

ZIP preflight bounds archive entries, total expanded bytes and compression
ratio; it refuses duplicate names, traversal/absolute paths, encrypted entries,
symlink entries and XML entity/DOCTYPE declarations. Corrupt or empty conversion,
partial Docling success, missing models and oversized output refuse rather than
becoming a fabricated document. Native PDF uses Docling's real native parser;
it never claims model-recognized tables. Full standard PDF explicitly disables
remote services and external plugins. No fallback calls an external LLM.

## Structured evidence and readers

`chimera.document-layout/1` retains the complete Docling JSON and its exact hash.
The lightweight core checks its versioned envelope/hash; consumers interpreting
the full tree must use the pinned Docling reader. The worker validates the full
vendor document before serialization. This separation does not propagate an
untyped vendor mapping into the controller or import Docling there.

`chimera.document-parse/1` records source URL/bytes hash, native-text hash,
layout hash, policy digest, parser revision, pipeline (`docx`, `native`,
`standard`), title origin, page/table counts, artifact-manifest digest and
language-confidence/sample shape. Language scores are not calibrated
probabilities. Headings/first text provide a derived title; no missing publisher
identity, author or publication date is invented.

The existing `Extracted`, `Document`, `Harvest` reader and extraction ledger
carry and revalidate these records. A changed native text, layout hash, raw
source or effective parsing policy is refused on read. The generic goal loop
consumes the same extractor port, not a special case for a vendor package.

## Acceptance still open

- Model-based PDF tables, multicolumn ordering, OCR and offline model admission.
- Marker math fallback and its separate pinned dependency/model recipe.
- A representative real public DOCX/PDF corpus; the current native PDF is a real
  controlled PDF fixture, not proof of corpus-level layout accuracy.
- PDF/DOCX reference follow-up under the planned audited one-hop scope policy.
- Governed TAIPAN landing/registration and the full C5 runtime acceptance.

Primary interface consulted:
[Docling offline options, native PDF and binary streams](https://docling-project.github.io/docling/usage/advanced_options/).
