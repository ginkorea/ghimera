# Live discovery and native collection — 6 October 2026

Status: bounded acceptance evidence against candidate `a763210`; not a full
intent-to-reviewed-answer acceptance or a published release. No package rename,
model startup, deployment or platform write was performed during this trial.

## What actually ran

The standalone interpreter was
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**, importing
`/tmp/chimera-c0-20261006/src/chimera/__init__.py`. It resolves no TAIPAN SDK.
Source requests ran on the laptop without platform or source credentials.

A private recipe selected `https://searx.dresden.network/search`, after consulting
the public [SearXNG instance registry](https://searx.space/). The registry was
only a selection aid: the successful response below is the actual evidence.
This endpoint is not a package default or a guaranteed available service.

The concrete `SearxSearch`, final `GroundedSearch.discover` boundary and
`SearchHistory` processed this query:

```text
Python graphlib TopologicalSorter prepare get_ready done CycleError
```

The response contained **eight hits**, **9,562 raw bytes**, observed in
**8.507861 client seconds**, using the standalone interpreter above. The native
typed observation retained the original response, hit URLs/titles/snippets,
query/question ID, provider revision `search-api/1` and direct pinned transport.
Its ledger binds raw bytes and the complete typed response separately.
The second configured service was not requested because the first succeeded.

A separate credentials-free continuation read that retained observation,
verified its binding and passed the first two actual hits into the existing
`FetchLadder`, `CurlRoute` and `HtmlExtractor`. Neither source URL was invented
by a model or inserted as a preselected answer document. Both robots documents
were checked and accounted for before the source pages were fetched.

| Discovered source | Raw bytes | Native characters | Outcome |
| --- | ---: | ---: | --- |
| `https://docs.python.org/3/library/graphlib.html` | 40,908 | 14,474 | Parsed, English |
| `https://universopython.com/en/blog/python-graphlib-topological-sort` | 57,552 | 5,992 | Parsed, English |

These counts come from the same standalone Python **3.11.16** interpreter.
The original bodies, native text, extraction recipes, parser revisions,
source/text hashes, transport, discovery membership and fetch ledger are
retained privately. No search snippet was passed off as native answer evidence.

## Readback and artifact identity

- `gate-work/search-live-next-20261006/search-results.json`:
  SHA-256 `aee2aadf4e5410c5dc5b7a325ea4ef1605369cd2b6f52c7f6d43b92a490a3e3c`.
- `gate-work/search-collect-live-20261006/prepared.json`:
  SHA-256 `973c8f527feb1c0012c34a33e611009e7a14529f1be0a1ec742a81aec641aea3`.

Offline readback in the standalone Python **3.11.16** interpreter passed typed
observation/ledger validation, complete response digest, raw byte/hash binding,
actual-hit membership, source-body and native-text hashes, source URL binding
and extraction-policy binding for both sources. An initial private readback
incorrectly tried `Extracted.url`; the correct boundary is
`Extracted.extraction.source_url`. That harness error did not change source or
artifacts and is not counted as a successful verification.

## Next real acceptance, not a substituted success

The private infrastructure reader separately revalidated the governed catalogue
using `/home/gompert/.venvs/taipan/bin/python`, Python **3.13.14**, importing
`/home/gompert/data/workspace/TAIPAN/src/taipan/__init__.py`. At this observation,
the broker's `models` listing was empty. The approved, published completion
model pin was available in the catalogue; no inference consumer was acquired.
The catalogue's general multilingual semantic encoder was `draft` and
`unpinned`, not an admitted pinned embedding service. The pinned HIATUS models
are writing-style encoders and were not substituted for topical relevance.

This closes the previously unsuccessful **live discovery-to-native-extraction
check**, not autonomous planning, semantic scoring, frontier continuation,
independent review or calibrated completion. The next complete research-loop
trial still needs a genuinely pinned semantic encoder and real admitted model
bindings. Representative languages/publishers, browser, PDF/OCR/Marker,
checkpoint/resume and deployment acceptance remain required. Earlier challenge
refusals remain valid observations for their particular endpoints.
