"""Configured native CLI corpus lifecycle; local protocols, not model accuracy."""

import asyncio
import hashlib
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from ghimera.command import execute
from ghimera.command_corpus import CorpusCommandOptions, CorpusCommandResult, execute_corpus
from ghimera.config import GhimeraConfig
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import options, toml_lines
from tests.test_command_recovery import configured as model_configured
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_corpus_search import binding as search_binding
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus, endpoint
from tests.test_query_control_recovery import configured as query_configured
from tests.test_research_reuse import reuse_policy

__all__ = ["endpoint", "encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]

ENTRYPOINT = """
import os,sys
from ghimera import command,command_corpus
from ghimera.corpus import EvidenceCorpus
from ghimera.journal import DirectoryLedgerSink
from ghimera.model_work import ModelInvocation
from tests.test_http_fetch import ResolverFixture
mode=sys.argv[1]
execute=command_corpus.execute
async def resolved(options,**kwargs):
    kwargs['source_resolver']=ResolverFixture()
    return await execute(options,**kwargs)
command_corpus.execute=resolved
invoke=ModelInvocation.invoke
async def invoked(self,*args,**kwargs):
    phase=self._ledger.snapshot()[-1].model_intent.phase
    result=await invoke(self,*args,**kwargs)
    if mode=='answer_ack' and phase=='answer': os._exit(73)
    return result
ModelInvocation.invoke=invoked
append=DirectoryLedgerSink.append
def appended(self,row):
    append(self,row)
    if mode=='query_ack' and row.query_ack is not None: os._exit(73)
DirectoryLedgerSink.append=appended
if mode=='append_fail':
    async def failed(self,harvest): raise ValueError('private fixture error')
    EvidenceCorpus.append=failed
raise SystemExit(command.main(sys.argv[2:]))
"""


def envelope(tmp_path, cfg, policy, *, create=False, append=False, run_id="corpus-run", **binding):
    path = tmp_path / "corpus.toml"
    path.write_text("\n".join(toml_lines(policy.model_dump(mode="json"))))
    opts = options(
        tmp_path,
        cfg,
        schema="ghimera.collector-command/2",
        run_id=run_id,
        output_directory=tmp_path / (run_id + ".output"),
        execution=dict(schema="ghimera.command-execution/1", operation="run"),
    )
    return CorpusCommandOptions(
        schema="ghimera.collector-command/7",
        action="execute",
        command=opts,
        corpus=dict(
            schema="ghimera.command-corpus/1",
            config_path=path,
            config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            mode="create" if create else "open",
            append_result=append,
            **binding,
        ),
    )


def invoke(tmp_path, opts, mode="normal"):
    path = tmp_path / (mode + ".job.toml")
    path.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))
    return subprocess.run(
        [sys.executable, "-c", ENTRYPOINT, mode, "--job", str(path), "--max-job-bytes", "1000000"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def opened(opts, result, **updates):
    raw = opts.model_dump()
    raw["corpus"].update(mode="open", corpus_id=result.corpus_id, generation=result.generation)
    raw.update(updates)
    return CorpusCommandOptions.model_validate(raw)


def test_actual_cli_create_archive_failed_handoff_and_archive_only_retry(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, endpoint
):
    cfg = model_configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    policy = corpus_config(tmp_path, endpoint[0])
    opts = envelope(tmp_path, cfg, policy, create=True, append=True)
    failed = invoke(tmp_path, opts, "append_fail")
    assert failed.returncode == 2 and not failed.stderr
    saved = CorpusCommandResult.model_validate_json(failed.stdout)
    assert saved.handoff == "failed" and saved.command.status == "answered"
    assert saved.failure.category == "invalid" and saved.failure.refusal is None
    assert "private fixture error" not in failed.stdout
    archive_bytes = (opts.command.output_directory / "result.json").read_bytes()
    before = (
        dict(source_site[1]),
        len(search_endpoint[1]),
        len(model_endpoint[1]),
        len(encoder_endpoint[1]),
    )
    retry = opened(opts, saved, action="handoff")
    done = invoke(tmp_path, retry)
    assert done.returncode == 0 and not done.stderr, done.stderr
    result = CorpusCommandResult.model_validate_json(done.stdout)
    assert result.command == saved.command and result.handoff == "complete"
    assert result.appended.added_documents == 1 and result.generation == 1
    assert before == (
        dict(source_site[1]),
        len(search_endpoint[1]),
        len(model_endpoint[1]),
        len(encoder_endpoint[1]),
    )
    assert archive_bytes == (opts.command.output_directory / "result.json").read_bytes()
    encoding_count = len(result.appended.encoding_calls)
    assert len(endpoint[1]) == encoding_count and encoding_count > 0
    # Idempotent native append receipt after acknowledged handoff, no re-encoding.
    again = invoke(tmp_path, opened(opts, result, action="handoff"))
    assert again.returncode == 0 and len(endpoint[1]) == encoding_count
    assert json.loads(again.stdout)["appended"]["added_documents"] == 0
    changed = opts.command.request_path
    changed.write_text('{"intent":"changed"}')
    held = invoke(tmp_path, opened(opts, result, action="handoff"))
    assert held.returncode == 2 and len(endpoint[1]) == encoding_count


@pytest.mark.parametrize("consumer", ["discovery", "retained"])
def test_actual_cli_configured_consumer_and_native_query_ack_recovery(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, endpoint, consumer
):
    cfg = query_configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    policy = corpus_config(
        tmp_path,
        endpoint[0],
        encoding_recovery=dict(
            schema="ghimera.encoding-recovery/1",
            max_calls=100,
            max_input_chars=1000000,
            max_stored_bytes=10000000,
        ),
    )
    seed = envelope(tmp_path, cfg, policy, create=True, append=True, run_id="seed")
    seeded = invoke(tmp_path, seed)
    assert seeded.returncode == 0, seeded.stderr
    first = CorpusCommandResult.model_validate_json(seeded.stdout)
    store = corpus(policy, create=False)
    try:
        raw = cfg.model_dump()
        if consumer == "discovery":
            raw["search"] = search_binding(store).model_dump()
        else:
            from ghimera.corpus_evidence import CorpusEvidenceReader

            raw["research"]["retained_evidence"] = reuse_policy(
                CorpusEvidenceReader(reader_policy(store), store)
            ).model_dump()
        selected = GhimeraConfig.model_validate(raw)
    finally:
        store.close()
    opts = envelope(
        tmp_path, selected, policy, corpus_id=first.corpus_id, generation=first.generation
    )
    # A changed query-only pin preserves passage-space identity but not the full reader recipe.
    changed = policy.model_dump()
    changed["query_encoder"]["text_prefix"] = "changed: "
    changed_policy = type(policy).model_validate(changed)
    changed_opts = envelope(
        tmp_path, selected, changed_policy, corpus_id=first.corpus_id, generation=first.generation
    )
    contacts = len(endpoint[1]), len(model_endpoint[1]), len(search_endpoint[1])
    refused = invoke(tmp_path, changed_opts)
    assert refused.returncode == 2
    assert contacts == (len(endpoint[1]), len(model_endpoint[1]), len(search_endpoint[1]))
    opts = envelope(
        tmp_path, selected, policy, corpus_id=first.corpus_id, generation=first.generation
    )
    dead = invoke(tmp_path, opts, "query_ack")
    assert dead.returncode == 73, dead.stderr
    pin = hashlib.sha256(
        (cfg.journal.directory / opts.command.run_id / "research-control.json").read_bytes()
    ).hexdigest()
    before = len(endpoint[1]), len(search_endpoint[1]), len(model_endpoint[1])
    recover = opts.model_dump()
    recover["command"].update(
        schema="ghimera.collector-command/6",
        request_path=None,
        execution=dict(
            schema="ghimera.command-execution/5",
            operation="recover",
            recovery_boundary="query_return",
            snapshot_sha256=pin,
        ),
    )
    done = invoke(tmp_path, CorpusCommandOptions.model_validate(recover))
    assert done.returncode == 0, done.stderr
    result = ResearchResultArchive.read(
        opts.command.output_directory, max_bytes=opts.command.max_result_bytes
    )
    assert result.status == "answered"
    # Retained initial ACK permits one original planned query; discovery has no new query.
    assert len(endpoint[1]) == before[0] + (1 if consumer == "retained" else 0)
    assert len(search_endpoint[1]) == before[1]
    assert json.loads(done.stdout)["corpus_id"] == first.corpus_id


def test_pins_and_native_writer_refuse_before_contact_and_owner_closes(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, endpoint, monkeypatch
):
    cfg = model_configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    policy = corpus_config(tmp_path, endpoint[0])
    store = corpus(policy, create=True)
    opts = envelope(tmp_path, cfg, policy, corpus_id=store.identity, generation=0)
    before = len(model_endpoint[1]), len(endpoint[1]), len(search_endpoint[1])
    for change in ({"config_sha256": "0" * 64}, {"corpus_id": "0" * 32}, {"generation": 1}):
        raw = opts.model_dump()
        raw["corpus"].update(change)
        refused = invoke(tmp_path, CorpusCommandOptions.model_validate(raw))
        assert refused.returncode == 2
    with store._storage.writer():
        refused = invoke(tmp_path, opts)
        assert refused.returncode == 2
    assert before == (len(model_endpoint[1]), len(endpoint[1]), len(search_endpoint[1]))
    # Native execute borrows; factory rejection must not close the caller's owner.
    with pytest.raises(ValueError):
        asyncio.run(execute(opts.command, corpus=store))
    assert store.identity == opts.corpus.corpus_id
    from ghimera.corpus import EvidenceCorpus

    closed = []
    native_close = EvidenceCorpus.close

    def observed_close(self):
        native_close(self)
        closed.append(self)

    monkeypatch.setattr(EvidenceCorpus, "close", observed_close)
    raw = opts.model_dump()
    raw["corpus"]["corpus_id"] = "0" * 32
    with pytest.raises(ValueError):
        asyncio.run(execute_corpus(CorpusCommandOptions.model_validate(raw)))
    assert len(closed) == 1 and closed[0]._closed
    store.close()
    changed = policy.model_dump()
    changed["chunk_chars"] += 1
    changed_policy = type(policy).model_validate(changed)
    mismatch = envelope(
        tmp_path, cfg, changed_policy, corpus_id=opts.corpus.corpus_id, generation=0
    )
    refused = invoke(tmp_path, mismatch)
    assert refused.returncode == 2
    assert before == (len(model_endpoint[1]), len(endpoint[1]), len(search_endpoint[1]))
    reopened = corpus(policy, create=False)
    reopened.close()


def test_policy_requires_explicit_reserved_run_and_no_service_duplicate_owner(tmp_path):
    from ghimera.command import CommandOptions

    plain = CommandOptions(
        schema="chimera.collector-command/1",
        config_path=tmp_path / "config",
        request_path=tmp_path / "request",
        output_directory=tmp_path / "result",
        run_id="run",
        max_input_bytes=1000,
        max_result_bytes=1000,
    )
    with pytest.raises(ValidationError):
        CorpusCommandOptions(
            schema="ghimera.collector-command/7",
            action="execute",
            command=plain,
            corpus=dict(
                schema="ghimera.command-corpus/1",
                config_path=tmp_path / "corpus",
                config_sha256="0" * 64,
                mode="create",
                append_result=True,
            ),
        )
    # The service's CommandOptions field cannot acquire an independent /7 corpus owner.
    with pytest.raises(ValidationError):
        CommandOptions.model_validate(
            dict(plain.model_dump(), schema="ghimera.collector-command/7")
        )
