"""Shared bounded scheduling for complete pipeline chunk lifecycles."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def map_chunks(
    items: Sequence[T],
    worker: Callable[[int, T], Awaitable[R]],
    concurrency: int,
    *,
    error_label: str | None = None,
) -> list[R]:
    """Run indexed chunks with bounded concurrency and return source order.

    A normal chunk exception is recorded while the worker continues through
    the queue. Once all independent chunks finish, the first exception is
    re-raised unchanged by default. ``error_label`` opts into a combined
    RuntimeError naming every failed index. Cancellation stops and awaits all
    scheduler tasks before propagating to avoid leaving chunk work behind.
    """
    if not items:
        return []

    missing = object()
    results: list[R | Exception | object] = [missing] * len(items)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(len(items)):
        queue.put_nowait(index)

    async def run_worker() -> None:
        while True:
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                results[index] = await worker(index, items[index])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                results[index] = exc

    worker_count = min(len(items), max(1, int(concurrency)))
    tasks = [asyncio.create_task(run_worker()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    failures = [
        (index, result)
        for index, result in enumerate(results)
        if isinstance(result, Exception)
    ]
    if failures:
        if error_label is None:
            raise failures[0][1]
        detail = "; ".join(
            f"chunk {index}: {error}" for index, error in failures
        )
        raise RuntimeError(f"{error_label}: {detail}")
    if any(result is missing for result in results):
        raise RuntimeError("chunk scheduler left a chunk unresolved")
    return [result for result in results]  # type: ignore[misc]
