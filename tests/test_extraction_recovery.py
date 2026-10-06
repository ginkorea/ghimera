"""One real generic reparse, retained-byte identity, terminal failure and replay."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_html_extraction import ARTICLE, extractor, page

from chimera.config import ChimeraConfig
from chimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from chimera.extraction import ExtractionRequest
from chimera.extraction_attempts import ExtractionCancelled, ExtractionFailure
from chimera.fetch import FetchLadder
from chimera.loop import GoalLoop
from chimera.models import Goal, Harvest, Scope
from chimera.refusals import ChimeraRefused, RefusalCode

REDESIGNED = ARTICLE.replace('<article id="story">', '<div id="teaser"></div><article id="story">')


def client(tmp_path, **updates):
    settings = dict(profiles=[dict(host="example.org", profile_id="old", body="#teaser")])
    settings.update(updates)
    return extractor(tmp_path, **settings)


def test_real_generic_retry_rescues_empty_profile_without_refetching(tmp_path, monkeypatch):
    parser = client(tmp_path)
    run = parser._worker.run
    requests = []

    async def record(raw):
        requests.append(ExtractionRequest.model_validate_json(raw))
        return await run(raw)

    monkeypatch.setattr(parser._worker, "run", record)
    parsed = asyncio.run(parser.extract(page(REDESIGNED)))
    assert "infrastructure investment" in parsed.text
    assert [r.generic_only for r in requests] == [False, True]
    assert requests[0].page == requests[1].page == page(REDESIGNED)
    assert len(parsed.extraction.attempts) == 2
    first, second = parsed.extraction.attempts
    assert first.outcome == "refused" and first.refusal == RefusalCode.EXTRACTION_FAILED
    assert second.phase == "generic_retry" and second.outcome == "success"
    assert (
        first.source_sha256
        == second.source_sha256
        == hashlib.sha256(page(REDESIGNED).body).hexdigest()
    )
    assert parsed.extraction.selection == "generic"
    assert first.locators[0].field == "body" and first.locators[0].selector == "#teaser"
    assert second.locators == ()


def test_invalid_worker_wire_gets_one_generic_retry_and_no_vendor_diagnostics(
    tmp_path, monkeypatch
):
    parser = client(tmp_path)
    run = parser._worker.run
    responses = []

    async def corrupt_once(raw):
        if not responses:
            result = b"untrusted parser error text: not JSON"
        else:
            result = await run(raw)
        responses.append(result)
        return result

    monkeypatch.setattr(parser._worker, "run", corrupt_once)
    parsed = asyncio.run(parser.extract(page(ARTICLE)))
    assert parsed.extraction.attempts[0].outcome == "invalid_response"
    assert parsed.extraction.attempts[0].response_sha256 == hashlib.sha256(responses[0]).hexdigest()
    assert "untrusted parser error" not in parsed.model_dump_json()
    assert len(responses) == 2


def test_disabled_recovery_is_not_a_hidden_retry(tmp_path, monkeypatch):
    parser = client(
        tmp_path, recovery=dict(schema="chimera.extraction-recovery/1", mode="disabled")
    )
    run = parser._worker.run
    calls = []

    async def record(raw):
        calls.append(raw)
        return await run(raw)

    monkeypatch.setattr(parser._worker, "run", record)
    with pytest.raises(ExtractionFailure) as caught:
        asyncio.run(parser.extract(page(REDESIGNED)))
    assert len(calls) == len(caught.value.attempts) == 1


def test_generic_failure_is_terminal_after_two_attempts(tmp_path, monkeypatch):
    parser = client(tmp_path)
    run = parser._worker.run
    calls = []

    async def record(raw):
        calls.append(raw)
        return await run(raw)

    monkeypatch.setattr(parser._worker, "run", record)
    with pytest.raises(ExtractionFailure) as caught:
        asyncio.run(parser.extract(page("<html><body><main></main></body></html>")))
    assert caught.value.code == RefusalCode.EXTRACTION_FAILED
    assert len(calls) == len(caught.value.attempts) == 2
    assert all(a.outcome == "refused" for a in caught.value.attempts)


def test_deadline_and_state_contract_failure_do_not_retry(tmp_path, monkeypatch):
    parser = client(tmp_path, timeout_seconds=0.02)
    calls = []

    async def slow(raw):
        calls.append(raw)
        await asyncio.sleep(1)
        return b"not reached"

    monkeypatch.setattr(parser._worker, "run", slow)
    with pytest.raises(ExtractionFailure) as caught:
        asyncio.run(parser.extract(page()))
    assert caught.value.code == RefusalCode.BUDGET_EXHAUSTED
    assert len(calls) == 1
    assert caught.value.attempts[-1].refusal == RefusalCode.BUDGET_EXHAUSTED
    parser = client(tmp_path / "another")

    async def bad_state(raw):
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    monkeypatch.setattr(parser._worker, "run", bad_state)
    with pytest.raises(ExtractionFailure) as caught:
        asyncio.run(parser.extract(page()))
    assert len(caught.value.attempts) == 1


def test_external_cancellation_preserves_its_attempt_and_does_not_retry(tmp_path, monkeypatch):
    parser = client(tmp_path)
    started = asyncio.Event()
    calls = []

    async def slow(raw):
        calls.append(raw)
        started.set()
        await asyncio.sleep(1)
        return b"not reached"

    monkeypatch.setattr(parser._worker, "run", slow)

    async def exercise():
        task = asyncio.create_task(parser.extract(page()))
        await started.wait()
        task.cancel()
        with pytest.raises(ExtractionCancelled) as caught:
            await task
        assert caught.value.attempts[0].outcome == "cancelled"

    asyncio.run(exercise())
    assert len(calls) == 1


def test_collection_records_failed_and_successful_parsing_and_replay_refuses_omissions(tmp_path):
    class HtmlRoute(FakeRoute):
        async def attempt(self, request):
            return page(REDESIGNED)

    parser = client(tmp_path)
    settings = ChimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    settings.update(page_budget=1, extraction=parser.config.model_dump(by_alias=True))
    harvest = asyncio.run(
        GoalLoop(
            config=ChimeraConfig.model_validate(settings),
            fetcher=FetchLadder((HtmlRoute(),)),
            extractor=parser,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(page().url,)),
            Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
        )
    )
    observations = [r for r in harvest.ledger if r.event == "extraction_attempt"]
    assert len(observations) == 2
    assert [r.extraction_attempt.outcome for r in observations] == ["refused", "success"]
    assert harvest.receipt.fetches == 1
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    tampered = harvest.model_dump(mode="json", by_alias=True)
    del tampered["ledger"][observations[0].sequence]
    for index, row in enumerate(tampered["ledger"]):
        row["sequence"] = index
    with pytest.raises(ValidationError):
        Harvest.model_validate(tampered)
    orphaned = harvest.model_dump(mode="json", by_alias=True)
    orphaned["ledger"] = [r for r in orphaned["ledger"] if r["extraction"] is None]
    for index, row in enumerate(orphaned["ledger"]):
        row["sequence"] = index
    with pytest.raises(ValidationError, match="successful parsing must retain"):
        Harvest.model_validate(orphaned)


def test_terminal_failure_is_a_collection_ledger_chain_not_an_unhandled_error(tmp_path):
    class HtmlRoute(FakeRoute):
        async def attempt(self, request):
            return page("<html><body><main></main></body></html>")

    parser = client(tmp_path)
    settings = ChimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    settings.update(page_budget=1, extraction=parser.config.model_dump(by_alias=True))
    harvest = asyncio.run(
        GoalLoop(
            config=ChimeraConfig.model_validate(settings),
            fetcher=FetchLadder((HtmlRoute(),)),
            extractor=parser,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(page().url,)),
            Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
        )
    )
    observations = [r.extraction_attempt for r in harvest.ledger if r.event == "extraction_attempt"]
    assert len(observations) == 2
    assert all(a.outcome == "refused" for a in observations)
    assert harvest.documents == ()
    assert any(
        r.refusal == RefusalCode.EXTRACTION_FAILED and r.event == "refusal" for r in harvest.ledger
    )
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest


def test_collection_budget_timeout_keeps_asyncio_semantics_and_attempt_evidence(
    tmp_path, monkeypatch
):
    class HtmlRoute(FakeRoute):
        async def attempt(self, request):
            return page()

    parser = client(tmp_path, profiles=[], locator_drift=None)

    async def slow(raw):
        await asyncio.sleep(1)
        return b"not reached"

    monkeypatch.setattr(parser._worker, "run", slow)
    settings = ChimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    settings.update(
        page_budget=1, wall_seconds=0.03, extraction=parser.config.model_dump(by_alias=True)
    )
    harvest = asyncio.run(
        GoalLoop(
            config=ChimeraConfig.model_validate(settings),
            fetcher=FetchLadder((HtmlRoute(),)),
            extractor=parser,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(page().url,)),
            Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
        )
    )
    assert harvest.receipt.stop_reason == "budget_exhausted"
    observations = [r.extraction_attempt for r in harvest.ledger if r.event == "extraction_attempt"]
    assert len(observations) == 1 and observations[0].outcome == "cancelled"


def test_input_limits_do_not_launch_a_retry_worker(tmp_path, monkeypatch):
    parser = client(tmp_path, max_input_bytes=1)

    async def forbidden(raw):
        pytest.fail("input refusal must occur before any parser attempt")

    monkeypatch.setattr(parser._worker, "run", forbidden)
    with pytest.raises(ChimeraRefused) as caught:
        asyncio.run(parser.extract(page()))
    assert caught.value.code == RefusalCode.EXTRACTION_FAILED
