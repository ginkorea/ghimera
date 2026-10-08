"""Native non-reranked query ACK tampering and original override substitutability."""

import asyncio
import hashlib
import time

import pytest

from ghimera.budget import RunBudget
from ghimera.corpus_search import CorpusLeadSearch
from ghimera.corpus_search_wire import CorpusSearchWire
from ghimera.doubles import FakeJudge
from ghimera.journal import DirectoryLedgerSink
from ghimera.ledger import Ledger
from ghimera.models import Goal, LedgerRow
from ghimera.query_work import QueryWork, validate_query_rows
from ghimera.query_work_types import QueryAcknowledgement, QueryCursor
from ghimera.research_types import SearchResponse
from tests.test_c0 import config
from tests.test_corpus_search import binding
from tests.test_discovery_router import Adapter, discovery, provider, query, state
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus, endpoint, harvest
from tests.test_intent_research import policy

__all__ = ["endpoint"]


def test_native_non_reranked_ack_rehashed_tamper_preserves_original_encoding_but_holds(
    tmp_path, endpoint
):
    selected = corpus_config(
        tmp_path,
        endpoint[0],
        encoding_recovery=dict(
            schema="ghimera.encoding-recovery/1",
            max_calls=50,
            max_input_chars=100000,
            max_stored_bytes=1000000,
        ),
    )
    store = corpus(selected, create=True)
    cfg = config(
        search=binding(store, max_response_bytes=1000000),
        research=policy(search_concurrency=1),
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=tmp_path / "journal",
            max_record_bytes=2000000,
            max_journal_bytes=10000000,
            max_summary_bytes=2000000,
            max_records=2000,
        ),
        model_work=dict(
            schema="ghimera.model-work/1",
            max_input_bytes=2000000,
            max_unanswered_calls=4,
            uncertain_policy="hold",
            results=dict(
                schema="ghimera.model-results/1",
                max_result_bytes=65536,
                max_total_result_bytes=524288,
            ),
        ),
        research_recovery=dict(
            schema="ghimera.research-recovery/4",
            max_snapshot_bytes=4000000,
            clock_policy="include_downtime",
            tail_policy="acknowledged_model_return",
            query_control="serial_acknowledged",
        ),
    )
    budget = RunBudget(cfg, time.monotonic)
    ledger = Ledger(
        sink=DirectoryLedgerSink(cfg, "original-query", Goal(text="ports"), FakeJudge().model)
    )
    work = QueryWork(
        budget,
        ledger,
        QueryCursor(stage="discovery", round_number=1, query_index=0),
        lambda reservation: None,
    )
    adapter = CorpusLeadSearch(cfg.search, store)

    async def operation():
        await store.append(await harvest(("en", "ports report")))
        return await adapter.discover(query("ports"), budget, ledger, query_work=work)

    try:
        response = asyncio.run(operation())
        rows = ledger.snapshot()
        reservation = rows[0].query_reservation
        assert reservation is not None and reservation.corpus.generation == 1
        assert validate_query_rows(cfg, rows) == ()
        wire = CorpusSearchWire.model_validate_json(response.raw)
        assert wire.query.reranking is None and wire.query.reranking_run is None
        adapter.admit_query(reservation, response)
        before = len(endpoint[1]), budget.search_calls, budget.fetches, budget.bytes_read
        for field, value in (
            ("generation", 2),
            ("corpus_id", "0" * 32),
            ("config_sha256", "0" * 64),
            ("query_text", "schools"),
        ):
            changed_query = wire.query.model_dump()
            if field == "query_text":
                changed_query["query_sha256"] = hashlib.sha256(value.encode()).hexdigest()
            else:
                changed_query[field] = value
            if field == "generation":
                changed_query["encoding_recovery"]["generation"] = value
            changed_wire = CorpusSearchWire.model_validate(
                dict(
                    wire.model_dump(),
                    query=changed_query,
                    query_text=value if field == "query_text" else wire.query_text,
                )
            )
            forged_response = SearchResponse.model_validate(
                dict(response.model_dump(), raw=changed_wire.model_dump_json().encode())
            )
            body = forged_response.model_dump_json().encode()
            acknowledgement = QueryAcknowledgement(
                schema="ghimera.query-ack/1",
                reservation_sequence=0,
                outcome="returned",
                result_json=body,
                result_sha256=hashlib.sha256(body).hexdigest(),
            )
            forged = LedgerRow.model_validate(
                dict(
                    rows[1].model_dump(),
                    query_ack=acknowledgement,
                    bytes_read=len(forged_response.raw),
                    search_response_sha256=forged_response.content_digest(),
                    reason="grounded_search:" + hashlib.sha256(forged_response.raw).hexdigest(),
                )
            )
            assert changed_wire.query.encoding_call == wire.query.encoding_call
            assert (
                changed_wire.query.encoding_recovery.invocation_sha256
                == wire.query.encoding_recovery.invocation_sha256
            )
            with pytest.raises(ValueError, match="original corpus/query/generation"):
                validate_query_rows(cfg, (rows[0], forged))
            with pytest.raises(ValueError, match="authoritative encoding lineage"):
                store.admit_query(
                    reservation,
                    "ports",
                    pending=False,
                    sources=wire.sources,
                    query=changed_wire.query,
                )
        # Owning API must also reject a false generation in an untrusted typed object.
        false_encoding = wire.query.model_copy(
            update={
                "encoding_recovery": wire.query.encoding_recovery.model_copy(
                    update={"generation": 2}
                )
            }
        )
        with pytest.raises(ValueError, match="authoritative encoding lineage"):
            store.admit_query(
                reservation, "ports", pending=False, sources=wire.sources, query=false_encoding
            )
        assert before == (len(endpoint[1]), budget.search_calls, budget.fetches, budget.bytes_read)
        assert ledger.snapshot() == rows
    finally:
        ledger.close()
        store.close()


@pytest.mark.parametrize("configured_binding", [False, True])
def test_original_four_argument_override_dispatch_and_accounting(configured_binding):
    class OriginalSignature(Adapter):
        def __init__(self):
            super().__init__(urls=("https://example.org/report", "http://example.onion/report"))
            self.overrides = 0

        async def request_for_run(self, request, budget, ledger, rerank_decision):
            self.overrides += 1
            assert budget.search_calls == budget.fetches == 1
            assert ledger.snapshot() == () and rerank_decision is None
            return await self.request(request)

    adapter = OriginalSignature()
    if configured_binding:
        history, budget, ledger = state(discovery(provider("original")), {"original": adapter})
        observations = asyncio.run(history.discover_many(query()))
        response = observations[0].response
        assert [hit.url for hit in response.hits] == ["https://example.org/report"]
        assert ledger.snapshot()[0].route == "search:" + "@".join(provider("original").identity)
    else:
        cfg = config(research=policy())
        budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
        response = asyncio.run(adapter.discover(query(), budget, ledger))
        assert len(response.hits) == 2
    assert adapter.overrides == len(adapter.calls) == 1
    assert budget.search_calls == budget.fetches == len(ledger.snapshot()) == 1
    assert budget.bytes_read == ledger.snapshot()[0].bytes_read == len(response.raw)
    assert ledger.snapshot()[0].search_response_sha256 == response.content_digest()
