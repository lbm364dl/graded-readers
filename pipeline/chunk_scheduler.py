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
    active_indices: Sequence[int] | None = None,
    retained_worker: Callable[[int, T], Awaitable[R]] | None = None,
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
    admitted = None if active_indices is None else validate_active_indices(active_indices, len(items))
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
                if admitted is not None and index not in admitted:
                    if retained_worker is None:
                        raise ChunkDeferred('Chunk is outside this bounded admission')
                    results[index] = _Succeeded(await retained_worker(index, items[index]))
                else:
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
        if admitted is not None:
            error = ChunkAdmissionIncomplete('Bounded annotation batch remains incomplete', failures, successes)
            error.deferred_chunks = tuple(row for row in failures if isinstance(row.error,ChunkDeferred))
            error.attempted_failures = tuple(row for row in failures if not isinstance(row.error,ChunkDeferred))
            error.chunk_batch_failures = tuple(failures)
            error.chunk_batch_successes = successes
            raise error
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

class ChunkDeferred(RuntimeError):
    """Unselected occurrence has no currently eligible independent proof."""

class ChunkAdmissionIncomplete(ChunkBatchError):
    """Controlled partial stop, never a prose regeneration instruction."""
    def __init__(self, label, failures, successes):
        super().__init__(label, failures, successes)
        self.chunk_batch_failures = tuple(failures)
        self.chunk_batch_successes = tuple(successes)
        self.deferred_chunks = tuple(row for row in failures if isinstance(row.error, ChunkDeferred))
        self.attempted_failures = tuple(row for row in failures if not isinstance(row.error, ChunkDeferred))

def validate_active_indices(indices, count):
    rows=list(indices)
    if any(type(i) is not int or not 0 <= i < count for i in rows) or len(set(rows))!=len(rows):
        raise ValueError('Annotation admission indices must be unique existing zero-based positions')
    return frozenset(rows)

def admission_indices(harness, count, *, items=None):
    rows=getattr(harness,'annotation_active_indices',None)
    if rows is None:rows=getattr(getattr(harness,'args',None),'annotation_active_indices',None)
    if items is not None:
        packet=getattr(harness,'annotation_holds',None)
        if packet is None:packet=getattr(getattr(harness,'args',None),'annotation_holds',None)
        if packet is not None:
            import json
            from pathlib import Path
            if isinstance(packet,(str,Path)):
                from pipeline.worker_paths import checked_regular_file
                packet=json.loads(checked_regular_file(Path(packet).absolute()).read_text())
            if not isinstance(packet,dict) or type(packet.get('version')) is not int or packet.get('version')!=1 or not isinstance(packet.get('holds'),list):raise ValueError('Invalid investigation holds')
            for row in packet['holds']:
                index=row.get('chunk_index') if isinstance(row,dict) else None
                if type(index) is not int or not 1<=index<=count:raise ValueError('Hold index outside immutable chapter')
                investigation_hold(harness,index-1,items[index-1])
    if rows is None:return None
    if any(type(i) is not int or i < 1 for i in rows):raise ValueError('Annotation active chunk numbers are one-based positive integers')
    return validate_active_indices([i-1 for i in rows],count)


def investigation_hold(harness, index, text, context=None):
    """Authenticate explicit coordinator holds; historical verdicts stay untouched."""
    import hashlib,json
    from pipeline.annotation_adjudication import digest as source_digest
    from pathlib import Path
    packet=getattr(harness,'annotation_holds',None)
    if packet is None:packet=getattr(getattr(harness,'args',None),'annotation_holds',None)
    if packet is None:return None
    if isinstance(packet,(str,Path)):
        from pipeline.worker_paths import checked_regular_file
        packet=json.loads(checked_regular_file(Path(packet).absolute()).read_text())
    if not isinstance(packet,dict) or type(packet.get('version')) is not int or packet['version']!=1 or not isinstance(packet.get('holds'),list):
        raise ValueError('Invalid annotation investigation holds')
    rows=packet['holds'];numbers=[row.get('chunk_index') for row in rows if isinstance(row,dict)]
    if len(numbers)!=len(rows) or any(type(i) is not int or i<1 for i in numbers) or len(set(numbers))!=len(numbers):raise ValueError('Invalid or duplicated annotation hold positions')
    position=getattr(harness,'_annotation_source_positions',{}).get(index)
    if position is None and context and isinstance(context.get('chapter_text'),str):
        parent=context['chapter_text'];start=context.get('source_start')
        if type(start) is not int or start<0 or parent[start:start+len(text)]!=text:raise ValueError('Hold source position mismatch')
        position={'chunk_index':index,'source_start':start,'parent_text_digest':source_digest(parent),'source_text_digest':source_digest(text)}
    row=next((row for row in rows if row['chunk_index']==index+1),None)
    if row is None:return None
    if position is None:raise ValueError('Hold requires authoritative current source position')
    if type(row.get('source_start')) is not int or row['source_start']<0:raise ValueError('Invalid hold source start')
    for key in ('source_start','parent_text_digest','source_text_digest'):
        if row.get(key)!=position.get(key):raise ValueError('Investigation hold source binding changed')
    if row['source_text_digest']!=source_digest(text) or not isinstance(row.get('reason'),str) or not row['reason'].strip():raise ValueError('Invalid investigation hold source or reason')
    root=Path(__file__).resolve().parents[1]
    evidence=row.get('evidence')
    if not isinstance(evidence,list) or not evidence:raise ValueError('Investigation hold requires exact evidence')
    for item in evidence:
        rel=item.get('path') if isinstance(item,dict) else None
        if not isinstance(rel,str) or Path(rel).is_absolute() or '..' in Path(rel).parts:raise ValueError('Invalid hold evidence path')
        from pipeline.worker_paths import checked_regular_file
        target=checked_regular_file(root/rel)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=item.get('sha256'):raise ValueError('Investigation hold evidence changed')
    return dict(row)

class ChunkJobLimitReached(RuntimeError):
    """The exact admitted batch has exhausted its persistent new-job budget."""

from contextlib import contextmanager

@contextmanager
def bounded_chunk_jobs(harness, *, phase="annotation", job_limit=None):
    """Cache first; persist authenticated request reservations before paid work."""
    from pathlib import Path
    import hashlib,json,fcntl
    active=getattr(harness,'annotation_active_indices',None)
    if active is None:active=getattr(getattr(harness,'args',None),'annotation_active_indices',None)
    if active is None:
        yield
        return
    if phase not in ('annotation','discovery'):raise ValueError('Unknown admitted job phase')
    limit=job_limit if job_limit is not None else getattr(harness,'annotation_admission_job_limit',None)
    if limit is None:limit=getattr(getattr(harness,'args',None),'annotation_admission_job_limit',6)
    if type(limit) is not int or limit<1:raise ValueError('Annotation admission job limit must be positive')
    positions=getattr(harness,'_annotation_source_positions',{})
    if not positions:raise ValueError('Bounded job budget requires full immutable source positions')
    scope={'phase':phase,'active_chunks':sorted(active),'source_positions':{str(k):v for k,v in positions.items()}}
    encode=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    digest=lambda value:hashlib.sha256(encode(value).encode()).hexdigest()
    from pipeline.worker_paths import checked_directory, checked_regular_file, atomic_write_managed
    directory=checked_directory(Path(harness.run_dir).absolute()/'annotation-admission',create=True)
    path=directory/(digest(scope)+'.json');lock=directory/(digest(scope)+'.lock')
    if path.is_symlink() or lock.is_symlink():raise ValueError('Admission ledger cannot be a symlink')
    if lock.exists():checked_regular_file(lock)
    original=harness.runner
    from pipeline.annotation_review_guidance import COMPLETE_STAGE_INSTRUCTION_POLICY_VERSION
    contract={'version':1,'complete_stage_instruction_policy_version':COMPLETE_STAGE_INSTRUCTION_POLICY_VERSION,'phase':phase,'target_level':getattr(harness,'level',getattr(getattr(harness,'args',None),'target_level',None)),
              'model':original.model,'effort':getattr(original,'benchmark_effort',None) or 'low'}
    class Runner:
        def __getattr__(self,name):return getattr(original,name)
        async def call(self,job,prompt,schema,effort=None,**options):
            from pipeline.agent_harness import CachedCallUnavailable
            request={'job':job,'prompt':prompt,'schema':Path(schema).read_text(),
                     'workspace_context':options.get('workspace_context'),
                     'model':original.model,'effort':getattr(original,'benchmark_effort',None) or 'low',
                     'tool_profile':options.get('tool_profile')}
            if request['model']!='gpt-6-luna' or request['effort']!='low':raise ValueError('Admission worker policy changed')
            try:
                return await original.call(job,prompt,schema,effort,cache_only=True,**{k:v for k,v in options.items() if k not in ('cache_only','refresh')})
            except CachedCallUnavailable:
                if options.get('cache_only'):raise
            with lock.open('a+') as handle:
                fcntl.flock(handle,fcntl.LOCK_EX)
                ledger=json.loads(checked_regular_file(path).read_text()) if path.exists() else {'version':2,'scope':scope,'contract':contract,'limit':limit,'requests':[]}
                # JSON stringifies integer map keys; compare canonical encoded scope.
                if type(ledger.get('version')) is not int or ledger['version']!=2 or ledger.get('contract')!=contract or encode(ledger['scope'])!=encode(scope) or ledger['limit']!=limit:raise ValueError('Admission budget context changed')
                key=digest(request)
                previous=next((row for row in ledger['requests'] if row['job']==job),None)
                if previous is not None:
                    if previous['request_digest']!=key:raise ValueError('Admission job request changed')
                    raise ChunkJobLimitReached('Previously reserved uncached job requires explicit investigation; no automatic paid retry')
                if len(ledger['requests'])>=limit:raise ChunkJobLimitReached('Persistent admitted batch job limit reached')
                ledger['requests'].append({'job':job,'request_digest':key,'request':request})
                atomic_write_managed(directory,path.name,(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n').encode())
            return await original.call(job,prompt,schema,effort,**options)
    harness.runner=Runner()
    try:yield
    finally:harness.runner=original

def persist_admission_outcomes(harness,error):
    """Preserve indexed callback evidence; this file grants no cache eligibility."""
    from pathlib import Path
    import json,hashlib
    data={'version':1,'status':'incomplete','published':False,
          'source_positions':getattr(harness,'_annotation_source_positions',{}),
          'successful_callbacks':[{'index':row.index,'value':row.value} for row in error.chunk_batch_successes],
          'unresolved':[{'index':row.index,'deferred':isinstance(row.error,ChunkDeferred),
                         'error_type':type(row.error).__name__,'diagnostic':str(row.error)} for row in error.chunk_batch_failures]}
    encoded=json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    directory=Path(harness.run_dir)/'annotation-admission'
    if directory.is_symlink():raise ValueError('Admission checkpoint directory is a symlink')
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/('checkpoint-'+hashlib.sha256(encoded.encode()).hexdigest()+'.json')
    if path.is_symlink():raise ValueError('Admission checkpoint cannot be a symlink')
    path.write_text(encoded)
    return path
