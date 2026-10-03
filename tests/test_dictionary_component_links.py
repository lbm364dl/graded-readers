from copy import deepcopy

import pytest

from pipeline.dictionary_component_links import extend


def entry(eid, word, parts):
    return dict(id=eid, headword=word, senses=[dict(id=eid + '-s1')],
                meaning_guide=dict(parts=[dict(text=t, contribution_en=c)
                                          for t, c in parts]))


def test_links_reuse_entries_and_ground_missing_components_without_new_occurrences():
    original = dict(entries=[
        entry('sigh', '叹气', [('叹', 'expressive sighing'), ('气', 'breath')]),
        entry('see', '看到', [('看', 'looking'), ('到', 'achieved result')]),
        entry('look', '看', []),
        entry('arrive', '到', []),
    ], occurrences=[dict(id='untouched')], reading_bindings=[dict(id='whole-unit')])
    snapshot = deepcopy(original)
    output = extend(original)
    assert original == snapshot
    assert output['occurrences'] == original['occurrences']
    assert output['reading_bindings'] == original['reading_bindings']
    by_id = {e['id']: e for e in output['entries']}
    parts = by_id['see']['meaning_guide']['parts']
    assert [p['entry_id'] for p in parts] == ['look', 'arrive']
    assert by_id['arrive']['character_ref']['character'] == '到'
    assert by_id['look']['character_ref']['dictionary'] == 'hanzi-etymology'
    assert all(p['sense_id'] is None for p in parts)  # no guessed sense match
    for part in by_id['sigh']['meaning_guide']['parts']:
        assert part['entry_id'] is None
        assert part['character_ref']['character'] == part['text']
        assert part['character_ref']['dictionary'] == 'hanzi-etymology'
    assert len(output['entries']) == len(original['entries'])
    assert extend(original) == output


def test_shared_characters_preserve_distinct_parent_contributions_and_stable_refs():
    a = entry('a', '甲乙', [('甲', 'first contribution'), ('乙', 'second')])
    b = entry('b', '甲丙', [('甲', 'different contribution'), ('丙', 'third')])
    first = extend(dict(entries=[a], occurrences=[]))
    both = extend(dict(entries=[b, a], occurrences=[]))
    old = first['entries'][0]['meaning_guide']['parts'][0]
    new = both['entries'][0]['meaning_guide']['parts'][0]
    assert old['character_ref'] == new['character_ref']
    assert old['contribution_en'] != new['contribution_en']


def test_whole_word_explanations_do_not_self_link_and_ambiguous_entries_are_not_guessed():
    output = extend(dict(entries=[
        entry('whole', '所以', [('所以', 'conventional whole')]),
        entry('a', '甲乙', [('甲', 'contextual'), ('乙', 'second')]),
        entry('homograph-1', '甲', []), entry('homograph-2', '甲', []),
    ], occurrences=[]))
    assert output['entries'][0]['meaning_guide']['parts'][0]['entry_id'] is None
    part = output['entries'][1]['meaning_guide']['parts'][0]
    assert part['link_status'] == 'character'


def test_multi_character_component_requires_a_real_entry():
    parent = entry('army', '黄巾军', [('黄巾', 'identifying label'), ('军', 'army')])
    with pytest.raises(ValueError, match='component word entry'):
        extend(dict(entries=[parent], occurrences=[]))
    word = entry('turban', '黄巾', [('黄', 'yellow'), ('巾', 'cloth')])
    output = extend(dict(entries=[parent, word], occurrences=[]))
    assert output['entries'][0]['meaning_guide']['parts'][0]['entry_id'] == 'turban'
    assert output['entries'][1]['component_uses'][0]['parent_entry_id'] == 'army'
    assert output['entries'][1]['meaning_guide']['parts'][0]['character_ref']['character'] == '黄'


def test_rejects_incorrect_spans():
    with pytest.raises(ValueError, match='reconstruct'):
        extend(dict(entries=[entry('a', '甲乙', [('乙甲', 'wrong')])], occurrences=[]))
