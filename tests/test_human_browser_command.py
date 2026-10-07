"""Concrete command + actual browser + terminal input + immutable result archive."""

import asyncio
import json
import os

import pytest
from pydantic import ValidationError

from ghimera.command import CommandOptions, execute, main
from ghimera.result_archive import ResearchResultArchive
from ghimera.terminal_assistance import TerminalHumanAssistant
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_collector_command import options, toml_lines
from tests.test_html_extraction import ARTICLE
from tests.test_http_fetch import ResolverFixture
from tests.test_human_browser import with_browser
from tests.test_terminal_assistance import policy, prompt, terminal

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def append_browser(opts, selected):
    browser = selected.model_dump(mode="json")
    origins = browser.pop("origins")
    addition = "\n".join(toml_lines({"human_browser": browser}))
    for item in origins:
        addition += "\n[[human_browser.origins]]\n" + "\n".join(toml_lines(item))
    opts.config_path.write_text(opts.config_path.read_text() + "\n" + addition)


def test_command_schema_keeps_legacy_wire_and_requires_explicit_human_policy(tmp_path):
    values = dict(
        schema="chimera.collector-command/1",
        config_path=tmp_path / "config.toml",
        request_path=tmp_path / "request.json",
        output_directory=tmp_path / "result",
        run_id="human-command",
        max_input_bytes=1000000,
        max_result_bytes=10000000,
    )
    assert "human_assistance" not in CommandOptions.model_validate(values).model_dump()
    with pytest.raises(ValidationError, match="requires command /3"):
        CommandOptions.model_validate(dict(values, human_assistance=policy()))
    with pytest.raises(ValidationError, match="explicit human assistance"):
        CommandOptions.model_validate(dict(values, schema="ghimera.collector-command/3"))
    active = CommandOptions.model_validate(
        dict(values, schema="ghimera.collector-command/3", human_assistance=policy())
    )
    assert CommandOptions.model_validate_json(active.model_dump_json()) == active


def test_interactive_example_parses_without_implicit_terminal_or_network_work():
    import tomllib
    from pathlib import Path

    example = CommandOptions.model_validate(
        tomllib.loads(Path("examples/collector-interactive.toml").read_text())
    )
    assert example.schema_version == "ghimera.collector-command/3"
    assert example.human_assistance.max_response_bytes == 256
    assert example.execution is None


def test_command_collects_from_the_human_selected_browser_after_terminal_decision(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, url = assembled(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    origin = url.removesuffix("/plain")
    content = json.dumps(ARTICLE).replace("</", "<\\/")
    source_site[4]["/plain"] = (
        "<title>Just a moment</title><p>Verify you are human</p>"
        "<button id='human'>Fixture human assistance</button>"
        f"<script>document.querySelector('#human').onclick=()=>"
        f"{{document.documentElement.innerHTML={content};}};</script>"
    ).encode()

    async def scenario(selected, page, unrelated):
        selected = selected.model_copy(
            update={
                "origins": (selected.origins[0].model_copy(update={"path_prefixes": ("/plain",)}),)
            }
        )
        opts = options(
            tmp_path, config, schema="ghimera.collector-command/3", human_assistance=policy()
        )
        append_browser(opts, selected)
        with terminal() as (master, slave, output):
            assistant = TerminalHumanAssistant(policy(), input_fd=slave, output=output)
            task = asyncio.create_task(
                execute(opts, source_resolver=ResolverFixture(), human_assistant=assistant)
            )
            visible = await prompt(master)
            record = next(
                line for line in visible.splitlines() if line.startswith(b"human_assistance:")
            )
            digest = json.loads(record.split(b":", 1)[1])["request_digest"]
            await page.locator("#human").click()
            await page.locator("article").wait_for()
            os.write(master, ("resume " + digest + "\n").encode())
            receipt = await task
        result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
        assert receipt.status == result.status == "answered"
        doc = result.harvest.documents[0]
        assert doc.human_browser.assistance[0].action == "resume"
        assert result.answer.claims[0].citations[0].matches(doc)
        assert doc.extracted.title == "Port infrastructure report"
        assert source_site[1]["/plain"] == 1
        assert result.harvest.receipt.effective_config.human_browser == selected
        assert not page.is_closed() and not unrelated.is_closed()

    asyncio.run(with_browser(tmp_path, origin, scenario))


def test_terminal_command_without_browser_refuses_before_network_or_archive(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = options(
        tmp_path, config, schema="ghimera.collector-command/3", human_assistance=policy()
    )
    with pytest.raises(ValueError, match="explicit browser binding"):
        asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    assert not source_site[1] and not model_endpoint[1] and not opts.output_directory.exists()


def test_cli_rejects_noninteractive_terminal_before_network_or_archive(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch, capsys
):
    from tests.test_human_browser import policy as browser_policy

    config, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = options(
        tmp_path, config, schema="ghimera.collector-command/3", human_assistance=policy()
    )
    append_browser(opts, browser_policy())
    job = tmp_path / "job.toml"
    job.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))
    monkeypatch.setattr(os, "isatty", lambda fd: False)
    assert main(["--job", str(job), "--max-job-bytes", "1000000"]) == 2
    captured = capsys.readouterr()
    assert "command_" in captured.err and captured.out == ""
    assert not source_site[1] and not model_endpoint[1] and not opts.output_directory.exists()
