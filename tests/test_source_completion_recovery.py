"""Real native source ACK death/restart witnesses, not site/model accuracy."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.collection_service import CollectionServiceConfig
from ghimera.command import CommandOptions, execute
from ghimera.config import GhimeraConfig
from ghimera.journal import read_journal
from ghimera.result_archive import ArchiveReservation, ResearchResultArchive
from ghimera.source_work import SourceWorkStore, read_source_work
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import options, toml_lines
from tests.test_command_recovery import ENTRYPOINT as COMMAND_ENTRYPOINT
from tests.test_command_recovery import counts
from tests.test_http_fetch import ResolverFixture
from tests.test_service_recovery import ENTRYPOINT as SERVICE_ENTRYPOINT
from tests.test_service_recovery import configuration

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]

SOURCE_DEATH = """
from ghimera.source_work import SourceWorkStore
original_processed = SourceWorkStore.processed
def processed(self, token, result, ledger_end, **kwargs):
    try:
        original_processed(self, token, result, ledger_end, **kwargs)
    except Exception:
        import traceback
        traceback.print_exc()
        raise
    if kwargs.get('control') is not None and boundary == 'source_ack':
        os._exit(79)
SourceWorkStore.processed = processed
"""


def settings(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, *, graph=False
):
    service, cfg = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, graph=graph
    )
    raw = cfg.model_dump()
    raw["research_recovery"] = dict(
        schema="ghimera.research-recovery/2",
        max_snapshot_bytes=4000000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
        source_completion=dict(
            schema="ghimera.source-completion-recovery/1",
            execution="serial",
            max_capsule_bytes=8000000,
        ),
    )
    raw["source_work"]["frontier"] = dict(
        schema="ghimera.source-frontier/1",
        max_entries=100,
        max_entry_bytes=10000,
        max_frontier_bytes=1000000,
    )
    raw["research"]["max_pages_per_round"] = 1
    raw["research"]["max_rounds"] = 1
    cfg = GhimeraConfig.model_validate(raw)
    # Graph roles/relations require the fixture's explicit array table encoding.
    text = "\n".join(
        toml_lines({k: v for k, v in cfg.model_dump(mode="json").items() if k != "graph"})
    )
    if graph:
        graph_text = (
            Path("examples/research-graph.toml")
            .read_text()
            .replace(
                'sink_path = "/tmp/chimera-research-graphs"',
                "sink_path = " + json.dumps(str(tmp_path / "graph")),
            )
        )
        text += "\n[graph]\n" + graph_text.replace("[[roles]]", "[[graph.roles]]").replace(
            "[[relations]]", "[[graph.relations]]"
        )
    service.command.config_path.write_text(text)
    service = CollectionServiceConfig.model_validate(
        dict(
            service.model_dump(),
            recovery=dict(
                schema="ghimera.service-recovery/2",
                boundary="source_completion",
                on_restart="adopt_acknowledged",
                max_adoption_attempts=2,
            ),
        )
    )
    return service, cfg


def command_invoke(tmp_path, opts, boundary):
    path = tmp_path / (boundary + ".toml")
    path.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))
    entry = COMMAND_ENTRYPOINT.replace(
        "native_invoke = ModelInvocation.invoke",
        "boundary = sys.argv[1]\n" + SOURCE_DEATH + "\nnative_invoke = ModelInvocation.invoke",
    )
    return subprocess.run(
        [sys.executable, "-c", entry, boundary, "--job", str(path), "--max-job-bytes", "1000000"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def service_invoke(tmp_path, config, boundary):
    path = tmp_path / "service-config.json"
    path.write_text(config.model_dump_json())
    entry = SERVICE_ENTRYPOINT.replace(
        "native_invoke = ModelInvocation.invoke",
        SOURCE_DEATH + "\nnative_invoke = ModelInvocation.invoke",
    ).replace("{'ack', 'unknown'}", "{'source_ack', 'unknown'}")
    return subprocess.run(
        [sys.executable, "-c", entry, str(path), boundary],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def starting(tmp_path, cfg):
    opts = options(
        tmp_path,
        cfg,
        schema="ghimera.collector-command/2",
        execution=dict(schema="ghimera.command-execution/1", operation="run"),
    )
    base = f"http://fixture.example:{cfg.research.allowed_ports[0]}"
    opts.request_path.write_text(
        json.dumps(dict(intent="find ports", seeds=[base + "/plain", base + "/z-next"]))
    )
    return opts


def recovering(opts, pin):
    return CommandOptions.model_validate(
        dict(
            opts.model_dump(),
            schema="ghimera.collector-command/4",
            request_path=None,
            execution=dict(
                schema="ghimera.command-execution/3",
                operation="recover",
                snapshot_sha256=pin,
                recovery_boundary="source_completion",
            ),
        )
    )


def test_fresh_exact_command_adopts_atomic_source_without_repeated_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    _, cfg = settings(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    dead = command_invoke(tmp_path, opts, "source_ack")
    assert dead.returncode == 79, dead.stderr
    cut = SourceWorkStore.completion(cfg, opts.run_id)
    assert cut.snapshot.progress.harvest.receipt.fetches >= 1
    assert len(cut.snapshot.session.visited) == 1
    assert cut.snapshot.session.frontier
    reservation = (opts.output_directory / "reservation.json").read_bytes()
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = command_invoke(tmp_path, recovering(opts, cut.sha256), "recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert before[:2] == after[:2] and before[3] == after[3]
    # Only new post-collection assessment/answer/review, no repeated planner or source verdict.
    assert after[2] == before[2] + 3
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.harvest.documents == cut.snapshot.progress.harvest.documents
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
    assert (
        result.harvest.receipt.encoding_calls
        == cut.snapshot.progress.harvest.receipt.encoding_calls
    )
    assert len(result.rounds) == cfg.research.max_rounds == 1
    assert (
        result.harvest.receipt.elapsed_seconds
        >= cut.snapshot.progress.harvest.receipt.elapsed_seconds
    )
    assert (opts.output_directory / "reservation.json").read_bytes() == reservation


def test_fresh_service_adopts_source_with_native_graph_and_original_output(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    service, cfg = settings(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, graph=True
    )
    dead = service_invoke(tmp_path, service, "source_ack")
    assert dead.returncode == 79, dead.stderr
    job = json.loads(next(service.directory.glob("*.json")).read_bytes())
    run_id = job["run_id"]
    cut = SourceWorkStore.completion(cfg, run_id)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = service_invoke(tmp_path, service, "recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    job = json.loads(done.stdout)
    assert job["phase"] == "completed" and job["adoption_attempts"] == 1
    assert job["snapshot_sha256"] == cut.sha256 and job["recovery_boundary"] == "source_completion"
    after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert before[:2] == after[:2] and before[3] == after[3]
    assert after[2] == before[2] + 3
    result = ResearchResultArchive.read(
        service.directory / (run_id + ".output"), max_bytes=service.command.max_result_bytes
    )
    assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
    assert result.harvest.graph is not None
    assert read_journal(cfg.journal, run_id).state == "complete"


@pytest.mark.parametrize(
    "change", ["tamper", "later_intent", "source_writer", "output_writer", "wrong_pin"]
)
def test_source_adoption_holds_changed_unknown_or_owned_tail_before_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, change
):
    _, cfg = settings(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    dead = command_invoke(tmp_path, opts, "source_ack")
    assert dead.returncode == 79, dead.stderr
    cut = SourceWorkStore.completion(cfg, opts.run_id)
    owned = None
    if change == "tamper":
        db = sqlite3.connect(
            cfg.journal.directory / opts.run_id / "source-work" / "operations.sqlite"
        )
        db.execute("UPDATE source_completion SET payload=?", (b"{}",))
        db.commit()
        db.close()
    elif change == "later_intent":
        store = SourceWorkStore.resume(cfg, opts.run_id, len(cut.journal.rows))
        queued = read_source_work(cfg, opts.run_id).queued[0]
        store.begin(queued.request, len(cut.journal.rows))
        store.close()
    elif change == "source_writer":
        owned = SourceWorkStore.resume(cfg, opts.run_id, len(cut.journal.rows))
    elif change == "output_writer":
        owned = ResearchResultArchive.resume(
            opts.output_directory,
            reservation=ArchiveReservation.model_validate_json(
                (opts.output_directory / "reservation.json").read_bytes()
            ),
            max_bytes=opts.max_input_bytes,
        )
    pin = "0" * 64 if change == "wrong_pin" else cut.sha256
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    try:
        with pytest.raises((ValueError, OSError)):
            asyncio.run(execute(recovering(opts, pin), source_resolver=ResolverFixture()))
    finally:
        if owned is not None:
            owned.close()
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize("boundary", ["unknown", "ack"])
def test_source_control_never_adopts_a_later_unknown_or_acknowledged_model_tail(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, boundary
):
    _, cfg = settings(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    dead = command_invoke(tmp_path, opts, boundary)
    assert dead.returncode == (74 if boundary == "unknown" else 73), dead.stderr
    with sqlite3.connect(
        cfg.journal.directory / opts.run_id / "source-work" / "operations.sqlite"
    ) as db:
        pin = db.execute("SELECT sha256 FROM source_completion WHERE id=1").fetchone()[0]
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    with pytest.raises(ValueError):
        asyncio.run(execute(recovering(opts, pin), source_resolver=ResolverFixture()))
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


def offline_settings(tmp_path, *, pages=1, references=False):
    from tests.test_reference_expansion import references as reference_policy
    from tests.test_research_phase_recovery import configured

    raw = configured(tmp_path).model_dump()
    raw["research_recovery"] = dict(
        schema="ghimera.research-recovery/2",
        max_snapshot_bytes=4000000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
        source_completion=dict(
            schema="ghimera.source-completion-recovery/1",
            execution="serial",
            max_capsule_bytes=8000000,
        ),
    )
    raw["source_work"] = dict(
        schema="ghimera.source-work/1",
        max_operations=100,
        max_page_bytes=2000000,
        max_result_bytes=4000000,
        max_operation_bytes=8000000,
        max_store_bytes=32000000,
        database_timeout_seconds=2.0,
        frontier=dict(
            schema="ghimera.source-frontier/1",
            max_entries=100,
            max_entry_bytes=10000,
            max_frontier_bytes=1000000,
        ),
    )
    raw["research"]["max_pages_per_round"] = pages
    raw["research"]["max_rounds"] = 1
    if references:
        raw["references"] = reference_policy()
    return GhimeraConfig.model_validate(raw)


def offline_loop(cfg, *, reject=False, duplicate=False):
    from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.loop import GoalLoop
    from ghimera.models import Extracted, LinkCandidate
    from ghimera.research import ResearchLoop
    from tests.test_intent_research import (
        AnalystFixture,
        PlannerFixture,
        ReviewerFixture,
        SearchFixture,
    )
    from tests.test_reference_expansion import reference

    class Judge(FakeJudge):
        async def document(self, *args, **kwargs):
            result = await super().document(*args, **kwargs)
            return result.model_copy(update={"decision": "reject"}) if reject else result

    class Extractor(FakeExtractor):
        async def extract(self, page):
            text = "Ports source: https://example.org/queued"
            return Extracted(
                title="Port report",
                text=text,
                language="en",
                links=(LinkCandidate(url="https://example.org/queued", anchor=text),),
                references=(reference("https://example.org/queued", text),)
                if cfg.references
                else (),
            )

    class Planner(PlannerFixture):
        def __init__(self):
            self.requests = []

        async def plan(self, request):
            self.requests.append(request)
            return await super().plan(request)

    route, search, planner = FakeRoute(same_content=duplicate), SearchFixture(), Planner()
    loop = ResearchLoop(
        config=cfg,
        collector=GoalLoop(
            config=cfg,
            fetcher=FetchLadder((route,)),
            extractor=Extractor(),
            scorer=KeywordScorer(),
            judge=Judge(),
        ),
        search=search,
        planner=planner,
        analyst=AnalystFixture(),
        reviewer=ReviewerFixture(),
    )
    return loop, route, search, planner


class AcknowledgedCut(BaseException):
    pass


def offline_cut(cfg, monkeypatch, *, reject=False, duplicate=False, count=1):
    from ghimera.research_types import ResearchRequest

    original = SourceWorkStore.processed

    def cut(self, token, result, ledger_end, **kwargs):
        original(self, token, result, ledger_end, **kwargs)
        if kwargs.get("control") is not None and len(self.report().operations) >= count:
            raise AcknowledgedCut

    loop, *_ = offline_loop(cfg, reject=reject, duplicate=duplicate)
    with monkeypatch.context() as patch:
        patch.setattr(SourceWorkStore, "processed", cut)
        with pytest.raises(AcknowledgedCut):
            asyncio.run(
                loop.run(
                    ResearchRequest(
                        intent="find ports",
                        seeds=("https://example.org/a", "https://example.org/b"),
                    ),
                    run_id="cut",
                )
            )
    return SourceWorkStore.completion(cfg, "cut")


@pytest.mark.parametrize("reject", [False, True])
def test_completed_none_or_merged_duplicate_preserves_frontier_references_and_original_spend(
    tmp_path, monkeypatch, reject
):
    cfg = offline_settings(tmp_path, pages=1 if reject else 2, references=not reject)
    cut = offline_cut(cfg, monkeypatch, reject=reject, duplicate=True, count=1 if reject else 2)
    operations = read_source_work(cfg, "cut").operations
    if reject:
        assert operations[-1].result is None and not cut.snapshot.progress.harvest.documents
    else:
        assert len(cut.snapshot.progress.harvest.documents) == 1
        assert len(cut.snapshot.progress.harvest.documents[0].occurrences) == 1
        assert cut.snapshot.session.reference_hops and cut.snapshot.session.reference_origins
        assert any(row.reference is not None for row in cut.journal.rows)
    assert cut.snapshot.session.frontier
    assert bool(cut.snapshot.session.content_revisions) == (not reject)
    resumed, route, search, planner = offline_loop(cfg, reject=reject, duplicate=True)
    result = asyncio.run(
        resumed.recover("cut", snapshot_sha256=cut.sha256, boundary="source_completion")
    )
    assert not route.requests and not search.requests and not planner.requests
    assert result.harvest.documents == cut.snapshot.progress.harvest.documents
    assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    assert len(result.rounds) == cfg.research.max_rounds


def test_source_restart_accounts_original_downtime_without_new_contact(tmp_path, monkeypatch):
    cfg = offline_settings(tmp_path)
    cut = offline_cut(cfg, monkeypatch)
    monkeypatch.setattr(
        "ghimera.research.time.time", lambda: cut.snapshot.saved_at + cfg.wall_seconds + 1
    )
    resumed, route, search, planner = offline_loop(cfg)
    result = asyncio.run(
        resumed.recover("cut", snapshot_sha256=cut.sha256, boundary="source_completion")
    )
    assert result.stop_reason == "budget_exhausted"
    assert not route.requests and not search.requests and not planner.requests
    assert result.harvest.receipt.judge_calls == cut.snapshot.progress.harvest.receipt.judge_calls


def test_source_admission_rejects_changed_native_runtime(tmp_path, monkeypatch):
    cfg = offline_settings(tmp_path)
    cut = offline_cut(cfg, monkeypatch)
    altered = cut.snapshot.runtime.model_copy(update={"extractor_revision": "different@2"})
    with pytest.raises(ValueError, match="collaborators"):
        SourceWorkStore.completion(cfg, "cut", cut.sha256, expected_runtime=altered)


def test_capsule_bound_rolls_back_source_ack_atomically(tmp_path):
    from ghimera.research_types import ResearchRequest
    from ghimera.source_work import SourceWorkFailure

    raw = offline_settings(tmp_path).model_dump()
    raw["research_recovery"]["source_completion"]["max_capsule_bytes"] = 1
    cfg = GhimeraConfig.model_validate(raw)
    loop, *_ = offline_loop(cfg)
    with pytest.raises(SourceWorkFailure):
        asyncio.run(loop.run(ResearchRequest(intent="find ports"), run_id="bounded"))
    original = read_source_work(cfg, "bounded")
    assert original.operations[-1].state == "processing"
    assert original.operations[-1].ledger_end is None
    with pytest.raises(ValueError, match="no atomic"):
        SourceWorkStore.completion(cfg, "bounded")


def test_inert_policy_and_exact_source_command_examples_are_typed():
    from ghimera.collection_service import ServiceRecoveryPolicy
    from ghimera.research_recovery_config import ResearchRecoveryConfig

    example = tomllib.loads(Path("examples/source-completion-recovery.toml").read_text())
    policy = ResearchRecoveryConfig.model_validate(example["research_recovery"])
    assert policy.source_completion.execution == "serial"
    assert ServiceRecoveryPolicy.model_validate(example["service_recovery"]).on_restart == "hold"
    options = CommandOptions.model_validate(
        tomllib.loads(Path("examples/collector-source-recover.toml").read_text())
    )
    assert options.execution.recovery_boundary == "source_completion"


def test_source_policy_is_explicit_inert_and_rejects_opted_in_overlap(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    _, cfg = settings(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    raw = cfg.model_dump()
    raw["research_recovery"]["schema"] = "ghimera.research-recovery/1"
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)
    from tests.test_execution import policy as execution_policy

    concurrent = dict(cfg.model_dump(), execution=execution_policy().model_dump())
    with pytest.raises(ValidationError, match="serial"):
        GhimeraConfig.model_validate(concurrent)
    # Removing ONLY the opt-in source policy must preserve ordinary concurrency.
    concurrent["research_recovery"].pop("source_completion")
    concurrent["research_recovery"]["schema"] = "ghimera.research-recovery/1"
    assert GhimeraConfig.model_validate(concurrent).execution == execution_policy()
