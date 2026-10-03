import asyncio
import copy
import json

import pytest

from pipeline import dictionary_adaptive_editor as adaptive
from pipeline import dictionary_meaning_guides as guides
from pipeline.agent_harness import CodexRunner, CachedCallUnavailable


def corpus():
    return dict(entries=[dict(id='three-hundred', headword='三百', reading='sānbǎi',
        kind='word', senses=[dict(id='number', definition='three hundred')])], occurrences=[])


def packet():
    return dict(needs_research=False, research_questions=[], guides=[dict(
        entry_id='three-hundred', structure='compositional', claim_ids=[],
        explanation_en='Three groups of one hundred make three hundred.', caveat_en='',
        parts=[dict(text='三', contribution_en='three', claim_ids=[]),
               dict(text='百', contribution_en='one hundred', claim_ids=[])])])


def dossier():
    return dict(entry_id='three-hundred', status='supported', gaps=[],
        searches=[dict(query='Chinese numbers', opened_urls=['https://example.org/numbers'])],
        sources=[dict(id='s1', url='https://example.org/numbers', title='Numbers',
                      accessed_at='2026-01-01', summary_en='Three hundred.')],
        claims=[dict(id='c1', text_en='Three groups of one hundred.', source_ids=['s1'])])


def test_simple_entry_has_two_offline_calls_and_no_fabricated_research(tmp_path, monkeypatch):
    calls = []
    async def call(self, job, prompt, schema, effort, **kwargs):
        calls.append((job.rsplit('/', 1)[-1], effort, kwargs['tool_profile']))
        assert 'Do not hide uncertainty' in prompt
        return packet()
    monkeypatch.setattr(CodexRunner, 'call', call)
    path = tmp_path / 'guides.json'
    output = asyncio.run(guides.update(corpus(), path, run_dir=tmp_path / 'runs'))
    assert calls == [('adaptive-propose', 'low', 'offline'), ('adaptive-review', 'high', 'offline')]
    guide = output['entries'][0]['meaning_guide']
    assert guide['evidence_status'] == 'reviewed_unresearched'
    assert 'research' not in guide
    calls.clear()
    assert asyncio.run(guides.update(corpus(), path)) == output
    assert not calls


@pytest.mark.parametrize('trigger', ['writer', 'reviewer', 'historical', 'explicit'])
def test_uncertainty_and_history_escalate_without_bypassing_sources(tmp_path, monkeypatch, trigger):
    calls = []
    async def call(self, job, prompt, schema, effort, **kwargs):
        stage = job.rsplit('/', 1)[-1]
        calls.append(stage)
        if stage in ('research', 'verify-research'):
            assert kwargs['tool_profile'] == 'research'
            return dossier()
        result = packet()
        if stage == 'adaptive-propose' and trigger == 'writer' or stage == 'adaptive-review' and trigger == 'reviewer':
            result.update(needs_research=True, research_questions=['Verify this component contribution.'])
        if stage == 'adaptive-propose' and trigger == 'historical':
            result['guides'][0]['explanation_en'] = 'It originated in an ancient counting system.'
        if stage in ('propose', 'review'):
            result['guides'][0]['claim_ids'] = ['c1']
        return result
    monkeypatch.setattr(CodexRunner, 'call', call)
    requests = [dict(id='r1', entry_id='three-hundred', reason='Check sources', force_research=True)] if trigger == 'explicit' else []
    output = asyncio.run(guides.update(corpus(), tmp_path / 'guides.json', requests=requests, run_dir=tmp_path / 'runs'))
    assert calls[-4:] == ['research', 'verify-research', 'propose', 'review']
    assert len(calls) == (4 if trigger == 'explicit' else 6)
    assert output['entries'][0]['meaning_guide']['research'] == dossier()


def test_reviewer_can_fix_a_malformed_draft_component(tmp_path, monkeypatch):
    async def call(self, job, *args, **kwargs):
        result = packet()
        if job.endswith('adaptive-propose'):
            result['guides'][0]['parts'][0]['text'] = '四'
        return result
    monkeypatch.setattr(CodexRunner, 'call', call)
    output = asyncio.run(guides.update(corpus(), tmp_path / 'guides.json', run_dir=tmp_path / 'runs'))
    assert output['entries'][0]['meaning_guide']['parts'][0]['text'] == '三'


def test_second_scoped_repair_corrects_a_repeated_identity_typo(tmp_path, monkeypatch):
    stages = []
    async def call(self, job, prompt, *args, **kwargs):
        stage = job.rsplit('/', 1)[-1]
        stages.append(stage)
        result = packet()
        if stage in ('adaptive-review', 'adaptive-review-repair'):
            result['guides'][0]['entry_id'] = 'three-hundredd'
        if stage == 'adaptive-review-repair-2':
            assert 'ONLY allowed entry_id is three-hundred' in prompt
        return result
    monkeypatch.setattr(CodexRunner, 'call', call)
    output = asyncio.run(guides.update(corpus(), tmp_path/'guides.json', run_dir=tmp_path/'runs'))
    assert output['entries'][0]['meaning_guide']['editorial_route'] == 'offline_review'
    assert stages == ['adaptive-propose', 'adaptive-review',
                      'adaptive-review-repair', 'adaptive-review-repair-2']


def test_corrected_draft_classification_alone_does_not_require_research(tmp_path, monkeypatch):
    calls = []
    async def call(self, job, *args, **kwargs):
        calls.append(job)
        result = packet()
        if job.endswith('adaptive-propose'):
            result['guides'][0]['structure'] = 'lexicalized'
        return result
    monkeypatch.setattr(CodexRunner, 'call', call)
    output = asyncio.run(guides.update(corpus(), tmp_path / 'guides.json', run_dir=tmp_path / 'runs'))
    assert len(calls) == 2
    assert output['entries'][0]['meaning_guide']['editorial_route'] == 'offline_review'


def test_unknown_interrogative_referent_is_not_unknown_word_formation():
    row = packet()['guides'][0]
    row['explanation_en'] = 'どこ asks about an unknown place; a particle marks its role.'
    assert not adaptive.evidence_needed(row)
    row['explanation_en'] = 'It refers to a place that is unknown or being asked about.'
    assert not adaptive.evidence_needed(row)
    row['explanation_en'] = 'The contribution of the first component is unknown.'
    assert adaptive.evidence_needed(row)
    row['explanation_en'] = '誰 asks which person is meant when their identity is unknown.'
    assert not adaptive.evidence_needed(row)
    row['explanation_en'] = 'The component\'s identity is unknown.'
    assert adaptive.evidence_needed(row)
    row['explanation_en'] = 'The name was historically used for a person whose identity is unknown.'
    assert adaptive.evidence_needed(row)


def test_conventional_meaning_alone_is_not_a_historical_research_claim():
    row = packet()['guides'][0]
    row.update(structure='lexicalized', explanation_en='A conventional expression for an uprising.',
               caveat_en='The specific uprising meaning is conventional and cannot be derived from the parts alone.')
    assert not adaptive.evidence_needed(row)
    row['caveat_en'] = 'The contribution of the first component is unclear.'
    assert adaptive.evidence_needed(row)


@pytest.mark.parametrize('problem', ['citation', 'parts', 'uncertainty'])
def test_invalid_offline_approval_cannot_publish(tmp_path, monkeypatch, problem):
    async def call(self, job, *args, **kwargs):
        result = packet()
        if job.rsplit('/', 1)[-1].startswith('adaptive-review'):
            if problem == 'citation': result['guides'][0]['claim_ids'] = ['invented-source']
            if problem == 'parts': result['guides'][0]['parts'][0]['text'] = '四'
            if problem == 'uncertainty': result['research_questions'] = ['An unresolved question']
        return result
    monkeypatch.setattr(CodexRunner, 'call', call)
    path = tmp_path / 'guides.json'
    with pytest.raises(ValueError):
        asyncio.run(guides.update(corpus(), path, run_dir=tmp_path / 'runs'))
    assert not path.exists()


def test_completed_research_skips_adaptive_calls_and_is_preserved(tmp_path, monkeypatch):
    async def cached(*args): return dossier()
    monkeypatch.setattr('pipeline.dictionary_editor.cached_research', cached)
    calls = []
    async def call(self, job, *args, **kwargs):
        calls.append(job.rsplit('/', 1)[-1])
        result = packet()
        result['guides'][0]['claim_ids'] = ['c1']
        return result
    monkeypatch.setattr(CodexRunner, 'call', call)
    output = asyncio.run(guides.update(corpus(), tmp_path / 'guides.json'))
    assert calls == ['propose', 'review']
    assert output['entries'][0]['meaning_guide']['editorial_route'] == 'cached_research'
    assert output['entries'][0]['meaning_guide']['research'] == dossier()


def test_cache_only_miss_cannot_launch_an_agent(tmp_path, monkeypatch):
    async def launch(*args, **kwargs): pytest.fail('Cache lookup launched a process')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    schema = tmp_path / 'schema.json'
    schema.write_text('{}')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))
    with pytest.raises(CachedCallUnavailable):
        asyncio.run(runner.call('job', 'prompt', schema, 'low', cache_only=True, tool_profile='research'))


def test_cached_dossier_requires_both_research_and_verification(tmp_path, monkeypatch):
    from pipeline.dictionary_editor import cached_research
    marker = tmp_path / 'agents/job/research/meta.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    calls = []
    async def call(self, job, *args, **kwargs):
        assert kwargs['cache_only'] is True
        calls.append(job)
        if job.endswith('verify-research'):
            raise CachedCallUnavailable(job)
        return dossier()
    monkeypatch.setattr(CodexRunner, 'call', call)
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))
    item = guides.inputs(corpus())[0]
    assert asyncio.run(cached_research(runner, 'job', item, None, [])) is None
    assert calls == ['job/research', 'job/verify-research']


def test_escalated_research_cache_reuses_original_questions(tmp_path, monkeypatch):
    from pipeline.dictionary_editor import cached_research
    for stage in ('research', 'adaptive-propose', 'adaptive-review'):
        (tmp_path / 'agents/job' / stage).mkdir(parents=True)
    (tmp_path / 'agents/job/research/meta.json').write_text('{}')
    proposed = packet()
    proposed.update(needs_research=True, research_questions=['Check the component.'])
    (tmp_path / 'agents/job/adaptive-propose/result.json').write_text(json.dumps(proposed))
    (tmp_path / 'agents/job/adaptive-review/result.json').write_text(json.dumps(packet()))
    calls = []
    async def call(self, job, prompt, *args, **kwargs):
        assert kwargs['cache_only']
        calls.append(job)
        if 'Research escalation: Check the component.' not in prompt:
            raise CachedCallUnavailable(job)
        return dossier()
    monkeypatch.setattr(CodexRunner, 'call', call)
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))
    assert asyncio.run(cached_research(runner, 'job', guides.inputs(corpus())[0], None, [])) == dossier()
    assert calls == ['job/research', 'job/research', 'job/verify-research']
