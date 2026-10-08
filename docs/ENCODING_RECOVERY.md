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
There is no automatic remote status reconciliation. Explicit caller-owned
abandonment can authorize one separately charged attempt as described below.

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

## Explicit unknown-outcome decisions

For newly admitted invocations, the same native SQLite owner records the exact
original corpus configuration before contact. The caller obtains an immutable
`EncodingInvocationObservation` with
`EvidenceCorpus.observe_encoding_invocation(invocation_sha256)`, then supplies a
versioned `EncodingReconciliationDecision` bound to that complete observation
and its digest. Its only action is `abandon_and_authorize_new_attempt`. Caller
attribution and reason are supplied by the caller; neither is a claim of verified
human approval or proof that the server did no work. See the non-active typed
[example](../examples/encoding_reconciliation.py).

`EvidenceCorpus.reconcile_encoding(decision)` takes the existing native writer
lease and commits the immutable decision plus one new `unknown` invocation in
one transaction. It retains the original unknown invocation and its call,
character and byte reservations. The new attempt is charged separately against
the original remaining quota, including bounded admission/decision history
bytes. It requires the exact original corpus identity, service, inputs,
configuration and current generation. A later policy increase is supported by
normal corpus work, but cannot become original reconciliation authorization.
Stale observations, changed inputs/control, absent original admission evidence,
and exhausted original quotas refuse without model contact. Historical unknown
rows lacking the admission record are never repaired by inferring a policy.

The returned `EncodingAttemptAuthorization` is an exact decision/admission
receipt. Supply it explicitly as `search(..., encoding_authorization=receipt)`
or `append(..., encoding_authorizations=(receipt,))`; ordinary calls continue
to refuse the original unknown. Native contact consumes the authorization
durably once under the same writer lease. Recovery-enabled queries retain that
lease through encoder contact, so a concurrent decision cannot abandon a live
invocation. Append validates every supplied receipt before any batch contact.
Death after the decision but before consumption allows that original receipt
to be used once. Death after consumption leaves another charged unknown and
refuses replay. An acknowledged new batch can be reused locally with its own
original audit provenance; the old unknown remains unchanged and charged.

An exact repeated decision recovers its historical receipt without creating
another attempt. It does not refresh contact permission. Another decision over
the old snapshot refuses. Further unknown attempts require their own fresh
observations and explicit decisions within the same original quota.
`encoding_reconciliation_history()` returns the retained ordered decision
records. Existing encoding/model wires, legacy disabled configurations and
acknowledgement reuse retain their contracts.

This increment implements explicit native encoding reconciliation only.
Arbitrary interrupted source, research-model, graph and service adoption remain
separate I03 requirements. Protocol doubles and native crash tests do not
establish remote reconciliation, model quality, deployed acceptance or release.
