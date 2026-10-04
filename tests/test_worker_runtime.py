import json
import os

import pytest

from pipeline.worker_runtime import confined_launch, reject_repository_mutations, retain_logs, safe_job_directory, preserve_prior_source_mutation


def test_worker_runtime_keeps_tools_without_shared_write_roots(tmp_path):
    repo = tmp_path / 'repo'
    workspace = repo / 'runs/job/workspace'
    workspace.mkdir(parents=True)
    runtime = workspace.parent / 'runtime'
    command = ['codex', 'exec', '--json', '-s', 'danger-full-access', '-m', 'gpt-6-luna']
    wrapped, env = confined_launch(command, repository=repo, workspace=workspace,
                                  runtime=runtime, events=workspace.parent / 'events',
                                  stderr=workspace.parent / 'stderr')
    assert wrapped[wrapped.index('--') + 1:][:5] == ['codex', '--no-daemon', '-a', 'never', 'exec']
    roots = [wrapped[i + 1] for i, arg in enumerate(wrapped) if arg == '--writable-root']
    assert roots == [str(workspace), str(runtime)]
    assert str(repo) not in roots
    assert not any('shell_tool=false' in arg or 'mcp_servers={}' in arg for arg in wrapped)
    for name in ('HOME', 'CODEX_HOME'):
        assert env.get(name) == os.environ.get(name)
    assert env['TMPDIR'] == str(runtime / 'tmp')
    assert env['PYTHONDONTWRITEBYTECODE'] == '1'
    assert command[:2] == ['codex', 'exec']


def test_retained_logs_ignore_symlink_runtime_outputs(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    events, stderr = tmp_path / 'events', tmp_path / 'stderr'
    events.write_text('coordinator\n')
    (runtime / 'events.jsonl').write_text('worker\n')
    outside = tmp_path / 'unrelated'
    outside.write_text('private')
    (runtime / 'stderr.log').symlink_to(outside)
    retain_logs(runtime, events, stderr)
    assert events.read_text() == 'coordinator\nworker\n'
    assert not stderr.exists()
    assert outside.read_text() == 'private'


@pytest.mark.parametrize('relative', [False, True])
def test_historical_source_mutation_rejected_but_workspace_draft_allowed(tmp_path, relative):
    repo = tmp_path / 'repo'
    job = repo / 'runs/job'
    workspace = job / 'workspace'
    workspace.mkdir(parents=True)
    events = job / 'events.attempt-01.jsonl'
    def event(path):
        return json.dumps({'type': 'item.completed', 'item': {
            'type': 'file_change', 'changes': [{'path': str(path), 'kind': 'update'}]}}) + '\n'
    events.write_text(event('candidate.json' if relative else workspace / 'candidate.json'))
    reject_repository_mutations(job, repo)
    events.write_text(event('../../../pipeline/validator.py' if relative else repo / 'pipeline/validator.py'))
    with pytest.raises(ValueError, match='outside its draft workspace'):
        reject_repository_mutations(job, repo)


def test_all_three_languages_share_the_confined_runner():
    from pipeline.agent_harness import CodexRunner
    from pipeline.agent_harness import ChapterHarness
    from pipeline.japanese_agent_harness import JapaneseChapterHarness
    from pipeline.korean_agent_harness import CodexRunner as KoreanRunner
    assert issubclass(JapaneseChapterHarness, ChapterHarness)
    assert ChapterHarness.__init__.__globals__['CodexRunner'] is CodexRunner
    assert KoreanRunner is CodexRunner


def test_nested_chinese_japanese_jobs_keep_job_local_write_roots(tmp_path):
    job = safe_job_directory(tmp_path/'runs', 'annotations/chunk_0001/semantic_repair_01')
    assert job == tmp_path/'runs/agents/annotations/chunk_0001/semantic_repair_01'
    workspace = job/'workspace'
    workspace.mkdir()
    wrapped, _ = confined_launch(['codex','exec'], repository=tmp_path, workspace=workspace,
                                runtime=job/'runtime', events=job/'events', stderr=job/'stderr')
    roots = [wrapped[i+1] for i,arg in enumerate(wrapped) if arg == '--writable-root']
    assert roots == [str(workspace),str(job/'runtime')]
    for unsafe in ('../pipeline', '/pipeline', 'annotations/../pipeline', 'annotations//job', 'annotations\\job'):
        with pytest.raises(ValueError, match='safe relative path'):
            safe_job_directory(tmp_path/'runs', unsafe)


@pytest.mark.parametrize('alias', ['workspace', 'runtime', 'meta.json', 'events.attempt-01.jsonl'])
def test_stale_job_alias_fails_before_coordinator_touches_target(tmp_path, alias):
    repo = tmp_path / 'repo'
    source = repo / 'pipeline'
    source.mkdir(parents=True)
    sentinel = source / 'sentinel'
    sentinel.write_text('protected')
    job = safe_job_directory(repo / 'runs', 'job')
    (job / alias).symlink_to(source if alias in {'workspace', 'runtime'} else sentinel)
    with pytest.raises(ValueError):
        safe_job_directory(repo / 'runs', 'job')
    assert sentinel.read_text() == 'protected'
    assert set(p.name for p in source.iterdir()) == {'sentinel'}


def test_runtime_alias_cannot_be_resolved_into_a_writable_source_root(tmp_path):
    repo = tmp_path / 'repo'
    source = repo / 'pipeline'
    source.mkdir(parents=True)
    workspace = repo / 'runs/agents/job/workspace'
    workspace.mkdir(parents=True)
    runtime = workspace.parent / 'runtime'
    runtime.symlink_to(source)
    with pytest.raises(ValueError):
        confined_launch(['codex', 'exec'], repository=repo, workspace=workspace,
                        runtime=runtime, events=workspace.parent/'events', stderr=workspace.parent/'stderr')
    assert list(source.iterdir()) == []


def test_prior_source_mutation_is_retained_before_clean_retry(tmp_path):
    repo = tmp_path / 'repo'
    job = safe_job_directory(repo / 'runs', 'job')
    workspace = job / 'workspace'
    workspace.mkdir()
    (workspace / 'candidate.json').write_text('{"old":true}')
    (job / 'result.json').write_text('{"old":true}')
    (job / 'meta.json').write_text('{"return_code":0,"fingerprint":"matching"}')
    event = {'type':'item.completed', 'item':{'type':'file_change','changes':[{'path':str(repo/'pipeline/validator.py')} ]}}
    (job/'events.attempt-01.jsonl').write_text(json.dumps(event)+'\n')
    previous_events = (job/'events.attempt-01.jsonl').read_bytes()
    preserve_prior_source_mutation(job, repo)
    archives = list((job/'history').iterdir())
    assert len(archives) == 1
    assert json.loads((archives[0]/'rejection.json').read_text())['status'].startswith('historical submission rejected')
    assert (archives[0]/'workspace/candidate.json').read_text() == '{"old":true}'
    assert (archives[0]/'events.attempt-01.jsonl').read_bytes() == previous_events
    assert not (job/'events.attempt-01.jsonl').exists()
    assert not (job/'result.json').exists()
    assert not (job/'meta.json').exists()
    assert json.loads((archives[0]/'result.json').read_text()) == {'old': True}
    assert json.loads((archives[0]/'meta.json').read_text())['return_code'] == 0
    reject_repository_mutations(job, repo)
    clean_event = {'item':{'type':'file_change','changes':[{'path':str(workspace/'candidate.json')}]}}
    (job/'events.attempt-01.jsonl').write_text(json.dumps(clean_event)+'\n')
    reject_repository_mutations(job, repo)


def test_legacy_tool_profile_cannot_bypass_source_mutation_audit(tmp_path, monkeypatch):
    import pipeline.agent_harness as harness
    job = safe_job_directory(tmp_path/'runs', 'job')
    event = {'item': {'type': 'file_change', 'changes': [{'path':str(tmp_path/'pipeline/validator.py')}]}}
    (job/'events.attempt-01.jsonl').write_text(json.dumps(event)+'\n')
    monkeypatch.setattr(harness, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='outside its draft workspace'):
        harness.CodexRunner._check_tool_profile(job, None, {'return_code':0})


@pytest.mark.asyncio
async def test_stale_receipt_alias_is_unlinked_without_removing_target(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner
    source = tmp_path / 'protected.json'
    source.write_text('{"protected":true}')
    job = safe_job_directory(tmp_path/'runs', 'job')
    runtime = job / 'runtime'
    runtime.mkdir()
    (runtime / 'receipt.json').symlink_to(source)
    schema = tmp_path/'schema.json'
    schema.write_text('{"type":"object"}')
    class Input:
        def write(self, _value): pass
        async def drain(self): pass
        def close(self): pass
    class Process:
        pid = 12345
        returncode = 0
        stdin = Input()
        async def wait(self): return 0
    async def launch(*command, **kwargs):
        assert command[command.index('-o') + 1] == str(runtime/'receipt.json')
        assert source.read_text() == '{"protected":true}'
        workspace = job / 'workspace'
        (workspace/'candidate.json').write_text('{}')
        (runtime/'receipt.json').write_text('{"candidate_path":"candidate.json"}')
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    runner = CodexRunner(tmp_path/'runs', 'gpt-6-luna', asyncio.Semaphore(1))
    assert await runner.call('job', 'draft', schema, 'low') == {}
    assert source.read_text() == '{"protected":true}'


def test_completed_runtime_sqlite_cleanup_preserves_replay_artifacts_and_live_state(tmp_path):
    from pipeline.worker_runtime import cleanup_runtime_state
    runtime = tmp_path / 'agents/job/runtime'
    state = runtime / 'state'
    state.mkdir(parents=True)
    names = ['state_5.sqlite', 'state_5.sqlite-wal', 'state_5.sqlite-shm', 'logs_2.sqlite', '.state_5.sqlite.seed-dead123']
    for name in names:
        (state / name).write_bytes(b'private scratch')
    retained = [runtime/'receipt.json', runtime/'state-cache-setup.json', runtime/'events.jsonl',
                runtime/'stderr.log', state/'unrelated.json']
    for path in retained:
        path.write_bytes(b'exact retained evidence')
    assert cleanup_runtime_state(runtime, process_finished=False)['status'] == 'skipped'
    assert all((state/name).exists() for name in names)
    result = cleanup_runtime_state(runtime, process_finished=True)
    assert result['status'] == 'cleaned'
    assert sorted(result['removed_files']) == sorted(names)
    assert result['removed_bytes'] == len(names) * len(b'private scratch')
    assert not any((state/name).exists() for name in names)
    assert all(path.read_bytes() == b'exact retained evidence' for path in retained)


@pytest.mark.parametrize('alias', ['symlink', 'hardlink'])
def test_runtime_cleanup_refuses_aliased_sqlite_and_does_not_delete_outside_state(tmp_path, alias):
    from pipeline.worker_runtime import cleanup_runtime_state
    runtime = tmp_path / 'runtime'
    state = runtime/'state'
    state.mkdir(parents=True)
    outside = tmp_path/'external.sqlite'
    outside.write_bytes(b'protected')
    target = state/'state_5.sqlite'
    if alias == 'symlink':
        target.symlink_to(outside)
    else:
        os.link(outside, target)
    result = cleanup_runtime_state(runtime, process_finished=True)
    assert result['status'] == 'skipped'
    assert outside.read_bytes() == b'protected'
    assert target.exists()
