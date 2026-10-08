"""Interrupted native planning still delegates browser decisions to its caller."""

import asyncio
import hashlib
import json
import os

import pytest

from ghimera.command import execute
from ghimera.config import GhimeraConfig
from ghimera.research import ModelCalls
from ghimera.result_archive import ResearchResultArchive
from ghimera.terminal_assistance import TerminalHumanAssistant
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import options
from tests.test_command_recovery import configured, recovering
from tests.test_html_extraction import ARTICLE
from tests.test_http_fetch import ResolverFixture
from tests.test_human_browser import with_browser
from tests.test_human_browser_command import append_browser
from tests.test_terminal_assistance import policy, prompt, terminal

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


class InterruptedPlan(BaseException):
    """Explicit aborted operation after ACK; process death is tested separately."""


def test_recovery_preserves_real_browser_terminal_assistance_and_source_scope(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch
):
    config = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    origin = f"http://fixture.example:{source_site[0]}"
    content = json.dumps(ARTICLE).replace("</", "<\\/")
    source_site[4]["/plain"] = (
        "<title>Just a moment</title><p>Verify you are human</p>"
        "<button id='human'>Caller-controlled fixture interaction</button>"
        f"<script>document.querySelector('#human').onclick=()=>"
        f"{{document.documentElement.innerHTML={content};}};</script>"
    ).encode()
    native = ModelCalls.invoke
    aborted = False

    async def interrupt(self, event, model, request, call, **kwargs):
        nonlocal aborted
        result = await native(self, event, model, request, call, **kwargs)
        if event == "plan" and not aborted:
            aborted = True
            raise InterruptedPlan()
        return result

    monkeypatch.setattr(ModelCalls, "invoke", interrupt)

    async def scenario(selected, page, unrelated):
        selected = selected.model_copy(
            update={
                "origins": (selected.origins[0].model_copy(update={"path_prefixes": ("/plain",)}),)
            }
        )
        opts = options(
            tmp_path,
            config,
            schema="ghimera.collector-command/3",
            human_assistance=policy(),
            execution=dict(schema="ghimera.command-execution/1", operation="run"),
        )
        append_browser(opts, selected)
        effective = GhimeraConfig.from_toml(opts.config_path)
        with terminal() as (master, slave, output):
            assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
            with pytest.raises(InterruptedPlan):
                await execute(opts, source_resolver=ResolverFixture(), human_assistant=assistant)
            original_calls = len(model_endpoint[1])
            assert not source_site[1]
            snapshot = effective.journal.directory / opts.run_id / "research-control.json"
            pin = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            reservation = (opts.output_directory / "reservation.json").read_bytes()
            opts.request_path.unlink()
            task = asyncio.create_task(
                execute(
                    recovering(opts, pin),
                    source_resolver=ResolverFixture(),
                    human_assistant=assistant,
                )
            )
            visible = await prompt(master)
            line = next(
                item for item in visible.splitlines() if item.startswith(b"human_assistance:")
            )
            digest = json.loads(line.split(b":", 1)[1])["request_digest"]
            await page.locator("#human").click()  # The caller, not a challenge solver.
            await page.locator("article").wait_for()
            os.write(master, ("resume " + digest + "\n").encode())
            receipt = await task
        result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
        assert receipt.status == result.status == "answered"
        assert original_calls == 1
        assert sum(row.event == "plan" for row in result.harvest.ledger) == 1
        assert sum(row.model_replay is not None for row in result.harvest.ledger) == 1
        assert (opts.output_directory / "reservation.json").read_bytes() == reservation
        document = result.harvest.documents[0]
        assert document.human_browser.assistance[0].action == "resume"
        assert document.human_browser.final_url == origin + "/plain"
        assert result.harvest.receipt.effective_config.human_browser == selected
        assert result.answer.claims[0].citations[0].matches(document)
        assert source_site[1]["/plain"] == 1
        assert not page.is_closed() and not unrelated.is_closed()

    asyncio.run(with_browser(tmp_path, origin, scenario))
