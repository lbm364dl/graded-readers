import asyncio
import copy
import json

import pytest

from pipeline import dictionary_meaning_guides as guides
from pipeline.usage_dictionary import decision_digest


@pytest.fixture(autouse=True)
def use_researched_route(monkeypatch):
    # This suite covers the existing sourced editor. The two-call route and its
    # integration are exercised separately in test_dictionary_adaptive_editor.
    async def escalate(*args):
        return None, []
    monkeypatch.setattr('pipeline.dictionary_adaptive_editor.edit', escalate)


@pytest.fixture
def corpus():
    return dict(entries=[dict(id='word-kandao', headword='看到', reading='kàndào',
                              kind='construction', senses=[dict(id='sense-see', definition='see; notice')])],
                occurrences=[dict(entry_id='word-kandao', sense_id='sense-see',
                                  sentence='我看到了。', gloss='saw')])


def row():
    return dict(entry_id='word-kandao', structure='grammatical',
                claim_ids=['c1'],
                explanation_en='看 is looking; 到 marks reaching the result: actually seeing something.',
                parts=[dict(text='看', contribution_en='the action of looking', claim_ids=['c1']),
                       dict(text='到', contribution_en='successful perception as its result', claim_ids=['c1'])],
                caveat_en='')


def test_lexicalized_limit_can_be_in_explanation_without_duplicate_caveat(corpus):
    item = row()
    item.update(structure='lexicalized', explanation_en=
                'The parts suggest a literal interpretation. That alone does not explain '
                'the conventional meaning, which must be learned as a whole.')
    guides.validate_rows(guides.inputs(corpus), [item])


def test_component_reconstruction_error_identifies_duplicate_whole_word(corpus):
    item = row()
    item['parts'].append(dict(text='看到', contribution_en='see', claim_ids=[]))
    with pytest.raises(ValueError, match='reconstruct') as error:
        guides.validate_rows(guides.inputs(corpus), [item])
    assert "['看', '到', '看到']" in str(error.value)
    assert '看到看到' in str(error.value)
    assert 'exactly once' in str(error.value)


def test_investigation_questions_are_internal_and_validated(corpus):
    item = row()
    item['investigation_questions'] = [dict(component='到', question='Is this analysis complete?')]
    guides.validate_rows(guides.inputs(corpus), [item])
    item['investigation_questions'][0]['component'] = '相'
    with pytest.raises(ValueError, match='Investigation component'):
        guides.validate_rows(guides.inputs(corpus), [item])


def test_lexicalized_limit_can_describe_what_characters_do_not_specify(corpus):
    item = row()
    item.update(structure='lexicalized', explanation_en=
                'This is a conventional title. Its characters do not by themselves '
                'specify the exact rank or duties.')
    guides.validate_rows(guides.inputs(corpus), [item])


def research():
    return dict(entry_id='word-kandao', status='supported', gaps=[],
                sources=[dict(id='s1', url='https://example.org/see', title='Dictionary',
                              accessed_at='2026-01-01', summary_en='Seeing as the result of looking.')],
                claims=[dict(id='c1', text_en='Seeing as a result.', source_ids=['s1'])],
                searches=[dict(query='看到 result complement', opened_urls=['https://example.org/see'])])


def decisions(corpus):
    rows = [dict(row(), input_fingerprint=guides.input_fingerprint(guides.inputs(corpus)[0]))]
    return dict(reviewed=True, guides=rows, review_digest=decision_digest(rows))


def test_enrichment_does_not_change_dictionary_identity_or_usage(corpus):
    original = copy.deepcopy(corpus)
    output = guides.extend(corpus, decisions(corpus))
    assert corpus == original
    assert output['occurrences'] == corpus['occurrences']
    assert output['entries'][0]['id'] == 'word-kandao'
    assert output['entries'][0]['senses'] == corpus['entries'][0]['senses']
    assert output['entries'][0]['meaning_guide']['parts'][1]['text'] == '到'


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'unknown', 'parts', 'empty', 'structure', 'opaque', 'unreviewed', 'edited', 'stale', 'inline_citation'])
def test_bad_or_stale_explanations_cannot_publish(corpus, problem):
    doc = decisions(corpus)
    item = doc['guides'][0]
    if problem == 'missing': doc['guides'].clear()
    elif problem == 'duplicate': doc['guides'].append(copy.deepcopy(item))
    elif problem == 'unknown': item['entry_id'] = 'unknown'
    elif problem == 'parts': item['parts'][1]['text'] = '见'
    elif problem in {'empty', 'edited'}: item['explanation_en'] = ''
    elif problem == 'structure': item['structure'] = 'ancient-origin'
    elif problem == 'opaque': item['structure'] = 'lexicalized'
    elif problem == 'unreviewed': doc['reviewed'] = False
    elif problem == 'inline_citation': item['explanation_en'] += ' [c1]'
    else: corpus['entries'][0]['senses'][0]['definition'] = 'changed sense'
    if problem != 'edited': doc['review_digest'] = decision_digest(doc['guides'])
    with pytest.raises(ValueError): guides.extend(corpus, doc)


def test_independent_review_is_published_not_draft(corpus, tmp_path, monkeypatch):
    calls = []
    async def call(self, job, prompt, schema, effort, **kwargs):
        stage = job.rsplit('/', 1)[-1]
        calls.append((stage, effort, kwargs['tool_profile']))
        if stage in ('research', 'verify-research'): return research()
        assert 'independent of the current corpus' in prompt
        assert 'explanation_en, contribution_en and caveat_en equally corpus-independent' in prompt
        assert 'smallest canonical form' in prompt
        if stage == 'review':
            assert 'if all supplied examples are replaced' in prompt
            assert 'not on reporting\nthe research process' in prompt
            assert 'Do not append a\nhistorical-origin disclaimer' in prompt
            assert 'use claim_ids fields' in prompt
        result = row()
        if stage == 'propose': result['explanation_en'] = 'unreviewed draft'
        return {'guides': [result]}
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    path = tmp_path / 'guides.json'
    output = asyncio.run(guides.update(corpus, path))
    assert calls == [('research', 'low', 'research'), ('verify-research', 'high', 'research'),
                     ('propose', 'low', 'offline'),
                     ('review', 'high', 'offline')]
    assert output['entries'][0]['meaning_guide']['explanation_en'] == row()['explanation_en']
    calls.clear()
    assert asyncio.run(guides.update(corpus, path)) == output
    assert calls == []


def test_invalid_review_gets_one_validated_repair(corpus, tmp_path, monkeypatch):
    stages = []
    async def call(self, job, prompt, schema, effort, **kwargs):
        stage = job.rsplit('/', 1)[-1]
        stages.append(stage)
        if stage in ('research', 'verify-research'):
            return research()
        result = row()
        if stage == 'review':
            result['parts'][0]['text'] = '見'
        if stage == 'review-repair':
            assert 'do not reconstruct' in prompt
            assert effort == 'high'
            assert kwargs['tool_profile'] == 'offline'
        return {'guides': [result]}
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    output = asyncio.run(guides.update(corpus, tmp_path / 'guides.json'))
    assert stages[-1] == 'review-repair'
    assert output['entries'][0]['meaning_guide']['parts'] == row()['parts']


def test_source_change_invalidates_only_affected_entry(corpus, tmp_path, monkeypatch):
    second = copy.deepcopy(corpus['entries'][0])
    second['id'] = 'other-word'
    corpus['entries'].append(second)
    path = tmp_path / 'guides.json'
    previous = []
    for item in guides.inputs(corpus):
        previous.append(dict(row(), entry_id=item['entry']['id'],
                             input_fingerprint=guides.input_fingerprint(item)))
    path.write_text(json.dumps(dict(reviewed=True, guides=previous, review_digest=decision_digest(previous))))
    corpus['entries'][0]['senses'][0]['definition'] = 'notice visually'
    async def call(self, job, prompt, schema, effort, **kwargs):
        assert 'other-word' not in prompt
        if job.endswith('research'): return research()
        return {'guides': [row()]}
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    output = asyncio.run(guides.update(corpus, path))
    assert len(output['entries']) == 2


def test_failed_review_preserves_previous_artifact(corpus, tmp_path, monkeypatch):
    path = tmp_path / 'guides.json'
    path.write_text('{}')
    async def call(self, job, *args, **kwargs):
        if job.endswith('research'): return research()
        return {'guides': []}
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    with pytest.raises(ValueError): asyncio.run(guides.update(corpus, path))
    assert path.read_text() == '{}'


def test_policy_change_does_not_silently_rewrite_reviewed_guides(corpus, monkeypatch):
    old = decisions(corpus)
    monkeypatch.setattr(guides, 'POLICY_VERSION', guides.POLICY_VERSION + 1)
    guides.extend(corpus, old)


def test_new_examples_do_not_trigger_editorial_work(corpus, tmp_path, monkeypatch):
    path = tmp_path / 'guides.json'
    path.write_text(json.dumps(decisions(corpus)))
    corpus['occurrences'].append(dict(entry_id='word-kandao', sense_id='sense-see',
                                      sentence='他看到我。', gloss='sees me'))
    async def call(*args, **kwargs): pytest.fail('Existing sense must not be re-researched')
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    output = asyncio.run(guides.update(corpus, path))
    assert len(output['occurrences']) == 2
    assert output['entries'][0]['meaning_guide']['explanation_en'] == row()['explanation_en']


def test_exact_legacy_migration_preserves_reviewed_text_without_calls(corpus, tmp_path, monkeypatch):
    path = tmp_path / 'guides.json'
    rows = [dict(row(), input_fingerprint=decision_digest([2, guides.inputs(corpus)[0]]))]
    path.write_text(json.dumps(dict(reviewed=True, guides=rows, review_digest=decision_digest(rows))))
    async def call(*args, **kwargs): pytest.fail('Migration must preserve reviewed text')
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    asyncio.run(guides.update(corpus, path))
    migrated = json.loads(path.read_text())['guides'][0]
    assert migrated['explanation_en'] == row()['explanation_en']
    assert migrated['editorial_policy'] == 2
    assert migrated['evidence_status'] == 'legacy_unresearched'
    assert list((tmp_path / 'meaning-guide-history').glob('*.json'))


def test_targeted_requests_are_deduplicated_into_one_job_and_consumed(corpus, tmp_path, monkeypatch):
    path = tmp_path / 'guides.json'
    path.write_text(json.dumps(decisions(corpus)))
    requests = [dict(id='r1', entry_id='word-kandao', reason='Explain the result'),
                dict(id='r2', entry_id='word-kandao', reason='Research the components')]
    corpus['occurrences'] *= 10
    keep, jobs = guides.editorial_plan(corpus, decisions(corpus), requests)
    assert not keep and len(jobs) == 1 and len(jobs[0]['requests']) == 2
    calls = []
    async def call(self, job, *args, **kwargs):
        calls.append(job)
        return research() if job.endswith('research') else {'guides': [row()]}
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    asyncio.run(guides.update(corpus, path, requests=requests))
    assert len(calls) == 4
    calls.clear()
    asyncio.run(guides.update(corpus, path, requests=requests))
    assert not calls
    approved = json.loads(path.read_text())['guides'][0]
    assert approved['revision'] == 1
    assert len(approved['fulfilled_requests']) == 2
    requests[0]['reason'] = 'A genuinely changed request'
    assert len(guides.editorial_plan(corpus, json.loads(path.read_text()), requests)[1]) == 1


def test_changed_sense_and_not_new_examples_causes_one_job(corpus):
    previous = decisions(corpus)
    corpus['entries'][0]['senses'].append(dict(id='second-sense', definition='A new meaning'))
    keep, jobs = guides.editorial_plan(corpus, previous, [])
    assert not keep and len(jobs) == 1
    assert jobs[0]['reason'] == 'coverage_changed'


@pytest.mark.parametrize('force', [False, True])
def test_explicit_evidence_reuse_skips_research_unless_forced(corpus, tmp_path, monkeypatch, force):
    prior = decisions(corpus)
    prior['guides'][0].update(research=research(), revision=1, evidence_status='supported')
    prior['review_digest'] = decision_digest(prior['guides'])
    path = tmp_path / 'guides.json'
    path.write_text(json.dumps(prior))
    request = dict(id='rephrase', entry_id='word-kandao', reason='Make the wording clearer',
                   reuse_reviewed_evidence=True, force_research=force)
    calls = []

    async def call(self, job, *args, **kwargs):
        calls.append(kwargs['tool_profile'])
        return research() if job.endswith('research') else {'guides': [row()]}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    asyncio.run(guides.update(corpus, path, requests=[request]))
    assert calls.count('research') == (2 if force else 0)
    assert calls.count('offline') == 2


def test_missing_entry_and_invalid_requests(corpus):
    assert guides.editorial_plan(corpus, {}, [])[1][0]['reason'] == 'new_entry'
    with pytest.raises(ValueError, match='request'):
        guides.editorial_plan(corpus, decisions(corpus), [dict(id='x', entry_id='missing', reason='edit')])


def test_overlapping_registry_editors_are_rejected(corpus, tmp_path):
    import fcntl
    path = tmp_path / 'guides.json'
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='already running'):
            asyncio.run(guides.update(corpus, path))


@pytest.mark.parametrize('field', ['explanation_en', 'caveat_en', 'contribution_en'])
def test_source_specific_narration_cannot_publish(corpus, field):
    item = row()
    target = item['parts'][0] if field == 'contribution_en' else item
    target[field] = 'In the example, the two people go into the shop.'
    with pytest.raises(ValueError, match='particular source example'):
        guides.validate_rows(guides.inputs(corpus), [item])


def test_generic_sentence_context_is_not_source_narration(corpus):
    item = row()
    item['caveat_en'] = 'Its role as subject or object comes from the sentence.'
    guides.validate_rows(guides.inputs(corpus), [item])
