"""Real PTY input: exact-request decisions, deadline/cancellation and safe prompts."""

import asyncio
import io
import os
import time
from contextlib import contextmanager

import pytest

from ghimera.human_browser_types import BrowserAssistanceRequest
from ghimera.terminal_assistance import TerminalAssistanceConfig, TerminalHumanAssistant


def policy(**updates):
    values = dict(
        schema="ghimera.terminal-assistance/1", max_response_bytes=128, max_response_attempts=2
    )
    values.update(updates)
    return TerminalAssistanceConfig.model_validate(values)


def request(**updates):
    values = dict(
        schema="ghimera.browser-assistance/1",
        capture_id="capture-1",
        attempt=1,
        request_url="https://publisher.example/report?secret=HIDDEN",
        final_url="https://publisher.example/report?secret=HIDDEN#FRAGMENT",
        session_id="session-1",
        target_id="target-1",
        policy_digest="a" * 64,
        reason="login_wall",
        observed_dom_sha256="b" * 64,
        observed_dom_bytes=12,
        deadline_unix_seconds=time.time() + 3,
    )
    values.update(updates)
    return BrowserAssistanceRequest.model_validate(values)


@contextmanager
def terminal():
    master, slave = os.openpty()
    output = os.fdopen(os.dup(slave), "w", encoding="utf-8")
    try:
        yield master, slave, output
    finally:
        output.close()
        os.close(slave)
        os.close(master)


async def prompt(master):
    loop = asyncio.get_running_loop()
    observed = bytearray()
    ready = loop.create_future()

    def receive():
        observed.extend(os.read(master, 4096))
        if b" OR decline " in observed and not ready.done():
            ready.set_result(bytes(observed))

    loop.add_reader(master, receive)
    try:
        async with asyncio.timeout(3):
            return await ready
    finally:
        loop.remove_reader(master)


@pytest.mark.parametrize("action", ["resume", "decline"])
def test_terminal_response_binds_request_and_never_displays_query_credentials(action):
    with terminal() as (master, slave, output):

        async def scenario():
            pending = request()
            assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
            task = asyncio.create_task(assistant.assist(pending))
            visible = await prompt(master)
            assert b"HIDDEN" not in visible and b"FRAGMENT" not in visible
            assert b"https://publisher.example/report" in visible
            assert pending.content_digest().encode() in visible
            os.write(master, (action + " " + pending.content_digest() + "\n").encode())
            decision = await task
            assert decision.action == action and decision.request_digest == pending.content_digest()
            os.fstat(slave)

        asyncio.run(scenario())


def test_stale_response_cannot_approve_current_request():
    with terminal() as (master, slave, output):

        async def scenario():
            pending = request()
            assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
            task = asyncio.create_task(assistant.assist(pending))
            await prompt(master)
            os.write(master, ("resume " + "c" * 64 + "\n").encode())
            await asyncio.sleep(0)
            assert not task.done()
            os.write(master, ("decline " + pending.content_digest() + "\n").encode())
            assert (await task).action == "decline"

        asyncio.run(scenario())


@pytest.mark.parametrize("finish", ["timeout", "cancel"])
def test_wait_is_bounded_and_removes_reader_without_closing_terminal(finish):
    with terminal() as (master, slave, output):

        async def scenario():
            pending = request(
                deadline_unix_seconds=time.time() + (0.1 if finish == "timeout" else 3)
            )
            assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
            task = asyncio.create_task(assistant.assist(pending))
            await prompt(master)
            if finish == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert (await task).action == "timeout"
            os.fstat(slave)
            assert not asyncio.get_running_loop().remove_reader(slave)

        asyncio.run(scenario())


def test_expired_request_never_prompts_or_reads_and_nonterminal_input_refuses():
    with terminal() as (_, slave, output):
        assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
        assert asyncio.run(assistant.assist(request(deadline_unix_seconds=1))).action == "timeout"
    with pytest.raises(ValueError, match="interactive terminal"):
        TerminalHumanAssistant(policy(), input_fd=0, output=io.StringIO())


def test_other_async_work_continues_and_waiting_prompt_uses_its_own_deadline():
    with terminal() as (master, slave, output):

        async def scenario():
            assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
            first_request = request()
            first = asyncio.create_task(assistant.assist(first_request))
            await prompt(master)
            second_request = request(
                capture_id="capture-2", deadline_unix_seconds=time.time() + 0.1
            )
            second = asyncio.create_task(assistant.assist(second_request))
            independent_work = []

            async def other_work():
                await asyncio.sleep(0)
                independent_work.append("finished")

            await other_work()
            assert independent_work == ["finished"] and not first.done()
            assert (await second).action == "timeout"
            os.write(master, ("resume " + first_request.content_digest() + "\n").encode())
            assert (await first).action == "resume"

        asyncio.run(scenario())


@pytest.mark.parametrize("response", [b"x" * 150 + b"\n", b"resume\nresume\n"])
def test_bad_input_has_bounded_bytes_and_attempts(response):
    with terminal() as (master, slave, output):

        async def scenario():
            assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
            task = asyncio.create_task(assistant.assist(request()))
            await prompt(master)
            os.write(master, response)
            with pytest.raises(ValueError):
                await task
            assert not asyncio.get_running_loop().remove_reader(slave)

        asyncio.run(scenario())
