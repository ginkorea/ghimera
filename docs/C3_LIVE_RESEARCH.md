# Concrete live intent research — 6 October 2026

Status: completed English integration diagnostic, not representative quality
acceptance or completion of all planned collector capabilities.

The run invoked the actual configuration-driven `Collector.run` rather than
substituting fixtures or individually calling model ports. It used
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, importing the
owned `src/chimera` tree before the subsequent namespace migration.

## Actual path and inputs

The intent asked how Python `graphlib.TopologicalSorter` prepares and advances
a dependency graph and reports cycles. A real self-hosted planner generated
search queries; the actual SearXNG provider was
`https://searx.dresden.network/search`. Source requests, guarded redirects,
robots handling and real HTML extraction stayed on the laptop. The extraction
worker used pinned Scrapling, Crawl4AI and Lingua, not fabricated page text.

Semantic scoring used the already cached
`sentence-transformers/paraphrase-MiniLM-L6-v2` snapshot
`3bf4ae7445aa77c8daaef06518dd78baffff53c9`, with actual weight SHA-256
`2ce4480dc3b2f8edeee50c43765c72768e79fc0113d3f73773dded4887cca298`.
Its temporary loopback CPU service used native attention-mask mean pooling,
384 dimensions and explicit token-length refusal rather than silent truncation.
This English integration encoder is not a multilingual production selection.

Planning, judging, assessment, drafting and review used the broker-admitted
`openai/gpt-oss-20b` revision `6cee5e81ee83917806bbde320786a8fb61efebee`.
Only a consumer-scoped invocation capability entered the standalone collector,
in memory. No general platform credential reached discovery or source fetching.

The private recipe configured two research rounds, one query per round, four
results per query, an eight-host ceiling and finite page/byte/time/model/encoding
budgets. It enabled a research graph at intent creation and persistent journal.
These are trial inputs, not hard-coded production operating choices.

## Observed outcome and readback

The native result was `answered`, collection stop `goal_satisfied`, with two
rounds, two retained search observations and one accepted document:
`https://universopython.com/en/blog/python-graphlib-topological-sort`.
This is a secondary source, not independent verification against Python's
reference documentation. The answer made three claims; all three source
quotations matched exact retained native text, offsets and raw/text hashes.
The same-model review marked the three claims supported and its answer digest
matched the exact retained answer. Citation integrity does not prove entailment
or factual accuracy; the reviewer is not independent.

The sealed receipt from the standalone Python **3.11.16** interpreter recorded:

| Measure | Observed |
| --- | ---: |
| Accounted fetches | 14 |
| Retained/accounted bytes read | 1,037,635 |
| Model calls | 14 |
| Encoding calls | 17 |
| Encoding input characters | 22,620 |
| Collection elapsed seconds | 138.106 |
| Durable graph batch files | 34 |

Elapsed time is client collection wall time, not GPU compute time or billing.
These are ungoverned engineering observations, not platform run-report figures.

Native archive readback revalidated the complete typed result and checksum;
journal readback validated the hash chain and complete summary. The archive
was 445,472 bytes with SHA-256
`aacc9a6e311b6bdc4c8c9a471840b123b5018d26995146c8196de9e57d1c39ca`.
Private artifacts remain under `gate-work/collector-real-research-20261006/`;
they are ignored working records, not shipped corpus or credentials.

The consumer was released, its exclusively created startup
`job-a826dcde9305` was separately observed `cancelled`, and the temporary CPU
encoder was stopped and observed terminal. No shared model service, feed
configuration or unrelated job was changed.

## Still required

Representative intent/language/source evaluation, independent reviewer binding
and calibrated decision policy remain open. The original browser/publisher,
PDF/OCR/Marker, reference adequacy, checkpoint/resume and runtime/egress
acceptance requirements also remain. This successful concrete run joins the
previous discovery and model-port checks; it does not erase those requirements.
