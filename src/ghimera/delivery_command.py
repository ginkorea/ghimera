"""Unattended local delivery command; no implicit collector or remote destination."""

import argparse
import asyncio
import signal
import sqlite3
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NoReturn

from pydantic import Field, model_validator

from ghimera.delivery_config import (
    DeliveryOutboxConfig,
    DeliveryWorkerConfig,
    DirectoryDeliveryConfig,
    Seconds,
)
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.delivery_types import DeliveryWorkerStatus
from ghimera.delivery_worker import DeliveryWorker
from ghimera.directory_delivery import DirectoryDeliverySink
from ghimera.models import Record
from ghimera.result_archive import bounded_file


class DeliveryCommandConfig(Record):
    schema_version: Literal["ghimera.delivery-command/1"] = Field(alias="schema")
    outbox: DeliveryOutboxConfig
    destination: DirectoryDeliveryConfig
    worker: DeliveryWorkerConfig
    status_seconds: Seconds

    @model_validator(mode="after")
    def bound_destination(self) -> "DeliveryCommandConfig":
        if (
            self.outbox.target != self.destination.target
            or self.outbox.directory == self.destination.directory
            or self.worker.max_prunes_per_cycle > self.outbox.max_items
        ):
            raise ValueError("delivery command needs distinct stores and one exact destination")
        return self


async def execute(
    config: DeliveryCommandConfig,
    *,
    emit: Callable[[DeliveryWorkerStatus], None],
) -> DeliveryWorkerStatus:
    """Open existing stores, handle graceful termination and emit non-secret health."""
    config = DeliveryCommandConfig.model_validate(config.model_dump())
    # Opening does not create/overwrite an absent queue or destination. Their
    # initial creation is an explicit, separately owned library operation.
    worker = DeliveryWorker(
        config.worker,
        outbox=DeliveryOutbox(config.outbox),
        sink=DirectoryDeliverySink(config.destination),
    )
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    stop_requested = asyncio.Event()

    async def report() -> None:
        while True:
            emit(worker.status)
            await asyncio.sleep(config.status_seconds)

    async def stop_on_signal() -> None:
        await stop_requested.wait()
        await worker.stop()  # Apply the grace deadline even during a long active cycle.

    try:
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, stop_requested.set)
            installed.append(name)
        async with worker:
            async with asyncio.TaskGroup() as group:
                reporting = group.create_task(report())
                stopping = group.create_task(stop_on_signal())
                try:
                    await worker.wait()
                finally:
                    reporting.cancel()
                    if not stop_requested.is_set():
                        stopping.cancel()
    finally:
        for name in installed:
            loop.remove_signal_handler(name)
    emit(worker.status)
    return worker.status


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "delivery_arguments_invalid\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Deliver an existing Ghimera outbox until SIGTERM or SIGINT.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--max-config-bytes", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        config = DeliveryCommandConfig.model_validate(
            tomllib.loads(bounded_file(args.config, args.max_config_bytes).decode())
        )
        asyncio.run(execute(config, emit=_emit))
    except ValueError:
        sys.stderr.write("delivery_input_invalid\n")
        return 2
    except (OSError, sqlite3.Error):
        sys.stderr.write("delivery_io_failed\n")
        return 2
    except ExceptionGroup:
        # Never print source/destination exception details as service diagnostics.
        sys.stderr.write("delivery_worker_failed\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("delivery_interrupted\n")
        return 130
    return 0


def _emit(state: DeliveryWorkerStatus) -> None:
    sys.stdout.write(state.model_dump_json() + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
