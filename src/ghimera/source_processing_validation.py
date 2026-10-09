"""Strict admission of only the saved source consumer and its original ACK tail."""

import hashlib

from ghimera.config import GhimeraConfig
from ghimera.journal_types import JournalReport
from ghimera.judgment_context import native_scoring_reading
from ghimera.models import LinkCandidate, Verdict
from ghimera.source_processing import SourceProcessingCursor, row_pin
from ghimera.source_work_types import SourceOperation


def validate_processing(
    config: GhimeraConfig,
    cursor: SourceProcessingCursor,
    operation: SourceOperation,
    journal: JournalReport,
) -> None:
    original = cursor.original
    rows = journal.rows
    if (
        config.research_recovery is None
        or config.research_recovery.source_processing is None
        or original.progress.harvest.receipt.effective_config != config
        or journal.header.config != config
        or journal.header.run_id != original.run_id
        or journal.header.goal != original.progress.harvest.goal
        or journal.header.judge != original.runtime.judge
        or journal.state != "unsealed"
        or journal.incomplete_tail
        or operation.operation_id != original.operation_id
        or operation.state != "processing"
        or row_pin(operation) != cursor.operation_sha256
        or row_pin(SourceOperation.model_validate(dict(operation.model_dump(), state="acquired")))
        != original.operation_sha256
        or operation.page is None
        or len(cursor.prefix_sha256) > len(rows)
        or tuple(row_pin(row) for row in rows[: len(cursor.prefix_sha256)]) != cursor.prefix_sha256
        or rows[: len(original.progress.harvest.ledger)] != original.progress.harvest.ledger
        or cursor.stage in {"parsing", "consuming"}
    ):
        raise ValueError(
            "processing requires exact acquired original, recipe, prefix and acknowledged stage"
        )
    if cursor.extracted is None:
        raise ValueError("unknown parser work remains held, including locator-health effects")
    reading = native_scoring_reading(rows, cursor.extracted)
    if (
        reading.source_url != operation.page.final_url
        or reading.source_sha256 != hashlib.sha256(operation.page.body).hexdigest()
    ):
        raise ValueError("parser reading is not the retained original Page")
    if cursor.ranked is not None:
        scores = tuple(
            row
            for row in rows[: len(cursor.prefix_sha256)]
            if row.scoring_source is not None
            and row.scoring_source.source_url == reading.source_url
            and row.scoring_source.source_sha256 == reading.source_sha256
        )
        if not scores or scores[-1].similarity is None or config.scoring is None:
            raise ValueError("retained ranking lacks its original source-bound score")
        from ghimera.semantic_scoring import keyword_score

        native_links = tuple(
            sorted(
                cursor.extracted.links,
                key=lambda link: keyword_score(original.progress.harvest.goal, link),
                reverse=True,
            )[: config.scoring.max_links]
        )
        evidence = scores[-1].similarity
        if len(native_links) != len(evidence.links):
            raise ValueError("retained ranking changed the complete native link candidates")
        expected = tuple(
            sorted(
                (
                    LinkCandidate(url=link.url, anchor=link.anchor, score=score.score)
                    for link, score in zip(native_links, evidence.links, strict=True)
                ),
                key=lambda link: link.score,
                reverse=True,
            )
        )
        if cursor.ranked != expected:
            raise ValueError("retained ranking differs from the original native scorer result")
    for verdict, disposition, second in (
        (cursor.first_verdict, cursor.first_disposition, False),
        (cursor.second_verdict, cursor.second_disposition, True),
    ):
        if verdict is None:
            continue
        events = tuple(
            row
            for row in rows[: len(cursor.prefix_sha256)]
            if row.event == "verdict"
            and row.refusal is None
            and row.url in {operation.request.url, operation.page.final_url}
            and row.reason == f"{verdict.decision}: {verdict.reason}"
            and row.model_call == verdict.model_call
            and (row.document_judgment is None or row.document_judgment.second_look == second)
        )
        if not events:
            raise ValueError("retained verdict lacks its original consumer acknowledgement")
        observed = events[-1]
        judgment = observed.document_judgment
        from ghimera.model_work import record_output

        originals = tuple(
            row
            for row in rows[: len(cursor.prefix_sha256)]
            if row.model_ack is not None
            and row.model_ack.outcome == "returned"
            and row.model_ack.stored_output is not None
            and row.model_ack.stored_output.body() == record_output(verdict)
        )
        if not originals or disposition != (
            judgment.client_disposition if judgment is not None else verdict.decision
        ):
            raise ValueError("retained verdict changed its original decision/HOLD disposition")
    tail = rows[len(cursor.prefix_sha256) :]
    if cursor.stage == "scoring":
        allowed = {
            "scoring_source",
            "run_encoding_intent",
            "encoding",
            "run_encoding_replay",
            "intent_reference",
        }
        if any(row.event not in allowed for row in tail):
            raise ValueError("source scoring has unrelated later effects")
        intents = tuple(row for row in tail if row.run_encoding_intent is not None)
        for row in intents:
            intent = row.run_encoding_intent
            acks = tuple(
                ack
                for ack in rows
                if ack.run_encoding_ack is not None
                and ack.run_encoding_ack.original_intent_sequence == row.sequence
            )
            if (
                intent is None
                or intent.scope.document_sha256 != row_pin(cursor.extracted)
                or len(acks) != 1
            ):
                raise ValueError("unknown or foreign original encoding remains charged and held")
    elif cursor.stage in {"first_verdict", "second_verdict"}:
        sequence = cursor.model_sequence
        if sequence is None:
            raise ValueError("pending verdict lacks original coordinates")
        if tail:
            model_intent = rows[sequence].model_intent if sequence < len(rows) else None
            acks = tuple(
                row
                for row in tail
                if row.model_ack is not None and row.model_ack.intent_sequence == sequence
            )
            expected_url = (
                operation.page.final_url
                if config.document_judgment is not None
                else operation.request.url
            )
            if (
                model_intent is None
                or sequence != len(cursor.prefix_sha256)
                or model_intent.phase != "verdict"
                or model_intent.input_sha256 != cursor.model_request_sha256
                or rows[sequence].url != expected_url
                or rows[sequence].model != original.runtime.judge
                or len(acks) != 1
                or len(tail) != 2
            ):
                raise ValueError("pending verdict requires exactly its original intent and ACK")
            ack = acks[0].model_ack
            if ack is None or ack.outcome != "returned" or ack.stored_output is None:
                raise ValueError("unknown verdict outcome remains charged and held")
            Verdict.model_validate_json(ack.stored_output.body())
    elif tail:
        raise ValueError("source cursor cannot adopt arbitrary later journal effects")
