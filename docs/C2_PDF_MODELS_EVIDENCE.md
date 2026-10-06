# Offline model-based PDF evidence

Source candidate measured 6 October 2026 in `/tmp/chimera-c0-20261006`, following
authorized-session commit `e227e6e`. Not a published wheel or live deployment.

## Environments

Controller/gate interpreter:
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing
`/tmp/chimera-c0-20261006/src/chimera/__init__.py`.

Actual model worker interpreter:
`/home/gompert/data/workspace/TAIPAN/.codex-tmp/go-spider-docling-standard-20261006/venv/bin/python`,
Python 3.11.16, using the same explicitly supplied source tree. This is a
private CPU-only sibling runtime on the data volume, not a shared platform
runtime. No TAIPAN package was installed in the controller; platform
doctor/floor checks are not applicable to this independent collector.

Docling slim 2.134.0, core 2.99.0, IBM models 4.0.3, Torch 2.9.1+cpu,
Torchvision 0.24.1+cpu, Transformers 4.57.6, ONNX Runtime 1.23.2,
RapidOCR 3.9.1 and Lingua 2.1.1 were actually installed. Each selected
model-runtime package is checked by the worker before pipeline construction.
The example contains all eight admitted model files with their measured sizes
and SHA-256 hashes; combined manifest digest:
`b5870ec8ca436990007331073af336da957fc1ddde966f0e4dbb826349580fef`.

## Real inference, controlled source

The supplied source is a raster-only PDF containing a heading, two columns of
prose and a ruled table. There is no embedded native text for an extractor to
borrow. `scripts/make_pdf_fixture.py` regenerates it from the specified font;
the PDF dates are fixed. This is a **controlled fixture**, not public publisher
acceptance or an accuracy benchmark.

Initial real Docling inference recovered the text and table but interleaved the
columns. Changing OCR mode and disabling vendor dilation did not fix that
observed defect. The final configurable geometric ordering uses a bounded XY
cut over the actual detected boxes, preserving original elements and other
vendor pipeline stages. The failing order check then passed in real inference.
No vendor files or process-global settings were patched.

Final acceptance command, run from the checkout:

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  PYTHONPATH=/tmp/chimera-c0-20261006/src PYTHONDONTWRITEBYTECODE=1 \
  timeout --kill-after=10s 300s .venv/bin/python -m chimera.document_acceptance \
  --config /home/gompert/data/workspace/TAIPAN/.codex-tmp/go-spider-docling-standard-20261006/acceptance-config.json \
  --pdf /home/gompert/data/workspace/TAIPAN/.codex-tmp/go-spider-docling-standard-20261006/acceptance-raster-2columns.pdf \
  --source-url https://fixture.example.invalid/report.pdf \
  --expectations examples/pdf-expectations.json \
  --output-directory /home/gompert/data/workspace/TAIPAN/.codex-tmp/go-spider-docling-standard-20261006/final-acceptance
```

Terminal exit zero; **32.015 seconds** on the worker above, one page, one table.
The table contained `Location / Capacity`, `Taiwan / 42`, `Japan / 19`, and
the entire left prose column appeared before the right. Required text/order
checks passed. This timing includes worker/model startup and is a single local
measurement, not claimed sustained throughput.

Source SHA-256:
`0d6d206929cfd3ebd25c03966bdab751dc97a1f9d547708d257d94bc85152f01`.
Native extracted text SHA-256:
`0aeffc1fac29387bc7cefb26ad2adb769a19e5a09f2df829bbc8ed3c9b2ed18c`.
Configuration digest:
`e563db639173a9f6e04380fb4cb56ba2a365fcc82bd36a3260596438a8ce19b0`.

Original PDF, `extracted.json`, `effective-config.json`, `expectations.json`
and `report.json` are retained in the private acceptance directory. The report
explicitly says `fetch_verified=false`: its fixture URL is an attached claim,
not evidence of publisher retrieval. The source never entered a hosted model
service; vendor remote services/downloads and GPU visibility were disabled.
Runs were outside the separately reproduced command-sandbox asyncio fault;
the passive worker guard still refused network/subprocess initiation.

## Identity preservation and limitations

The existing native configuration was compared with the actual published
`be0eccc` class. Serialized bytes and digest are identical:
`1909013fa9610d348f93efbfbd3ef8c3fd2cbc752f396b8f7317117f7a9b1a8a`.
The regression freezes that result; no existing native policy identity moves.

Remaining acceptance includes diverse real publishers, multilingual OCR,
ambiguous/vertical/complex layout quality, Marker math fallback, OS-level worker
hardening and full runtime/egress acceptance. XY cut is geometric, not a
semantic confidence estimator; native PDF separator geometry retains vendor
ordering. The current example is one explicitly named English OCR recognizer.
No claim of complete C2, full goal completion or real-model research accuracy
follows from the fixture result.

## Frozen-source package gate

`scripts/gate.sh` completed successfully with Python 3.11.16 importing this
checkout: Ruff passed, all 82 checked files were formatted, mypy passed over
58 source files, and pytest reported **268 passed in 254.07 seconds**, with no
failed or skipped tests. The test run used the configured real Chromium and
`bwrap`; platform credentials were removed from its environment. This is
regression evidence, separate from the real offline PDF inference above, and
does not close the remaining publisher or deployment acceptance gaps.
