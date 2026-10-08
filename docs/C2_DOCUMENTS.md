# Offline document conversion

Status: standalone source candidate. Actual Docling DOCX conversion, native PDF
text and model-based offline PDF layout/table/OCR are implemented and exercised.
The controlled raster-PDF check does not close representative-corpus or Marker
acceptance; the exact boundary is recorded in `C2_PDF_MODELS_EVIDENCE.md`.

## Ownership and configuration

Native worker imports expose only a private immutable inventory of this
installation's Ghimera code and its existing legacy facade, not the caller's
site-packages directory. The configured interpreter owns all dependencies.
Inventory count and individual byte sizes bound admission and copying; changed
files and links refuse. Projection creation, parsing and reaping share the
existing worker slot/deadline, and exact private code copies are removed after
the child is reaped, including refusal/cancellation. No serialized configuration
or dependency pin changes. The same package-only helper serves passive workers
and the isolated browser's read-only package bind. This fixes installed-package
dependency collisions; it establishes no OCR recognition quality.

`chimera.document-extraction/1`, parsed once at the main configuration's
`document_extraction` field, supplies the absolute worker interpreter and
private scratch path, concurrency/deadlines (including cleanup), byte/text/page
and ZIP expansion limits, CPU threads, language candidates and PDF pipeline.
`examples/documents-native.toml` is an explicit non-active native-text baseline.
Its operator must supply a private interpreter and scratch directory; no shared
runtime or personal cache is changed. The TOML omits `artifacts_directory` in
native mode because TOML has no null; the model supplies only that absence.

`chimera.document-extraction/2` additionally requires an explicit
`ghimera.document-media/1` policy. `pdf_download_types` admits only selected
generic binary MIME types (`application/octet-stream`, `binary/octet-stream`)
and only when the original bytes begin with `%PDF-`. The actual PDF parser must
still validate the document; a signature is not validity or safety proof.
There is no extension-based guess, HTML relabeling, generic ZIP/DOCX sniffing,
or automatic fallback. The fetch/research scope must separately include the
selected MIME type; `Collector` checks both before any source/model I/O.
Use the non-active `examples/documents-downloads.toml` recipe at the full
configuration's `document_extraction` field. `/1` continues to reject generic
binary downloads and omits the new field, preserving its existing digest.

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

### Pacific scanned-PDF candidate (not yet published)

`examples/documents-pacific.toml` selects `ghimera.pdf-models/2` and an owned
in-process `tesserocr` adapter inside the existing Docling worker. It declares Simplified Chinese (`chi_sim`),
Traditional Chinese (`chi_tra`), Japanese (`jpn` and `jpn_vert`), Korean (`kor`),
Tagalog/Filipino (`fil`), English, Indonesian, Malay, Vietnamese and Thai packs.
These are native OCR inputs, not translations. Select a source-specific subset
through configuration; loading every pack is not an accuracy or speed policy.
Keep the artifact manifest complete even when a run selects a subset.

The new contract requires the Python binding's exact package version and native
engine version, an artifact-relative data directory, nonempty ordered language
packs, page segmentation, orientation policy, OCR mode and scale. Orientation
may preserve the source or use explicitly thresholded native detection; a weak
orientation signal never silently rotates a page. Text and source boxes come
from one recognition result, not a second recognition of tightly cropped lines.
All selected `.traineddata` files
and the pinned vendor's auxiliary `osd.traineddata` must be admitted by size and
SHA-256. There is no system tessdata or guessed-language fallback. Native OCR
executes inside the existing bounded parser worker, not an unmanaged CLI child.
Legacy `/1` and native-text recipe digests remain unchanged.

The separate `pdf-ocr` extra adds `tesserocr==2.11.0`; the standard worker still needs
the explicit CPU layout/table dependencies above. Install that binding in the
private worker (`ghimera[documents,pdf-ocr]`) when choosing the new recipe.
Existing native-text/English installations do not acquire this new dependency.
Its native engine can vary by
wheel/build: measure it with `tesserocr.tesseract_version()` and admit the exact
version in configuration, rather than assume the system CLI is the same engine.
The sample was provisioned with `tesseract 5.5.1`. Language detection is a
separate policy: Lingua's `zh` does not distinguish Chinese scripts, and its
candidate set must not be confused with OCR pack availability. Khmer, Lao and
Burmese image packs do not imply this document detector can identify them.

Representative publisher scans, vertical/mixed-script layouts, semantic entity
extraction and retrieval quality remain separate acceptance requirements.
The first actual Chinese raster-PDF check **failed**. The owned one-pass adapter
fixes the observed tight-line re-recognition corruption. Subsequent controlled
scans recovered the required terms in Traditional Chinese, Japanese, Korean
and Tagalog; Simplified Chinese still misread a title term. This is neither
representative language accuracy nor complete acceptance. See
`PACIFIC_PDF_CANDIDATE.md`; pack admission and passing configuration tests
must not be reported as accepted document quality.

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

With the `/2` recipe, `chimera.document-parse/2` carries
`ghimera.document-media-evidence/1`: unchanged publisher MIME declaration,
resolved parser format, `declared` or `pdf_header_at_start` method, source hash
and media-policy digest. The original `Page` and raw source bytes are never
rewritten to fit the parser. Parent/worker and result readers validate the
resolution against the original bytes and effective policy. This is parser
provenance, not authentication of the publisher's claims. `/1` receipts retain
their original shape. Actual primary-source fetch observations and their
remaining parse-acceptance boundary are in
[C2_DOCUMENT_DOWNLOAD_EVIDENCE.md](C2_DOCUMENT_DOWNLOAD_EVIDENCE.md).

The existing `Extracted`, `Document`, `Harvest` reader and extraction ledger
carry and revalidate these records. A changed native text, layout hash, raw
source or effective parsing policy is refused on read. The generic goal loop
consumes the same extractor port, not a special case for a vendor package.

## Acceptance still open

- Representative PDF tables, multicolumn ordering and non-English OCR quality
  beyond the controlled scanned/table fixture.
- Marker math fallback and its separate pinned dependency/model recipe.
- A representative real public DOCX/PDF corpus. In addition to controlled
  fixtures, the official native Chinese constitutional PDF now passes
  production parsing and serialized reader replay (see the download evidence).
  That single text-heavy document is not corpus-level layout/chart acceptance.
- Representative PDF/DOCX reference-follow-up adequacy under the implemented
  configurable reference policy.
- Governed TAIPAN landing/registration and the full C5 runtime acceptance.

Primary interface consulted:
[Docling offline options, native PDF and binary streams](https://docling-project.github.io/docling/usage/advanced_options/).
