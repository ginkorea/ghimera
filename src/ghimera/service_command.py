"""Authenticated loopback HTTP service and explicit native startup recipe."""

import argparse
import asyncio
import hmac
import ipaddress
import json
import os
import signal
import sys
import tomllib
from pathlib import Path
from typing import Annotated, Literal, NoReturn

from pydantic import Field, SecretStr, model_validator

from ghimera.collection_service import CollectionService, CollectionServiceConfig, ManualBoundary
from ghimera.delivery_config import DirectoryDeliveryConfig
from ghimera.delivery_types import DeliveryItem, Positive
from ghimera.directory_delivery import DirectoryDeliverySink
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelReconciliationDecision,
)
from ghimera.models import Record
from ghimera.research_types import ResearchRequest
from ghimera.result_archive import bounded_file


class ServiceCommandConfig(Record):
    schema_version: Literal["ghimera.service-command/1"] = Field(alias="schema")
    service: CollectionServiceConfig
    host: str
    port: Annotated[int, Field(strict=True, ge=0, le=65535)]
    credential_environment_variable: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    max_request_bytes: Positive
    max_header_bytes: Positive
    request_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_connections: Positive
    delivery_destination: DirectoryDeliveryConfig | None = None

    @model_validator(mode="after")
    def loopback(self) -> "ServiceCommandConfig":
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError(
                "HTTP service binds loopback; remote access requires an owned TLS proxy"
            )
        return self


class CorpusSearchRequest(Record):
    text: Annotated[str, Field(min_length=1)]
    top_k: Positive
    languages: tuple[str, ...] = ()


class ModelObservationRequest(Record):
    schema_version: Literal["ghimera.model-observation-request/1"] = Field(alias="schema")
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RecoveryBoundaryRequest(Record):
    """Explicit manual selector; not a new permission or native acknowledgment."""

    schema_version: Literal[
        "ghimera.service-recovery-request/1", "ghimera.service-recovery-request/2"
    ] = Field(alias="schema")
    boundary: ManualBoundary
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def versioned(self) -> "RecoveryBoundaryRequest":
        if self.schema_version == "ghimera.service-recovery-request/2":
            if self.boundary != "source_processing" or self.snapshot_sha256 is None:
                raise ValueError("processing selector requires its exact original cursor")
        elif self.boundary == "source_processing" or "snapshot_sha256" in self.model_fields_set:
            raise ValueError("original selector /1 forbids processing and cursor fields")
        return self

    @classmethod
    def read(cls, body: bytes) -> "RecoveryBoundaryRequest":
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate recovery selector field")
                result[key] = value
            return result

        # json is the untrusted dynamic boundary; the closed owning record
        # narrows its result before any service state or native admission.
        return cls.model_validate(json.loads(body, object_pairs_hook=unique))


class CollectionHttpServer:
    """Bounded single-request HTTP/1.1 connections; all routes require one owner bearer.

    No public bind, CORS, redirects, access logs or request-selected filesystem
    paths. An operator-owned TLS reverse proxy may expose this private endpoint.
    """

    def __init__(
        self,
        config: ServiceCommandConfig,
        service: CollectionService,
        *,
        credential: SecretStr,
        destination: DirectoryDeliverySink | None = None,
    ) -> None:
        self.config = ServiceCommandConfig.model_validate(config.model_dump())
        self.service = service
        if self.config.service != service.config:
            raise ValueError("HTTP service requires its exact configured lifecycle owner")
        value = credential.get_secret_value()
        if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError("HTTP bearer is absent or invalid")
        self._credential, self._destination = credential, destination
        if (destination.policy if destination else None) != config.delivery_destination:
            raise ValueError("HTTP destination requires exact configured storage")
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None or not self._server.sockets:
            raise ValueError("server has not started")
        address = self._server.sockets[0].getsockname()
        return str(address[0]), int(address[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._accept, self.config.host, self.config.port, limit=self.config.max_header_bytes
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in self._connections:
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is None:
            writer.close()
            return
        if len(self._connections) >= self.config.max_connections:
            writer.close()
            await writer.wait_closed()
            return
        self._connections.add(task)
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                await self._handle(reader, writer)
        except (
            ValueError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            self._connections.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _reply(self, writer: asyncio.StreamWriter, code: int, data: bytes) -> None:
        writer.write(
            (
                f"HTTP/1.1 {code} Response\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n"
            ).encode()
            + data
        )
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        if len(head) > self.config.max_header_bytes:
            raise ValueError("header exceeds allowance")
        lines = head.decode("ascii").split("\r\n")
        method, path, version = lines[0].split(" ")
        if version != "HTTP/1.1":
            raise ValueError("HTTP version unsupported")
        headers: dict[str, str] = {}
        for line in lines[1:-2]:
            name, value = line.split(":", 1)
            name = name.lower()
            if name in headers:
                raise ValueError("duplicate header")
            headers[name] = value.strip()
        supplied = headers.get("authorization", "")
        if not hmac.compare_digest(supplied, "Bearer " + self._credential.get_secret_value()):
            await self._reply(writer, 401, b'{"error":"unauthorized"}')
            return
        if "transfer-encoding" in headers:
            raise ValueError("transfer encoding unsupported")
        length = int(headers.get("content-length", "0"))
        if not 0 <= length <= self.config.max_request_bytes:
            await self._reply(writer, 413, b'{"error":"request_too_large"}')
            return
        body = await reader.readexactly(length)
        try:
            code, result = await self._dispatch(method, path, body)
        except ValueError:
            code, result = 409, b'{"error":"operation_refused"}'
        except OSError:
            code, result = 503, b'{"error":"storage_unavailable"}'
        except Exception:
            code, result = 503, b'{"error":"dependency_unavailable"}'
        await self._reply(writer, code, result)

    async def _dispatch(self, method: str, path: str, body: bytes) -> tuple[int, bytes]:
        if method == "GET" and path == "/health":
            return 200, json.dumps(self.service.health()).encode()
        if method == "GET" and path == "/manifest":
            return 200, json.dumps(
                dict(self.service.manifest(), api=self.config.model_dump(mode="json"))
            ).encode()
        if method == "POST" and path == "/runs":
            return 202, self.service.submit(
                ResearchRequest.model_validate_json(body)
            ).model_dump_json().encode()
        parts = path.split("/")
        if len(parts) in {3, 4} and parts[1] == "runs":
            run_id = parts[2]
            if method == "GET" and len(parts) == 3:
                return 200, self.service.status(run_id).model_dump_json().encode()
            if method == "GET" and len(parts) == 4 and parts[3] == "model-attempts":
                return 200, json.dumps(
                    [
                        receipt.model_dump(mode="json")
                        for receipt in self.service.model_attempt_history(run_id)
                    ]
                ).encode()
            if method == "POST" and len(parts) == 4 and body:
                if parts[3] == "observe-model-unknown":
                    # A single digest, not an untyped configuration or request replacement.
                    request = ModelObservationRequest.model_validate_json(body)
                    observed = await self.service.observe_model_unknown(
                        run_id, snapshot_sha256=request.snapshot_sha256
                    )
                    return 200, observed.model_dump_json().encode()
                if parts[3] == "reconcile-model":
                    receipt = await self.service.reconcile_model(
                        run_id, ModelReconciliationDecision.model_validate_json(body)
                    )
                    return 200, receipt.model_dump_json().encode()
                if parts[3] == "recover":
                    if (
                        self.service.config.recovery is not None
                        and self.service.config.recovery.schema_version
                        in {"ghimera.service-recovery/6", "ghimera.service-recovery/7"}
                    ):
                        selector = RecoveryBoundaryRequest.read(body)
                        processing = (
                            self.service.config.recovery.schema_version
                            == "ghimera.service-recovery/7"
                        )
                        if selector.schema_version != (
                            "ghimera.service-recovery-request/2"
                            if processing
                            else "ghimera.service-recovery-request/1"
                        ):
                            raise ValueError("selector differs from exact configured profile")
                        job = (
                            await self.service.recover(
                                run_id,
                                snapshot_sha256=selector.snapshot_sha256,
                            )
                            if processing
                            else await self.service.recover(run_id, boundary=selector.boundary)
                        )
                        return (
                            202
                            if job.phase == "recovering"
                            or processing
                            and job.phase == "handoff_pending"
                            else 409
                        ), job.model_dump_json().encode()
                    job = await self.service.recover(
                        run_id, attempt=ModelAttemptAuthorization.model_validate_json(body)
                    )
                    return (
                        202 if job.phase == "recovering" else 409
                    ), job.model_dump_json().encode()
            if method == "POST" and len(parts) == 4 and not body:
                action = parts[3]
                if action == "pause":
                    return 202, self.service.pause(run_id).model_dump_json().encode()
                if action == "resume":
                    return 202, self.service.resume(run_id).model_dump_json().encode()
                if action == "recover":
                    if (
                        self.service.config.recovery is not None
                        and self.service.config.recovery.schema_version
                        == "ghimera.service-recovery/7"
                    ):
                        job = await self.service.recover(run_id, archive_only=True)
                    else:
                        job = await self.service.recover(run_id)
                    return (
                        202 if job.phase in {"recovering", "handoff_pending"} else 409
                    ), job.model_dump_json().encode()
                if action == "cancel":
                    return 200, (await self.service.cancel(run_id)).model_dump_json().encode()
        if len(parts) == 3 and parts[1] == "deliveries" and self._destination is not None:
            identity = parts[2]
            if method == "GET":
                ack = await self._destination.lookup(identity)
                return (
                    (404, b'{"error":"absent"}')
                    if ack is None
                    else (200, ack.model_dump_json().encode())
                )
            if method == "PUT":
                item = DeliveryItem.model_validate_json(body)
                if item.identity != identity:
                    raise ValueError("remote delivery identity differs from payload")
                ack = await self._destination.deliver(item)
                return 200, ack.model_dump_json().encode()
        if method == "POST" and path == "/corpus/query":
            query = CorpusSearchRequest.model_validate_json(body)
            result = await self.service.query(
                query.text, top_k=query.top_k, languages=query.languages
            )
            return 200, result.model_dump_json().encode()
        return 404, b'{"error":"unknown_route"}'


async def serve(config: ServiceCommandConfig, *, create: bool) -> None:
    value = os.environ.get(config.credential_environment_variable)
    if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("service credential is absent or invalid")
    service = CollectionService(config.service)
    destination = (
        DirectoryDeliverySink(config.delivery_destination) if config.delivery_destination else None
    )
    server = CollectionHttpServer(
        config, service, credential=SecretStr(value), destination=destination
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    try:
        await service.start(create=create)
        await server.start()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, stop.set)
            installed.append(name)
        sys.stdout.write(json.dumps(dict(service.health(), address=server.address)) + "\n")
        sys.stdout.flush()
        await stop.wait()
    finally:
        for name in installed:
            loop.remove_signal_handler(name)
        await server.stop()
        await service.stop()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "service_arguments_invalid\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Run the configured private Ghimera collection API.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--max-config-bytes", required=True, type=int)
    parser.add_argument("--create-service-store", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = ServiceCommandConfig.model_validate(
            tomllib.loads(bounded_file(args.config, args.max_config_bytes).decode())
        )
        asyncio.run(serve(config, create=args.create_service_store))
    except (ValueError, OSError):
        sys.stderr.write("service_startup_refused\n")
        return 2
    except Exception:
        sys.stderr.write("service_startup_failed\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
