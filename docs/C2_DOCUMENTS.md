# Offline document conversion

Status: standalone source candidate. Actual Docling DOCX conversion, native PDF
text and model-based offline PDF layout/table/OCR are implemented and exercised.
The controlled raster-PDF check does not close representative-corpus or Marker
acceptance; the exact boundary is recorded in `C2_PDF_MODELS_EVIDENCE.md`.

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

## Explicit full-PDF recipe

`examples/documents-standard.toml` gives a non-active CPU recipe with exact
sizes/hashes for eight prepared layout, table and English OCR artifacts.
`pdf_models` is the versioned `chimera.pdf-models/1` boundary: exact layout
repository/revision, engine, file, detector threshold, table mode/cell matching,
image scale, explicit worker package versions and one OCR recognizer with
explicit local files. The main interpreter remains lightweight. This example
is **English OCR**, not universal OCR because language detection accepts several
languages. Other scripts require a supported recognizer and matching manifest;
there is no automatic language/model substitution.

The worker needs the package's lightweight core (`pydantic`, `curl-cffi`,
`protego`) as well as the declared document dependencies. Install into a private
worker, never into a shared runtime. The verified CPU recipe used:

```bash
uv pip install --python /path/to/private/worker/bin/python --torch-backend cpu \
  'pydantic==2.13.5' 'curl-cffi==0.16.3' 'protego==0.7.0' \
  'docling-slim[convert-core,format-docx,format-pdf,models-local]==2.134.0' \
  'docling-core==2.99.0' 'lingua-language-detector==2.1.1' \
  'torch==2.9.1' 'torchvision==0.24.1' 'transformers==4.57.6' \
  'onnxruntime==1.23.2' 'rapidocr==3.9.1'
```

Provisioning is separate from collection. The layout repository is pinned to
`40bde044036bb181c130ddf6c51792187268748f`; the accurate TableFormer files came
from `docling-project/docling-models` at
`fc0f2d45e2218ea24bce5045f58a389aed16dc23`. Prefetch the selected RapidOCR
files separately and compare them with the example manifest. Runtime never
reaches a model hub. Dependency version mismatch refuses rather than changing
models. Actual bytes, not a directory name, determine artifact admission.

`chimera.reading-order/1` selects vendor order or bounded geometric XY cut.
Gap thresholds, column direction and recursion depth are configuration. XY cut
orders observed columns without modifying text/boxes, separates full-width
heading/table bands and preserves every source block. RTL column order is an
explicit choice, not inferred from language. Ambiguous overlaps use stable
geometric order. Native PDF separator geometry retains the vendor's existing
ordering. The vendor pipeline subclass reuses layout, OCR, tables, captions,
footnotes and assembly; no vendor source or process-global state is patched.
This is a pinned integration, not a claim of semantic reading-order accuracy
on all layouts. Native `/1` policy serialization/digests omit the absent new
model field, preserving existing native-text identities.

## Reproduce local acceptance

Supply a full TOML/JSON collector configuration containing `document_extraction`, prepared
models and a PDF with explicit expectations. `examples/pdf-expectations.json`
is for the controlled scan described in the evidence, not arbitrary publishers.
`scripts/make_pdf_fixture.py --font /path/to/font.ttf --output /path/to/report.pdf`
reconstructs that raster-only scan without fetching anything; Pillow must be
installed. Fixture dates are fixed and the output must not already exist.

```bash
python -m ghimera.document_acceptance \
  --config /path/to/collector.toml --pdf /path/to/report.pdf \
  --source-url https://publisher.example.invalid/report.pdf \
  --expectations /path/to/expectations.json \
  --output-directory /path/to/private/empty-acceptance-directory
```

The real worker must meet expected text/order/page/table checks. The command
preserves original bytes, structured extraction and a versioned acceptance
receipt in a private directory, refuses overwriting prior evidence and reports
only non-secret metadata. The URL is explicitly a caller-supplied claim, **not
proof of a successful publisher fetch**. Normal collection still uses the
guarded fetch ladder. Text checks are exact observations, not a calibrated
model-quality score or proof of every table cell on an unstated corpus.

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

- Representative PDF tables, multicolumn ordering and non-English OCR quality
  beyond the controlled scanned/table fixture.
- Marker math fallback and its separate pinned dependency/model recipe.
- A representative real public DOCX/PDF corpus; the current native PDF is a real
  controlled PDF fixture, not proof of corpus-level layout accuracy.
- Representative PDF/DOCX reference-follow-up adequacy under the implemented
  configurable reference policy.
- Governed TAIPAN landing/registration and the full C5 runtime acceptance.

Primary interface consulted:
[Docling offline options, native PDF and binary streams](https://docling-project.github.io/docling/usage/advanced_options/).
