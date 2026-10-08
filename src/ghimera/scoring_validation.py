"""Shared semantic-reference replay rules for harvests and durable observations."""

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghimera.config import GhimeraConfig
    from ghimera.models import LedgerRow


def validate_reference_rows(
    config: "GhimeraConfig", goal_text: str, rows: tuple["LedgerRow", ...]
) -> None:
    policy = config.scoring
    prepared = None
    if policy is not None and policy.intent_encoder != policy.encoder:
        query_sequences = {
            row.intent_reference.encoding_sequence
            for row in rows
            if row.intent_reference is not None
        }
        for row in rows:
            call = row.encoding_call
            if (
                call is not None
                and call.service == policy.intent_encoder
                and (
                    row.url is not None
                    or (call.outcome == "success" and row.sequence not in query_sequences)
                )
            ):
                raise ValueError("successful query encoding must prepare the original intent")
    for row in rows:
        if row.intent_reference is not None:
            observed = row.intent_reference
            if (
                policy is None
                or policy.reference_source != "intent"
                or prepared is not None
                or observed.encoding_sequence >= row.sequence
            ):
                raise ValueError("intent references require one prior encoding in intent mode")
            encoding = rows[observed.encoding_sequence]
            if encoding.encoding_call is None or encoding.url is not None:
                raise ValueError("intent references must identify their prior encoding call")
            observed.validate_binding(goal_text, policy.intent_encoder, encoding.encoding_call)
            prepared = observed
        if row.similarity is None:
            continue
        if row.similarity.goal_sha256 != hashlib.sha256(goal_text.encode()).hexdigest():
            raise ValueError("similarity observations must bind this run's original intent")
        expected = (
            prepared.references.sha256
            if policy is not None and policy.reference_source == "intent" and prepared is not None
            else policy.references_sha256
            if policy is not None
            else None
        )
        if row.similarity.references_sha256 != expected:
            raise ValueError("similarity observations must bind their configured reference vectors")
        if prepared is not None:
            reference = prepared.references.chunks[0]
            if any(
                window.reference_source_id != reference.source_id
                or window.reference_text_sha256 != reference.text_sha256
                for window in row.similarity.windows
            ):
                raise ValueError("similarity windows must identify the prepared intent reference")
