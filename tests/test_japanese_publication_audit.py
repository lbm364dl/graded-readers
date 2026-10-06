from copy import deepcopy
import pytest
from pipeline import japanese_usage_dictionary as words
from pipeline import japanese_grammar_dictionary as grammar
from pipeline import japanese_component_links as components
from pipeline.japanese_dictionary_publication_audit import audit


@pytest.fixture
def publication(monkeypatch):
    source = 'assets/annotations/japanese_fake_n4.json'
    sources = {source: dict(level='N4', text='食べ物')}
    use = dict(id='word-use', source=source)
    dictionary = dict(sources=sources, occurrences=[use], entries=[
        dict(id='food', headword='食べ物', reading='たべもの', kind='word', meaning_guide=dict(parts=[
            dict(text='食べ', entry_id='eat', contribution_en='eat')])),
        dict(id='eat', headword='食べる', reading='たべる', kind='word', meaning_guide=dict(parts=[]),
            component_uses=[dict(parent_entry_id='food', contribution_en='eat')])])
    lessons = dict(sources=sources, entries=[dict(id='stem')], occurrences=[
        dict(candidate_id='form-use', source=source, layer='form',
            start=0, surface='食べ物', display_base_form='食べ物',
            display_meaning_en='food', display_base_meaning_en='food')])
    assets = {words.OUTPUT: deepcopy(dictionary), grammar.OUTPUT: deepcopy(lessons),
        words.ROOT/'app'/source: dict(text='食べ物', segments=[dict(text='食べ物')])}
    monkeypatch.setattr(words, 'annotations', lambda: [dict(level='n4')])
    monkeypatch.setattr(words, 'build', lambda: dictionary)
    monkeypatch.setattr(grammar, 'build', lambda: lessons)
    monkeypatch.setattr(words, 'read', lambda path, *args: assets[path])
    monkeypatch.setattr(words, 'source_data', lambda *args: (sources, [], [use], 'fp'))
    monkeypatch.setattr(words, 'expression_data', lambda *args: ([], [], 'fp'))
    monkeypatch.setattr(grammar, 'candidates', lambda *args: (sources, [dict(id='form-use')]))
    monkeypatch.setattr(components, 'requests', lambda dictionary: [])
    return dictionary, lessons, assets


def test_publication_audit_requires_full_requested_scope(publication):
    with pytest.raises(ValueError, match='Incomplete level coverage'):
        audit()
    assert audit(['n4'])['coverage']['assets/annotations/japanese_fake_n4.json']['form_steps'] == 1


@pytest.mark.parametrize('problem,message', [
    ('component', 'Unreviewed dictionary component destinations'),
    ('asset', 'App dictionary assets differ'),
    ('grammar_link', 'Dangling component grammar link'),
    ('word_link', 'Dangling lexical component link'),
    ('reverse', 'Stale or missing reverse component links'),
    ('form_meaning', 'Unreviewed whole-form meanings'),
    ('source_text', 'App source text differs'),
    ('word_occurrence', 'Missing or duplicated source word occurrences'),
    ('grammar_occurrence', 'Missing or duplicated grammar/form-step occurrences'),
    ('compatibility', 'Invalid component compatibility destination'),
    ('base_meaning', 'Inconsistent reviewed chain base meanings'),
])
def test_publication_audit_rejects_partial_or_stale_publication(publication, monkeypatch, problem, message):
    dictionary, lessons, assets = publication
    if problem == 'component':
        monkeypatch.setattr(components, 'requests', lambda dictionary: [dict(id='pending')])
    elif problem == 'asset':
        assets[words.OUTPUT]['entries'].pop()
    elif problem == 'grammar_link':
        dictionary['entries'][0]['meaning_guide']['parts'][0]['grammar_entry_ids'] = ['missing']
    elif problem == 'word_link':
        dictionary['entries'][0]['meaning_guide']['parts'][0]['entry_id'] = 'missing'
    elif problem == 'reverse':
        dictionary['entries'][1]['component_uses'] = []
    elif problem == 'form_meaning':
        lessons['occurrences'][0]['display_meaning_en'] = ''
    elif problem == 'source_text':
        assets[words.ROOT/'app'/'assets/annotations/japanese_fake_n4.json']['text'] = 'stale'
    elif problem == 'word_occurrence':
        dictionary['occurrences'].append(deepcopy(dictionary['occurrences'][0]))
    elif problem == 'grammar_occurrence':
        lessons['occurrences'].append(deepcopy(lessons['occurrences'][0]))
    elif problem == 'compatibility':
        dictionary['entries'][1]['superseded_by'] = 'food'
    elif problem == 'base_meaning':
        lessons['occurrences'].append(dict(lessons['occurrences'][0],
            candidate_id='form-use-2', display_base_meaning_en='a different base meaning'))
        monkeypatch.setattr(grammar, 'candidates', lambda *args: (lessons['sources'],
            [dict(id='form-use'), dict(id='form-use-2')]))
    if problem != 'asset':
        assets[words.OUTPUT] = deepcopy(dictionary)
        assets[grammar.OUTPUT] = deepcopy(lessons)
    with pytest.raises(ValueError, match=message):
        audit(['n4'])
