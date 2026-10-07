# Grounded review and explicit date assertions

Status: development source, not published package or real-model quality acceptance.
Select `ghimera.semantic-verification/3` through the existing defined-ontology
semantic recipe; [the non-active example](../examples/semantics-grounded.toml)
shows the additional explicit `max_coverage_findings` bound. Missing this bound
refuses configuration; older review profiles cannot silently acquire it.

This responds to two observed failures in the
[native bilingual diagnostic](ORGANIZATION_BILINGUAL_EVIDENCE.md): a reviewer
called absent dates unsupported claims, and reported incomplete coverage
without identifying what was missing. Original trial records remain unchanged.

## Date-state contract

The new `ghimera.semantic-review/3` response retains independent mention
type/role and relationship entailment/direction checks. Its validity object is:

```json
{"asserted": false, "assessment": null}
```

This is valid only when **both original proposed dates are null**. It makes
no temporal claim and does not mean the relation is timeless or currently true.
If either proposed date is non-null, `asserted` must be true and `assessment`
must contain a supported/unsupported/ambiguous date judgment and reason.
The deterministic boundary checks this against the unchanged original relation;
an asserted date cannot be disguised as unasserted to bypass review.

Overall verdicts aggregate only applicable dimensions. Any unsupported check
dominates; otherwise any ambiguity dominates. Unasserted dates are neutral,
but entailment and direction still need support. No original date, judgment,
proposal, confidence threshold or source span is repaired after inference.

## Concrete omissions, not invented facts

`coverage_findings` contains discriminated `mention` or `relation` witnesses.
A mention identifies its configured role, exact native surface, citation and
per-surface zero-based occurrence. A relation identifies a configured rule,
exact native endpoint occurrences and an exact evidence quote containing both.
Original line breaks and spelling are retained; no translation, alias merge,
span completion or quotation normalization is used to pass validation.

The boundary checks source existence, citation, occurrence, role/rule vocabulary,
distinctness, the caller's finding count, and whether the supposedly missing
item was already proposed. These checks bind the model assessment to source
text; they do not independently prove its ontology or entailment judgments.

`incomplete` requires at least one concrete witness. If omissions cannot be
identified, coverage is `uncertain`, not proven complete; `adequate` and
`uncertain` have empty finding lists. A model's `adequate` judgment is still
not a completeness proof. Hitting a finding limit is not coverage acceptance.

Findings remain in review observations and serialized semantic windows. They
never insert new entities or relations into the graph. The existing bounded
planning-gap projection passes them to follow-up research with source identity
and reviewer provenance; the planner must verify them before making new claims.
Caller-supplied planning contexts also recheck the witnesses against retained
native sources. Witness surface/evidence characters count against the existing
planning evidence allowance and retained omission count. Context, finding,
call, output and wall limits remain explicit;
there is no second hidden review or automatic retry.

## Compatibility and evidence

Verification/1 and /2 remain separate policies, prompts and response types.
Readers preserve /3 rather than downgrading it into a legacy response; profile
mismatches refuse. Original proposal observations remain immutable. The same
collector/model ports, ledger, graph owner, archive and journal are reused.

Contract fixtures cover date-state binding, failed dimensions, literal omission
witnesses, occurrence/citation errors, already-covered and duplicate items,
operator limits, independent projection, archive/journal replay and planning.
These are implementation checks, not model recall, entailment accuracy or a
verified organizational network. Fresh positive/negative native-document model
acceptance and the broader workflow in
[ORGANIZATION_RESEARCH.md](ORGANIZATION_RESEARCH.md) remain required.

The bounded revised semantic/planning suite passed **111 checks, zero failed,
zero skipped**, in 5.96 seconds using
`/tmp/chimera-c0-20261006/.venv/bin/python`, Python 3.11.16, importing this
checkout's `src/ghimera`. A wider preceding run found one input-budget failure:
the review schema exceeded the fixture's 10,000-character research allowance.
That test now explicitly supplies 20,000 characters, with another check proving
that the smaller allowance still refuses before the review wire is called.
Production defaults were not increased. The first sandboxed asynchronous run
was interrupted and is not counted as passing.

Full-gate browser dependencies remain explicit operator inputs:
`CHIMERA_TEST_BROWSER` and `CHIMERA_TEST_ISOLATOR`. The gate now refuses missing,
relative, non-file or non-executable fixture paths before starting a long suite.
A full-gate invocation initially omitted the isolator and was interrupted after
its missing-setting errors; that invocation is not a full-gate pass.

The corrected complete `scripts/gate.sh` run finished on 7 October 2026 UTC
using that same owned Python 3.11.16 interpreter and checkout: **593 passed,
zero failed, zero skipped**, in 528.77 seconds. Offline lock checking resolved
137 packages; lint/formatting passed for 137 files and strict typing passed for
94 source files. The installed Chromium ran with `/usr/bin/bwrap` and local
fixture services. No public challenge target, actual inference service or pool
job was used. This establishes source compatibility, not real-model quality,
full organizational research acceptance, publication or deployment.
