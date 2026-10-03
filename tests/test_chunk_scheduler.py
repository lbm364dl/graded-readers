import asyncio

import pytest

from pipeline.chunk_scheduler import map_chunks


@pytest.mark.asyncio
async def test_map_chunks_bounds_workers_preserves_order_and_advances_lifecycle():
    active = 0
    maximum_active = 0
    events = []

    async def lifecycle(index, value):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        events.append(("generate", index))
        if index == 0:
            await asyncio.sleep(0.01)
        events.append(("review", index))
        active -= 1
        return value.upper()

    result = await map_chunks(["a", "b", "c", "d"], lifecycle, 2)
    assert result == ["A", "B", "C", "D"]
    assert maximum_active == 2
    assert events.index(("review", 1)) < events.index(("generate", 2))


@pytest.mark.asyncio
async def test_map_chunks_drains_failures_and_raises_combined_indices():
    visited = []

    async def worker(index, item):
        visited.append(index)
        if index in {1, 4}:
            raise ValueError(f"failed {index}")
        return item

    with pytest.raises(RuntimeError) as failure:
        await map_chunks(list("abcdef"), worker, 2, error_label="chunk work failed")
    assert sorted(visited) == list(range(6))
    assert "chunk 1: failed 1" in str(failure.value)
    assert "chunk 4: failed 4" in str(failure.value)


@pytest.mark.asyncio
async def test_map_chunks_default_preserves_original_error_type_after_drain():
    visited = []

    class PlannedNameError(ValueError):
        pass

    async def worker(index, item):
        visited.append(index)
        if index == 0:
            raise PlannedNameError("missing planned name")
        await asyncio.sleep(0)
        return item

    with pytest.raises(PlannedNameError, match="missing planned name"):
        await map_chunks(["a", "b", "c"], worker, 2)
    assert sorted(visited) == [0, 1, 2]


@pytest.mark.asyncio
async def test_map_chunks_cancellation_awaits_all_running_workers():
    started = asyncio.Event()
    cancelled = []
    active = 0

    async def worker(index, item):
        nonlocal active
        active += 1
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(index)
            await asyncio.sleep(0)
            raise
        finally:
            active -= 1

    task = asyncio.create_task(map_chunks(list("abcd"), worker, 2))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(cancelled) == 2
    assert active == 0
