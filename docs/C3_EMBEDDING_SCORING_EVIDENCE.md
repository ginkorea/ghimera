# Embedding scoring: source gate and installed-wheel evidence

Local engineering evidence, not a governed TAIPAN run or a model-quality
evaluation. No production service was queried and no source credentials were used.

## Candidate

Branch: `gompert/chimera-c3-embedding-scoring-20261006`.
Implementation commit: `160737e` (`C3: add audited self-hosted embeddings and
native shelf relevance scoring`), on browser redirect evidence `cf725b2`.
No GitHub push, release tag, package publication or production activation.

Contract owners: `EmbeddingServiceConfig` and `ScoringConfig`; the existing
`PrivateModelService`/`PinnedModelHttp` boundary is shared with completions.
`SelfHostedEncoder` implements the existing encoder port plus its audited batch
extension. `EmbeddingScorer` inherits the final scorer template; references,
encoder and run ledger are injected. The implementation does not import a
second HTTP stack, launch inference or download weights.

## Source interpreter and full gate

Verified interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python
`3.11.16`. Actual import:
`/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
`taipan` resolves none: this is the standalone spider package, so TAIPAN
doctor/floor are not applicable. Pytest version: `9.1.1`.

Command, from the owned worktree:

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh
```

Final result on that interpreter/import: **216 passed, 0 failed, 0 skipped**,
pytest elapsed **223.32 seconds**, exit status zero. Ruff and formatting passed
for 66 source/test files; strict mypy passed for 49 source files; the offline
lock check resolved the unchanged 137-package lock. The tests used actual owned
loopback sockets and the provisioned isolated Chromium; no browser download.
Source and tests were unchanged while the final suite ran.

The earlier full attempt ended with 215 passed and one mutation-witness failure:
the new anchor check redundantly enforced candidate URLs and masked deletion of
the old URL check. The correction consolidated URL and anchor identity into one
guard; it did not weaken source identity. All 46 focused mutation/scoring checks
then passed in 41.86 seconds on the same interpreter before the final full gate.

Coverage includes:

- Correct embedding input/index binding even for reverse-ordered responses;
  native Unicode and explicit prefixes; dimension requests and usage.
- Wrong model, missing or duplicate vectors, wrong dimensions, zero/nonfinite
  vectors, malformed JSON, truncated/oversized responses and redirects.
- Call, character, per-text, request-byte and batch limits before I/O; shared
  run accounting and deadline; refusal/cancellation evidence.
- Private endpoint and credential policy, reference digest/model/revision/
  dimension/prefix rebinding, injected object validation and keyword-fallback refusal.
- Native span and context-coverage evidence, shelf cosine, keyword/semantic link
  ranking, retained judge verdicts and harvest receipt tamper detection.
- Shared scorer conformance now includes the semantic implementation. Deliberate
  mutations require executable failures for encoding reservation, returned model
  identity and unique input indexes, alongside the existing shared invariants.

## Built artifacts

Built offline from the committed implementation:

```bash
env UV_CACHE_DIR=/tmp/chimera-c0-uv-cache \
  uv build --offline --no-sources --out-dir dist/c3-embedding-scoring
```

Wheel: `dist/c3-embedding-scoring/taipan_chimera-0.1.0-py3-none-any.whl`.
SHA-256 content hash:
`83e552033cd221701e553f0a123b47b2d7f9dfe990adf638fcee86598610d75b`.

Source archive: `dist/c3-embedding-scoring/taipan_chimera-0.1.0.tar.gz`.
SHA-256 content hash:
`ab86a65af3665864b254d7ee05f39a1b17f82270232201ebdd7a62372f5c20d6`.

These are artifact hashes, not catalogue IDs. The evidence document itself was
added after building; those artifacts intentionally bind the implementation
commit rather than the subsequent evidence commit.

## Installed-wheel execution

Reused only the existing task-owned non-editable browser acceptance environment:
`/tmp/chimera-c1-browser-wheel-20261006/bin/python`, Python `3.11.16`.
Installed only the new local `taipan-chimera` wheel with offline `uv pip`,
`--no-deps --reinstall-package taipan-chimera`. Dependency readback: 103 installed
packages compatible. No shared or production runtime was modified.

Actual import during acceptance:
`/tmp/chimera-c1-browser-wheel-20261006/lib/python3.11/site-packages/chimera/__init__.py`.
Ran from `/tmp` with Python `-I` and no `PYTHONPATH` or TAIPAN credentials:

```bash
env -u PYTHONPATH -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  /tmp/chimera-c1-browser-wheel-20261006/bin/python -I \
  /tmp/chimera-c0-20261006/gate-work/accept-embedding-wheel.py \
  /tmp/chimera-c0-20261006
```

Acceptance process exited zero. Its emitted fields were:
`http_calls=2`, `input_chars=126`, `observed_usage=6`,
`native_preserved=true`, `link_scores=[1.0, 0.8, 0.0]`,
`model_quality_measured=false`. Requests went only to the owned loopback
`/v1/embeddings` fixture. Each request hash matched the server's captured bytes;
call and prefixed-character reservations matched the ledger. Returned entries
were deliberately reversed and were rebound by index before scoring.

The artificial reference bundle digest was
`ed044ae9d3298484c1650836e8876fb72e009d162302a64083ad5e109f8645b1`.
It is a fixture content hash, not an admitted shelf or production model.

## Completion boundary

This verifies packaged implementation behavior, not real-model retrieval
accuracy, multilingual performance, encoder revision attestation or production
acceptance. Remaining full-spider work includes admitted models and quality,
calibrated judge decision bands, one-hop references, full layout/OCR, additional
browser routes, governed shelf/harvest integration and runtime/egress acceptance.
Full C0–C5 completion is not claimed.

Withheld: none; no governed result or restricted source was read.
Unresolved: real model/admitted-shelf acceptance and the full-spider requirements
above. No catalogue identifiers were composed or substituted for local evidence.
