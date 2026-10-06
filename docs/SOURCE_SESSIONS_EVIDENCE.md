# Authorized source-session evidence

Measured 6 October 2026 in `/tmp/chimera-c0-20261006` on the branch
`gompert/go-spider-reference-expansion-20261006`, after reference commit
`6676651`. This is a source candidate, not a published release.

## Environment and gate

Interpreter: `/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16.
The imported package resolved to
`/tmp/chimera-c0-20261006/src/chimera/__init__.py`; no TAIPAN package was
installed. Platform doctor/floor checks do not apply to this independent library.

The serial `scripts/gate.sh` run completed with exit status zero:

- Locked dependency check, lint and formatting passed.
- Strict source type checking passed.
- Full suite: **253 passed, zero failed, zero skipped, 239.46 seconds**.

Reproduction from the checkout root:

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache \
  timeout --kill-after=10s 600s bash scripts/gate.sh
```

This run was outside the command sandbox: a separately reproduced sandbox
asyncio thread-completion hang prevented otherwise identical fixture tests
from completing there. The browser itself retained its configured bwrap
isolation. No production credentials, subscriptions, accounts or protected
real-world sources were used.

## What this establishes

`tests/test_source_sessions.py` covers exact-origin/path binding, explicit
plain-HTTP opt-in, missing and mismatched credential refusal before source I/O,
cross-origin and out-of-path redirect isolation, expired-credential refusal,
malformed-secret error redaction, nonoverlapping sessions and search-identity
separation. Real local HTTP observations establish which requests actually
received headers; serialized selection records are not used as that proof.

A real isolated Chromium run fetched an authorized initial page and protected
script through the parent HTTP boundary, rendered the expected text and retained
non-secret resource policy evidence. Harvest readback rejected a changed policy
digest. Source credentials were fixture-only values and never persisted in
the harvest or the browser worker payload.

## Not established

Real publisher subscription acceptance, interactive login, JavaScript cookie
workflows, POST workflows and automatic renewal remain unverified or unsupported
as stated in `SOURCE_SESSIONS.md`. No claim of complete C1/C3/C5 acceptance or
real-world research accuracy follows from these fixture checks.
