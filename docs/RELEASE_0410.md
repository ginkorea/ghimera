# Ghimera 0.4.10 release acceptance

Status: SOURCE AND INSTALLED ACCEPTANCE PASSED; PUBLICATION PENDING.

The independent core source candidate is `71f9ea8`, integrating model-return
replay, the three bounded implementation lanes and accepted visual
collector/graph/model-context bindings. The combined frozen `scripts/gate.sh`
passed 1,318 tests in 1673.41 seconds without failures/skips, under Python
3.11.16 at `/tmp/chimera-c0-20261006/.venv/bin/python`, importing the exact
owned core source. Offline lock, Ruff, formatting and strict typing passed.
Source commit is `71f9ea8cd58b483cad4c4e901870d4e4c63394e7`, tree
`7bd43371b2532ae8f8324fcdb45d230159571db4`.

Release metadata changes version/lock self-version, its assertion and prose
only. Production source and examples must remain byte-identical to the gated
commit. Build an exact source archive, build the wheel from that archive, inspect
all package members against Git blobs and independently install it into a fresh
owned environment with no editable source or platform SDK import.

Installed acceptance exercised the three command entry points, native
collection service lifecycle, archive-only handoff retry, durable remote
delivery/readback/retention, original model-return replay, accepted-image graph
projection and citation basis. These are controlled protocol/storage checks,
not real served-model or multilingual output-quality measurements. All 46
selected native checks passed in 42.57 seconds, with no failures/skips, using
Python 3.11.16 in an independent owned wheel environment. Tests/examples lived
in separate scratch without `src`; imports resolved the installed wheel, not
an editable checkout, and the platform SDK was absent. Third-party dependencies
were reused from the existing accepted environment without project/editable
metadata; this is not complete optional-extra runtime admission.

The initial private artifacts matched all 190 package files and 492 tracked
source-archive files to Git; both Twine checks passed. Final release-prose
changes require rebuilding and inspecting new artifacts before publication.

## Actual transport and source evidence

A native Patchright 1.63.0 Chromium capture through the laptop's existing Tor
listener passed. Read-only native launch-flag verification and an uncached
Tor Project probe reported `IsTor=true`. The public source page round-tripped
through native page/ledger evidence; no model calls or credentials were used.
This proves browser transport/source retention, not anonymity or research
quality. A bounded attempt against the Tor Project's published onion support
service failed with `fetch_failed`; actual onion investigation remains open.

Publication requires an unused version, passing checks, exact artifact hashes,
an immutable new tag on the official repository, successful PyPI upload and
independent original public-file readback. Previous artifacts/tags remain
unchanged. The capability tracker remains authoritative about open quality,
interrupted-control adoption, deployed service and actual onion acceptance.
