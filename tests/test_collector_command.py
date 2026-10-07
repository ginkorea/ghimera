"""Concrete command assembly and private complete-result persistence, not LLM accuracy."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.command import CommandOptions, CredentialBindings, execute, main
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def toml_lines(raw, path=()):
    """Serialize this test's scalar/list/table configuration, no production parser."""
    lines = []
    if path:
        lines.append("[" + ".".join(path) + "]")
    for key, value in raw.items():
        if value is not None and not isinstance(value, dict):
            assert not (isinstance(value, list) and any(isinstance(item, dict) for item in value))
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    for key, value in raw.items():
        if isinstance(value, dict):
            lines.extend(toml_lines(value, path + (key,)))
    return lines


def options(tmp_path, cfg, **updates):
    config_path = tmp_path / "collector.toml"
    config_path.write_text("\n".join(toml_lines(cfg.model_dump(mode="json"))))
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({"intent": "find ports"}))
    raw = dict(
        schema="chimera.collector-command/1",
        config_path=config_path,
        request_path=request_path,
        output_directory=tmp_path / "result",
        run_id="research-1",
        max_input_bytes=1000000,
        max_result_bytes=10000000,
    )
    raw.update(updates)
    return CommandOptions(**raw)


def test_command_uses_concrete_adapters_and_archives_originals_and_citations(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = options(tmp_path, cfg)
    summary = asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert summary.status == result.status == "answered"
    assert summary.documents == len(result.harvest.documents) == 1
    assert result.harvest.documents[0].raw
    assert result.answer.claims[0].citations[0].matches(result.harvest.documents[0])
    assert result.harvest.receipt.effective_config == cfg
    assert opts.output_directory.stat().st_mode & 0o777 == 0o700
    assert {p.name for p in opts.output_directory.iterdir()} == {"result.json", "receipt.json"}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in opts.output_directory.iterdir())
    before = len(model_endpoint[1]), len(search_endpoint[1]), len(encoder_endpoint[1])
    with pytest.raises(OSError):
        asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    assert before == (len(model_endpoint[1]), len(search_endpoint[1]), len(encoder_endpoint[1]))


def test_command_imports_an_owned_pdf_before_research_and_archives_its_provenance(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    from ghimera import LocalDocumentSeed
    from tests.test_document_extraction import config as document_config
    from tests.test_document_extraction import native_pdf
    from tests.test_local_inputs import recipe

    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    data = cfg.model_dump()
    data.update(
        document_extraction=document_config(tmp_path).document_extraction,
        local_inputs=recipe(tmp_path),
    )
    from ghimera.config import GhimeraConfig

    cfg = GhimeraConfig.model_validate(data)
    owned_pdf = tmp_path / "owned-seed-document.pdf"
    original = native_pdf()
    owned_pdf.write_bytes(original)
    import hashlib

    seed = LocalDocumentSeed(
        path=owned_pdf, sha256=hashlib.sha256(original).hexdigest(), content_type="application/pdf"
    )
    opts = options(tmp_path, cfg)
    opts.request_path.write_text(
        json.dumps({"intent": "find ports", "local_documents": [seed.model_dump(mode="json")]})
    )
    summary = asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert summary.status == result.status == "answered"
    imported = next(doc for doc in result.harvest.source_documents if doc.local_input)
    assert imported.url == seed.source_id and imported.raw == original
    assert str(owned_pdf) not in result.model_dump_json()
    observations = result.harvest.ledger
    intake = next(row.sequence for row in observations if row.event == "local_input")
    first_plan = next(row.sequence for row in observations if row.event == "plan")
    assert intake < first_plan
    assert result.answer.claims[0].citations[0].matches(imported)


def test_command_preflight_bounds_credentials_and_request_before_outbound(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = options(tmp_path, cfg)
    opts.request_path.write_text('{"intent":""}')
    with pytest.raises(ValidationError):
        asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    opts.request_path.write_text(json.dumps({"intent": "ports" * 100}))
    from ghimera.refusals import GhimeraRefused

    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    opts.request_path.write_text(json.dumps({"intent": "find ports"}))
    small = opts.model_copy(update={"max_input_bytes": 1})
    with pytest.raises(ValueError):
        asyncio.run(execute(small, source_resolver=ResolverFixture()))
    bindings = tmp_path / "bindings.json"
    bindings.write_text(
        json.dumps(
            {
                "schema": "chimera.command-credentials/1",
                "completions": [
                    {"endpoint": cfg.models.planner.endpoint, "environment_variable": "ABSENT_KEY"}
                ],
            }
        )
    )
    monkeypatch.delenv("ABSENT_KEY", raising=False)
    with pytest.raises(ValueError):
        asyncio.run(execute(opts.model_copy(update={"bindings_path": bindings})))
    assert not opts.output_directory.exists()
    assert not model_endpoint[1] and not search_endpoint[1] and not encoder_endpoint[1]
    assert not source_site[1]


def test_result_archive_refuses_tamper_missing_seal_link_and_oversize(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = options(tmp_path, cfg)
    asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    original = (opts.output_directory / "result.json").read_bytes()
    for allowance in (0, True, len(original) - 1):
        with pytest.raises((ValueError, OSError)):
            ResearchResultArchive.read(opts.output_directory, max_bytes=allowance)
    (opts.output_directory / "result.json").write_bytes(original + b" ")
    with pytest.raises(ValueError):
        ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    (opts.output_directory / "result.json").write_bytes(original)
    os.link(opts.output_directory / "result.json", tmp_path / "linked.json")
    with pytest.raises(ValueError):
        ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    (tmp_path / "linked.json").unlink()
    (opts.output_directory / "receipt.json").unlink()
    with pytest.raises(OSError):
        ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)


def test_small_result_allowance_leaves_no_complete_archive(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = options(tmp_path, cfg, max_result_bytes=1)
    with pytest.raises(ValueError):
        asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    assert opts.output_directory.exists()
    assert not tuple(opts.output_directory.iterdir())


def test_archive_symlink_parent_or_public_directory_refuses_before_work(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        ResearchResultArchive.create(alias / "result", run_id="new")
    private = ResearchResultArchive.create(tmp_path / "private", run_id="new")
    (tmp_path / "private").chmod(0o755)
    try:
        with pytest.raises(ValueError):
            private.check()
    finally:
        private.close()


def test_credential_bindings_are_explicit_and_separate(monkeypatch):
    monkeypatch.setenv("MODEL_KEY", "fixture-model-secret")
    monkeypatch.setenv("ENCODER_KEY", "fixture-encoder-secret")
    monkeypatch.setenv("SOURCE_COOKIE", "fixture-source-cookie")
    bindings = CredentialBindings.model_validate(
        {
            "schema": "chimera.command-credentials/1",
            "completions": [
                {
                    "endpoint": "http://127.0.0.1/v1/chat/completions",
                    "environment_variable": "MODEL_KEY",
                }
            ],
            "encoder_environment_variable": "ENCODER_KEY",
            "sources": [
                {
                    "session_id": "entitled",
                    "headers": [{"name": "cookie", "environment_variable": "SOURCE_COOKIE"}],
                }
            ],
        }
    )
    resolved = bindings.resolve()
    assert resolved.models[next(iter(resolved.models))].get_secret_value() == "fixture-model-secret"
    assert resolved.encoder.get_secret_value() == "fixture-encoder-secret"
    assert resolved.sources["entitled"].headers[0][1].get_secret_value() == "fixture-source-cookie"
    assert "fixture-" not in repr(resolved)
    assert "fixture-" not in bindings.model_dump_json()
    for value in ("", "contains\nnewline"):
        monkeypatch.setenv("MODEL_KEY", value)
        with pytest.raises(ValueError):
            bindings.resolve()


def test_completion_credentials_partition_visual_roles_and_refuse_unbound_endpoints(tmp_path):
    from pydantic import SecretStr

    from ghimera.command import ResolvedCredentials
    from ghimera.config import GhimeraConfig
    from tests.test_served_models import service
    from tests.test_visuals import recipe, run_config

    text, vision, reviewer = service(8101), service(8102), service(8103)
    configured_visual = recipe(tmp_path)
    raw = run_config(configured_visual).model_dump()
    visual = configured_visual.model_dump()
    visual.update(vision=vision, reviewer=reviewer)
    raw.update(
        models={
            "schema": "chimera.model-bindings/1",
            "planner": text,
            "analyst": text,
            "reviewer": text,
            "judge": text,
        },
        visuals=visual,
    )
    cfg = GhimeraConfig.model_validate(raw)
    secrets = {
        s.endpoint: SecretStr(f"fixture-{i}") for i, s in enumerate((text, vision, reviewer))
    }
    bindings = ResolvedCredentials(models=secrets, encoder=None, sources={})
    text_bound, vision_bound, reviewer_bound = bindings.completion_roles(cfg)
    assert text_bound == {text.endpoint: secrets[text.endpoint]}
    assert vision_bound == secrets[vision.endpoint] and reviewer_bound == secrets[reviewer.endpoint]
    with pytest.raises(ValueError, match="explicit recipe endpoint"):
        ResolvedCredentials(
            models={**secrets, "https://unbound.example/": SecretStr("fixture")},
            encoder=None,
            sources={},
        ).completion_roles(cfg)
    assert "fixture-" not in repr(bindings)


def test_cli_help_and_safe_invalid_input_do_not_echo_values(tmp_path, capsys):
    job = tmp_path / "bad.toml"
    job.write_text('schema = "SECRET-INVALID-SCHEMA"')
    assert main(["--job", str(job), "--max-job-bytes", "10000"]) == 2
    output = capsys.readouterr()
    assert "command_input_invalid" in output.err and "SECRET" not in output.err
    assert output.out == ""
    completed = subprocess.run(
        [sys.executable, "-m", "ghimera", "--help"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "--job" in completed.stdout and not completed.stderr


def test_receipt_failure_leaves_originals_but_no_seal_and_never_overwrites(tmp_path, monkeypatch):
    from tests.test_intent_research import run

    result, _, _ = run()
    archive = ResearchResultArchive.create(tmp_path / "interrupted", run_id="interrupted")
    publish = archive._publish

    def interrupted(name, data):
        if name == "receipt.json":
            raise OSError("simulated I/O error")
        publish(name, data)

    monkeypatch.setattr(archive, "_publish", interrupted)
    try:
        with pytest.raises(OSError):
            archive.write(result, max_bytes=10000000)
        retained = (tmp_path / "interrupted" / "result.json").read_bytes()
        assert result.model_dump_json().encode() == retained
        with pytest.raises(OSError):
            ResearchResultArchive.read(tmp_path / "interrupted", max_bytes=10000000)
        monkeypatch.setattr(archive, "_publish", publish)
        with pytest.raises(FileExistsError):
            archive.write(result, max_bytes=10000000)
        assert (tmp_path / "interrupted" / "result.json").read_bytes() == retained
        assert not (tmp_path / "interrupted" / ".result.json.pending").exists()
    finally:
        archive.close()


def test_completed_partial_research_is_retained_not_promoted_to_answered(tmp_path):
    from tests.test_intent_research import AnalystFixture, run

    partial, _, _ = run(analyst=AnalystFixture(missing=True))
    archive = ResearchResultArchive.create(tmp_path / "partial", run_id="partial")
    try:
        receipt = archive.write(partial, max_bytes=10000000)
        assert receipt.status == "partial"
        reread = ResearchResultArchive.read(tmp_path / "partial", max_bytes=10000000)
        assert reread == partial and reread.status != "answered"
    finally:
        archive.close()


def test_command_template_is_versioned_and_credentials_template_contains_only_names():
    import tomllib

    template = CommandOptions.model_validate(
        tomllib.loads(Path("examples/collector-command.toml").read_text())
    )
    assert template.schema_version == "chimera.collector-command/1"
    assert template.bindings_path is None and template.references_path is None
    bindings = CredentialBindings.model_validate_json(
        Path("examples/credential-bindings.json").read_bytes()
    )
    assert bindings.encoder_environment_variable == "COLLECTOR_EMBEDDING_KEY"
    assert bindings.completions[0].environment_variable == "COLLECTOR_COMPLETION_KEY"
    for changes in ({"schema": "chimera.collector-command/2"}, {"max_result_bytes": True}):
        with pytest.raises(ValidationError):
            CommandOptions.model_validate(dict(template.model_dump(), **changes))


def test_main_writes_only_receipt_and_preserves_failure_exit_status(tmp_path, monkeypatch, capsys):
    import ghimera.command as command
    from ghimera.result_archive import ArchiveReceipt
    from tests.test_c0 import config

    cfg = config()
    opts = options(tmp_path, cfg)
    job = tmp_path / "job.toml"
    job.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))

    async def stored(options):
        assert options == opts
        return ArchiveReceipt(
            schema="chimera.research-archive/1",
            run_id="research-1",
            result_sha256="a" * 64,
            result_bytes=123,
            status="partial",
            documents=0,
        )

    monkeypatch.setattr(command, "execute", stored)
    assert main(["--job", str(job), "--max-job-bytes", "100000"]) == 1
    captured = capsys.readouterr()
    receipt = ArchiveReceipt.model_validate_json(captured.out)
    assert receipt.status == "partial" and not captured.err
    assert "find ports" not in captured.out


@pytest.mark.parametrize(
    "arguments",
    [
        ["--job", "/tmp/unused", "--max-job-bytes", "PRIVATE-FIXTURE-VALUE"],
        ["--job", "/tmp/unused", "--max-job-bytes", "10", "--key", "PRIVATE-FIXTURE-VALUE"],
    ],
)
def test_argument_errors_never_echo_untrusted_values(arguments, capsys):
    with pytest.raises(SystemExit) as raised:
        main(arguments)
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "command_arguments_invalid\n"
