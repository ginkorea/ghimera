# Reference expansion candidate evidence

Status: local source candidate and full standalone gate. No new publication,
deployment, public-source corpus, served-model accuracy or platform acceptance.

## Environment and gate

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
TAIPAN resolves none; platform doctor/floor are not applicable to this standalone
environment. Base: published `go-spider` v0.2.0, source `be0eccc827dccdbc2bde0a6f6a19639e5c102ef7`.

```bash
cd /tmp/chimera-c0-20261006
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```

Actual full gate: **240 passed, 0 failed, 0 skipped**, **231.27 seconds**,
on the interpreter above. Strict mypy passed on **52 source files**; Ruff and
format checks passed on **72 files**. Offline lock check resolved **137 packages**.
The earlier focused reference/cited-by/intent run passed **30 checks** on that
same interpreter before the final additional budget/provenance checks.

The restricted execution sandbox hung on an existing graph test. A five-second
minimal `asyncio.run(asyncio.to_thread(...))` diagnostic also timed out there,
without crawler, graph or network code. The exact diagnostic succeeded outside
that sandbox. The hanging test process was terminated by its exact task-owned
PID, then the full gate ran outside the faulty sandbox, with credentials removed.
Neither interrupted run counts as a pass. No production process was touched.

## What the checks prove

- Observed document references use accepted retained native source evidence.
  Real local Docling DOCX conversion produces replayable native URL locators.
- Configured depth permits multiple hops; disabled, denied and out-of-scope
  reference paths do not reach the fetcher. Rejected sources do not seed them.
- Citing-source queries derive from native title/source URL, reserve before I/O,
  bind an actual provider response digest and share collection spend.
- Follow-up rounds retain query reservations and the combined source-host cap.
  A regression first failed by fetching a third host under a two-host cap;
  the scope handoff now prevents it.
- Document references and citing-source candidates share the per-parent queue
  cap. Failed citing-source queries remain accounted without fabricated seeds.
- Serialized readers reject changed native-source bindings, invented frontier
  origins and lost query/response-digest provenance.
- The existing HTTP/Tor, isolated browser, extraction, deduplication, model,
  graph, scoring and conformance/mutation checks still pass in the full gate.

## Boundaries still open

Provider raw response bytes are identified by digest, not embedded as a
cryptographically replayable search archive. A returned hit is a candidate
citing source, not proof of a bibliographic citation. Representative reference
adequacy, redirect/navigation ancestry and real-model quality remain acceptance
work. Other C1–C5 gaps remain tracked in C0 and the owning feature documents.
No newly built artifact or installed-wheel result is claimed by this record.
