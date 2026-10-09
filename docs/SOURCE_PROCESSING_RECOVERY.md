# Original source-processing acknowledgements

This opt-in unpublished candidate adds research-recovery `/7`, requiring both
native acquisition and `source-processing-recovery/1`. Older `/1–6` recipe
dumps, source operations, native model wires and prompt revisions remain
unchanged. The [inert fragment](../examples/source-processing-recovery.toml)
requires a complete configured native journal, SourceWork frontier, retained
model results and scoring `/3` RUN encoding policy. It neither supplies an
endpoint nor selects an alternative model/runtime.

The existing SourceWork SQLite transaction owns one bounded
`source-processing-control/1` cursor beside the original acquired Page/control.
Its store profile `/4` is selected only for this new policy. The cursor retains
the exact original operation/request/Page, research recipe/control, runtime,
session/frontier, budget reference and journal prefix; acknowledged parser
reading and ordered native ranking; first/second native verdict plus original
client disposition; and exact original model reservation coordinates. It does
not create a corpus identity, second database, quota or graph implementation.

`Collector.recover(..., boundary="source_processing",
snapshot_sha256=original_cursor_digest)` is the public native process-fresh
entry. Inspect its exact current cut using
`SourceWorkStore.processing_read(config, original_run_id)`. The expected digest
is checked again under the original source writer on restoration. No latest-cut
selection or broad service profile is inferred. Existing command/service profiles
remain unchanged; new disjoint profiles expose this same native boundary.

## Native command and service entrypoints

The [inert command](../examples/collector-processing-recover.toml) requires
collector-command `/9`, execution `/7`, explicit `source_processing` and the
exact original cursor digest. It omits the request file and resumes only the
original output reservation. The configured native `processing_read` preflight
precedes credential resolution, assembly or new contacts. Native Collector
restoration again binds the original model/runtime and journal/source writer.

The [inert service fragment](../examples/collection-service-processing-recovery.toml)
selects fixed service-recovery `/7` and jobs `/8`, against an original native
research-recovery `/7` recipe. Its ordinary command template remains `/2`.
Existing service-recovery `/6` permissions still contain only query/acquisition;
neither its selector `/1` nor any older fixed profile accepts processing.
Service `/7` cannot carry model reconciliation or permitted-boundary fields,
including explicit nulls. Never rewrite a retained recipe/job to opt it in.

With `on_restart="hold"`, authenticated HTTP recovery requires selector `/2`,
`boundary="source_processing"` and the exact original cursor digest. Duplicate
or unknown fields and foreign versions refuse. Direct service recovery uses
`await service.recover(original_run_id, snapshot_sha256=original_cursor_digest)`.
The existing `adopt_acknowledged` restart choice may inspect the original native
cut automatically; it is not a retry of UNKNOWN work. Admission revalidates the
original request/model/runtime and SourceWork writer before persisting the
original job's lifetime adoption attempt and digest, then launches native `/9`.
Execution revalidates them again. Prepared graph restoration is deliberately
left to the core source-bound graph owner before **any** source, model or encoding
contact: service admission does not fabricate a new expected graph snapshot.
Graph drift can therefore launch an attempt that ends truthfully held without
contact; that attempt remains consumed.

Cancelled/failed work, live owners, torn proof, changed policy/recipe/request,
UNKNOWN results and exhausted unfinished-source adoption allowances remain
held or refused. A complete original archive is instead adopted through the
existing full archive/reservation/journal/graph readback and ordinary handoff,
even when source adoption attempts are exhausted or no cursor is supplied.
An empty HTTP recover body is archive-only in **both** restart modes: it may
complete that handoff, but cannot adopt unfinished processing even when native
automatic restart adoption is configured. No new storage,
service lifecycle, output reservation, handoff or HTTP authentication is added.

Supported cuts are an acknowledged parser reading before scoring; exact RUN
vector ACKs before scoring consumer return; acknowledged native ranking before
judgment; original first/second verdict ACK before consumer application; and
retained native verdict/HOLD consumption before later source stages. The exact
source, query/window/link inputs, encoder recipes, original sequences and
retained vectors are revalidated by the existing RUN owner. Original model ACK
replay uses `ModelInvocation.replay`, validates the original logical request and
result, and does not create another reservation/contact. An unstarted saved
verdict may contact once under its original remaining budget. UNKNOWN intents
remain charged and held. Relevance/HOLD is never overridden by recovery.

The native graph owner captures its **actual prepared immutable batch under its
existing graph lock before sink contact**. Recovery checks the saved graph
prefix plus only these exact batches, rederiving original acquired-source
discovery and document observations from the original graph policy/Page/parser
reading. Existing immutable sequence files support same-byte idempotence.
An acknowledged missing batch, unrelated/changed graph suffix, torn reading,
foreign source, changed recipe/runtime/model or live writer refuses. No graph
ACK or HTTP evidence is invented from text equality.

Elapsed time and restart downtime use the original acquired control reference;
native budgets derive original charged model and encoding intents, including
UNKNOWN. Recovery neither reopens an owned file nor refetches its retained Page
or repeats parsing. A parser that has not durably returned a reading remains
held even when its intent preceded contact: HTML locator-health observations
are persistent effects, not automatically repeatable local computation.

## Evidence and limits

Focused real-process kill regressions in
`test_source_processing_recovery.py` and `test_source_processing_interfaces.py`
passed all 53 selected cases without skips under Python 3.11.16 against this
isolated source, with the platform SDK absent. This includes the corrected native
RUN journal event-name binding and corrupt persisted cursor readback; no source
guard was relaxed to obtain the pass. The latter module invokes
actual native execute, fresh service owners and authenticated HTTP, retaining
original output/source/encoding/model proof. Loopback HTTP/model/encoder results are controlled protocol
fixtures, not real-model quality or publication evidence. Strict static checks
and test readiness are reported separately from behavioral acceptance.

After verdict consumption the cursor deliberately enters `consuming`; cuts in
semantic extraction/review, visuals, identity, dedup/frontier mutation and
completed-source handoff still hold. Model-based PDF parsing without a native
parser ACK, discovery/retained-reader concurrency, cancelled source work and
arbitrary graph effects are not adopted. This slice does not close I03 as a
whole or establish useful real-model answers. Real acceptance requires the
original available model and source policy independently of lifecycle fixtures.
