"""Composed native recovery witnesses; local doubles do not establish quality."""

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.research_recovery_config import ResearchRecoveryConfig
from tests.test_query_control_recovery import (
    configured as query_configured,
)
from tests.test_query_control_recovery import (
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def recovery(version):
    raw = dict(
        schema=f"ghimera.research-recovery/{version}",
        max_snapshot_bytes=4000000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
    )
    if version in (4, 6):
        raw["query_control"] = "serial_acknowledged"
    if version in (5, 6):
        raw["source_acquisition"] = dict(
            schema="ghimera.source-acquisition-recovery/1",
            execution="serial",
            max_capsule_bytes=4000000,
        )
    return raw


def combined(cfg):
    raw = cfg.model_dump()
    raw["research_recovery"] = recovery(6)
    raw["research"]["search_concurrency"] = 1
    if cfg.source_work is None:
        raw["source_work"] = dict(
            schema="ghimera.source-work/1",
            max_operations=100,
            max_page_bytes=2000000,
            max_result_bytes=4000000,
            max_operation_bytes=8000000,
            max_store_bytes=32000000,
            database_timeout_seconds=2.0,
        )
    if raw["source_work"].get("frontier") is None:
        raw["source_work"]["frontier"] = dict(
            schema="ghimera.source-frontier/1",
            max_entries=100,
            max_entry_bytes=10000,
            max_frontier_bytes=1000000,
        )
    return GhimeraConfig.model_validate(raw)


def test_explicit_combined_policy_requires_both_and_roundtrips():
    raw = recovery(6)
    policy = ResearchRecoveryConfig.model_validate(raw)
    assert json.loads(policy.model_dump_json()) == raw
    for key in ("query_control", "source_acquisition"):
        with pytest.raises(ValidationError):
            ResearchRecoveryConfig.model_validate({k: v for k, v in raw.items() if k != key})


@pytest.mark.parametrize("version", [4, 5])
def test_original_single_boundary_dumps_remain_exact_without_extra_controls(version):
    raw = recovery(version)
    assert ResearchRecoveryConfig.model_validate(raw).model_dump_json() == json.dumps(
        raw, separators=(",", ":")
    )


@pytest.mark.parametrize("field,value", [("execution", "parallel"), ("max_capsule_bytes", 0)])
def test_combined_acquisition_remains_native_typed_and_serial(field, value):
    raw = recovery(6)
    raw["source_acquisition"][field] = value
    with pytest.raises(ValidationError):
        ResearchRecoveryConfig.model_validate(raw)


@pytest.mark.parametrize("version,key", [(4, "source_acquisition"), (5, "query_control")])
@pytest.mark.parametrize("explicit_null", [False, True])
def test_single_boundary_policies_never_accept_other_boundary(version, key, explicit_null):
    raw = recovery(version)
    ResearchRecoveryConfig.model_validate(raw)
    raw[key] = None if explicit_null else recovery(6)[key]
    with pytest.raises(ValidationError):
        ResearchRecoveryConfig.model_validate(raw)


@pytest.mark.parametrize("version", [4, 5, 6])
@pytest.mark.parametrize("key", ["source_completion", "model_reconciliation"])
def test_new_profiles_reject_unselected_legacy_control_even_null(version, key):
    raw = recovery(version)
    raw[key] = None
    with pytest.raises(ValidationError):
        ResearchRecoveryConfig.model_validate(raw)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_original_profiles_keep_exact_dumps_and_typed_optional_completion(version):
    raw = recovery(version)
    if version in (2, 3):
        raw["source_completion"] = dict(
            schema="ghimera.source-completion-recovery/1", execution="serial", max_capsule_bytes=20
        )
    if version == 3:
        raw["model_reconciliation"] = dict(
            schema="ghimera.model-reconciliation-policy/1",
            max_decisions_per_run=1,
            max_decision_bytes=20,
        )
    policy = ResearchRecoveryConfig.model_validate(raw)
    assert policy.model_dump_json() == json.dumps(raw, separators=(",", ":"))
    # Known optional None stays omitted; the newly introduced acquisition field
    # was previously forbidden even when null and cannot widen legacy profiles.
    for key in ("query_control", "source_completion", "model_reconciliation"):
        if key not in raw:
            assert (
                ResearchRecoveryConfig.model_validate(dict(raw, **{key: None})).model_dump_json()
                == policy.model_dump_json()
            )
    with pytest.raises(ValidationError):
        ResearchRecoveryConfig.model_validate(dict(raw, source_acquisition=None))


@pytest.mark.parametrize("boundary", ["ack", "unstarted", "unknown"])
def test_combined_fresh_query_adopts_or_holds_original_once(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, boundary, monkeypatch
):
    import tests.test_query_control_recovery as native

    cfg = combined(
        query_configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    )
    monkeypatch.setattr(native, "configured", lambda *args: cfg)
    witness = (
        native.test_actual_unknown_query_remains_charged_and_never_contacts_on_reopen
        if boundary == "unknown"
        else native.test_fresh_command_adopts_original_query_or_unstarted_reservation_once
    )
    args = (tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    witness(*args) if boundary == "unknown" else witness(*args, boundary)


def test_combined_original_learned_query_survives_both_crashes(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch
):
    import tests.test_query_control_recovery as native

    cfg = combined(
        query_configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    )
    monkeypatch.setattr(native, "configured", lambda *args: cfg)
    native.test_unstarted_learned_retained_query_then_score_ack_crash_reuses_original_key(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )


@pytest.mark.parametrize("kind", ["web", "local"])
def test_combined_fresh_acquired_bytes_never_reacquires(tmp_path, kind, monkeypatch):
    import tests.test_source_acquisition_recovery as native

    original = native.configured
    monkeypatch.setattr(native, "configured", lambda path: combined(original(path)))
    native.test_fresh_acquired_bytes_continue_native_parser_graph_without_reacquisition(
        tmp_path, kind
    )


CROSSOVER = """
import asyncio, hashlib, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.journal import DirectoryLedgerSink
from ghimera.research_recovery_store import ResearchRecoveryStore
from ghimera.research_types import ResearchRequest
from ghimera.source_work import SourceWorkStore
from tests.test_intent_research import SearchFixture
from tests.test_source_acquisition_recovery import researcher, DocumentRoute
cfg=GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
mode=sys.argv[2]
contacts=Path(sys.argv[1]).parent/'contacts.jsonl'
def contact(kind):
    with contacts.open('a') as stream: stream.write(kind+'\\n')
search=SearchFixture.request
async def searching(self, request):
    contact('search')
    return await search(self, request)
SearchFixture.request=searching
fetch=DocumentRoute.attempt
async def fetching(self, request):
    contact('fetch')
    return await fetch(self, request)
DocumentRoute.attempt=fetching
append=DirectoryLedgerSink.append
def appended(self,row):
    append(self,row)
    if mode=='query' and row.query_ack is not None: os._exit(73)
DirectoryLedgerSink.append=appended
acquired=SourceWorkStore.acquired
def acquiring(self,token,page,**kwargs):
    acquired(self,token,page,**kwargs)
    if mode=='acquire' and kwargs.get('control') is not None: os._exit(79)
SourceWorkStore.acquired=acquiring
async def run():
    loop=researcher(cfg)
    if mode=='query':
        await loop.run(ResearchRequest(intent='find ports',seeds=('https://example.org/one',)),run_id='cross')
    elif mode=='acquire':
        pin=hashlib.sha256((cfg.journal.directory/'cross'/'research-control.json').read_bytes()).hexdigest()
        await loop.recover('cross',snapshot_sha256=pin,boundary='query_return')
    else:
        cut=SourceWorkStore.acquisition(cfg,'cross')
        result=await loop.recover('cross',snapshot_sha256=cut.sha256,boundary='source_acquisition')
        Path(sys.argv[3]).write_text(result.model_dump_json())
asyncio.run(run())
"""


def test_same_original_run_crosses_query_ack_then_acquisition_ack_in_three_processes(tmp_path):
    from ghimera.journal import read_journal
    from ghimera.research_types import ResearchResult
    from ghimera.source_work import SourceWorkStore, read_source_work
    from tests.test_source_acquisition_recovery import configured

    cfg = combined(configured(tmp_path))
    config_path = tmp_path / "cross.json"
    config_path.write_text(cfg.model_dump_json())
    output = tmp_path / "result.json"
    for mode, code in (("query", 73), ("acquire", 79), ("finish", 0)):
        process = subprocess.run(
            [sys.executable, "-c", CROSSOVER, str(config_path), mode, str(output)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert process.returncode == code, process.stderr
        if mode == "acquire":
            cut = SourceWorkStore.acquisition(cfg, "cross")
            before = cut.journal.rows
            original_receipt = cut.snapshot.progress.harvest.receipt
    result = ResearchResult.model_validate_json(output.read_bytes())
    assert (tmp_path / "contacts.jsonl").read_text().splitlines() == ["search", "fetch"]
    assert result.harvest.ledger[: len(before)] == before
    assert sum(row.query_ack is not None for row in result.harvest.ledger) == 1
    assert sum(row.source_acquisition is not None for row in result.harvest.ledger) == 1
    assert result.harvest.receipt.fetches == original_receipt.fetches
    assert result.harvest.receipt.bytes_read == original_receipt.bytes_read
    assert result.search_calls == 1
    assert len(result.harvest.documents) == 1
    assert read_source_work(cfg, "cross").operations[0].state == "processed"
    assert read_journal(cfg.journal, "cross").state == "complete"
