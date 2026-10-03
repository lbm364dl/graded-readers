"""Per-attempt writable runtime for tool-enabled content workers.

The coordinator retains authority over run records and repository source.
Codex's existing authentication/configuration remain readable in their normal
location; only its documented runtime databases and logs move into scratch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time

from pipeline.worker_paths import checked_directory, checked_regular_file


def safe_job_directory(run_dir: Path, job: str) -> Path:
    if (not job or Path(job).is_absolute() or '\\' in job
            or any(part in {'', '.', '..'} for part in job.split('/'))):
        raise ValueError('Worker job must be a safe relative path')
    directory = checked_directory(run_dir.absolute() / 'agents' / job, create=True)
    for path in directory.iterdir():
        if path.name in {'workspace', 'runtime'}:
            checked_directory(path)
        if path.name in {'meta.json', 'result.json', 'receipt.json', 'attempts.json', 'submission-recovery.json'} or path.name.startswith(('events.attempt-', 'stderr.attempt-')):
            checked_regular_file(path)
    return directory


def confined_launch(command: list[str], *, repository: Path, workspace: Path,
                    runtime: Path, events: Path, stderr: Path) -> tuple[list[str], dict[str, str]]:
    if workspace.name != 'workspace' or runtime != workspace.parent / 'runtime':
        raise ValueError('Worker write roots must be the exact job workspace and runtime')
    checked_directory(workspace)
    checked_directory(runtime, create=True)
    for name in ('tmp', 'logs', 'state', 'cache'):
        checked_directory(runtime / name, create=True)
    # These files are per-attempt outputs, never stale evidence from a retry.
    for name in ('events.jsonl', 'stderr.log'):
        (runtime / name).unlink(missing_ok=True)
    child = list(command)
    child[1:1] = ['--no-daemon', '-a', 'never']
    exec_index = child.index('exec')
    child[exec_index + 1:exec_index + 1] = [
        '-c', f'log_dir={json.dumps(str(runtime / "logs"))}',
        '-c', f'sqlite_home={json.dumps(str(runtime / "state"))}',
    ]
    wrapped = [sys.executable, str(repository / 'pipeline/landlock_exec.py'),
               '--repository-root', str(repository),
               '--writable-root', str(workspace), '--writable-root', str(runtime),
               '--cwd', str(workspace),
               '--stdout-log', str(runtime / 'events.jsonl'),
               '--stderr-log', str(runtime / 'stderr.log')]
    # Codex 0.160 opens its existing installation identity O_RDWR even for
    # ephemeral runs. Keep the normal home/config/auth location; permit only
    # this proven bookkeeping file, never a home directory or source tree.
    codex_runtime_location = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    identity = codex_runtime_location / 'installation_id'
    if identity.is_file():
        wrapped += ['--runtime-file', str(identity.absolute())]
    wrapped += ['--', *child]
    env = dict(os.environ, TMPDIR=str(runtime / 'tmp'),
               XDG_CACHE_HOME=str(runtime / 'cache'),
               npm_config_cache=str(runtime / 'cache/npm'),
               PYTHONDONTWRITEBYTECODE='1')
    return wrapped, env


def retain_logs(runtime: Path, events: Path, stderr: Path) -> None:
    """Copy finished child output into coordinator-owned evidence paths."""
    for name, target in (('events.jsonl', events), ('stderr.log', stderr)):
        source = runtime / name
        if source.is_file() and not source.is_symlink():
            checked_regular_file(source)
            if target.exists() or target.is_symlink():
                checked_regular_file(target.absolute())
            with target.open('ab') as handle:
                handle.write(source.read_bytes())


def preserve_prior_source_mutation(job_dir: Path, repository: Path) -> None:
    """Keep rejected historical evidence before a fresh confined attempt."""
    try:
        reject_repository_mutations(job_dir, repository)
    except ValueError as error:
        archive = checked_directory(job_dir / 'history' / f'source-mutation-{time.time_ns()}', create=True)
        for path in job_dir.iterdir():
            if path.name == 'history' or path.name == 'runtime':
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.copytree(path, archive / path.name, symlinks=True)
            elif path.is_file() and not path.is_symlink():
                shutil.copy2(path, archive / path.name)
        (archive / 'rejection.json').write_text(json.dumps({'reason': str(error),
            'status': 'historical submission rejected; fresh confined attempt required'}) + '\n')
        # Isolate the rejected attempt only after its exact evidence is safely
        # retained. New attempts get fresh logs, not a permanent rejection
        # inherited from an unrelated historical tool event.
        invalidated = [path for name in ('result.json', 'meta.json', 'receipt.json')
                       if (path := job_dir / name).exists()]
        invalidated.extend(job_dir.glob('events.attempt-*.jsonl'))
        for path in invalidated:
            saved = archive / path.name
            if saved.read_bytes() != path.read_bytes():
                raise ValueError('Prior worker evidence changed during quarantine')
        # Invalidate successful cache records before removing the evidence that
        # rejects them. A failed subsequent workspace build must not resurrect
        # this rejected candidate on the next invocation.
        for path in invalidated:
            path.unlink()


def reject_repository_mutations(job_dir: Path, repository: Path) -> None:
    """Reject historical submissions whose tool evidence changed shared files.

    This complements kernel enforcement for new launches. It does not claim
    to infer arbitrary shell writes from transcripts; preserved candidates
    still undergo the existing exact-artifact and linguistic checks.
    """
    allowed = [(job_dir / name).resolve() for name in ('workspace', 'runtime')]
    for events in job_dir.glob('events.attempt-*.jsonl'):
        with events.open(encoding='utf-8') as handle:
            for line in handle:
                if 'file_change' not in line:
                    continue
                event = json.loads(line)
                item = event.get('item', {})
                if item.get('type') != 'file_change':
                    continue
                for change in item.get('changes', []):
                    raw = change.get('path')
                    if not isinstance(raw, str):
                        raise ValueError('Worker file-change evidence lacks a path')
                    path = Path(raw)
                    if not path.is_absolute():
                        path = job_dir / 'workspace' / path
                    path = path.resolve()
                    if path.is_relative_to(repository.resolve()) and not any(
                            path.is_relative_to(root) for root in allowed):
                        raise ValueError('Worker changed repository files outside its draft workspace')
