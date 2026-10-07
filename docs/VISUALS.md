# Selective image evidence and Pacific OCR

The library can enrich accepted HTML documents with relevant PNG/JPEG/WebP
images. Add a `visuals` recipe to `GhimeraConfig` using
[`examples/visuals-pacific.toml`](../examples/visuals-pacific.toml) as the typed
fragment. Install `ghimera[images]` into the selected raster worker environment,
and provide a local Tesseract executable and offline language data. The library
never installs or silently downloads packs at runtime.

The Pacific recipe declares Simplified Chinese (`chi_sim`), Traditional Chinese
(`chi_tra`), Japanese (`jpn`), Korean (`kor`), Filipino/Tagalog (`fil`), English,
Indonesian, Malay, Vietnamese, Thai, Khmer, Lao, Burmese and Māori. Native
document language selects a configured subset, with an explicit fallback set
for unknown language. An unqualified `zh` selects both Chinese packs; a script
hint selects the requested script; the example also maps `zh-CN`/`zh-SG` to
Simplified and `zh-TW`/`zh-HK` to Traditional. Tagalog `tl` and Filipino `fil`
share the declared Filipino pack. Language configuration is not an accuracy
guarantee, particularly for mixed scripts, low resolution and vertical text.
Vertical layouts may need the corresponding upstream vertical model and a
separate segmentation recipe; do not pretend a horizontal model validated them.

Official pack names and available models:
[Tesseract language data](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)
and [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast).
For a reproducible deployment acquire packs from immutable upstream revisions
and retain their licences and acquisition manifest. The controlled Japanese
fixture required `tessdata_best` rather than `tessdata_fast`; its `jpn_vert`
supporting pack must also be installed and declared. This is not evidence that
all Japanese layouts or Pacific languages meet a production quality threshold.
Every OCR result records
the executable and selected pack content hashes. Missing declared packs fail
initial adapter admission; changed bytes refuse subsequent work.

## Selection and retention

1. Passive HTML inspection captures `img` URLs (`data-src` then `src`), alt/title,
   a following figure caption, element index and declared dimensions. No page
   execution or image download occurs during inspection.
2. Configured exclusion tokens, presentation attributes, dimensions, candidate
   terms, host/port scope and per-page limits admit likely useful figures.
   Logos/icons and unmatched candidates produce only a refusal observation.
3. Admitted images use the existing fetch ladder, robots, network/Tor/session,
   pacing and run byte/time accounting, without browser escalation. Rendering
   recipes that retain arbitrary image/media resources are incompatible with
   selective visuals; disable those resources so they cannot store logos first.
4. An owned offline raster worker bounds media, bytes, pixels and animation.
   Local Tesseract emits OCR words, confidence observations and normalized
   original-image regions. Confidence is not calibrated factual probability.
5. With no vision recipe, readable OCR is submitted to the existing relevance
   judge. Rejected/held or unreadable text retains no image. No diagram relations
   are inferred from OCR alone.
6. Optional `visuals.vision` and `visuals.reviewer` bind explicit local completion
   services (`json_object`). `Collector` accepts separate memory-only
   `vision_credential` and `visual_reviewer_credential` inputs. Actual original
   pixels plus OCR go to interpretation and a separate review request; wrong
   image/proposal hashes, truncated replies and unsupported claims refuse.
   The command's existing environment bindings can name these exact completion
   endpoints too; they are partitioned by configured role, never discovered or
   forwarded to another endpoint. `Collector.from_toml` accepts the same
   explicit memory-only visual credentials.
7. Only accepted original bytes and source-bound regions survive as
   `Document.images`. Rejected bytes are removed from the conditional fetch
   cache and worker files are cleaned. This path creates no image vectors; an
   index consumer must admit only these accepted records, never all page images.

Original HTML/PDF preservation may include inline or embedded images. This
feature does not rewrite the original source to erase those bytes.

## Provenance and boundaries

`ghimera.image-evidence/1` contains parent URL/content hash, element index and
caption, final image URL, original image content hash and bytes, OCR region
spans, executable/pack hashes and effective visual recipe hash. Optional visual
claims have normalized region references and separate interpretation/review
request and response hashes. These are derived observations, not native page
text offsets. They are preserved by the ordinary archive read/write boundary.

Current limits are explicit: HTML `srcset`/`picture` selection, embedded PDF
figure crops, standalone image seed dispatch, automatic graph promotion and
answer citation rendering for visual regions are not built. Existing scanned
PDF OCR remains the configured document adapter, not this raster enrichment
path. Durable corpus indexing is a separate open PRD row. Representative
multilingual infographic/diagram accuracy and a real served-vision run remain
acceptance requirements; protocol fixtures cannot close them.

The gate uses explicitly configured Tesseract, English traineddata and a font
fixture; it exercises real local raster/OCR execution and source retention,
not a benchmark of all declared Pacific languages.
