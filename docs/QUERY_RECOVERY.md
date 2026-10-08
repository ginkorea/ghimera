# Original serial query control recovery

This opt-in slice saves actual discovery and retained-reader control in the
existing `ResearchRecoveryStore`. It adds `ghimera.research-control-snapshot/2`
for query boundaries; model snapshot `/1`, ordinary concurrent collection and
unselected recipes remain unchanged. The inert policy fragment is
[query-control-recovery.toml](../examples/query-control-recovery.toml).

Select `research_recovery/4` with `query_control = "serial_acknowledged"` before
starting the original run. This profile requires research search concurrency
one, and, when configured, serial ordered discovery without cold-start fanout.
It refuses unsupported overlap rather than changing ordinary concurrency.
Native journal record/total bounds and the existing `max_snapshot_bytes` bound
apply; this introduces neither another database nor a separate query quota.
The effective non-secret recipe remains in the original receipt and snapshot.
Corpus-backed queries also require the existing native encoding recovery policy.

Before query/provider/encoding contact, the native driver saves the original
outer reservation: one operation identity, stage/round/query/provider cursor,
exact request bytes/hash, original search/fetch/retained debit ordinals, and,
for corpus queries, exact corpus recipe, generation and encoding identity.
Cited-by reservations additionally retain the original reference journal row
and source identity. The same snapshot contains the original request, plan,
partial result, search/retrieval observations, native session/frontier, graph,
runtime identities and collection quantum. Native journal append commits
`query_intent` before contact. An unstarted saved reservation resumes that
same identity once; query text is never used to infer an original operation.

The successful terminal native row retains the complete bounded
`SearchResponse` or `CorpusEvidenceBundle` as `query_ack` before admitting it
into outer in-memory control. A fresh process can adopt that original result
without another provider, encoding or learned-score call. For a cut after an
original learned-score ACK but before the outer query ACK, native
`ModelInvocation.replay` reuses the original score bytes and original corpus
operation/encoding ACK. It may reconstruct local immutable corpus retrieval;
it does not prepare/contact the scorer, re-encode, or create another corpus
operation. Existing corpus/source projection, generation, native writer locks,
model/result lineage, runtime and journal proofs are rechecked before contact.

Use the same original request, recipe, runtime and unfinished output reservation:

```python
result = await collector.recover(
    original_run_id,
    snapshot_sha256=original_query_snapshot_sha256,
    boundary="query_return",
)
```

The native command route is `ghimera.collector-command/6` with execution `/5`,
`operation = "recover"`, `recovery_boundary = "query_return"` and the exact
snapshot pin. The service route selects `ghimera.service-recovery/4` with
`boundary = "query_return"` before submission; jobs use `/5`. Existing service
startup/manual recovery, durable per-job adoption attempts and authenticated
control routes remain the owners. The command factory binds a retained reader
only from its explicit typed reader and exact borrowed corpus. The additive
[configured corpus command /7](COMMAND_CORPUS.md) owns that native corpus for
standalone CLI use; the service
uses its configured native corpus. Completion uses the original result archive,
optional corpus append and outbox handoff. Completed archive admission refuses
unresolved query originals as well as unresolved model/source work.

Restoration preserves original calls, bytes, learned-score reservations, round
limits, provider usage and collection quantum. Downtime is charged to the
original wall budget; exhausted bounds refuse before new contact. Full local
ACK adoption is not a new query or model reservation. Recovery does not accept
an immediate round-pause policy; native cancellation remains available.

## Limits and evidence

Only the exact serial saved query boundary is admitted: an original unstarted
reservation, a full successful outer ACK, or the supported original learned
score ACK. Pending/UNKNOWN query, encoding or score outcomes remain charged
and held. Refused/cancelled query rows retain native terminal accounting, but
are not successful-return adoption boundaries. Later intents/effects, torn or
changed journal/control/source/graph evidence, changed corpus generation/pins,
and active writers remain refusals. This does not close arbitrary mid-source,
concurrent, reader, discovery or graph interruption recovery. In particular,
it does not guess which remote call succeeded or reconcile unresolved chains.

Dedicated tests use real process termination, fresh native command/service
startup, local HTTP fixtures and native SQLite/FAISS with controlled CPU score
and encoding protocol fixtures. They prove state/accounting/contact behavior,
not live model accuracy, production deployment or multilingual ranking quality.
