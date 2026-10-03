"""Shared bounded scheduling for complete pipeline chunk lifecycles."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class ChunkFailure:
    index: int
    error: Exception


@dataclass(frozen=True)
class ChunkSuccess:
    index: int
    value: object


class ChunkBatchError(RuntimeError):
    """Combined failure retaining all indexed sibling outcomes."""

    def __init__(self, label: str, failures: Sequence[ChunkFailure],
                 successes: Sequence[ChunkSuccess]) -> None:
        self.label = label
        self.chunk_failures = tuple(failures)
        self.chunk_successes = tuple(successes)
        detail = "; ".join(
            f"chunk {failure.index}: {failure.error}" for failure in failures
        )
        super().__init__(f"{label}: {detail}")


@dataclass(frozen=True)
class _Succeeded:
    value: object


@dataclass(frozen=True)
class _Failed:
    error: Exception


async def map_chunks(
    items: Sequence[T],
    worker: Callable[[int, T], Awaitable[R]],
    concurrency: int,
    *,
    error_label: str | None = None,
) -> list[R]:
    """Run indexed chunks with bounded concurrency and return source order.

    A normal chunk exception is recorded while the worker continues through
    the queue. On failure, all indexed failures and successful sibling results
    are retained on the raised error. By default the original exception with
    the highest chunk_failure_priority is re-raised (ties use lowest index),
    preserving typed caller contracts. error_label raises a combined
    RuntimeError subclass with the same evidence. Cancellation stops and awaits
    all scheduler tasks.
    """
    if not items:
        return []

    missing = object()
    results: list[_Succeeded | _Failed | object] = [missing] * len(items)
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
                results[index] = _Succeeded(await worker(index, items[index]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                results[index] = _Failed(exc)

    worker_count = min(len(items), max(1, int(concurrency)))
    tasks = [asyncio.create_task(run_worker()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    failures = [ChunkFailure(index, result.error)
                for index, result in enumerate(results) if isinstance(result, _Failed)]
    if failures:
        successes = tuple(ChunkSuccess(index, result.value)
                          for index, result in enumerate(results)
                          if isinstance(result, _Succeeded))
        if error_label is None:
            def priority(failure: ChunkFailure) -> tuple[int, int]:
                value = getattr(failure.error, "chunk_failure_priority", 0)
                return (value if isinstance(value, int) else 0, -failure.index)
            error = max(failures, key=priority).error
            try:
                error.chunk_batch_failures = tuple(failures)
                error.chunk_batch_successes = successes
                if hasattr(error, "add_note"):
                    indexes = ", ".join(str(item.index) for item in failures)
                    error.add_note(f"Independent chunk failures after drain: {indexes}")
            except (AttributeError, TypeError):
                pass
            raise error
        raise ChunkBatchError(error_label, failures, successes) from failures[0].error
    if any(result is missing for result in results):
        raise RuntimeError("chunk scheduler left a chunk unresolved")
    return [result.value for result in results if isinstance(result, _Succeeded)]  # type: ignore[misc]
