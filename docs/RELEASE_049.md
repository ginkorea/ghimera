# Ghimera 0.4.9 release acceptance

Status: PUBLISHED AND ORIGINAL ARTIFACTS VERIFIED.
The full infrastructure PRD remains open. Prior public artifacts and tags remain
immutable; none is rebuilt, replaced or moved by this candidate.

## Exact source gate

Source commit `2335afc599c27816f07d64ca91ee1133f9e585ab`, tree
`e863a329c75fe5c1bdc8d9733751c8d15b80d0d8`, adds durable pre-call model intent,
phase reservation, linked observations and uncertainty holds through the existing
native journal. The frozen full `scripts/gate.sh` completed with 1,239 passed
in 1389.64 seconds, without failures/skips. Python 3.11.16 at
`/tmp/chimera-c0-20261006/.venv/bin/python` imported the owned checkout's
`src/ghimera/__init__.py`. Offline lock, Ruff, formatting and strict typing also
passed. Exact staged tree and absence of unstaged changes were verified before
the source commit.

These controlled native/protocol checks do not measure served-model quality,
remote token spend, multilingual recognition, source coverage or deployment.

## Artifact and independent installed acceptance

The release metadata changes only version/lock self-version, its assertion and
documentation; production source and examples must remain byte-identical to the
gated source commit. Five release metadata checks passed in 1.81 seconds,
without failures/skips, under the same Python 3.11.16 interpreter/source
binding. The explicit offline lock check accepted the same 142 packages; only
the project's self-version changed. Complete wheel/source-archive Git-blob
closure, license, fresh non-editable installed imports and both command entry
points passed before publication.

Independent installed acceptance uses fresh child processes terminating before
reservation, after durable intent, after controlled port contact and after
acknowledged return. Fresh native journal reads must preserve exact original
request/result hashes, reservation counts, uncertainty and held budget restore.
It uses injected ports, not actual remote models or test-module imports.

The installed four-window check passed. Before reservation, the journal held
no invocation. After durable intent and after controlled contact, its original
reservation remained uncertain and native budget restore refused retry. After
acknowledged return, the request/result pins and one reservation survived a
fresh-process native read. This is not a whole-session resume or answer replay
test and involved zero actual model/service calls.

The independently installed environment used Python 3.11.16 at
`wheel-model-recovery-env/bin/python` in the private operator workspace; its
import resolved that environment's own `site-packages/ghimera`, not editable
source. Before installation, neither project import existed. Third-party
dependency metadata matched the previously accepted environment; this is not
complete optional-extra or served-model runtime admission. Both `ghimera` and
`ghimera-delivery` help entry points passed.

The source archive was built offline, then the wheel was built from that
archive, with explicit Python 3.11.16 and the pinned build backend. Complete
inspection compared all 178 wheel package members and 454 tracked source
members against the exact release Git blobs, checked metadata/license and
rejected untracked/missing content. Both Twine checks passed under the private
Python 3.11.16 tooling environment.

## Publication readback

Release commit `a7653c035c688f7fe3b01b29ebe90059573fdca4` was atomically
fast-forwarded to GitHub main with new annotated tag `v0.4.9`, object
`4203c08c46008b8884bc62f006c72f4f07368812`, peeling to that same commit.
Independent remote readback matched all three identities. Prior tags were not
changed. Both files uploaded successfully to the official PyPI destination.
Independent public metadata and original-file readback matched the accepted
local bytes, with TLS verification, redirects refused and no credentials:

- Wheel: 418,122 bytes; SHA-256
  `797ddabc686e78d9748a854b279cb818789210c61aa9fb06f2311518524f48f1`.
- Source archive: 1,185,687 bytes; SHA-256
  `43e8271026bfca5d86821e7c9ffde6c957584a02ae7db036bee7b043f8676a30`.

These immutable artifacts are not rebuilt to incorporate this later
evidence-only documentation update.

## Remaining scope

Whole interrupted-session adoption, durable model-result replay, evidence-backed
reconciliation decisions, graph uncertainty, retained-reader control-state
restore and embedding/encoding request intents remain I03 requirements. Existing
completed-round continuation is not arbitrary crash recovery. A return marker
does not make a generated claim accepted or prove that a remote request was
unspent. Simplified Chinese scanned-PDF OCR remains quality-unvalidated; no
actual Qwen model acceptance is claimed.
