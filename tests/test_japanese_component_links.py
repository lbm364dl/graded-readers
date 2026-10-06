from copy import deepcopy
import asyncio
import json
import pytest
from pipeline import japanese_component_links as links
from pipeline import japanese_usage_dictionary as words


def test_part_routing_keeps_inflected_surfaces_and_word_formation_evidence():
    dictionary = dict(entries=[dict(id='food', headword='食べ物', reading='たべもの',
        senses=[dict(id='food-1', definition='food')], meaning_guide=dict(
            explanation_en='The stem of eat plus thing forms food.',
            parts=[dict(text='食べ', contribution_en='Stem of 食べる: eating.'),
                   dict(text='物', contribution_en='thing')]))])
    original = deepcopy(dictionary)
    rows = links.requests(dictionary)
    assert len(rows) == 1
    assert rows[0]['text'] == '食べ'
    assert rows[0]['parent_entry_id'] == 'food'
    assert rows[0]['contribution_en'] == 'Stem of 食べる: eating.'
    assert dictionary == original
    assert links.requests(dictionary) == rows
    dictionary['entries'][0]['meaning_guide']['parts'][0]['grammar_entry_ids'] = ['stem']
    assert links.requests(dictionary) == []


def test_component_aliases_restore_only_known_ids_and_report_missing_destinations():
    rows = [dict(id='long-stable-a'), dict(id='long-stable-b')]
    compact, aliases = links.routing_payload(rows)
    assert [r['id'] for r in compact] == ['P0', 'P1']
    aliases['long-word-id'] = 'W0'
    data = dict(entries=[], bindings=[dict(candidate_id='P0', entry_id='W0'), dict(candidate_id='typo')])
    restored = links.routing_ids(data, {short: canonical for canonical, short in aliases.items()})
    assert [b['candidate_id'] for b in restored['bindings']] == ['long-stable-a', 'typo']
    assert restored['bindings'][0]['entry_id'] == 'long-word-id'
    assert links.routing_ids(restored, aliases) == data
    data['bindings'] = [dict(candidate_id=b['candidate_id'], entry_id='',
        new_headword='', new_reading='', grammar_entry_ids=['lesson']) for b in restored['bindings']]
    with pytest.raises(ValueError, match='missing=.*long-stable-b.*unknown=.*typo'):
        links.validate(data, rows, dict(entries=[]), dict(entries=[dict(id='lesson')]))


def test_duplicate_functional_identity_is_named_and_different_readings_remain_distinct():
    rows = [dict(id='part')]
    data = dict(entries=[dict(headword='でも', reading='でも', kind='word', definitions=['even'])],
        bindings=[dict(candidate_id='part', entry_id='', new_headword='でも',
            new_reading='でも', grammar_entry_ids=[])])
    dictionary = dict(entries=[dict(id='demo', headword='でも', reading='でも', kind='particle')])
    with pytest.raises(ValueError, match='Duplicate component lexical identity: でも.*grammar_entry_ids'):
        links.validate(data, rows, dictionary, dict(entries=[]))
    data['entries'][0].update(headword='生', reading='なま', definitions=['raw'])
    data['bindings'][0].update(new_headword='生', new_reading='なま')
    dictionary['entries'][0].update(headword='生', reading='せい', kind='word')
    links.validate(data, rows, dictionary, dict(entries=[]))


def test_reviewed_stem_routes_to_real_base_word_and_grammar(tmp_path, monkeypatch):
    monkeypatch.setattr(links, 'REGISTRY', tmp_path/'bindings.json')
    monkeypatch.setattr(words, 'COMPONENTS', tmp_path/'components.json')
    dictionary = dict(entries=[dict(id='food', headword='食べ物', reading='たべもの', kind='word',
        senses=[dict(id='food-1', definition='food')], meaning_guide=dict(
            explanation_en='Eat plus thing makes food.', parts=[
                dict(text='食べ', contribution_en='Stem of eat.'),
                dict(text='物', contribution_en='thing')]))])
    rows = links.requests(dictionary)
    grammar = dict(entries=[dict(id='stem', title='ます-stem', reading='ますごかん',
        kind='conjugation', summary_en='Verb stem')])
    data = dict(entries=[dict(headword='食べる', reading='たべる', kind='word', definitions=['to eat'])],
        bindings=[dict(candidate_id=rows[0]['id'], entry_id='', new_headword='食べる',
            new_reading='たべる', grammar_entry_ids=['stem'])])
    calls = []
    class Runner:
        async def call(self, job, *args, **kwargs):
            calls.append(kwargs['tool_profile'])
            payload = json.JSONDecoder().raw_decode(args[0].split('JSON only.\n', 1)[1])[0]
            assert payload['parts'][0]['id'] == 'P0'
            result = deepcopy(data)
            result['bindings'][0]['candidate_id'] = 'P0'
            return result
    new = asyncio.run(links.resolve(Runner(), dictionary, grammar))
    assert calls == ['offline', 'offline']
    assert [e['headword'] for e in new] == ['食べる']
    new[0]['meaning_guide'] = dict(parts=[])
    dictionary['entries'].extend(new)
    links.attach(dictionary)
    part = dictionary['entries'][0]['meaning_guide']['parts'][0]
    assert part['text'] == '食べ'
    assert part['entry_id'] == new[0]['id']
    assert part['grammar_entry_ids'] == ['stem']
    assert new[0]['component_uses'] == [dict(parent_entry_id='food', contribution_en='Stem of eat.')]
    links.attach(dictionary)
    assert len(new[0]['component_uses']) == 1  # Rebuilding never duplicates reverse links.
    assert asyncio.run(links.resolve(Runner(), dictionary, grammar)) == []
    assert len(calls) == 2
    bad = deepcopy(data)
    bad['bindings'][0]['grammar_entry_ids'] = ['invented']
    with pytest.raises(ValueError, match='Unknown component grammar'):
        links.validate(bad, rows, dict(entries=dictionary['entries'][:1]), grammar)
    bad['bindings'][0]['grammar_entry_ids'] = ['stem', 'stem']
    with pytest.raises(ValueError, match='Duplicate component grammar'):
        links.validate(bad, rows, dict(entries=dictionary['entries'][:1]), grammar)
    bad = deepcopy(data)
    bad['bindings'][0]['new_headword'] = '飲む'
    bad['bindings'][0]['new_reading'] = 'のむ'
    with pytest.raises(ValueError, match='Undefined new component word: 飲む') as failure:
        links.validate(bad, rows, dict(entries=dictionary['entries'][:1]), grammar)
    assert 'Unrequested component entries: 食べる' in str(failure.value)
    bad = deepcopy(data)
    bad['bindings'][0].update(new_headword='', new_reading='')
    with pytest.raises(ValueError, match='Unrequested component entries: 食べる'):
        links.validate(bad, rows, dict(entries=dictionary['entries'][:1]), grammar)


def test_existing_component_word_alias_is_restored_before_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(links, 'REGISTRY', tmp_path/'bindings.json')
    monkeypatch.setattr(words, 'COMPONENTS', tmp_path/'components.json')
    dictionary = dict(entries=[
        dict(id='food', headword='食べ物', reading='たべもの', kind='word', senses=[],
            meaning_guide=dict(explanation_en='Eat plus thing.', parts=[
                dict(text='食べ', contribution_en='Stem of eat.')])),
        dict(id='existing-verb', headword='食べる', reading='たべる', kind='word',
            senses=[], meaning_guide=dict(parts=[]))])
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            payload = json.JSONDecoder().raw_decode(prompt.split('JSON only.\n', 1)[1])[0]
            assert payload['words'][1]['id'] == 'W1'
            return dict(entries=[], bindings=[dict(candidate_id='P0', entry_id='W1',
                new_headword='', new_reading='', grammar_entry_ids=[])])
    assert asyncio.run(links.resolve(Runner(), dictionary, dict(entries=[]))) == []
    binding = words.read(links.REGISTRY)['bindings'][0]
    assert binding['entry_id'] == 'existing-verb'
    assert binding['id'].startswith('ja-part-')
    links.attach(dictionary)
    assert dictionary['entries'][0]['meaning_guide']['parts'][0]['entry_id'] == 'existing-verb'
    assert links.requests(dictionary) == []


def test_functional_catalog_exposes_existing_identities_without_word_destinations(tmp_path, monkeypatch):
    monkeypatch.setattr(links, 'REGISTRY', tmp_path/'bindings.json')
    dictionary = dict(entries=[
        dict(id='parent', headword='それでも', reading='それでも', kind='word', senses=[],
            meaning_guide=dict(explanation_en='Nevertheless.', parts=[
                dict(text='でも', contribution_en='Concessive connection.', entry_id='demo')])),
        dict(id='demo', headword='でも', reading='でも', kind='particle', senses=[],
            meaning_guide=dict(parts=[]))])
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            payload = json.JSONDecoder().raw_decode(prompt.split('JSON only.\n', 1)[1])[0]
            assert payload['functional_entries'] == [dict(headword='でも', reading='でも', kind='particle')]
            assert all(e['headword'] != 'でも' for e in payload['words'])
            return dict(entries=[], bindings=[dict(candidate_id='P0', entry_id='',
                new_headword='', new_reading='', grammar_entry_ids=['concession'])])
    grammar = dict(entries=[dict(id='concession', title='でも', reading='でも',
        kind='particle', summary_en='Concessive connection')])
    assert asyncio.run(links.resolve(Runner(), dictionary, grammar)) == []
    links.attach(dictionary)
    assert dictionary['entries'][0]['meaning_guide']['parts'][0]['grammar_entry_ids'] == ['concession']
    assert links.requests(dictionary) == []


def test_functional_component_uses_precise_grammar_and_invalidates_stale_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(links, 'REGISTRY', tmp_path/'bindings.json')
    dictionary = dict(entries=[
        dict(id='parent', headword='気の毒', reading='きのどく', kind='word',
            senses=[], meaning_guide=dict(explanation_en='An expression.', parts=[
                dict(text='の', contribution_en='Connects the nouns.', entry_id='no'),
                dict(text='毒', contribution_en='poison', entry_id='poison')])),
        dict(id='no', headword='の', reading='の', kind='particle',
            meaning_guide=dict(parts=[])),
        dict(id='poison', headword='毒', reading='どく', kind='word',
            meaning_guide=dict(parts=[])),
    ])
    rows = links.requests(dictionary)
    assert len(rows) == 1 and rows[0]['text'] == 'の'
    binding = {k: rows[0][k] for k in
        ('id', 'parent_entry_id', 'part_index', 'text', 'contribution_en')}
    binding.update(entry_id='', grammar_entry_ids=['noun-linking-no'])
    words.atomic_json(links.REGISTRY, dict(reviewed=True, bindings=[binding],
        review_digest=words.decision_digest([binding])))
    original = deepcopy(dictionary)
    links.attach(dictionary)
    part = dictionary['entries'][0]['meaning_guide']['parts'][0]
    assert 'entry_id' not in part
    assert part['grammar_entry_ids'] == ['noun-linking-no']
    assert 'component_uses' not in dictionary['entries'][1]
    assert dictionary['entries'][2]['component_uses'] == [
        dict(parent_entry_id='parent', contribution_en='poison')]
    assert links.requests(dictionary) == []
    original['entries'][0]['meaning_guide']['parts'][0]['contribution_en'] = 'A different role.'
    links.attach(original)
    assert original['entries'][0]['meaning_guide']['parts'][0]['entry_id'] == 'no'
    assert len(links.requests(original)) == 1
