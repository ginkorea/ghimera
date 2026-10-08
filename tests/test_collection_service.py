"""Native command checkpoint and service wire witnesses; model replies are fixtures."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from pydantic import SecretStr

from ghimera.collection_service import CollectionService, CollectionServiceConfig
from ghimera.command import execute
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.delivery_types import DeliveryItem
from ghimera.directory_delivery import DirectoryDeliverySink
from ghimera.remote_delivery import RemoteDeliveryConfig, RemoteDeliverySink
from ghimera.research_types import ResearchRequest
from ghimera.service_command import CollectionHttpServer, ServiceCommandConfig
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import toml_lines
from tests.test_command_continuation import recipe, starting
from tests.test_delivery_outbox import destination, policy
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus as native_corpus
from tests.test_evidence_corpus import harvest
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def service_config(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, **extra
):
    cfg = recipe(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    return CollectionServiceConfig(
        schema="ghimera.collection-service/1",
        directory=tmp_path / "service",
        command=opts,
        max_jobs=8,
        max_active_jobs=1,
        max_manifest_bytes=10000,
        rounds_per_checkpoint=1,
        handoff_retry_seconds=0.02,
        max_handoff_attempts=3,
        shutdown_grace_seconds=0.1,
        **extra,
    )


async def until(service, run_id, phase):
    async with asyncio.timeout(10):
        while service.status(run_id).phase != phase:
            await asyncio.sleep(0.01)


def test_native_pause_restart_resume_preserves_archive_and_outbox(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    box_cfg = policy(tmp_path / "outbox")
    config = service_config(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, outbox=box_cfg
    )

    async def operation():
        box = DeliveryOutbox(box_cfg, create=True)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def native(options):
            calls.append(options.execution.operation)
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return await execute(options, source_resolver=ResolverFixture())

        service = CollectionService(config, executor=native, outbox=box)
        await service.start(create=True)
        request = ResearchRequest.model_validate_json(config.command.request_path.read_bytes())
        job = service.submit(request)
        await entered.wait()
        assert service.pause(job.run_id).phase == "pausing"
        release.set()
        await until(service, job.run_id, "paused")
        checkpoint = service.status(job.run_id).checkpoint
        assert checkpoint is not None
        await service.stop()
        reopened = CollectionService(config, executor=native)
        await reopened.start()
        assert reopened.status(job.run_id).checkpoint == checkpoint
        reopened.resume(job.run_id)
        await until(reopened, job.run_id, "completed")
        done = reopened.status(job.run_id)
        assert done.archive is not None and done.delivery_id is not None
        assert (await box.state(done.delivery_id)).status == "pending"
        assert calls[0] == "run" and all(call == "resume" for call in calls[1:])
        with pytest.raises(ValueError):
            reopened.resume(job.run_id)
        await reopened.stop()
        await reopened.start()
        assert reopened.status(job.run_id) == done
        await reopened.stop()

    asyncio.run(operation())


def test_interrupted_native_window_is_held_and_never_blindly_restarted(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config = service_config(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )

    async def operation():
        entered = asyncio.Event()
        calls = 0

        async def stalled(options):
            nonlocal calls
            calls += 1
            entered.set()
            await asyncio.Event().wait()

        service = CollectionService(config, executor=stalled)
        await service.start(create=True)
        job = service.submit(ResearchRequest(intent="port research"))
        await entered.wait()
        await service.stop()
        reopened = CollectionService(config, executor=stalled)
        await reopened.start()
        assert reopened.status(job.run_id).phase == "held"
        with pytest.raises(ValueError):
            reopened.resume(job.run_id)
        assert calls == 1
        await reopened.cancel(job.run_id)
        assert reopened.status(job.run_id).phase == "cancelled"
        await reopened.stop()

    asyncio.run(operation())


def test_startup_fences_two_owners_and_policy_drift(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config = service_config(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )

    async def no_work(options):
        raise AssertionError("no work expected")

    async def operation():
        first = CollectionService(config, executor=no_work)
        await first.start(create=True)
        second = CollectionService(config, executor=no_work)
        with pytest.raises(BlockingIOError):
            await second.start()
        job = first.submit(ResearchRequest(intent="port research"))
        first.pause(job.run_id)
        await first.stop()
        changed = config.model_copy(update={"max_jobs": 9})
        with pytest.raises(ValueError, match="policy"):
            await CollectionService(changed, executor=no_work).start()

    asyncio.run(operation())


def test_lost_enqueue_ack_reuses_exact_corpus_receipt_without_refetch_and_serves_queries(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    corpus_cfg = corpus_config(tmp_path, encoder_endpoint[0])
    box_cfg = policy(tmp_path / "outbox")
    config = service_config(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        corpus=corpus_cfg,
        outbox=box_cfg,
    ).model_copy(update={"max_manifest_bytes": 1000000})

    async def operation():
        class LostAck(DeliveryOutbox):
            calls = 0

            async def enqueue(self, result):
                state = await super().enqueue(result)
                self.calls += 1
                if self.calls == 1:
                    raise OSError("fixture_lost_queue_ack")
                return state

        box = LostAck(box_cfg, create=True)
        corpus = native_corpus(corpus_cfg, create=True)
        calls = []

        async def native(options):
            calls.append(options.execution.operation)
            return await execute(options, source_resolver=ResolverFixture())

        service = CollectionService(config, executor=native, corpus=corpus, outbox=box)
        await service.start(create=True)
        request = ResearchRequest.model_validate_json(config.command.request_path.read_bytes())
        job = service.submit(request)
        await until(service, job.run_id, "completed")
        completed = service.status(job.run_id)
        assert completed.handoff_attempts == 2 and completed.corpus is not None
        assert completed.corpus.added_documents == 1
        assert (await box.summary()).items == 1
        assert box.calls == 2 and calls.count("run") == 1
        query = await service.query("port infrastructure", top_k=1)
        assert query.hits and query.hits[0].passage.source_url.endswith("/plain")
        await service.stop()
        corpus.close()
        # Fresh factory opens native corpus/outbox; no runtime doubles are needed
        # for a completed job or its query interface.
        reopened = CollectionService(config)
        await reopened.start()
        assert reopened.status(job.run_id) == completed
        fresh = await reopened.query("port infrastructure", top_k=1)
        assert fresh.hits[0].passage.source_sha256 == query.hits[0].passage.source_sha256
        await reopened.stop()
        await reopened.start()
        assert reopened.status(job.run_id) == completed
        assert reopened.manifest()["collector"] is not None
        await reopened.stop()

    asyncio.run(operation())


async def wire(server, method, path, body=b"", credential="fixture-owner"):
    reader, writer = await asyncio.open_connection(*server.address)
    writer.write(
        (
            f"{method} {path} HTTP/1.1\r\nAuthorization: Bearer {credential}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode()
        + body
    )
    await writer.drain()
    header = await reader.readuntil(b"\r\n\r\n")
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return int(header.split(b" ")[1]), json.loads(response)


def test_real_http_authentication_health_controls_and_remote_durable_delivery(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config = service_config(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    dest_cfg = destination(tmp_path / "destination")
    http_cfg = ServiceCommandConfig(
        schema="ghimera.service-command/1",
        service=config,
        host="127.0.0.1",
        port=0,
        credential_environment_variable="GHIMERA_OWNER",
        max_request_bytes=5000000,
        max_header_bytes=10000,
        request_timeout_seconds=10,
        max_connections=8,
        delivery_destination=dest_cfg,
    )

    async def operation():
        async def stalled(options):
            await asyncio.Event().wait()

        service = CollectionService(config, executor=stalled)
        await service.start(create=True)
        sink = DirectoryDeliverySink(dest_cfg, create=True)
        server = CollectionHttpServer(
            http_cfg, service, credential=SecretStr("fixture-owner"), destination=sink
        )
        await server.start()
        try:
            assert (await wire(server, "GET", "/health", credential="wrong"))[0] == 401
            assert (await wire(server, "GET", "/health"))[1]["phase"] == "running"
            code, job = await wire(server, "POST", "/runs", b'{"intent":"port research"}')
            assert code == 202
            run_id = job["run_id"]
            assert (await wire(server, "GET", "/runs/" + run_id))[0] == 200
            assert (await wire(server, "POST", "/runs/" + run_id + "/cancel"))[1][
                "phase"
            ] == "cancelled"
            remote = RemoteDeliverySink(
                RemoteDeliveryConfig(
                    schema="ghimera.remote-delivery/1",
                    target=dest_cfg.target,
                    endpoint=f"http://127.0.0.1:{server.address[1]}/deliveries",
                    timeout_seconds=5,
                    max_response_bytes=10000,
                    max_request_bytes=5000000,
                    max_header_bytes=10000,
                    approved_addresses=("127.0.0.1",),
                    allow_plaintext=True,
                    allow_plaintext_credentials=True,
                    authorization="bearer",
                    credential_environment_variable="GHIMERA_OWNER",
                ),
                credential=SecretStr("fixture-owner"),
            )
            box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
            material = await harvest(("zh", "港口研究"))
            queued = await box.enqueue(material)
            await remote.deliver(
                DeliveryItem(
                    schema="ghimera.delivery-item/1", target=remote.target, result=material
                )
            )
            acknowledged = await box.dispatch(remote)
            assert acknowledged[0].status == "acknowledged"
            assert await sink.result(queued.delivery_id) == material
            await box.prune_acknowledged(queued.delivery_id, remote)
            assert not (await box.state(queued.delivery_id)).payload_retained
            assert (await box.enqueue(material)).status == "acknowledged"
        finally:
            await server.stop()
            await service.stop()

    asyncio.run(operation())


def test_actual_native_service_command_start_health_sigterm_and_restart(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config = service_config(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    http = ServiceCommandConfig(
        schema="ghimera.service-command/1",
        service=config,
        host="127.0.0.1",
        port=0,
        credential_environment_variable="GHIMERA_TEST_OWNER",
        max_request_bytes=1000000,
        max_header_bytes=10000,
        request_timeout_seconds=5,
        max_connections=4,
    )
    path = tmp_path / "service-command.toml"
    path.write_text("\n".join(toml_lines(http.model_dump(mode="json", exclude_none=True))) + "\n")
    for creation in (True, False):
        with (
            (tmp_path / ("report-create" if creation else "report-restart")).open("w+") as report,
            (tmp_path / ("errors-create" if creation else "errors-restart")).open("w+") as errors,
        ):
            argv = [
                sys.executable,
                "-m",
                "ghimera.service_command",
                "--config",
                str(path),
                "--max-config-bytes",
                "1000000",
            ]
            if creation:
                argv.append("--create-service-store")
            child = subprocess.Popen(
                argv,
                stdout=report,
                stderr=errors,
                env=dict(
                    os.environ,
                    GHIMERA_TEST_OWNER="fixture-owner",
                    PYTHONPATH=str(Path.cwd() / "src"),
                ),
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and child.poll() is None:
                    report.seek(0)
                    line = report.readline()
                    if line:
                        ready = json.loads(line)
                        break
                    time.sleep(0.02)
                else:
                    raise AssertionError("native service did not report readiness")
                endpoint = f"http://{ready['address'][0]}:{ready['address'][1]}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for route in ("/health", "/manifest"):
                    request = urllib.request.Request(
                        endpoint + route, headers={"Authorization": "Bearer fixture-owner"}
                    )
                    with opener.open(request, timeout=5) as response:
                        assert response.status == 200
                        assert "fixture-owner" not in response.read().decode()
                child.send_signal(signal.SIGTERM)
                assert child.wait(timeout=5) == 0
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
            errors.seek(0)
            assert errors.read() == ""
