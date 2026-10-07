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
   execution or image download occurs during inspection. The unreleased explicit
   responsive policy below also admits bounded `srcset` and `<picture>` choices.
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
   The unreleased [native evidence corpus](EVIDENCE_CORPUS.md) does this for OCR
   and reviewed visual text, retaining original-image region anchors.

Original HTML/PDF preservation may include inline or embedded images. This
feature does not rewrite the original source to erase those bytes.

## Explicit responsive intake — unreleased

Set `VisualConfig.responsive` from the non-active fragment
[`examples/responsive-images.toml`](../examples/responsive-images.toml).
An absent policy preserves the previous serialized configuration/candidate
shape and `data-src`/`src` precedence. Operators choose attribute precedence,
attribute/variant/source limits, maximum declared width/density and whether
conditional `<picture>` alternatives are included. This does not install models
or change source scope, robots, transport, image retention or byte budgets.

Each eligible source group contributes at most its largest declared variant
within those bounds; supported `<picture>` alternatives and the `img` fallback
remain distinct candidates. `all_declared` retains media conditions rather than
claiming they match a viewport; `unconditional_only` skips conditional sources.
Neither setting implements browser layout or full HTML parsing. Real browser
selection depends on layout and device/environment information; see the
[HTML image specification](https://html.spec.whatwg.org/multipage/images.html),
inspected 7 October 2026. The collector does not interpret `sizes` as a rendered
width or convert a declaration into actual decoded-image dimensions.

The passive tokenizer preserves commas within URL tokens and rejects invalid
descriptors, mixed width/density sets, unsupported schemes, credentials and
control characters. A variant-count, attribute or picture-source overflow
refuses that set rather than pretending its prefix is complete; an explicit
valid fallback may still be selected. Page and image caps remain bounded and
may omit alternatives. They are collection limits, not exhaustive coverage.
Logo/presentation filtering still happens before any image resource request.

Every selected responsive candidate carries `ghimera.responsive-selection/1`:
effective policy and markup digests, decoding name, exact source token and
attribute digest, width/density descriptor, picture-source index, original
media/sizes/type strings and encountered omission codes. Archive revalidation
replays selection against retained markup; merely editing a descriptor or
policy hash cannot make it a valid observed source choice. The markup decoder
is recorded, not independently reconstructed from an archived HTTP header.

## Required language acceptance

The primary targets are English, Simplified Chinese, Traditional Chinese,
Japanese, Korean, Tagalog/Filipino, Indonesian, Malay, Vietnamese and Thai.
Khmer, Lao, Burmese, Māori and Russian are additional coverage targets. Treat
the two Chinese scripts as separate acceptance cases even when language
detection returns `zh`. A Tagalog `tl` route using the `fil` OCR package does
not establish support for every Philippine language.

Admission, native image OCR, native/scanned PDF extraction, relevant-image
selection, semantic extraction and native/cross-language retrieval are separate
checks. Keep source text in its original script and preserve source/region
anchors; an English translation cannot stand in for native extraction acceptance.
The controlled execution observations are in
[VISUAL_LANGUAGE_ACCEPTANCE.md](VISUAL_LANGUAGE_ACCEPTANCE.md); they are not
representative multilingual accuracy. In particular, the
[scanned-PDF candidate](PACIFIC_PDF_CANDIDATE.md) still failed a required
Simplified-Chinese title term, and Thai has not received the rendered-script
quality check in that record. Those gaps remain open.

## Provenance and boundaries

`ghimera.image-evidence/1` contains parent URL/content hash, element index and
caption, final image URL, original image content hash and bytes, OCR region
spans, executable/pack hashes and effective visual recipe hash. Optional visual
claims have normalized region references and separate interpretation/review
request and response hashes. These are derived observations, not native page
text offsets. They are preserved by the ordinary archive read/write boundary.

Current limits are explicit: embedded PDF figure crops, standalone image seed
dispatch, automatic graph promotion and
answer citation rendering for visual regions are not built. Existing scanned
PDF OCR remains the configured document adapter, not this raster enrichment
path. The responsive and durable-corpus source candidates are unreleased;
service wiring and representative corpus acceptance remain open PRD rows. Representative
multilingual infographic/diagram accuracy and a real served-vision run remain
acceptance requirements; protocol fixtures cannot close them.

The gate uses explicitly configured Tesseract, English traineddata and a font
fixture; it exercises real local raster/OCR execution and source retention,
not a benchmark of all declared Pacific languages.

The responsive candidate and its visual/corpus/collector/continuation importers
returned **84 passed in 88.64 seconds**, no failures or skips, on Python 3.11.16
at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing
`/tmp/ghimera-responsive-visuals-20261007/src/ghimera`. Source/tests/examples
were frozen during the run. Witnesses include native captions, bounded width/
density/lazy/picture selection, malformed URLs, exact archive replay, unchanged
legacy wire shape, every primary Pacific OCR route, and actual English Tesseract
execution followed by SQLite/FAISS retrieval of the retained OCR/image anchors.
The model wire uses credential-free loopback replies, not a real semantic model.
Full responsive-candidate gate and publication remain pending.
