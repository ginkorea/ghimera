"""One real generic reparse, retained-byte identity, terminal failure and replay."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_html_extraction import ARTICLE, extractor, page

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.extraction import ExtractionRequest
from ghimera.extraction_attempts import ExtractionCancelled, ExtractionFailure
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Harvest, Scope
from ghimera.refusals import GhimeraRefused, RefusalCode

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
    # No hidden retry: one rich attempt, then the page is kept as plain text.
    parsed = asyncio.run(parser.extract(page(REDESIGNED)))
    evidence = parsed.extraction
    assert evidence is not None and evidence.tier == "fallback_plain"
    assert len(calls) == len(evidence.fallback_attempts) == 1


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
    # The deadline is never retried in the rich tier; the page is kept as plain text.
    evidence = asyncio.run(parser.extract(page())).extraction
    assert evidence is not None and evidence.tier == "fallback_plain"
    assert evidence.fallback_refusal == RefusalCode.BOUND_EXCEEDED
    assert "not a spend limit" in (evidence.fallback_detail or "")
    assert len(calls) == 1
    assert evidence.fallback_attempts[-1].refusal == RefusalCode.BOUND_EXCEEDED
    parser = client(tmp_path / "another")

    async def bad_state(raw):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    monkeypatch.setattr(parser._worker, "run", bad_state)
    evidence = asyncio.run(parser.extract(page())).extraction
    assert evidence is not None and evidence.fallback_refusal == RefusalCode.ADAPTER_CONTRACT
    assert len(evidence.fallback_attempts) == 1


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
    settings = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    settings.update(extraction=parser.config.model_dump(by_alias=True))
    harvest = asyncio.run(
        GoalLoop(
            config=GhimeraConfig.model_validate(settings),
            fetcher=FetchLadder((HtmlRoute(),)),
            extractor=parser,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(page().url,)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
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
    settings = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    settings.update(extraction=parser.config.model_dump(by_alias=True))
    harvest = asyncio.run(
        GoalLoop(
            config=GhimeraConfig.model_validate(settings),
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


def test_reader_deadline_keeps_the_page_as_plain_text_and_the_run_ends_normally(
    tmp_path, monkeypatch
):
    class HtmlRoute(FakeRoute):
        async def attempt(self, request):
            return page()

    parser = client(tmp_path, profiles=[], locator_drift=None, timeout_seconds=0.03)

    async def slow(raw):
        await asyncio.sleep(1)
        return b"not reached"

    monkeypatch.setattr(parser._worker, "run", slow)
    settings = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    settings.update(extraction=parser.config.model_dump(by_alias=True))
    harvest = asyncio.run(
        GoalLoop(
            config=GhimeraConfig.model_validate(settings),
            fetcher=FetchLadder((HtmlRoute(),)),
            extractor=parser,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(page().url,)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    # A reader past its own deadline never ends the run and never drops the page:
    # the page is kept as plain text naming the refused rich tier.
    assert harvest.receipt.stop_reason == "frontier_empty"
    assert len(harvest.documents) == 1
    evidence = harvest.documents[0].extracted.extraction
    assert evidence is not None and evidence.tier == "fallback_plain"
    assert evidence.fallback_refusal == RefusalCode.BOUND_EXCEEDED
    assert any(r.reason == "extraction_fallback_plain" for r in harvest.ledger)


def test_a_page_over_the_input_bound_is_cut_before_the_reader_sees_it(tmp_path, monkeypatch):
    bound = 64
    parser = client(tmp_path, max_input_bytes=bound)
    seen = []

    async def record(raw):
        seen.append(ExtractionRequest.model_validate_json(raw).page.body)
        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED, "fixture reader refuses")

    monkeypatch.setattr(parser._worker, "run", record)
    source = page()
    assert len(source.body) > bound
    evidence = asyncio.run(parser.extract(source)).extraction
    assert seen and all(body == source.body[:bound] for body in seen)
    # The kept reading names the whole page and the bytes it did not read.
    assert evidence is not None and evidence.omitted_bytes == len(source.body) - bound
    assert evidence.source_sha256 == hashlib.sha256(source.body).hexdigest()
