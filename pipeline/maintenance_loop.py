"""Bounded shared incident collection and diagnosis alongside production runs."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path

from pipeline.maintenance import scan_runs
from pipeline.maintenance_triage import run as triage, save


def check_locations(run_dirs, output):
    """Maintenance artifacts must never overwrite a monitored run."""
    destination = Path(output).resolve()
    for supplied in run_dirs:
        run = Path(supplied).resolve()
        if destination.is_relative_to(run) or run.is_relative_to(destination):
            raise ValueError('Maintenance output and production run directories must not overlap')


async def cycle(run_dirs, output, *, since=None, minimum_jobs=3, limit=1, triage_runner=None,
                github_repository=None, github_writes=2):
    check_locations(run_dirs, output)
    report = scan_runs(run_dirs, minimum_jobs=minimum_jobs, since=since)
    report_path = output / 'incidents.json'
    save(report_path, report)
    result = await triage(report_path, output / 'triage', limit=limit, runner=triage_runner)
    github = {'enabled': False}
    if github_repository:
        from pipeline.maintenance_github import sync
        from pipeline.maintenance_triage import ROOT
        state = output / 'triage' / 'triage-state.json'
        github = await asyncio.to_thread(sync, report, result, github_repository,
            repo_root=ROOT, triage_state=state if state.exists() else None,
            state_path=output / 'github-state.json', max_writes=github_writes)
        github['enabled'] = True
        github['repository'] = github_repository
    status = {'updated_at': datetime.now(timezone.utc).isoformat(), 'incidents': report['incident_count'],
              'clusters': len(report['clusters']), 'new_diagnoses': len(result['new_diagnoses']),
              'known_clusters': result['known_clusters'], 'phase': 'collect_and_propose',
              'fix_promotion': 'requires_implementation_tests_and_independent_review',
              'github': github}
    save(output / 'status.json', status)
    return status


async def watch(args):
    check_locations(args.run_dir, args.output)
    while True:
        try:
            status = await cycle(args.run_dir, args.output, since=args.since,
                minimum_jobs=args.minimum_jobs, limit=args.limit,
                github_repository=getattr(args, 'github_repo', None),
                github_writes=getattr(args, 'github_writes', 2))
            print(json.dumps(status), flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Keep diagnosis failures separate from production chapter state.
            status = {'updated_at': datetime.now(timezone.utc).isoformat(), 'phase': 'maintenance_error',
                      'error': f'{type(error).__name__}: {error}'}
            save(args.output / 'status.json', status)
            print(json.dumps(status), flush=True)
            if args.once:
                raise
        if args.once:
            return
        await asyncio.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', action='append', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--since', type=float)
    parser.add_argument('--minimum-jobs', type=int, default=3)
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--interval', type=float, default=120)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--github-repo', help='Explicit owner/repo for recurring evidence issues')
    parser.add_argument('--github-writes', type=int, default=2)
    args = parser.parse_args()
    if args.interval <= 0 or args.minimum_jobs < 1 or args.limit < 1 or args.github_writes < 0:
        parser.error('interval, minimum-jobs and limit must be positive; github-writes must be nonnegative')
    try:
        check_locations(args.run_dir, args.output)
    except ValueError as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'maintenance.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('a maintenance loop already owns this output directory')
        asyncio.run(watch(args))


if __name__ == '__main__':
    main()
