"""Fresh native service caller decisions, output adoption and original handoff."""

import asyncio
import json
import subprocess
import sys

from ghimera.collection_service import CollectionServiceConfig
from ghimera.config import GhimeraConfig
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.journal import read_journal
from ghimera.model_reconciliation import authorization, unreconciled_model_sequences
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import toml_lines
from tests.test_command_recovery import counts
from tests.test_evidence_corpus import corpus as native_corpus
from tests.test_service_recovery import configuration

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


ENTRYPOINT = """
import asyncio, hashlib, json, os, sys
from pathlib import Path
import ghimera.collection_service as module
from ghimera.collection_service import CollectionService, CollectionServiceConfig
from ghimera.model_work import ModelInvocation
from ghimera.model_reconciliation_types import ModelReconciliationDecision
from ghimera.research_types import ResearchRequest
from tests.test_http_fetch import ResolverFixture
cfg=CollectionServiceConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
boundary=sys.argv[2]
native=ModelInvocation.invoke
async def dying(self,call,observe):
    intent=self._ledger.snapshot()[self._sequence].model_intent
    if intent.phase=='answer' and boundary=='original_unknown' and self._authorization is None:
        async def lost():
            await call()
            os._exit(74)
        return await native(self,lost,observe)
    result=await native(self,call,observe)
    if intent.phase=='answer' and boundary=='attempt_ack' and self._authorization is not None:
        os._exit(75)
    return result
ModelInvocation.invoke=dying
execute=module.execute
async def resolved(options,**kwargs):
    return await execute(options,source_resolver=ResolverFixture(),**kwargs)
module.execute=resolved
update=CollectionService._update
def updating(self,run_id,phase,**kwargs):
    if boundary=='reserve' and kwargs.get('model_attempt') is not None:
        os._exit(79)  # Durable native decision exists; service receipt ACK is lost.
    if boundary=='output' and kwargs.get('archive') is not None:
        os._exit(78)
    return update(self,run_id,phase,**kwargs)
CollectionService._update=updating
async def main():
    service=CollectionService(cfg)
    await service.start(create=boundary=='original_unknown')
    if boundary=='original_unknown':
        job=service.submit(ResearchRequest.model_validate_json(cfg.command.request_path.read_bytes()))
        run_id=job.run_id
    else:
        run_id=next(iter(service._jobs))
    if boundary=='reserve':
        pin=hashlib.sha256((service._validator.config.journal.directory/run_id/'research-control.json').read_bytes()).hexdigest()
        observed=await service.observe_model_unknown(run_id,snapshot_sha256=pin)
        decision=ModelReconciliationDecision(schema='ghimera.model-reconciliation/1',
            action='abandon_and_authorize_new_attempt', operation_id='owned-service-attempt',
            caller='native fixture caller',reason='explicit original unknown abandonment',
            observed=observed,observed_sha256=observed.sha256)
        receipt=await service.reconcile_model(run_id,decision)
        assert service.status(run_id).phase=='held'
        print(receipt.model_dump_json(),flush=True)
        await service.stop()
        return
    if boundary=='attempt_ack':
        receipt=service.model_attempt_history(run_id)[0]
        assert service.status(run_id).phase=='held'
        await service.recover(run_id,attempt=receipt)
    async with asyncio.timeout(75):
        while service.status(run_id).phase not in {'held','completed','failed','cancelled'}:
            await asyncio.sleep(.01)
    print(service.status(run_id).model_dump_json(),flush=True)
    await service.stop()
asyncio.run(main())
"""


def invoke(tmp_path, config, boundary):
    path = tmp_path / "model-service.json"
    path.write_text(config.model_dump_json())
    return subprocess.run(
        [sys.executable, "-c", ENTRYPOINT, str(path), boundary],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_fresh_service_decision_and_completed_archive_adoption_keep_unknown_and_handoff(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, cfg = configuration(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        handoff=True,
        graph=True,
        attempts=2,
    )
    raw = cfg.model_dump()
    raw["research_recovery"].update(
        schema="ghimera.research-recovery/3",
        model_reconciliation=dict(
            schema="ghimera.model-reconciliation-policy/1",
            max_decisions_per_run=2,
            max_decision_bytes=16384,
        ),
    )
    cfg = GhimeraConfig.model_validate(raw)
    # Keep the owning fixture's complete graph array tables unchanged.
    text = config.command.config_path.read_text().replace(
        'schema = "ghimera.research-recovery/1"', 'schema = "ghimera.research-recovery/3"'
    )
    text += "\n" + "\n".join(
        toml_lines(
            raw["research_recovery"]["model_reconciliation"],
            ("research_recovery", "model_reconciliation"),
        )
    )
    config.command.config_path.write_text(text)
    raw = config.model_dump()
    raw["recovery"].update(schema="ghimera.service-recovery/3", model_reconciliation="caller_only")
    config = CollectionServiceConfig.model_validate(raw)
    native_corpus(config.corpus, create=True).close()
    asyncio.run(DeliveryOutbox(config.outbox, create=True).check_ready())
    died = invoke(tmp_path, config, "original_unknown")
    assert died.returncode == 74, died.stderr
    run_id = json.loads(next(config.directory.glob("*.json")).read_bytes())["run_id"]
    original = read_journal(cfg.journal, run_id)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    held = invoke(tmp_path, config, "hold")
    assert held.returncode == 0 and json.loads(held.stdout)["phase"] == "held", held.stderr
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    reserved = invoke(tmp_path, config, "reserve")
    assert reserved.returncode == 79, reserved.stderr
    receipt = authorization(read_journal(cfg.journal, run_id).rows[-1]).model_dump(mode="json")
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    pending = invoke(tmp_path, config, "hold")
    assert pending.returncode == 0 and json.loads(pending.stdout)["phase"] == "held", pending.stderr
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    died = invoke(tmp_path, config, "attempt_ack")
    assert died.returncode == 75, died.stderr
    after_attempt = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert after_attempt[:2] == before[:2] and after_attempt[2] == before[2] + 1
    assert after_attempt[3] == before[3]
    died = invoke(tmp_path, config, "output")
    assert died.returncode == 78, died.stderr
    after_output = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert after_output[:2] == before[:2] and after_output[2] == before[2] + 2
    assert after_output[3] == before[3]
    done = invoke(tmp_path, config, "finish")
    assert done.returncode == 0, done.stderr
    job = json.loads(done.stdout)
    assert job["phase"] == "completed" and job["corpus"] and job["delivery_id"]
    assert job["model_attempt"] == receipt and job["adoption_attempts"] == 2
    result = ResearchResultArchive.read(
        config.directory / (run_id + ".output"), max_bytes=config.command.max_result_bytes
    )
    assert result.harvest.ledger[: len(original.rows)] == original.rows
    assert result.harvest.receipt.judge_calls == sum(
        row.model_intent is not None for row in result.harvest.ledger
    )
    sealed = read_journal(cfg.journal, run_id)
    assert sealed.state == "complete" and len(sealed.uncertain_model_calls) == 1
    assert not unreconciled_model_sequences(sealed.rows)
    assert asyncio.run(DeliveryOutbox(config.outbox).state(job["delivery_id"])).status == "pending"
    final = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert (
        final[:3] == after_output[:3] and final[3] > after_output[3]
    )  # First corpus handoff encoding only.
