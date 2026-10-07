# Pacific scanned-PDF candidate — 7 October 2026

Status: **native-binding source published in 0.4.1; that profile's Simplified
Chinese quality check remains failed**. A subsequent explicit RapidOCR profile
passes the unchanged Chinese required-term controls below, not a full
transcription or representative language benchmark.
Public Ghimera 0.4.0 is immutable. The [combined release](RELEASE_041.md)
does not close the infrastructure PRD or the Simplified Chinese failure below.

## Scope

`ghimera.pdf-models/2` selects explicit offline native OCR packs through the
existing bounded Docling worker. The non-active Pacific example separates
Simplified/Traditional Chinese and admits Japanese (including `jpn_vert`),
Korean, Tagalog/Filipino, English, Indonesian, Malay, Vietnamese and Thai. A
source-specific subset is an operator configuration choice. Original PDF bytes,
native text, layout, package/native-engine pins and artifact/configuration
digests retain the existing receipt shape. Legacy English `/1` and native
configuration identities remain unchanged. No model weights enter the package.

The Python binding is a separate `pdf-ocr` extra: existing `documents` installs
do not acquire a new native dependency. The worker enforces the selected
binding's package version and native engine, not the system CLI's version.

## Actual local check

Python 3.11.16 at the private document-worker interpreter:
`/home/gompert/data/workspace/TAIPAN/.codex-tmp/go-spider-docling-standard-20261006/venv/bin/python`.
Source import: `/tmp/ghimera-cadence-20261007/src/ghimera`.
Packages were actually checked: Docling slim 2.134.0, IBM models 4.0.3,
Torch 2.9.1+cpu, Torchvision 0.24.1+cpu, Transformers 4.57.6,
ONNX Runtime 1.23.2 and tesserocr 2.11.0. The binding reports
`tesseract 5.5.1`. The complete 17-file local artifact manifest verified:
`116e3ade6412f9e79fb0a4c8045af1290fd15b612439d7410fa1b575d4cbd15c`.

One controlled raster-only Simplified Chinese PDF (three authored source lines,
no PDF text layer) ran through the real parser. Source SHA-256:
`eedb338d1d2733bdb51e4f6af6d13a78512d315cb02ca82a837a9d748c9b92dc`.
Configuration: `b68b1a32cc1abb4b3ea3321af242ab027a746c2294f7672772dde4611dcf0122`.
The run took 25.875 seconds including startup and **failed** both required
terms (`台湾`, `码头`). Corrupted OCR was subsequently mislabeled `mi` by
language detection. This is neither OCR accuracy nor throughput acceptance.

A native binding probe on the same PDF's rendered pixels measured orientation
zero (confidence 0.413). Whole-image recognition recovered the latter two
Chinese lines substantially better; the vendor's tight text-line
`SetRectangle` re-recognition corrupted them. Thus automatic rotation did not
explain this case. Do not silently weaken expected text, substitute an English
translation, or count package availability as successful native OCR.

Controlled outputs/configuration/probe are retained in the owned
`.codex-tmp/ghimera-pacific-pdf-20261007` tree. Native local execution avoided
the command sandbox's separately observed child-process timeout; timeout
results are not counted as completed extraction.

## One-pass recognition correction

The candidate now uses an owned `BaseOcrModel` adapter, reusing Docling's
region selection, source geometry conversion and PDF/OCR cell merge. A single
`Recognize` call supplies each selected region's text, boxes and confidence
through `GetIterator`. No tight-line `SetRectangle` re-recognition occurs.
The legacy RapidOCR path does not import the optional native binding.
Orientation is an explicit preserve/detect policy with a finite confidence
threshold; the sample preserves source orientation.

The same five controlled raster-only inputs ran through `DocumentExtractor`
with the private Python 3.11.16 worker above. Chinese packs were explicitly
changed to the immutable `tessdata_best` revision
`e12c65a915945e4c28e237a9b52bc4a8f39a0cec`, not substituted at runtime.
The complete revised artifact manifest verified as
`01ea2d7c9a2f37b2e1b844d930cfca589228ea77bb9efe956a016c349b3e1240`.

| Input | Required-term check | Seconds including worker startup |
|---|---|---:|
| Simplified Chinese | Failed: `台湾` misread; `码头` recovered | 21.516 |
| Traditional Chinese | `臺灣`, `碼頭` recovered | 22.309 |
| Japanese | `日本`, `政府`, `港湾` recovered | 21.093 |
| Korean | `한국`, `항만`, `정부` recovered | 21.531 |
| Tagalog | `daungan`, `pamahalaan`, `mananaliksik` recovered | 19.660 |

These checks test selected terms, not full transcription correctness or a
language accuracy benchmark. All five language labels matched the intended
language (both Chinese scripts return `zh`). The aggregate acceptance remains
**failed** because of Simplified Chinese. A native comparison on its unchanged
PDF with `chi_sim` versus `chi_sim+eng`, and segmentation 3, 6 and 11,
also misread that title: neither dropping English nor changing those modes
closed the gap. Do not correct OCR by guessing the expected source text.

Inputs, exact configurations, extracted documents and reports are retained in
the owned `.codex-tmp/ghimera-pacific-pdf-best-20261007/page-iterator` tree.
The adapter's child-process contract checks passed 13 tests in 15.98 seconds
under the isolated gate Python 3.11.16; this is not a full-package gate.
The final combined document/model/media/order regressions passed **43 tests
in 83.37 seconds**, without skips, under that same interpreter importing the
candidate source. Ruff and formatting passed; strict mypy passed across 122
source modules. Source/tests/examples were frozen during the reported run.
The first attempted regression command named two nonexistent test files and
ran no tests; only the corrected, completed command is counted above. The full
package gate subsequently passed at `36d024f`: **845 passed in 692.77 seconds**,
no failures/skips, using Python 3.11.16 at the isolated gate interpreter above.
Source/tests/examples remained frozen. See CONCURRENT_COLLECTION.md for the
resolved temporary-directory browser-launch failure in the initial full run.
This is software-contract evidence, not a change to the failed Simplified
Chinese quality verdict. Publication remains pending.

## Bounded next actions

Local regression evidence (not a full-package gate): Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing the source checkout
above, passed 41 document/config/media/order tests in 81.17 seconds before
the final example/segmentation restriction. After that small addition, the
12 model/adapter tests passed in 11.72 seconds. Neither run skipped tests.
Ruff/format and strict mypy on the two changed production modules passed.
No source/test edits were made during either reported passing run.

1. Investigate the remaining Simplified Chinese title error with unchanged
   source bytes and explicit recognizer settings; do not weaken its witness.
2. Add vertical and mixed-script inputs separately; installed `jpn_vert` is
   not evidence of accepted vertical-layout quality.
3. Require representative publisher scans and semantic extraction/retrieval
   evidence before claiming language quality. Keep OCR availability, language
   identification and semantic-model admission distinct.
4. Run the full package gate before merge/publication. No tag has moved and no
   shared model runtime or platform service was changed for this candidate.

## Explicit existing-recognizer comparison after 0.4.3 publication

On 7 October, the unchanged controlled Simplified and Traditional PDF bytes
above were reprocessed through the shipped `DocumentExtractor`, not an OCR
string repair or a mocked recognizer. An explicit `chimera.pdf-models/1` recipe
selected RapidOCR 3.9.1, PP-OCRv6 small, `language = "ch"`, full-page mode and
scale 3.0. The eight-artifact manifest was checked before work:
`b5870ec8ca436990007331073af336da957fc1ddde966f0e4dbb826349580fef`.
The recognition artifact's embedded character metadata contained both scripts;
that observation alone is not quality evidence. No model was downloaded.

Both real runs used Python 3.11.16 at the private document-worker interpreter
already named above, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera` on the published source line.
The standalone interpreter resolves no platform SDK; doctor/floor do not apply.
Two CPU threads and one parser slot were declared in the effective policy;
source bytes and expected terms were unchanged. Parser/source/config/artifact
receipt bindings were checked and exact extracted text/layout retained.

| Control | Original terms | Result | Seconds including worker startup |
|---|---|---|---:|
| Simplified Chinese | `台湾`, `码头` | Both recovered; language `zh` | 39.027 |
| Traditional Chinese | `臺灣`, `碼頭` | Both recovered; language `zh` | 33.218 |

The Simplified output still misreads `部门` as `部内`. Thus the previous title
failure has an explicitly configured alternative, but **full transcription
quality is not accepted**. Neither this pair nor the earlier term checks proves
general multilingual, vertical-layout, chart or semantic extraction quality.
The native-binding `/2` profile remains unchanged, including its recorded
failure. No automatic fallback or model substitution was added. Selecting a
different recognizer is an operator recipe choice with a distinct digest.

The new non-active `examples/documents-chinese-rapidocr.toml` candidate carries
the tested model/recognizer pins and explicit bounds, with placeholder private
paths. It is a document-policy fragment: validate it with
`DocumentExtractionConfig`, then supply it as `document_extraction` in the full
collector configuration. It is not an independent full collector config. The
existing English and Pacific examples retain their published identities. The
new example and its regression are development source, not part of the immutable
0.4.3 archives; their next publication remains separate. The complete
`scripts/gate.sh` at `ad0341ec57dd2a5bed8c85f9d8830f3ad6dd2252` returned
**951 passed in 866.03 seconds**, no failures or skips, using Python 3.11.16
at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-delivery-outbox-20261007/src/ghimera`. Ruff/format, strict mypy
and the offline lock check also passed. Source/tests/examples were frozen for
the entire gate. This verifies package behavior, not general OCR accuracy.

Reproduction records are retained under the owned
`ghimera-043-release-R1PMMC/rapidocr-zh-Hans` and `rapidocr-zh-Hant` operator
directories: original-source path/hash, expectations, exact effective config,
extracted document, report and the `compare_ocr.py` harness. The URLs in these
controlled runs were caller claims, not fetched publishers. No pool GPU,
model service or shared runtime changed.

## Qwen-VL follow-up boundary

[Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
has open Apache-2.0 weights and an
[official OCR cookbook](https://github.com/QwenLM/Qwen3-VL/blob/main/cookbooks/ocr.ipynb).
It remains a candidate for difficult native-script text and document structure,
not a tested replacement in this package. Text-only Qwen cannot read page pixels.

A model-assisted OCR profile must explicitly pin its private vision service,
model revision, page render recipe, image/input/output bounds and review policy.
Retain original PDF/page hashes and separately identify generated transcription;
do not mislabel model-generated boxes or confidence as native OCR observations.
Rendering stays in the owned bounded worker. Private model calls belong to the
existing model-control boundary, not the currently offline parser's network
environment or the crawl/Tor route. Keep source/native text intact; no summary,
translation, guessed repair or silent hosted fallback. Reuse the unchanged
Chinese controls, test complete source-line transcription, and extend vertical,
mixed-script and representative source acceptance before claiming support.
