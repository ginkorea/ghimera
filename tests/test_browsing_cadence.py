"""Request pacing protects origin floors without blocking other eligible hosts."""

import asyncio
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from protego import Protego
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.models import Page
from ghimera.politeness import Politeness, RobotsEntry, retry_after_seconds


def config(**cadence_updates):
    raw = GhimeraConfig.from_toml(Path("examples/collector.toml")).model_dump(by_alias=True)
    raw.update(per_host_delay_seconds=0.05, global_requests_per_second=10000.0)
    raw["cadence"] = {
        "schema": "ghimera.cadence/1",
        "jitter_seconds": 0.03,
        "throttle_statuses": [429, 503],
        "throttle_base_seconds": 2.0,
        "throttle_multiplier": 2.0,
        "max_backoff_seconds": 5.0,
        "respect_retry_after": True,
        **cadence_updates,
    }
    return GhimeraConfig.model_validate(raw)


class Clock:
    def __init__(self):
        self.now = 100.0
        self.delays = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay
        await asyncio.sleep(0)


def page(status=200, retry_after=None):
    return Page(
        url="https://example.org/report",
        final_url="https://example.org/report",
        status=status,
        content_type="text/html",
        body=b"report",
        headers=(("retry-after", retry_after),) if retry_after is not None else (),
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("-2", None),
        ("1.5", None),
        ("later", None),
        ("0", 0.0),
        (" 12 ", 12.0),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 5.0),
    ],
)
def test_retry_after_accepts_only_http_delta_seconds_or_aware_dates(value, expected):
    now = datetime(2015, 10, 21, 7, 27, 55, tzinfo=UTC).timestamp()
    assert retry_after_seconds(value, now) == expected
    assert math.isinf(retry_after_seconds("9" * 500, now))


def test_cadence_is_explicit_validated_and_absent_from_legacy_recipes():
    legacy = GhimeraConfig.from_toml(Path("examples/collector.toml"))
    assert "cadence" not in legacy.model_dump(by_alias=True)
    assert GhimeraConfig.model_validate(legacy.model_dump()) == legacy
    for bad in (
        {"jitter_seconds": -1},
        {"throttle_statuses": [429, 429]},
        {"throttle_multiplier": 0.5},
        {"max_backoff_seconds": 1.0},
    ):
        with pytest.raises(ValidationError):
            config(**bad)
    raw = config().model_dump(by_alias=True)
    raw["http"] = None
    with pytest.raises(ValidationError, match="shared HTTP scheduler"):
        GhimeraConfig.model_validate(raw)


def test_jitter_is_nonnegative_above_the_robots_floor():
    clock = Clock()
    scheduler = Politeness(config(), clock=clock, sleep=clock.sleep, uniform=lambda a, b: b)
    scheduler._robots["https://example.org"] = RobotsEntry(
        Protego.parse("User-agent: Chimera\nCrawl-delay: 1\n"), 1000.0
    )

    async def run():
        starts = []
        for _ in range(3):
            async with scheduler.slot("https://example.org/report"):
                starts.append(clock.now)
        return starts

    starts = asyncio.run(run())
    assert starts == pytest.approx([100.0, 101.03, 102.06])


def test_global_rate_remains_a_floor_when_jitter_is_enabled():
    raw = config().model_dump(by_alias=True)
    raw["global_requests_per_second"] = 2.0
    clock = Clock()
    scheduler = Politeness(
        GhimeraConfig.model_validate(raw), clock=clock, sleep=clock.sleep, uniform=lambda a, b: 0.0
    )

    async def run():
        starts = []
        for host in ("a.example", "b.example", "c.example"):
            async with scheduler.slot("https://" + host + "/report"):
                starts.append(clock.now)
        return starts

    assert asyncio.run(run()) == pytest.approx([100.0, 100.5, 101.0])


def test_throttle_backoff_grows_but_never_shortens_server_retry_after():
    clock = Clock()
    scheduler = Politeness(
        config(), clock=clock, wall_clock=lambda: 0.0, sleep=clock.sleep, uniform=lambda a, b: 0.0
    )
    state = scheduler._host("https://example.org")
    scheduler.observe_response(page(429))
    assert state.cooldown_until == 102.0
    scheduler.observe_response(page(503))
    assert state.cooldown_until == 104.0
    scheduler.observe_response(page(429, "12"))
    assert state.backoff_seconds == 5.0
    assert state.cooldown_until == 112.0  # Server wait exceeds our policy cap.
    scheduler.observe_response(page())  # An old parallel success cannot erase it.
    assert state.cooldown_until == 112.0
    assert state.backoff_seconds == 5.0
    clock.now = 113.0
    scheduler.observe_response(page())
    assert state.backoff_seconds == 0.0


def test_throttled_origin_does_not_reserve_global_capacity_and_cancel_leaves_it_free():
    async def run():
        clock = Clock()
        entered_wait = asyncio.Event()
        blocked = asyncio.Event()

        async def sleep(delay):
            if delay >= 1.0:
                entered_wait.set()
                await blocked.wait()
            clock.now += delay
            await asyncio.sleep(0)

        raw = config().model_dump(by_alias=True)
        raw.update(global_concurrency=1, per_host_concurrency=1)
        scheduler = Politeness(
            GhimeraConfig.model_validate(raw), clock=clock, sleep=sleep, uniform=lambda a, b: 0.0
        )
        scheduler.observe_response(page(429))

        async def slow():
            async with scheduler.slot("https://example.org/next"):
                pytest.fail("cooled host cannot start before its wait")

        task = asyncio.create_task(slow())
        await entered_wait.wait()
        async with asyncio.timeout(1.0):
            async with scheduler.slot("https://other.example/report"):
                pass
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(1.0):
            async with scheduler.slot("https://third.example/report"):
                pass

    asyncio.run(run())


def test_invalid_injected_jitter_refuses_without_leaking_global_slot():
    async def run():
        clock = Clock()
        samples = iter((-0.1, 0.0))
        raw = config().model_dump(by_alias=True)
        raw.update(global_concurrency=1, per_host_concurrency=1)
        scheduler = Politeness(
            GhimeraConfig.model_validate(raw),
            clock=clock,
            sleep=clock.sleep,
            uniform=lambda a, b: next(samples),
        )
        with pytest.raises(Exception, match="adapter_contract"):
            async with scheduler.slot("https://example.org/report"):
                pytest.fail("negative jitter admitted")
        async with asyncio.timeout(1.0):
            async with scheduler.slot("https://other.example/report"):
                pass

    asyncio.run(run())
