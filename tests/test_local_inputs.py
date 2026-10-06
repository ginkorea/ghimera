"""Owned local seeds enter real parsing and research, not a fictitious HTTP route."""

import asyncio
import hashlib
import threading
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.documents import DOCX_TYPE, DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph import DirectoryGraphSink
from ghimera.journal import read_journal
from ghimera.local_input_types import LocalDocumentSeed, LocalInputConfig
from ghimera.local_inputs import LocalInputLoader
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Harvest
from ghimera.refusals import GhimeraRefused
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, ResearchResult
from tests.test_document_extraction import config as document_config
from tests.test_document_extraction import docx, native_pdf
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
)
from tests.test_intent_research import (
    policy as research_policy,
)


def recipe(tmp_path, **updates):
    data = dict(
        schema="ghimera.local-inputs/1",
        allowed_roots=[str(tmp_path)],
        max_files_per_run=2,
        max_input_bytes=2_000_000,
        max_total_bytes=4_000_000,
    )
    data.update(updates)
    return LocalInputConfig.model_validate(data)


def seed(path, raw, **updates):
    data = dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), content_type=DOCX_TYPE)
    data.update(updates)
    return LocalDocumentSeed.model_validate(data)


def configured(tmp_path):
    data = document_config(tmp_path).model_dump()
    data.update(local_inputs=recipe(tmp_path), research=research_policy())
    return GhimeraConfig.model_validate(data)


def test_owned_file_snapshot_is_bounded_pinned_and_has_no_private_path_in_evidence(tmp_path):
    raw = docx()
    path = tmp_path / "private-owner-report.docx"
    path.write_bytes(raw)
    item = seed(path, raw)
    snapshot = LocalInputLoader(recipe(tmp_path)).read(item, max_bytes=len(raw))
    assert snapshot.raw == raw
    assert snapshot.evidence.source_id == "urn:ghimera:local:" + item.sha256
    assert snapshot.evidence.size_bytes == len(raw)
    assert str(path) not in snapshot.evidence.model_dump_json()
    assert "private-owner-report" not in snapshot.evidence.model_dump_json()
    with pytest.raises(GhimeraRefused):
        LocalInputLoader(recipe(tmp_path)).read(item, max_bytes=len(raw) - 1)
    with pytest.raises(GhimeraRefused):
        LocalInputLoader(recipe(tmp_path)).read(
            item.model_copy(update={"sha256": "0" * 64}), max_bytes=len(raw)
        )


def test_file_leaf_and_directory_symlinks_never_expand_the_approved_root(tmp_path):
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    approved.mkdir()
    outside.mkdir()
    raw = docx()
    target = outside / "report.docx"
    target.write_bytes(raw)
    (approved / "alias.docx").symlink_to(target)
    (approved / "nested").symlink_to(outside, target_is_directory=True)
    loader = LocalInputLoader(recipe(tmp_path, allowed_roots=[str(approved)]))
    for path in (target, approved / "alias.docx", approved / "nested" / "report.docx"):
        with pytest.raises(GhimeraRefused):
            loader.read(seed(path, raw), max_bytes=len(raw))
    with pytest.raises(ValidationError):
        recipe(tmp_path, allowed_roots=["/"])


@pytest.mark.parametrize("mime", (DOCX_TYPE, "application/pdf"))
def test_real_local_document_parse_keeps_native_evidence_without_network_fetch(tmp_path, mime):
    raw = docx() if mime == DOCX_TYPE else native_pdf()
    path = tmp_path / "report.document"
    path.write_bytes(raw)
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    data = configured(tmp_path).model_dump()
    data.update(
        graph=graph,
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=1_000_000,
            max_journal_bytes=10_000_000,
            max_summary_bytes=1_000_000,
            max_records=1000,
        ),
    )
    cfg = GhimeraConfig.model_validate(data)
    route = FakeRoute()
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
    )

    async def run():
        session = await loop.open(Goal(text="find ports"), run_id="local-seeded")
        await loop.import_local(session, (seed(path, raw, content_type=mime),))
        return loop.finish(session, "frontier_empty")

    result = asyncio.run(run())
    assert not route.requests and result.receipt.fetches == 0
    assert result.receipt.bytes_read == len(raw)
    assert len(result.documents) == 1 and result.documents[0].raw == raw
    document = result.documents[0]
    assert "terminal construction" in document.extracted.text
    assert document.local_input.source_id == document.url
    assert document.extracted.document_parse.source_url == document.url
    assert document.transport is None
    assert sum(row.event == "local_input" for row in result.ledger) == 1
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    nodes = tuple(node for node in result.graph.nodes if node.local_input is not None)
    assert len(nodes) == 1 and nodes[0].local_input == document.local_input
    assert asyncio.run(DirectoryGraphSink(cfg.graph, "local-seeded").replay())
    assert read_journal(cfg.journal, "local-seeded").rows == result.ledger
    broken = result.model_dump()
    broken["documents"][0]["local_input"]["size_bytes"] += 1
    with pytest.raises(ValidationError):
        Harvest.model_validate(broken)


def test_first_plan_receives_imported_evidence_and_can_answer_without_a_web_fetch(tmp_path):
    raw = docx()
    path = tmp_path / "report.docx"
    path.write_bytes(raw)
    cfg = configured(tmp_path)
    route, search = FakeRoute(), SearchFixture()
    collection = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
    )

    class SeedPlanner(PlannerFixture):
        async def plan(self, request):
            assert len(request.documents) == 1
            assert "Taiwan" in request.documents[0].extracted.text
            return (await super().plan(request)).model_copy(update={"queries": ()})

    loop = ResearchLoop(
        config=cfg,
        collector=collection,
        search=search,
        planner=SeedPlanner(),
        analyst=AnalystFixture(),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(
        loop.run(
            ResearchRequest(
                intent="find ports",
                local_documents=(seed(path, raw),),
            )
        )
    )
    assert result.status == "answered" and not route.requests and not search.requests
    assert result.answer.claims[0].citations[0].matches(result.harvest.documents[0])
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_invalid_local_seed_prevents_model_and_discovery_spend(tmp_path):
    cfg = configured(tmp_path)
    collection = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
    )

    class NeverPlanner(PlannerFixture):
        async def plan(self, request):
            raise AssertionError("an invalid local seed must refuse before model work")

    search = SearchFixture()
    loop = ResearchLoop(
        config=cfg,
        collector=collection,
        search=search,
        planner=NeverPlanner(),
        analyst=AnalystFixture(),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(
        loop.run(
            ResearchRequest(
                intent="find ports",
                local_documents=(seed(tmp_path / "absent.docx", b"absent"),),
            )
        )
    )
    assert result.status == "failed" and not search.requests
    assert result.harvest.receipt.judge_calls == 0
    assert result.harvest.receipt.bytes_read == 0


def test_cancelling_an_input_read_drains_its_own_task_and_accounts_bytes(tmp_path, monkeypatch):
    raw = docx()
    path = tmp_path / "report.docx"
    path.write_bytes(raw)
    cfg = configured(tmp_path)
    collection = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(satisfied=True),
    )
    started, release = threading.Event(), threading.Event()
    original = LocalInputLoader.read

    def delayed(self, item, *, max_bytes):
        started.set()
        assert release.wait(timeout=2)
        return original(self, item, max_bytes=max_bytes)

    monkeypatch.setattr(LocalInputLoader, "read", delayed)

    async def run():
        session = await collection.open(Goal(text="find ports"))
        task = asyncio.create_task(collection.import_local(session, (seed(path, raw),)))
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.budget._bytes_reserved == 0
        return collection.finish(session, "failed")

    result = asyncio.run(run())
    assert result.receipt.bytes_read == len(raw)
    assert result.receipt.fetches == result.receipt.judge_calls == 0
    assert result.ledger[0].event == "local_input" and result.ledger[0].refusal is not None


def test_reused_session_enforces_local_file_and_byte_quota_before_read(tmp_path):
    from ghimera.budget import RunBudget
    from ghimera.refusals import RefusalCode

    raw = configured(tmp_path).model_dump()
    raw["local_inputs"] = recipe(tmp_path, max_files_per_run=1)
    budget = RunBudget(GhimeraConfig.model_validate(raw), lambda: 0.0)
    allowance = budget.reserve_local_input()
    budget.release_bytes(allowance)
    with pytest.raises(GhimeraRefused) as exc:
        budget.reserve_local_input()
    assert exc.value.code == RefusalCode.BUDGET_EXHAUSTED
    budget.local_inputs = 0
    budget.local_input_bytes = budget.config.local_inputs.max_total_bytes
    with pytest.raises(GhimeraRefused) as exc:
        budget.reserve_local_input()
    assert exc.value.code == RefusalCode.BUDGET_EXHAUSTED
