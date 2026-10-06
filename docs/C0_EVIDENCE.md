# Chimera C0 source evidence

Status: source candidate only. No publication, live deployment, governed asset,
real crawl, GPU inference or TAIPAN acceptance is claimed.

## Source and interpreter

Repository: `/home/gompert/data/workspace/spider_core` (original checkout unchanged).
Branch: `gompert/chimera-c0-20261006`.
Worktree: `/tmp/chimera-c0-20261006`.
Tested code commit: `d79467180f08ab0d477507ae32ac776d8288171d`
(core `cfee70a2d77f732d6822f9bb75be70f090532226` plus fixture/whitespace cleanup).

Gate interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Verified import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
Private environment installed from the real package lock, not a sibling TAIPAN
checkout or a shared production runtime. Runtime dependency: Pydantic **2.13.5**.
Its pin was checked against the primary PyPI release metadata:
[Pydantic release record](https://pypi.org/pypi/pydantic/2.13.5/json).

## Full package gate

```bash
cd /tmp/chimera-c0-20261006
CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh
```

Actual final result on the interpreter above:

- `uv lock --check --offline`: passed, 18 packages resolved.
- Ruff: passed; format check: 16 files already formatted.
- Strict mypy: passed, 11 source files.
- Complete package pytest: **37 passed in 9.55 seconds**, no skips/errors/failures.
  Seven included tests deliberately mutate isolated copies of shared contracts
  and confirm their behavioral witnesses fail. No shared source is mutated.
- Git whitespace check passed after the final metadata-whitespace correction.

These are ungoverned local development measurements, not classifier accuracy,
production throughput or a platform run receipt. The initial tests-first run
failed collection before implementation (`ModuleNotFoundError: chimera`). An
intermediate gate failed because its pytest temporary parent was absent; the
gate now creates only its own private parent before testing.

## Built artifact and isolated wheel check

Built source distribution and wheel with `uv build --offline --no-sources`.
Artifacts from the tested code commit:

- `dist/taipan_chimera-0.1.0-py3-none-any.whl`:
  SHA-256 `f9e34b590cbdf6c8cf0e9e676e9fc74ce2b2d2149c49ca80b9cc228dc9b0f543`.
- `dist/taipan_chimera-0.1.0.tar.gz`:
  SHA-256 `2e968d4f74cf8b17989aa7d00632fc79531033e43559cf1f7f5b6b9ca0580d2a`.

Wheel-only interpreter: `/tmp/chimera-c0-wheel-check-20261006/bin/python`,
Python **3.11.16**. Import proven outside the source tree at
`/tmp/chimera-c0-wheel-check-20261006/lib64/python3.11/site-packages/chimera/__init__.py`,
with `PYTHONPATH` and TAIPAN credential variables unset. The final wheel was
reinstalled after fixture cleanup. A fixture goal run, page-budget stop and
serialized harvest reader roundtrip passed. Judge location remained explicitly
`test_double`; no model availability or accuracy claim follows.

An offline wheel install initially needed a missing cached runtime artifact;
public runtime dependencies were installed only in this private wheel-check
environment, then the final package wheel was reinstalled offline without
dependency changes. Nothing was installed into a shared/platform environment.

## Handoff boundaries

This evidence page is added after the tested code commit; it does not change
production code. Archives above predate this page and are local candidates,
not registry artifacts. The donor's edited swap file and `project.md` remain
unchanged in its original checkout. Old source and binary prototype artifacts
removed from the candidate remain recoverable in Git history.

Next bounded lanes: C1 real fetch/politeness and C2 extraction, followed by C3
local served-model binding and C4 native TAIPAN seam. Production placement stays
edge-only, harvest registration stays inert and the package pin happens only
after conductor review/release. No live configuration/service change occurred.

Withheld: no governed material was queried or masked by this lane.
Unresolved: real route/egress/extraction/model/TAIPAN acceptance remains C1–C5;
g39 migration is an assumption, not an action; donor MIT licence text is missing.
