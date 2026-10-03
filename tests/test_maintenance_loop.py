import asyncio
import json
from types import SimpleNamespace

import pytest

from pipeline import maintenance_loop as loop


def test_cycle_keeps_maintenance_proposals_separate_from_production_state(tmp_path, monkeypatch):
    run = tmp_path / 'production'
    run.mkdir()
    approved = run / 'report.json'
    approved.write_text('{"status":"complete"}')
    monkeypatch.setattr(loop, 'scan_runs', lambda *a, **k: {'clusters': [], 'incident_count': 0})
    status = asyncio.run(loop.cycle([run], tmp_path / 'maintenance'))
    assert status['phase'] == 'collect_and_propose'
    assert status['github'] == {'enabled': False}
    assert approved.read_text() == '{"status":"complete"}'
    assert json.loads((tmp_path / 'maintenance' / 'triage' / 'summary.json').read_text())['new_diagnoses'] == []


def test_once_failure_records_maintenance_error_without_hiding_it(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('Incomplete incident input')
    monkeypatch.setattr(loop, 'scan_runs', fail)
    args = SimpleNamespace(run_dir=[], output=tmp_path, since=None, minimum_jobs=3,
                           limit=1, once=True, interval=120)
    with pytest.raises(ValueError, match='Incomplete incident input'):
        asyncio.run(loop.watch(args))
    status = json.loads((tmp_path / 'status.json').read_text())
    assert status['phase'] == 'maintenance_error'


def test_github_sync_uses_persisted_diagnoses_and_keeps_errors_separate(tmp_path, monkeypatch):
    from pipeline import maintenance_github
    output = tmp_path / 'maintenance'
    output.mkdir()
    state = output / 'triage' / 'triage-state.json'
    state.parent.mkdir()
    state.write_text('{"clusters":{}}')
    monkeypatch.setattr(loop, 'scan_runs', lambda *a, **k: {'clusters': [], 'incident_count': 0})
    calls = []
    def sync(report, summary, repository, **kwargs):
        calls.append((report, summary, repository, kwargs))
        return {'errors': ['GitHub CLI returned an error'], 'created': 0, 'updated': 0}
    monkeypatch.setattr(maintenance_github, 'sync', sync)
    status = asyncio.run(loop.cycle([tmp_path / 'production'], output,
        github_repository='owner/repo', github_writes=1))
    assert status['phase'] == 'collect_and_propose'
    assert status['github']['enabled'] is True
    assert status['github']['errors'] == ['GitHub CLI returned an error']
    assert calls[0][2] == 'owner/repo'
    assert calls[0][3]['triage_state'] == state
    assert calls[0][3]['max_writes'] == 1


@pytest.mark.parametrize('location', ['same', 'child', 'parent', 'symlink'])
def test_overlap_rejected_before_writing_production_state(tmp_path, location):
    run = tmp_path / 'production'
    run.mkdir()
    original = run / 'status.json'
    original.write_text('{"status":"running"}')
    if location == 'same':
        output = run
    elif location == 'child':
        output = run / 'maintenance'
    elif location == 'parent':
        output = tmp_path
    else:
        output = tmp_path / 'alias'
        output.symlink_to(run, target_is_directory=True)
    with pytest.raises(ValueError, match='must not overlap'):
        asyncio.run(loop.cycle([run], output))
    assert original.read_text() == '{"status":"running"}'
    assert not (output / 'incidents.json').exists()
    args = SimpleNamespace(run_dir=[run], output=output, since=None, minimum_jobs=3,
                           limit=1, once=True, interval=120)
    with pytest.raises(ValueError, match='must not overlap'):
        asyncio.run(loop.watch(args))
    assert original.read_text() == '{"status":"running"}'
