# Pacific OCR execution acceptance

Status: acquisition and controlled native-script execution, not representative
infographic accuracy or a deployed visual model. Model files are not committed
or shipped in the wheel.

Official `tessdata_fast` revision resolved through its Git remote:
`87416418657359cb625c412a48b6e1d6d41c29bd`. The task acquired 14 declared packs
over verified HTTPS into `/tmp/ghimera-pacific-ocr-xa7zK3`; local Tesseract's
`--list-langs` returned `chi_sim`, `chi_tra`, `eng`, `fil`, `ind`, `jpn`, `khm`,
`kor`, `lao`, `mri`, `msa`, `mya`, `tha`, `vie`. Missing packs still fail adapter
admission. Acquisition does not establish equal accuracy across these languages.

The revised example declares 15 packs, including Japanese's supporting
`jpn_vert`. The native execution check used 13 packs from that fast revision
and `jpn`/`jpn_vert` from `tessdata_best` revision
`e12c65a915945e4c28e237a9b52bc4a8f39a0cec`. The fast Japanese pack returned no
words on the controlled fixture; the best Japanese pair recovered its labels.
This comparison is one generated fixture, not a language-quality benchmark.

On Python 3.11.16, importing the task's
`/tmp/ghimera-cadence-20261007/src/ghimera`, native local execution returned:

| Configured route | Authored fixture | OCR observation |
|---|---|---|
| `zh-Hans` | 台湾 港口 组织结构 | 台湾 港口 组 织 结构 |
| `zh-Hant` | 臺灣 港口 組織結構 | 臺灣 港口 組織 結構 |
| `ja` | 日本 港湾 組織図 | 日 本 港湾 組織 図 |
| `ko` | 대한민국 항구 조직도 | 대한민국 항구 조직도 |
| `tl` | Pilipinas daungan organisasyon | Pilipinas daungan organisasyon |
| `id` | Indonesia pelabuhan organisasi | Indonesia pelabuhan organisasi |
| `ms` | Malaysia pelabuhan organisasi | Malaysia pelabuhan organisasi |
| `vi` | Việt Nam cảng tổ chức | Việt Nam cảng tổ chức |
| `en` | PACIFIC PORT ORGANIZATION | PACIFIC PORT ORGANIZATION |

The Chinese/Japanese word segmentation differs from the authored spacing.
Each observation retained its exact engine and selected pack hashes. The
engine digest was
`fbd4a89b2e1559045dfeac7c98c92635897adba49eda1b102876c4a530045478`.
Restricted-namespace execution intermittently timed out on several inputs;
the subsequent native run read all nine fixtures, and a separate native
reprobe read Tagalog, Malay and Vietnamese twice each without a timeout.
That comparison does not establish the root cause of the earlier stalls or
production reliability. Thai, Khmer, Lao, Burmese and Māori packs were acquired
but did not receive a rendered native-script quality check in this run.

The release gate's ordinary OCR tests deliberately require explicit operator
paths for the engine, English data and fixture font. Additional controlled
Pacific-script execution uses the same production `TesseractOcr` adapter,
script-qualified routing and exact selected pack hashes. Native-script outputs
are inspected separately; generated chart labels are not a public-source
benchmark or evidence of real diagram understanding.

Remaining acceptance: real Simplified/Traditional Chinese organization charts,
Japanese and Korean mixed-script figures, Filipino/Tagalog infographics,
multicolumn/vertical text, rotated/low-resolution figures, multilingual intent
relevance, independent served-vision entailment and visual-region citation
rendering. Keep OCR observations separate from proposed chart relations.
