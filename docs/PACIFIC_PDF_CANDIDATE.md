# Pacific scanned-PDF candidate — 7 October 2026

Status: **source candidate; quality acceptance failed; not published**. Public
Ghimera 0.4.0 is unchanged. This candidate does not close the infrastructure PRD.

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

## Bounded next actions

Local regression evidence (not a full-package gate): Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python`, importing the source checkout
above, passed 41 document/config/media/order tests in 81.17 seconds before
the final example/segmentation restriction. After that small addition, the
12 model/adapter tests passed in 11.72 seconds. Neither run skipped tests.
Ruff/format and strict mypy on the two changed production modules passed.
No source/test edits were made during either reported passing run.

1. Correct the pinned OCR adapter's line handling while retaining source-bound
   boxes and the existing worker lifecycle; compare whole-page recognition
   with re-recognition and do not globally patch vendor classes.
2. Re-run all five authored Chinese-script/Japanese/Korean/Tagalog raster-PDF
   checks. Add vertical and mixed-script inputs separately.
3. Require representative publisher scans and semantic extraction/retrieval
   evidence before claiming language quality. Keep OCR availability, language
   identification and semantic-model admission distinct.
4. Run the full package gate before merge/publication. No tag has moved and no
   shared model runtime or platform service was changed for this candidate.
