from copy import deepcopy
import asyncio

import pytest

from pipeline import japanese_usage_dictionary as jp
from pipeline import dictionary_meaning_guides as guides


def fixture_entries(groups):
    return [dict(id=g['id'], headword=g['headword'], reading=g['reading'],
        kind='word', senses=[dict(id=g['id'] + '-1', definition='test meaning',
                                occurrences=[u['id'] for u in g['uses']])]) for g in groups]


def test_n5_groups_inflections_without_changing_source_boundaries():
    annotation = jp.read(jp.ANNOTATION)
    original = deepcopy(annotation)
    sources, groups, uses, fingerprint = jp.source_data(annotation)
    assert len(groups) == 56
    assert len(uses) == 94
    seeing = next(g for g in groups if g['headword'] == '見る')
    assert {u['surface'] for u in seeing['uses']} == {'見ました', '見て', '見ながら'}
    assert seeing['reading'] == 'みる'
    for use in uses:
        assert sources[use['source']]['text'][use['start']:use['end']] == use['surface']
        assert use['sentence'][use['start'] - use['sentence_start']:
                               use['end'] - use['sentence_start']] == use['surface']
    assert annotation == original
    assert fingerprint == jp.source_data(annotation)[3]


def test_japanese_same_written_different_reading_never_merges():
    annotation = dict(language='japanese', level='n5', chapters=[dict(number=1,
        text='生生', segments=[dict(surface='生', type='word', lemma='生', lemma_kana=r,
        surface_kana=r, meaning_en='test', part_of_speech='noun',
        dictionary_definition_en='') for r in ['なま', 'せい']])])
    _, groups, _, _ = jp.source_data(annotation)
    assert len(groups) == 2
    assert groups[0]['id'] != groups[1]['id']


def test_japanese_occurrences_cannot_be_lost_duplicated_or_reassigned():
    _, groups, _, _ = jp.source_data()
    entries = fixture_entries(groups)
    jp.validate_entries(entries, groups)
    broken = deepcopy(entries)
    broken[0]['senses'][0]['occurrences'].append(broken[0]['senses'][0]['occurrences'][0])
    with pytest.raises(ValueError, match='exactly one'):
        jp.validate_entries(broken, groups)
    broken = deepcopy(entries)
    broken[0]['senses'][0]['occurrences'].append(broken[1]['senses'][0]['occurrences'][0])
    with pytest.raises(ValueError, match='wrong lemma'):
        jp.validate_entries(broken, groups)


def test_existing_japanese_sense_definition_and_identity_are_immutable():
    _, groups, _, _ = jp.source_data()
    entries = fixture_entries(groups)
    broken = deepcopy(entries)
    broken[0]['senses'][0]['definition'] = 'different meaning'
    with pytest.raises(ValueError, match='immutable'):
        jp.validate_entries(broken, groups, entries)
    retired = deepcopy(entries)
    retired[0]['senses'][0]['occurrences'] = []
    jp.validate_entries(retired, groups[1:], entries)


def test_japanese_collocation_keeps_the_whole_expression():
    groups, uses, _ = jp.expression_data(jp.read(jp.ANNOTATION))
    assert len(groups) == 1
    assert groups[0]['headword'] == '目が回る'
    assert groups[0]['reading'] == 'めがまわる'
    assert uses[0]['surface'] == '目が回りました'
    entries = fixture_entries(groups)
    jp.validate_entries(entries, groups)
    data = jp.payload({}, entries, uses)
    assert data['reading_bindings'][0]['surface'] == '目が回りました'
    assert data['occurrences'][0]['layer'] == 'expression'


def test_japanese_component_links_are_language_specific_and_ambiguity_safe():
    data = {'entries': [
        dict(id='compound', headword='台所', reading='だいどころ', kind='word', meaning_guide={'parts': [
            dict(text='台', contribution_en='test'), dict(text='所', contribution_en='test')]}),
        dict(id='place', headword='所', reading='ところ', kind='word', meaning_guide={'parts': []}),
    ]}
    result = jp.attach_links(deepcopy(data))
    assert result['entries'][0]['meaning_guide']['parts'][0]['character_ref']['language'] == 'japanese'
    assert [(p['start'], p['end']) for p in result['entries'][0]['meaning_guide']['parts']] == [(0, 1), (1, 2)]
    assert result['entries'][0]['meaning_guide']['parts'][1]['entry_id'] == 'place'
    assert data['entries'][0]['meaning_guide']['parts'][1].get('entry_id') is None
    ambiguous = deepcopy(data)
    ambiguous['entries'].append(dict(id='another-place', headword='所', reading='しょ', kind='word', meaning_guide={'parts': []}))
    result = jp.attach_links(ambiguous)
    assert 'entry_id' not in result['entries'][0]['meaning_guide']['parts'][1]


def test_new_examples_do_not_queue_new_word_explanations():
    _, groups, uses, _ = jp.source_data()
    data = jp.payload({}, fixture_entries(groups), uses)
    item = next(i for i in guides.inputs(data) if i['entry']['headword'] == '見る')
    changed = deepcopy(item)
    changed['examples'].append(dict(sentence='猫を見る。', gloss='see a cat', sense_id=item['entry']['senses'][0]['id']))
    assert guides.input_fingerprint(item) == guides.input_fingerprint(changed)


def test_japanese_policies_do_not_inherit_chinese_language_instructions():
    assert 'JAPANESE' in jp.POLICIES['adaptive']
    assert 'Japanese dictionaries' in jp.POLICIES['research']
    assert 'Mandarin' in jp.POLICIES['adaptive']  # explicit prohibition
    assert 'CHINESE' not in jp.POLICIES['guide']


def test_published_n5_bindings_and_guides_are_complete():
    if not jp.OUTPUT.exists():
        pytest.skip('N5 agent review not yet published')
    published = jp.read(jp.OUTPUT)
    assert published == jp.build()
    assert all(e.get('meaning_guide') for e in published['entries'])
    by_id = {u['id']: u for u in published['occurrences']}
    for binding in published['reading_bindings']:
        assert binding['occurrence_id'] in by_id
        text = published['sources'][binding['source']]['text']
        assert text[binding['start']:binding['start'] + len(binding['surface'])] == binding['surface']


def test_shared_editor_uses_japanese_policies_and_reuses_approved_guide(tmp_path, monkeypatch):
    from pipeline.agent_harness import CodexRunner
    dictionary = dict(entries=[dict(id='ja-test', headword='猫', reading='ねこ', kind='word',
        senses=[dict(id='ja-test-1', definition='cat')])], occurrences=[])
    calls = []

    async def call(self, job, prompt, schema, effort, **options):
        calls.append(job)
        assert options['tool_profile'] == 'offline'
        assert 'JAPANESE' in prompt
        assert 'Write learner-facing explanations of the INTERNAL MEANING of Chinese words' not in prompt
        assert 'CHINESE TRANSLATION POLICY' not in prompt
        return dict(needs_research=False, research_questions=[], guides=[dict(
            entry_id='ja-test', structure='single_component', explanation_en='猫 names a cat.',
            caveat_en='', claim_ids=[], investigation_questions=[], parts=[])])

    monkeypatch.setattr(CodexRunner, 'call', call)
    path = tmp_path / 'guides.json'
    result = asyncio.run(guides.update(dictionary, path, policies=jp.POLICIES, requests=[]))
    assert len(calls) == 2
    assert result['entries'][0]['meaning_guide']['editorial_route'] == 'offline_review'
    again = asyncio.run(guides.update(dictionary, path, policies=jp.POLICIES, requests=[]))
    assert again == result
    assert len(calls) == 2


def test_unchanged_real_japanese_pilot_uses_zero_model_calls(monkeypatch):
    if not jp.OUTPUT.exists():
        pytest.skip('N5 agent review not yet published')
    from pipeline.agent_harness import CodexRunner

    async def forbidden(*args, **kwargs):
        pytest.fail('Unchanged Japanese dictionary called a model')

    monkeypatch.setattr(CodexRunner, 'call', forbidden)
    before = jp.read(jp.OUTPUT)
    after = asyncio.run(jp.update())
    assert before == after
