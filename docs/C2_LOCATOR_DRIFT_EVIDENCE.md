# Persistent locator drift — candidate evidence

Status: source candidate, not published or deployed. This addresses the drift
streak/doctor/generic-recovery part of F2, not its archived-publisher quality bar.

## Environment and regression gate

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python **3.11.16**.
Import: `/tmp/chimera-c0-20261006/src/chimera/__init__.py`.
The standalone environment resolves no TAIPAN SDK; its platform doctor/floor
checks do not apply. Platform credentials were removed from gate invocations.

Frozen source full `scripts/gate.sh`: **277 passed in 275.10 seconds**, no
failures or skips; strict mypy passed over **60 source files**, Ruff passed and
all **85 checked files** were formatted. Real configured Chromium with `bwrap`
was used, not a skipped browser fixture. No source/test edits occurred during
the full run. Earlier focused real-parser/store checks passed **21 tests in
35.33 seconds** on the same interpreter.

## What the checks establish

- Actual Scrapling/Crawl4AI parsing records consecutive configured-selector
  misses and keeps usable native text through the generic fallback.
- After three misses, a fresh extractor sees the latch; subsequent generic
  attempts do not pretend to be direct/adaptive locator successes.
- Failed document extraction with observed CSS misses still updates the doctor.
  Unrelated failures do not count as a selector miss or reset the streak.
- Host/profile/policy changes do not inherit another profile's drift. Explicit
  reset affects only the exact configured identity.
- Twelve concurrent completion updates through separate store objects are all
  retained by short transactions; no lock is held over fetch/parser work.
- Shared or corrupt state and foreign/duplicate locator events fail closed.
- A real parser result enters the normal goal loop; its drift is an explicit
  policy ledger event, round-trips with the harvest, and changing its publisher
  health binding makes the saved harvest refuse.
- Omitted new policy/evidence fields do not add serialized keys to existing
  configurations/records. Operator thresholds are configuration, not Python.

## Executed doctor

The actual CLI ran against the focused check's already-populated state using
`/tmp/chimera-locator-doctor-20261006.toml`:

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  .venv/bin/python -m chimera.locator_health \
  --config /tmp/chimera-locator-doctor-20261006.toml
```

It reported `example.org` / profile `missing`, consecutive misses **3**, miss
limit **3**, `generic_only=true`, finding `locator_drift`, and exited **1**.
Before/after database SHA-256 was identical:
`1076dbac273b4942fd8a3b47e82e57507e03545b614503da1064d5b7f267a325`.
Doctor inspection did not clear the finding or mutate the database. This
domain is a controlled parser fixture, not evidence of a live publisher change.

## Still open

Representative archived-publisher pairs and the PRD's 90% relocation bar remain
unproven. This is a completion-ordered profile health mechanism, not a model
quality estimate. An admitted parser attempt may finish after another attempt
sets the latch; it cannot clear it. Generic extraction still refuses unusable
documents rather than creating text or metadata. Full source/runtime/egress and
served-model research acceptance remain distinct open work.
