# Native corpus encoding recovery

Add the optional `encoding_recovery` section in
[`examples/corpus-encoding-recovery.toml`](../examples/corpus-encoding-recovery.toml)
to a reviewed corpus recipe. Its versioned limits bound lifetime calls, prefixed
input characters, and retained intent/result bytes. These are admission
reservations, not reported provider tokens. Failure, cancellation, reopening,
and configuration changes do not return a call or character allowance. Lowering
limits below existing reservations refuses at open. Raising limits preserves
prior reservations. An enrolled corpus refuses a configuration that disables
recovery; a legacy corpus without this option keeps its existing identity,
serialization, schema and behavior.

`CorpusStorage` owns recovery in its existing owner-private `corpus.sqlite`.
It commits the exact service configuration, texts, canonical request digest,
corpus identity, generation and purpose before calling the encoder. The row is
initially `unknown`: it proves admission, not contact, completion or usage.
An unresolved invocation refuses automatic replay. An observed refusal or
cancellation retains its reservation and cannot become a reusable success.
There is no automatic remote status reconciliation or operator override API.

After the adapter returns a validated successful batch, the exact vectors,
original `EncodingCall`, result digest and audit identifier commit in one native
transaction. This happens before the caller receives vectors and before an
append publishes originals/passages. A process death after this transaction
allows the same intent to recover the batch locally. Death after remote work
but before the transaction leaves `unknown`, preserving uncertainty without
inventing an acknowledgement. Recovery validates the original audit and vector
binding; changed or incomplete results refuse before another call.

Both native append and query use this boundary. A query's invocation binds the
generation of its index snapshot. A different generation creates a different
intent, preserving the corpus context pin. Recovered passage vectors retain the
original audit identifier; query results retain their original call observation.
`CorpusQuery.encoding_recovery` and `CorpusReceipt.encoding_recovery` expose the
invocation digest, original call identifier, generation and `reused` flag.
`EvidenceCorpus.encoding_recovery_state()` exposes persistent reservation totals,
acknowledged count and unknown count. These fields do not claim fresh provider
spend. A locally reused query emits no new `encoding_observer` callback; a
research controller must reconcile the original call against its own durable
accounting evidence.

The focused tests use real native SQLite/FAISS and in-memory model wire doubles.
They terminate real child processes before acknowledgement and after durable
acknowledgement, on append and query paths. They establish persistence,
admission, provenance, refusal and local reuse behavior. They do not establish
model quality, remote acceptance, deployed service behavior or a finished release.
