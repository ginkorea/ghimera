# Reviewed scanned-PDF transcription

Development source, not a published or accuracy-accepted capability. The
current public release remains 0.4.4. In particular, Simplified Chinese
scanned-PDF quality is still unaccepted; protocol fixtures do not change that.

The combined development gate completed under Python 3.11.16 with 1,023 tests
passed, zero failures/skips, plus Ruff, formatting, strict mypy (158 source files)
and offline lock validation. This verifies the integrated protocol, accounting,
cleanup and replay behavior; scripted model replies are not OCR-quality evidence.

The collector accepts an explicit `pdf_transcription` recipe beside its
existing `document_extraction` recipe. Model selection, private service
endpoints, credentials, language/script hints, rendering resolution, concurrency
and budgets are caller configuration. Importing this library never launches a
model or downloads weights. Qwen-VL is a candidate implementation of this
private vision-service boundary, not a hard-coded dependency or fallback.

`examples/pdf-transcription.toml` is a non-active, validated section example.
Load it as `PdfTranscriptionConfig` and put that value into the complete
`GhimeraConfig`; it is not a second full collector configuration. Install the
`pdf-transcription` extra in an owned rendering worker, point `worker_python`
at it and pin its installed renderer and image package versions. Source network
requests still run on the collector host; pixel/model requests use only the
explicit private model-control endpoints. Secrets are supplied separately via
`Collector(..., transcription_credential=..., transcription_reviewer_credential=...)`
or the same named arguments to `Collector.from_toml`.

## Selection and execution

- `native_refused`: use ordinary native extraction when it succeeds. Only its
  explicit `extraction_failed` refusal permits the model path. Time, adapter,
  permission and storage failures are never silently converted into model work.
- `always`: also transcribe readable PDFs, retaining the original parser reading
  beside the model reading. This is the appropriate mode for quality comparison
  or a native OCR result that passed extraction but contains incorrect words.
- The configured `language_hint` is caller-supplied, not a language detector's
  observation. Mixed-language corpora require explicit routing; declaring a hint
  is not evidence of model-language accuracy.
- Each PDF is rendered offline into bounded PNG pages. Original PDF digest,
  page order, dimensions, renderer configuration and exact PNG digests are retained.
- The transcriber reads the original script; it must not translate, summarize
  or repair names. A separately identified review model sees the same pixels
  and checks every proposed line and omissions. Calls consume the existing
  collector judge budget, with request/response digests and available token usage.
- Page calls can run concurrently under the declared page concurrency. One
  uncertain/omitted/truncated page cannot promote a partial PDF as complete.
  Failed and cancelled calls remain in the run ledger. No script correction or
  third model is silently attempted.

## Evidence and reuse

`Extracted.pdf_transcription` retains the complete page reviews, configuration
and either the original `Extracted` reading or the eligible native refusal.
Generated text does not reuse `document_parse`, native layout, native confidence
or native reference offsets. Native links remain discovery leads; the original
parser's reference records remain under its preserved reading.

The `Document` keeps the original PDF bytes. Admission and journal replay check
the source/configuration bindings and retain both model-call observations for
each accepted page. Existing relevance, embeddings, deduplication and semantic
extraction consume the selected reading. Graph extraction revision identifies
the reviewed-PDF stage. Citation templates say
`basis="reviewed_pdf_transcription"` and carry zero-based `page_indices`; they
cannot be relabelled as native citations. These are citations into a reviewed
machine reading of retained pixels, not proof that the model recognized them
correctly. Ordinary native citation wire identities remain unchanged.

Corpus passage projection also preserves the selected reading basis:
`kind="reviewed_pdf_transcription"` carries the exact ordered `page_indices`
intersected by each text chunk. Stored documents retain the full page/model
evidence. Corpus binding validation rejects a generated reading labelled as
native or a passage naming different source pages, including after SQLite
reopen and vector query. Ordinary native passages omit the empty page field,
preserving their existing wire identities. The downstream corpus change passed
45 focused tests under Python 3.11.16; a fresh combined gate is still required
for that change. These persistence tests use scripted model responses, not a
claim of Chinese recognition accuracy.

## Acceptance still required

Run the unchanged controlled Chinese scanned pages against an admitted, pinned
real vision model and a separately pinned reviewer. Compare character error
rate, omissions, required names and script preservation, not just required-term
presence. Keep the known Simplified character failure as a regression control;
do not normalize it away. Record resolution, model revision, generation shape,
latency and usage, and inspect both generated reading and original pixels.
Then exercise a complete installed collector, answer/citation, graph and corpus
readback on representative documents. No accuracy or throughput number from
scripted completion tests qualifies as this acceptance.
