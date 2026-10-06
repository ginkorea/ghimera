# go-spider 0.2.0 release verification

## Scope

Independent standalone library release, distribution `go-spider`, import
`chimera`, Python 3.11+. The rewritten implementation and earlier source
evidence are included. This is not a declaration that all planned spider
features or public-corpus/model acceptance are complete. README and CHANGELOG
describe the breaking migration and remaining work.

The public go-spider 0.1.0 PyPI metadata and source archive were inspected
without executing them: the existing licence expression is MIT, original
author Josh Gompert, original import `spider_core` and console command `spider`.
The release restores the MIT text and retains that attribution. New package
metadata embeds the standalone README and declares the actual repository.

## Full source gate

Executed from the owned release worktree using
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16; `chimera` resolved
to `/tmp/chimera-c0-20261006/src/chimera/__init__.py`, not an installed copy.

```bash
env -u TAIPAN_TOKEN -u TAIPAN_COGNITO_ACCESS_TOKEN \
  CHIMERA_TEST_BROWSER=/home/gompert/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  CHIMERA_TEST_ISOLATOR=/usr/bin/bwrap \
  CHIMERA_GATE_CACHE=/tmp/chimera-c0-uv-cache bash scripts/gate.sh
```

Exit 0: 219 passed, 0 failed, 0 skipped in 225.59 seconds. Ruff check and
format check passed (67 files); strict mypy passed (49 source files); offline
lock check resolved 137 packages. No production source or tests were edited
during the run. Actual isolated Chromium and local HTTP/SOCKS/model protocol
fixtures were exercised; this does not measure public-model accuracy.

After the full gate only README links changed to absolute tag-bound URLs, so
the PyPI rendering points at the intended documentation. Release metadata,
standalone wording and the README example receive a final bounded recheck.

## Artifact acceptance

Built both wheel and source archive with offline `uv build --no-sources`.
`/usr/bin/python3.11 -m twine check --strict` passed for both initial archives.
Final publication archives are rebuilt after the release commit and rechecked.

A fresh base-only environment at `/tmp/go-spider-v020-installcheck` ran Python
3.11.16 with no `chimera` before installation and no platform SDK installed.
Offline installation resolved 11 compatible packages. The installed wheel
resolved `chimera` inside that environment's site-packages, declared
`go-spider==0.2.0` and MIT, and preserved the standalone README in metadata.
The documented offline GoalLoop flow retained two fixture documents and
round-tripped Harvest validation with stop reason `frontier_empty`.

Artifact inspection confirmed the licence in the wheel and README/config
example in the source archive; no old import/console entry point, gate-work or
virtual environment was bundled. The old API/CLI absence is intentional and
documented. Release archive hashes are recorded with the published assets;
they are not embedded recursively inside their own source archive.

## Publication and completion boundaries

Owner requested README update and v0.2.0 publication. GitHub source/tag and PyPI
publication require separate readback; local builds alone are not publication.
No live crawl, private model call, production deployment or runtime change is
authorized or claimed by this package release record. The overall spider
implementation goal remains active with its original outstanding acceptance.
