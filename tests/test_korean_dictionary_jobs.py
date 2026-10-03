import asyncio
import copy
import json

import pytest

from pipeline import korean_dictionary_jobs as jobs
from pipeline.korean_agent_harness import KoreanHarness, read, save


def setup(tmp_path):
    words = {f'word-{i}': {'headword': f'word {i}', 'kind': 'word'} for i in range(3)}
    context = {'word_requests': words, 'grammar_requests': ['grammar-1', 'grammar-2'],
               'chapter': {'segments': []}}
    expected = {('word', identity): {'id': identity, **request, 'definition_en': 'Reusable definition.'}
                for identity, request in words.items()}
    expected.update({('grammar', identity): {'id': identity, 'title_en': 'Pattern title',
        'pattern': 'Pattern', 'explanation_en': 'Its own formation and meaning.'}
        for identity in context['grammar_requests']})
    class Runner:
        def __init__(self): self.calls = []; self.bad_first = False
        async def call(self, job, prompt, *args, **kwargs):
            self.calls.append(job)
            assert kwargs['tool_profile'] == 'offline'
            inputs = json.loads(prompt.rsplit('\nINPUT:\n', 1)[1])
            if job.endswith('-repair-plan'):
                value = {'entries': [{'kind': 'word', 'entry_id': 'word-0'}]}
            else:
                value = {'words': [copy.deepcopy(expected[('word', i)]) for i in inputs['word_requests']],
                         'grammar': [copy.deepcopy(expected[('grammar', i)]) for i in inputs['grammar_requests']]}
                if self.bad_first and job.endswith('-0') and value['words']:
                    value['words'][0]['headword'] = 'incorrect identity'
            save(tmp_path / 'agents' / job / 'result.json', value)
            save(tmp_path / 'agents' / job / 'meta.json', {'return_code': 0})
            return value
    runner = Runner()
    harness = KoreanHarness(tmp_path, 1, runner=runner)
    return harness, runner, context, expected


@pytest.mark.parametrize('tamper', [None, 'definition', 'tool', 'repair'])
def test_dictionary_batches_replay_verified_workers_and_repair_only_affected_entries(tmp_path, tamper):
    harness, runner, context, expected = setup(tmp_path)
    produce = jobs.producer(harness, 'Write reusable definitions.', context, batch_size=2)
    value = asyncio.run(produce('dictionary-0', []))
    assert jobs.entries(value) == expected
    meta = read(tmp_path / 'agents/dictionary-0/meta.json')
    assert len(meta['batches']) == 3
    assert jobs.replay(tmp_path, meta) == value
    if tamper in ['definition', 'tool']:
        path = tmp_path / 'agents' / meta['batches'][0]['job']
        if tamper == 'definition':
            changed = read(path / 'result.json')
            changed['grammar'][0]['explanation_en'] = 'Changed after approval.'
            save(path / 'result.json', changed)
        else:
            (path / 'events.attempt-01.jsonl').write_text(json.dumps({
                'item': {'type': 'web_search'}}) + '\n')
        with pytest.raises(ValueError, match='changed after review|outside its offline role'):
            jobs.replay(tmp_path, meta)
    else:
        expected[('word', 'word-0')]['definition_en'] = 'Reviewed clarification.'
        before = len(runner.calls)
        result = asyncio.run(produce('dictionary-1', ['Clarify word-0.']))
        assert jobs.entries(result) == expected
        assert sum('-entries-' in job for job in runner.calls[before:]) == 1
        new_meta = read(tmp_path / 'agents/dictionary-1/meta.json')
        assert jobs.replay(tmp_path, new_meta) == result
        if tamper == 'repair':
            path = tmp_path / 'agents' / new_meta['repair_plan_job'] / 'result.json'
            save(path, {'entries': [{'kind': 'grammar', 'entry_id': 'grammar-1'}]})
            with pytest.raises(ValueError, match='changed after review'):
                jobs.replay(tmp_path, new_meta)


def test_dictionary_batch_identity_failures_retry_without_redrafting_siblings(tmp_path):
    harness, runner, context, expected = setup(tmp_path)
    runner.bad_first = True
    value = asyncio.run(jobs.producer(harness, 'Write entries.', context, batch_size=2)('dictionary-0', []))
    assert jobs.entries(value) == expected
    assert runner.calls.count('dictionary-0-entries-000-0') == 1
    assert 'dictionary-0-entries-000-1' not in runner.calls  # The unaffected grammar batch stays intact.
    assert 'dictionary-0-entries-001-1' in runner.calls


def test_dictionary_assembly_still_requires_independent_whole_delta_review(tmp_path):
    harness, runner, context, expected = setup(tmp_path)
    original = runner.call
    async def call(job, prompt, *args, **kwargs):
        if '-review-' in job:
            runner.calls.append(job)
            inputs = json.loads(prompt.rsplit('\nINPUT:\n', 1)[1])
            assert jobs.entries(inputs['output']) == expected
            result = {'approved': True, 'issues': []}
            save(tmp_path / 'agents' / job / 'result.json', result)
            save(tmp_path / 'agents' / job / 'meta.json', {'return_code': 0})
            return result
        return await original(job, prompt, *args, **kwargs)
    runner.call = call
    from pipeline.korean_agent_harness import validate_delta
    value = asyncio.run(harness.stage('dictionary', 'Write only new entries.', 'dictionary',
        lambda delta: validate_delta(delta, context['word_requests'], set(context['grammar_requests']), {}, {}),
        context, producer=jobs.producer(harness, 'Write only new entries.', context, batch_size=2)))
    assert jobs.entries(value) == expected
    assert 'dictionary-review-0' in runner.calls
    assert harness.stages['dictionary']['approved'] is True
