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
