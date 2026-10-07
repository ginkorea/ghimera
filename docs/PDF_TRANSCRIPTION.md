# Reviewed scanned-PDF transcription

Published in 0.4.5 with independent installed-artifact acceptance and exact
public artifact readback; see [release evidence](RELEASE_045.md). Simplified
Chinese scanned-PDF quality is still unaccepted; protocol fixtures do not
change that.

The final combined PDF/corpus/graph gate completed under Python 3.11.16 with 1,031 tests
passed, zero failures/skips, plus Ruff, formatting, strict mypy (158 source files)
and offline lock validation. This verifies the integrated protocol, accounting,
cleanup and replay behavior including the graph follow-up below; scripted model
replies are not OCR-quality evidence.

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
45 focused tests under Python 3.11.16 and the combined gate above. These
persistence tests use scripted model responses, not a
claim of Chinese recognition accuracy.

The graph integration, included in 0.4.5, lets document nodes
retain compact `pdf_reading` references to the ordered page pixels and exact
transcription/review call records. This avoids duplicating PNG payloads in the
graph. The reading digest is part of generated representation identity, so the
same original and text processed by a different recipe is a distinct version.
Semantic graph spans carry `basis`, exact `page_indices` and `reading_sha256`;
append/replay refuses a native label or wrong page/reading on a generated node.
Graph-aware follow-up planning checks spans through that same reading-binding
contract rather than checking quote text alone.
Harvest validation checks the compact reading against the retained full PDF
evidence. Ordinary native node/evidence serialization remains unchanged. This
follow-up is included in the final combined count. Its five focused
regressions passed under Python 3.11.16, including durable replay, semantic
extraction, planning, page boundaries and native wire identity. The broader
graph, planning, journal, resume and local-input selection passed 109 tests with
no failures/skips under the same interpreter. The final combined gate passed
1,031 tests, zero failures/skips. Independent installed PDF/graph/corpus and
legacy browser-archive acceptance also passed; published original artifact
bytes matched exactly. Neither kind of acceptance measures real OCR accuracy.

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
