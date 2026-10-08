"""Fresh-process native query ACK witnesses, not provider/model accuracy."""

import asyncio
import hashlib
import json
import subprocess
import sys

import pytest

from ghimera.collection_service import CollectionServiceConfig
from ghimera.command import CommandOptions
from ghimera.config import GhimeraConfig
from ghimera.corpus_evidence import CorpusEvidenceReader
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.journal import read_journal
from ghimera.query_work import validate_query_rows
from ghimera.query_work_types import QueryReservation
from ghimera.research_recovery_types import ResearchQueryControlSnapshot
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import options, toml_lines
from tests.test_command_recovery import configured as original_configuration
from tests.test_command_recovery import counts
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_evidence_corpus import corpus as native_corpus
from tests.test_evidence_corpus import harvest
from tests.test_hybrid_retrieval import policy as hybrid_policy
from tests.test_offline_reranking import ScoresPort, learned_corpus, native_store
from tests.test_reference_expansion import references
from tests.test_research_reranking import run_policy
from tests.test_research_reuse import reuse_policy
from tests.test_service_recovery import ENTRYPOINT as SERVICE_ENTRYPOINT
from tests.test_service_recovery import configuration as service_configuration

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint):
    cfg = original_configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    raw = cfg.model_dump()
    raw["research_recovery"].update(
        schema="ghimera.research-recovery/4", query_control="serial_acknowledged"
    )
    raw["research"]["search_concurrency"] = 1
    return GhimeraConfig.model_validate(raw)


ENTRYPOINT = """
import os,sys
from ghimera import command
from ghimera.journal import DirectoryLedgerSink
from ghimera.research_recovery_store import ResearchRecoveryStore
from ghimera.search import GroundedSearch
from tests.test_http_fetch import ResolverFixture
from pathlib import Path
from ghimera.corpus_config import CorpusConfig
from tests.test_offline_reranking import native_store, ScoresPort
from tests.test_encoding_recovery import FixtureHttp
boundary=sys.argv[1]
recipe=Path(sys.argv[-3]).parent/'corpus-recipe.json'
store=None
if recipe.exists():
    cfg=CorpusConfig.model_validate_json(recipe.read_bytes())
    log=recipe.parent/'corpus-contacts.jsonl'
    def contact(kind):
        with log.open('a') as stream: stream.write(kind+'\\n')
    class Port(ScoresPort):
        async def prepare(self):
            contact('prepare')
            await super().prepare()
        async def score(self,request):
            contact('score')
            return await super().score(request)
    class Encoding(FixtureHttp):
        async def post(self,*args,**kwargs):
            contact('encoding')
            return await super().post(*args,**kwargs)
    store=native_store(cfg,Port(cfg.reranking),query=Encoding(cfg.query_encoder))
append=DirectoryLedgerSink.append
def appended(self,row):
    append(self,row)
    if row.query_ack is not None and boundary=='ack': os._exit(73)
    if row.query_ack is not None and boundary in {'initial_retained','planned_retained','cited_by'}:
        # The original native row is available from the durable owning journal.
        from ghimera.journal import read_journal
        original=read_journal(self._policy,self._header.run_id).rows[row.query_ack.reservation_sequence]
        if original.query_reservation.cursor.stage==boundary: os._exit(73)
    if row.model_ack is not None and boundary=='score_retained':
        from ghimera.journal import read_journal
        original=read_journal(self._policy,self._header.run_id).rows[row.model_ack.intent_sequence]
        if original.rerank_reservation is not None: os._exit(73)
DirectoryLedgerSink.append=appended
write=ResearchRecoveryStore.write
def written(self,snapshot):
    result=write(self,snapshot)
    if snapshot.phase=='query' and boundary=='unstarted': os._exit(73)
    return result
ResearchRecoveryStore.write=written
request=GroundedSearch.request_for_run
async def requested(self,*args,**kwargs):
    result=await request(self,*args,**kwargs)
    if boundary=='unknown': os._exit(74)
    return result
GroundedSearch.request_for_run=requested
execute=command.execute
async def resolved(opts): return await execute(opts,source_resolver=ResolverFixture(),corpus=store)
command.execute=resolved
raise SystemExit(command.main(sys.argv[2:]))
"""


def invoke(tmp_path, opts, boundary="none"):
    path = tmp_path / (boundary + ".toml")
    path.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))
    return subprocess.run(
        [
            sys.executable,
            "-c",
            ENTRYPOINT,
            boundary,
            "--job",
            str(path),
            "--max-job-bytes",
            "1000000",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def interrupted(tmp_path, cfg, boundary):
    opts = options(
        tmp_path,
        cfg,
        schema="ghimera.collector-command/2",
        execution=dict(schema="ghimera.command-execution/1", operation="run"),
    )
    died = invoke(tmp_path, opts, boundary)
    assert died.returncode == (74 if boundary == "unknown" else 73), died.stderr
    path = cfg.journal.directory / opts.run_id / "research-control.json"
    saved = ResearchQueryControlSnapshot.model_validate_json(path.read_bytes())
    return opts, hashlib.sha256(path.read_bytes()).hexdigest(), saved


def recovering(opts, pin):
    return CommandOptions.model_validate(
        dict(
            opts.model_dump(),
            schema="ghimera.collector-command/6",
            request_path=None,
            execution=dict(
                schema="ghimera.command-execution/5",
                operation="recover",
                recovery_boundary="query_return",
                snapshot_sha256=pin,
            ),
        )
    )


@pytest.mark.parametrize("boundary", ["ack", "unstarted"])
def test_fresh_command_adopts_original_query_or_unstarted_reservation_once(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, boundary
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin, saved = interrupted(tmp_path, cfg, boundary)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    original = read_journal(cfg.journal, opts.run_id).rows
    reservation = (opts.output_directory / "reservation.json").read_bytes()
    done = invoke(tmp_path, recovering(opts, pin))
    assert done.returncode == 0, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.status == "answered"
    assert result.harvest.ledger[: len(original)] == original
    intents = [row for row in result.harvest.ledger if row.query_reservation is not None]
    assert len(intents) == 1 and intents[0].query_reservation == saved.pending_query
    assert result.search_calls == 1 and len(result.search_observations) == 1
    assert len(search_endpoint[1]) == before[1] + (boundary == "unstarted")
    assert sum(row.event == "plan" for row in result.harvest.ledger) == 1
    assert (opts.output_directory / "reservation.json").read_bytes() == reservation


def test_actual_unknown_query_remains_charged_and_never_contacts_on_reopen(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin, saved = interrupted(tmp_path, cfg, "unknown")
    report = read_journal(cfg.journal, opts.run_id)
    assert report.rows[-1].query_reservation == saved.pending_query
    assert report.rows[-1].query_reservation.search_reservation == 1
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = invoke(tmp_path, recovering(opts, pin))
    assert done.returncode == 2
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert read_journal(cfg.journal, opts.run_id).rows == report.rows


def retained_configuration(tmp_path, cfg):
    recipe = learned_corpus(
        tmp_path,
        encoding_recovery=dict(
            schema="ghimera.encoding-recovery/1",
            max_calls=50,
            max_input_chars=100000,
            max_stored_bytes=1000000,
        ),
    )
    store = native_store(recipe, ScoresPort(recipe.reranking), create=True)
    asyncio.run(
        store.append(
            asyncio.run(harvest(("en", "Taiwan ports report with native source evidence")))
        )
    )
    reader = CorpusEvidenceReader(reader_policy(store, retrieval=hybrid_policy()), store)
    raw = cfg.model_dump()
    raw["research"]["retained_evidence"] = reuse_policy(reader).model_dump()
    raw["research"]["reranking"] = run_policy().model_dump()
    store.close()
    (tmp_path / "corpus-recipe.json").write_text(recipe.model_dump_json())
    return GhimeraConfig.model_validate(raw)


@pytest.mark.parametrize("boundary", ["initial_retained", "planned_retained", "score_retained"])
def test_fresh_native_command_retained_and_original_score_ack_no_repeat_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, boundary
):
    cfg = retained_configuration(
        tmp_path,
        configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint),
    )
    opts, pin, saved = interrupted(tmp_path, cfg, boundary)
    before = (tmp_path / "corpus-contacts.jsonl").read_text().splitlines()
    original = read_journal(cfg.journal, opts.run_id).rows
    operation = saved.pending_query.operation_id
    done = invoke(tmp_path, recovering(opts, pin))
    assert done.returncode == 0, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    after = (tmp_path / "corpus-contacts.jsonl").read_text().splitlines()
    # Initial query recovery still permits the original unstarted planned query.
    assert after[len(before) :] == (
        ["prepare", "encoding", "score"] if boundary != "planned_retained" else []
    )
    assert result.status == "answered" and result.search_calls == 0
    assert result.harvest.ledger[: len(original)] == original
    assert len(result.retrieval.observations) == 2
    assert len([row for row in result.harvest.ledger if row.query_reservation is not None]) == 2
    import sqlite3

    with sqlite3.connect(tmp_path / "corpus" / "corpus.sqlite") as database:
        assert (
            database.execute("SELECT COUNT(*) FROM operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
            == 1
        )


def test_fresh_command_cited_by_cursor_keeps_original_quantum_and_parent(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    raw = cfg.model_dump()
    raw["references"] = references(discover_cited_by=True, cited_by_query_budget=2)
    raw["research"]["max_pages_per_round"] = 2
    cfg = GhimeraConfig.model_validate(raw)
    opts, pin, saved = interrupted(tmp_path, cfg, "cited_by")
    original = read_journal(cfg.journal, opts.run_id).rows
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = invoke(tmp_path, recovering(opts, pin))
    assert done.returncode == 0, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.status == "answered"
    assert result.harvest.ledger[: len(original)] == original
    assert counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)[:2] == before[:2]
    assert result.search_calls == 2 and result.harvest.receipt.fetches == 4
    assert len([row for row in result.harvest.ledger if row.reference_query is not None]) == 1
    assert saved.pending_query.cursor.parent_sequence is not None
    assert saved.quantum_start == 1


def service_invoke(tmp_path, service, boundary):
    path = tmp_path / "service.json"
    path.write_text(service.model_dump_json())
    hook = """
from ghimera.journal import DirectoryLedgerSink
append=DirectoryLedgerSink.append
def appended(self,row):
    append(self,row)
    if row.query_ack is not None and boundary=='query_ack': os._exit(73)
DirectoryLedgerSink.append=appended
"""
    entry = SERVICE_ENTRYPOINT.replace(
        "native_invoke = ModelInvocation.invoke", hook + "\nnative_invoke = ModelInvocation.invoke"
    ).replace("{'ack', 'unknown'}", "{'query_ack', 'unknown'}")
    return subprocess.run(
        [sys.executable, "-c", entry, str(path), boundary],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_fresh_service_adopts_original_query_ack_and_native_corpus_outbox_handoff(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    service, original = service_configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, handoff=True
    )
    raw = original.model_dump()
    raw["research_recovery"].update(
        schema="ghimera.research-recovery/4", query_control="serial_acknowledged"
    )
    raw["research"]["search_concurrency"] = 1
    cfg = GhimeraConfig.model_validate(raw)
    service.command.config_path.write_text("\n".join(toml_lines(cfg.model_dump(mode="json"))))
    service = CollectionServiceConfig.model_validate(
        dict(
            service.model_dump(),
            recovery=dict(
                schema="ghimera.service-recovery/4",
                boundary="query_return",
                on_restart="adopt_acknowledged",
                max_adoption_attempts=2,
            ),
        )
    )
    native_corpus(service.corpus, create=True).close()
    asyncio.run(DeliveryOutbox(service.outbox, create=True).check_ready())
    died = service_invoke(tmp_path, service, "query_ack")
    assert died.returncode == 73, died.stderr
    run_id = json.loads(next(service.directory.glob("*.json")).read_bytes())["run_id"]
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = service_invoke(tmp_path, service, "recover")
    assert done.returncode == 0, done.stderr
    status = json.loads(done.stdout)
    assert status["phase"] == "completed" and status["adoption_attempts"] == 1
    assert status["corpus"] and status["delivery_id"]
    assert len(search_endpoint[1]) == before[1]
    result = ResearchResultArchive.read(
        service.directory / (run_id + ".output"), max_bytes=service.command.max_result_bytes
    )
    assert result.status == "answered" and result.search_calls == 1


def test_native_original_cursor_debit_provider_and_ack_mutations_refuse(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, _, saved = interrupted(tmp_path, cfg, "ack")
    rows = read_journal(cfg.journal, opts.run_id).rows
    original = rows[-2].query_reservation
    assert original is not None
    for update in ({"search_reservation": 2}, {"fetch_reservation": 2}):
        reservation = QueryReservation.model_validate(dict(original.model_dump(), **update))
        changed = rows[:-2] + (
            rows[-2].model_copy(update={"query_reservation": reservation}),
            rows[-1],
        )
        with pytest.raises(ValueError, match="debit"):
            validate_query_rows(cfg, changed)
    for update in (
        {"channel": "retained"},
        {"cursor": dict(original.cursor.model_dump(), stage="cited_by")},
        {"cursor": dict(original.cursor.model_dump(), parent_sequence=0)},
    ):
        with pytest.raises(ValueError):
            QueryReservation.model_validate(dict(original.model_dump(), **update))
    from ghimera.research_types import SearchRequest

    request = SearchRequest.model_validate_json(original.request_json).model_copy(
        update={"limit": cfg.research.results_per_query + 1}
    )
    body = request.model_dump_json().encode()
    bad = original.model_copy(
        update={"request_json": body, "request_sha256": hashlib.sha256(body).hexdigest()}
    )
    with pytest.raises(ValueError, match="allowance"):
        validate_query_rows(
            cfg, rows[:-2] + (rows[-2].model_copy(update={"query_reservation": bad}), rows[-1])
        )
    with pytest.raises(ValueError, match="return"):
        validate_query_rows(cfg, rows[:-1] + (rows[-1].model_copy(update={"reason": "changed"}),))
    for update in ({"query_index": 1}, {"round_number": 2}):
        with pytest.raises(ValueError):
            ResearchQueryControlSnapshot.model_validate(
                dict(
                    saved.model_dump(),
                    pending_query=dict(
                        saved.pending_query.model_dump(),
                        cursor=dict(saved.pending_query.cursor.model_dump(), **update),
                    ),
                )
            )


def test_selected_serial_search_refusal_preserves_native_partial_run_behavior(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    search_endpoint[2]["status"] = 503
    opts = options(
        tmp_path,
        cfg,
        schema="ghimera.collector-command/2",
        execution=dict(schema="ghimera.command-execution/1", operation="run"),
    )
    done = invoke(tmp_path, opts)
    assert done.returncode == 1 and not done.stderr, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.status == "failed" and result.search_calls > 0
    assert all(
        row.query_ack.outcome == "refused"
        for row in result.harvest.ledger
        if row.query_ack is not None
    )
    assert validate_query_rows(cfg, result.harvest.ledger) == ()


@pytest.mark.parametrize("drift", ["config", "active_writer", "later_intent", "downtime"])
def test_fresh_query_recovery_refuses_changed_original_or_unsafe_tail_before_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, drift
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin, saved = interrupted(tmp_path, cfg, "ack")
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    lock = None
    if drift == "config":
        raw = cfg.model_dump()
        raw["research"]["query_budget"] += 1
        opts.config_path.write_text(
            "\n".join(toml_lines(GhimeraConfig.model_validate(raw).model_dump(mode="json")))
        )
    elif drift == "active_writer":
        import fcntl

        lock = (cfg.journal.directory / opts.run_id / "ledger.jsonl").open("ab")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif drift == "later_intent":
        from ghimera.journal import DirectoryLedgerSink
        from ghimera.models import LedgerRow

        rows = read_journal(cfg.journal, opts.run_id).rows
        sink = DirectoryLedgerSink(
            cfg,
            opts.run_id,
            saved.progress.harvest.goal,
            saved.progress.harvest.receipt.judge,
            resume_rows=rows,
        )
        sink.append(
            LedgerRow(
                sequence=len(rows),
                event="query_intent",
                reason="query_reserved",
                query_reservation=saved.pending_query.model_copy(
                    update={
                        "operation_id": "a" * 32,
                        "search_reservation": 2,
                        "fetch_reservation": 2,
                    }
                ),
            )
        )
        sink.close()
    else:
        path = cfg.journal.directory / opts.run_id / "research-control.json"
        raw = saved.model_dump()
        raw["saved_at"] -= cfg.wall_seconds + 1
        path.write_text(ResearchQueryControlSnapshot.model_validate(raw).model_dump_json())
        pin = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        done = invoke(tmp_path, recovering(opts, pin))
        assert done.returncode == 2
        assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    finally:
        if lock is not None:
            lock.close()


@pytest.mark.parametrize("drift", ["generation", "source", "corpus_writer"])
def test_native_retained_ack_admission_rechecks_actual_corpus_owner_before_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, drift
):
    cfg = retained_configuration(
        tmp_path,
        configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint),
    )
    opts, pin, _ = interrupted(tmp_path, cfg, "planned_retained")
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    contacts = (tmp_path / "corpus-contacts.jsonl").read_bytes()
    from ghimera.corpus_config import CorpusConfig

    recipe = CorpusConfig.model_validate_json((tmp_path / "corpus-recipe.json").read_bytes())
    lock = None
    if drift == "generation":
        store = native_store(recipe, ScoresPort(recipe.reranking))
        asyncio.run(store.append(asyncio.run(harvest(("en", "new unrelated retained original")))))
        store.close()
    elif drift == "source":
        import sqlite3

        with sqlite3.connect(recipe.directory / "corpus.sqlite") as database:
            database.execute("UPDATE documents SET payload=?", (b"{}",))
    else:
        from ghimera.corpus_storage import CorpusStorage

        lock = CorpusStorage(recipe, create=False)
        lease = lock.writer()
        lease.__enter__()
    try:
        done = invoke(tmp_path, recovering(opts, pin))
        assert done.returncode == 2
        assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
        assert contacts == (tmp_path / "corpus-contacts.jsonl").read_bytes()
    finally:
        if lock is not None:
            lease.__exit__(None, None, None)
            lock.close()


def test_fresh_native_discovery_score_ack_reuses_original_outer_corpus_operation(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    from pathlib import Path

    from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.loop import GoalLoop
    from ghimera.models import Goal, Scope
    from tests.test_corpus_search import binding

    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    recipe = learned_corpus(
        tmp_path,
        encoding_recovery=dict(
            schema="ghimera.encoding-recovery/1",
            max_calls=50,
            max_input_chars=100000,
            max_stored_bytes=1000000,
        ),
    )
    store = native_store(recipe, ScoresPort(recipe.reranking), create=True)
    fixture_url = f"http://fixture.example:{source_site[0]}/plain"
    # A native controlled original supplies only a lead. Subsequent accepted
    # research evidence still comes from the existing local HTTP source fixture.
    collector = GoalLoop(
        config=GhimeraConfig.from_toml(Path("examples/chimera.toml")),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    material = asyncio.run(
        collector.run(
            Goal(text="ports", seeds=(fixture_url,)),
            Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(source_site[0],),
                max_depth=0,
                content_types=("text/html",),
            ),
        )
    )
    asyncio.run(store.append(material))
    raw = cfg.model_dump()
    raw["search"] = binding(
        store, retrieval=hybrid_policy(), max_response_bytes=1000000
    ).model_dump()
    raw["research"]["reranking"] = run_policy().model_dump()
    store.close()
    (tmp_path / "corpus-recipe.json").write_text(recipe.model_dump_json())
    cfg = GhimeraConfig.model_validate(raw)
    opts, pin, saved = interrupted(tmp_path, cfg, "score_retained")
    assert saved.pending_query.channel == "discovery"
    original = read_journal(cfg.journal, opts.run_id).rows
    contacts = (tmp_path / "corpus-contacts.jsonl").read_bytes()
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = invoke(tmp_path, recovering(opts, pin))
    assert done.returncode == 0, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.status == "answered" and result.search_calls == 1
    assert (tmp_path / "corpus-contacts.jsonl").read_bytes() == contacts
    assert result.harvest.ledger[: len(original)] == original
    assert len(model_endpoint[1]) == before[2] + 4  # Original plan/score are not contacted again.
    assert len(search_endpoint[1]) == before[1] == 0
    import sqlite3

    with sqlite3.connect(recipe.directory / "corpus.sqlite") as database:
        assert (
            database.execute("SELECT COUNT(*) FROM operations WHERE kind='query'").fetchone()[0]
            == 1
        )
        assert database.execute(
            "SELECT status FROM operations WHERE id=?", (saved.pending_query.operation_id,)
        ).fetchone() == ("committed",)
    assert len([row for row in result.harvest.ledger if row.query_reservation is not None]) == 1
    assert len([row for row in result.harvest.ledger if row.model_replay is not None]) == 1
