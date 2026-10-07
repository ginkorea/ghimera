"""Owned blocking work drains before its caller releases state or descriptors."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

Result = TypeVar("Result")


async def off_loop(function: Callable[[], Result]) -> Result:
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise
