"""Persistent publisher health, real adaptive parsing, and failure isolation."""

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from test_html_extraction import ARTICLE, extractor, page, policy

from chimera.extraction_config import LocatorProfile
from chimera.extraction_types import LocatorEvent
from chimera.locator_health import LocatorHealthStore
from chimera.locator_types import LocatorDriftPolicy
from chimera.refusals import ChimeraRefused, RefusalCode


def drift(**updates):
    return dict(
        schema="chimera.locator-drift-policy/1",
        consecutive_miss_limit=3,
        state_timeout_seconds=5.0,
        **updates,
    )


def profile(**updates):
    return LocatorProfile.model_validate(
        dict(host="example.org", profile_id="news", body="#story", **updates)
    )


def observed(status="missing"):
    return (LocatorEvent(field="body", status=status, selector="#story"),)


def test_consecutive_misses_survive_restart_and_latch_until_exact_reset(tmp_path):
    rules = LocatorDriftPolicy.model_validate(drift())
    store = LocatorHealthStore(tmp_path, rules)
    assert store.read(profile()).finding == "ok"
    assert not (tmp_path / "locator-health-v1.sqlite").exists()
    for count in (1, 2, 3):
        health = LocatorHealthStore(tmp_path, rules).observe(profile(), "a" * 64, observed())
        assert health.consecutive_misses == count
        assert health.generic_only == (count == 3)
    assert store.observe(profile(), "b" * 64, observed("direct")) == health
    assert store.observe(profile(), "b" * 64, ()).finding == "locator_drift"
    assert store.reset(profile()).completed_observations == 0
    assert store.read(profile()).finding == "ok"
    assert store.observe(profile(), "b" * 64, observed("direct")).consecutive_misses == 0


def test_relocation_resets_streak_but_errors_without_misses_do_not(tmp_path):
    store = LocatorHealthStore(tmp_path, LocatorDriftPolicy.model_validate(drift()))
    store.observe(profile(), "a" * 64, observed())
    store.observe(profile(), "a" * 64, observed())
    assert (
        store.observe(profile(), "b" * 64, observed("direct"), completed=False).consecutive_misses
        == 2
    )
    assert store.observe(profile(), "b" * 64, observed("relocated")).consecutive_misses == 0
    assert store.observe(profile(), "c" * 64, observed(), completed=False).consecutive_misses == 1


def test_host_profile_and_policy_changes_do_not_inherit_stale_drift(tmp_path):
    store = LocatorHealthStore(tmp_path, LocatorDriftPolicy.model_validate(drift()))
    for _ in range(3):
        store.observe(profile(), "a" * 64, observed())
    changed = LocatorProfile(host="example.org", profile_id="news", body="#renamed")
    foreign = LocatorProfile(host="other.example", profile_id="news", body="#story")
    assert store.read(changed).finding == store.read(foreign).finding == "ok"
    other_policy = LocatorDriftPolicy(
        schema="chimera.locator-drift-policy/1", consecutive_miss_limit=4, state_timeout_seconds=5.0
    )
    assert LocatorHealthStore(tmp_path, other_policy).read(profile()).finding == "ok"


def test_simultaneous_completions_are_not_lost(tmp_path):
    rules = LocatorDriftPolicy(
        schema="chimera.locator-drift-policy/1",
        consecutive_miss_limit=100,
        state_timeout_seconds=5.0,
    )

    def observe(index):
        return LocatorHealthStore(tmp_path, rules).observe(
            profile(), hashlib.sha256(str(index).encode()).hexdigest(), observed()
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        completed = list(executor.map(observe, range(12)))
    assert len(completed) == 12
    final = LocatorHealthStore(tmp_path, rules).read(profile())
    assert final.completed_observations == final.consecutive_misses == 12


def test_invalid_state_or_foreign_locator_observations_fail_closed(tmp_path):
    store = LocatorHealthStore(tmp_path, LocatorDriftPolicy.model_validate(drift()))
    with pytest.raises(ChimeraRefused):
        store.observe(
            profile(), "a" * 64, (LocatorEvent(field="title", selector="h1", status="missing"),)
        )
    store.observe(profile(), "a" * 64, observed())
    path = tmp_path / "locator-health-v1.sqlite"
    path.chmod(0o644)
    with pytest.raises(ChimeraRefused) as exc:
        store.read(profile())
    assert exc.value.code == RefusalCode.ADAPTER_CONTRACT
    path.chmod(0o600)
    with path.open("wb") as stream:
        stream.write(b"not a database")
    with pytest.raises(ChimeraRefused):
        store.read(profile())


def test_real_parser_latches_generic_mode_but_keeps_native_text_and_provenance(tmp_path):
    settings = dict(
        profiles=[dict(host="example.org", profile_id="missing", body="#absent")],
        locator_drift=drift(),
    )
    client = extractor(tmp_path, **settings)

    async def exercise():
        results = [await client.extract(page()) for _ in range(3)]
        # A fresh controller/process sees the latched publisher state.
        fourth = await extractor(tmp_path, **settings).extract(page(ARTICLE))
        return results, fourth

    results, fourth = asyncio.run(exercise())
    assert [r.extraction.locator_health.consecutive_misses for r in results] == [1, 2, 3]
    assert fourth.text == results[0].text
    assert fourth.extraction.selection == "generic"
    assert fourth.extraction.profile_id == "missing"
    assert fourth.extraction.locators == ()
    assert fourth.extraction.locator_health.finding == "locator_drift"
    assert fourth.extraction.locator_health.completed_observations == 3


def test_real_failed_extractions_still_record_css_misses(tmp_path):
    client = extractor(
        tmp_path,
        profiles=[dict(host="example.org", profile_id="bad", body="#absent")],
        locator_drift=drift(),
    )

    async def exercise():
        for _ in range(3):
            with pytest.raises(ChimeraRefused):
                await client.extract(page("<html><body><main></main></body></html>"))

    asyncio.run(exercise())
    store = LocatorHealthStore(client.config.locator_directory, client.config.locator_drift)
    assert store.read(client.config.profiles[0]).finding == "locator_drift"


def test_no_policy_keeps_existing_config_identity_and_invalid_policy_refuses(tmp_path):
    old = policy(tmp_path, locator_drift=None)
    assert "locator_drift" not in old.model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        LocatorDriftPolicy(
            schema="chimera.locator-drift-policy/1",
            consecutive_miss_limit=0,
            state_timeout_seconds=5.0,
        )


def test_drift_is_a_harvest_ledger_finding_and_rejects_foreign_health(tmp_path):
    from pathlib import Path

    from chimera.config import ChimeraConfig
    from chimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from chimera.fetch import FetchLadder
    from chimera.loop import GoalLoop
    from chimera.models import Goal, Harvest, Scope

    class HtmlRoute(FakeRoute):
        async def attempt(self, request):
            return page()

    client = extractor(
        tmp_path,
        profiles=[dict(host="example.org", profile_id="missing", body="#absent")],
        locator_drift=drift(),
    )
    data = ChimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    data.update(page_budget=1, extraction=client.config.model_dump(by_alias=True))

    async def exercise():
        await client.extract(page())
        await client.extract(page())
        return await GoalLoop(
            config=ChimeraConfig.model_validate(data),
            fetcher=FetchLadder((HtmlRoute(),)),
            extractor=client,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(page().url,)),
            Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
        )

    harvest = asyncio.run(exercise())
    finding = next(r for r in harvest.ledger if r.reason.startswith("locator_drift:"))
    assert finding.event == "policy"
    assert finding.extraction.locator_health.consecutive_misses == 3
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    tampered = harvest.model_dump(mode="json", by_alias=True)
    tampered["documents"][0]["extracted"]["extraction"]["locator_health"]["host"] = "other.example"
    with pytest.raises(ValidationError):
        Harvest.model_validate(tampered)
