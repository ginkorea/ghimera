"""Non-active caller-owned decision example; importing this contacts no service."""

from ghimera.corpus import EvidenceCorpus
from ghimera.encoding_reconciliation import (
    EncodingAttemptAuthorization,
    EncodingReconciliationDecision,
)


def authorize_one_attempt(
    corpus: EvidenceCorpus, invocation_sha256: str, *, caller: str, reason: str
) -> EncodingAttemptAuthorization:
    """Call only after deciding to abandon this exact unknown, retaining its full charge."""
    observed = corpus.observe_encoding_invocation(invocation_sha256)
    decision = EncodingReconciliationDecision(
        schema="ghimera.encoding-reconciliation/1",
        action="abandon_and_authorize_new_attempt",
        caller=caller,
        reason=reason,
        observed=observed,
        observed_sha256=observed.sha256,
    )
    # This records a decision and charges an attempt; it does not contact a model.
    # Pass the returned receipt explicitly to this same corpus's append or search.
    return corpus.reconcile_encoding(decision)
