import asyncio
import json

import pytest

from pipeline.dictionary_editor import validate_research, validate_citations
from pipeline.agent_harness import CodexRunner


@pytest.fixture
def dossier():
    return dict(entry_id='word', status='supported', gaps=[],
                sources=[dict(id='s1', url='https://example.org/word', title='Dictionary',
                              accessed_at='2026-01-01', summary_en='A supported account.')],
                claims=[dict(id='c1', text_en='A supported claim.', source_ids=['s1'])],
                searches=[dict(query='word history', opened_urls=['https://example.org/word'])])


def test_valid_sources_and_claims(dossier):
    validate_research(dossier, 'word')
    validate_citations(dict(claim_ids=['c1'], parts=[dict(claim_ids=['c1'])]), dossier)


@pytest.mark.parametrize('problem', ['entry', 'unopened', 'url', 'date', 'source', 'duplicate', 'gap', 'claim'])
def test_rejects_bad_research_provenance(dossier, problem):
    if problem == 'entry': dossier['entry_id'] = 'another'
    elif problem == 'unopened': dossier['searches'][0]['opened_urls'] = []
    elif problem == 'url': dossier['sources'][0]['url'] = 'javascript:alert(1)'
    elif problem == 'date': dossier['sources'][0]['accessed_at'] = '2999-01-01'
    elif problem == 'source': dossier['claims'][0]['source_ids'] = ['missing']
    elif problem == 'duplicate': dossier['sources'] *= 2
    elif problem == 'gap': dossier['status'] = 'partial'
    else: dossier['claims'] = []
    with pytest.raises(ValueError): validate_research(dossier, 'word')


def test_unknown_citation_rejected(dossier):
    with pytest.raises(ValueError, match='unknown'):
        validate_citations(dict(claim_ids=['c1'], parts=[dict(claim_ids=['missing'])]), dossier)


def test_tool_audit_accepts_unicode_line_separators_inside_json_strings(tmp_path):
    event = dict(item=dict(type='web_search', snippet='first\u2028second\u0085third'))
    (tmp_path / 'events.attempt-01.jsonl').write_text(json.dumps(event, ensure_ascii=False) + '\n')
    CodexRunner._check_tool_profile(tmp_path, 'research', dict(attempt_count=1))


def test_unresolved_means_an_explicit_research_gap_not_no_explanation(dossier):
    dossier.update(status='unresolved', claims=[], sources=[], gaps=['No accessible account located.'])
    validate_research(dossier, 'word')


def test_worker_capability_audit(tmp_path):
    events = tmp_path / 'events.attempt-01.jsonl'
    events.write_text(json.dumps(dict(item=dict(type='web_search'))))
    CodexRunner._check_tool_profile(tmp_path, 'research', {})
    with pytest.raises(ValueError, match='outside'):
        CodexRunner._check_tool_profile(tmp_path, 'offline', {})
    events.write_text(json.dumps(dict(item=dict(type='agent_message'))))
    CodexRunner._check_tool_profile(tmp_path, 'offline', {})
    with pytest.raises(ValueError, match='actually use'):
        CodexRunner._check_tool_profile(tmp_path, 'research', {})
    events.write_text(json.dumps(dict(item=dict(type='command_execution'))))
    with pytest.raises(ValueError, match='outside'):
        CodexRunner._check_tool_profile(tmp_path, 'research', {})


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['offline', 'research'])
async def test_worker_capabilities_are_command_flags_and_cache_identity(tmp_path, monkeypatch, profile):
    schema = tmp_path / 'schema.json'
    schema.write_text('{"type":"object"}')
    launches = []
    class Stdin:
        def write(self, value): pass
        async def drain(self): pass
        def close(self): pass
    class Process:
        pid, returncode, stdin = 12345, 0, Stdin()
        async def wait(self): return 0
    async def execute(*command, **kwargs):
        from pathlib import Path
        launches.append(command)
        Path(command[command.index('-o') + 1]).write_text('{}')
        kwargs['stdout'].write((json.dumps(dict(item=dict(type='web_search' if
            'web_search="live"' in command else 'agent_message'))) + '\n').encode())
        kwargs['stdout'].flush()
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', execute)
    runner = CodexRunner(tmp_path, 'model', asyncio.Semaphore(1), 10)
    await runner.call('job', 'prompt', schema, 'low', tool_profile=profile)
    assert 'features.shell_tool=false' in launches[0]
    assert 'features.unified_exec=false' in launches[0]
    assert f'web_search="{"live" if profile == "research" else "disabled"}"' in launches[0]
    await runner.call('job', 'prompt', schema, 'low', tool_profile=profile, cache_only=True)
    assert len(launches) == 1
    from pipeline.agent_harness import CachedCallUnavailable
    events = tmp_path / 'agents/job/events.attempt-01.jsonl'
    events.write_text(json.dumps(dict(item=dict(type='mcp_tool_call'))) + '\n')
    with pytest.raises(CachedCallUnavailable):
        await runner.call('job', 'prompt', schema, 'low', tool_profile=profile, cache_only=True)
    assert len(launches) == 1
    await runner.call('job', 'prompt', schema, 'low', tool_profile=profile)
    assert len(launches) == 2
    other = 'research' if profile == 'offline' else 'offline'
    await runner.call('job', 'prompt', schema, 'low', tool_profile=other)
    assert len(launches) == 3


@pytest.mark.asyncio
async def test_research_repairs_invalid_source_links_then_verifies(dossier):
    from pipeline.dictionary_editor import research
    calls = []
    class Runner:
        async def call(self, job, *args, **kwargs):
            calls.append(job)
            output = json.loads(json.dumps(dossier))
            if job.endswith('/research'):
                output['sources'][0]['url'] = 'https://example.org/wrong'
            return output
    result = await research(Runner(), 'job', dict(entry=dict(id='word')), None, [])
    assert result == dossier
    assert calls == ['job/research', 'job/research-repair', 'job/verify-research']


@pytest.mark.asyncio
async def test_malformed_repair_json_has_one_format_retry_then_normal_verification(dossier):
    from pipeline.dictionary_editor import research
    calls = []
    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            calls.append(job)
            assert kwargs['tool_profile'] == 'research'
            output = json.loads(json.dumps(dossier))
            if job.endswith('/research'):
                output['sources'][0]['url'] = 'https://example.org/wrong'
            if job.endswith('/research-repair'):
                raise json.JSONDecodeError('malformed', '{', 1)
            if job.endswith('-json-retry'):
                assert effort == 'high'
                assert 'valid schema JSON' in prompt
            return output
    assert await research(Runner(), 'job', dict(entry=dict(id='word')), None, []) == dossier
    assert calls == ['job/research', 'job/research-repair', 'job/research-repair-json-retry',
                     'job/verify-research']


@pytest.mark.asyncio
async def test_research_repairs_are_bounded_and_do_not_bypass_validation(dossier):
    from pipeline.dictionary_editor import research
    calls = []
    class Runner:
        async def call(self, job, *args, **kwargs):
            calls.append(job)
            output = json.loads(json.dumps(dossier))
            output['gaps'] = ['An unresolved question remains.']
            return output
    with pytest.raises(ValueError, match='no unresolved gaps'):
        await research(Runner(), 'job', dict(entry=dict(id='word')), None, [])
    assert calls == ['job/research', 'job/research-repair', 'job/research-repair-2']
