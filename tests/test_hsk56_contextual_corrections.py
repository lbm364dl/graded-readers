"""Regression checks for evidence-based drink glosses in the new corpus."""
import json

import pytest

from pipeline import dictionary_corpus as corpus


@pytest.mark.parametrize('level', ['hsk5', 'hsk6'])
def test_drink_glosses_do_not_invent_a_beverage_subtype(level):
    chapter = json.loads(corpus.source(level).read_text())['chapters'][0]
    segments = chapter['segments']
    assert ''.join(s['text'] for s in segments) == chapter['text']
    drinks = [s for s in segments if s['text'] in {'酒', '饮酒', '喝酒'}]
    assert drinks
    assert all('alcohol' in s['meaning_en'] for s in drinks)
    assert all('wine' not in o['meaning_en'].lower()
               for o in chapter.get('grammar_overlays', []))
    changes = json.loads((corpus.DIRECTORY / 'contextual-gloss-corrections.json').read_text())
    for change in changes['changes']:
        if change['level'] != level:
            continue
        segment = segments[change['segment_index']]
        if change.get('superseded_by_boundary_review'):
            assert segment['text'] == change['current_surface']
            assert segment['meaning_en'] == change['current_gloss']
            assert 'wine' not in segment['meaning_en'].lower()
            continue
        assert segment['text'] == change['surface']
        assert segment['meaning_en'] == change['corrected']


@pytest.mark.parametrize('level, count', [('hsk5', 3), ('hsk6', 1)])
def test_naming_predicates_do_not_become_person_specific_dictionary_words(level, count):
    chapter = json.loads(corpus.source(level).read_text())['chapters'][0]
    assert not any(s['text'] in {'名备', '名羽'} for s in chapter['segments'])
    notes = [o for o in chapter['grammar_overlays']
             if o['grammar_candidate_key'] == 'given-name-predicate']
    assert len(notes) == count
    starts, offset = {}, 0
    for index, segment in enumerate(chapter['segments']):
        starts[offset] = index
        offset += len(segment['text'])
    for note in notes:
        assert chapter['text'][note['start']:note['end']] == note['text']
        index = starts[note['start']]
        predicate, name = chapter['segments'][index:index + 2]
        assert predicate['text'] == '名'
        assert predicate['type'] == 'word'
        assert predicate['pinyin'] == 'míng'
        assert name['type'] == 'name'
        assert name['text'] in {'备', '羽', '飞'}
        assert name['pinyin'] in {'Bèi', 'Yǔ', 'Fēi'}
        assert predicate['text'] + name['text'] == note['text']


def test_hsk5_name_arguments_stay_separate_taps_with_contextual_notes():
    from pipeline import chinese_reading_units as reading
    chapter = json.loads(corpus.source('hsk5').read_text())['chapters'][0]
    decisions = json.loads((corpus.DIRECTORY / 'hsk5.reading-units.json').read_text())
    reading.build(decisions, corpus.source('hsk5'))
    phrases = {'姓张', '名飞', '字翼德', '名备', '改姓曹'}
    assert not phrases.intersection(u['text'] for u in decisions['chapters']['1'])
    notes = {note['text']: note for note in chapter['grammar_overlays']}
    assert phrases <= notes.keys()
    for phrase in phrases:
        note = notes[phrase]
        assert chapter['text'][note['start']:note['end']] == phrase
        assert note['meaning_en']


def test_hsk6_sense_review_boundary_corrections_preserve_real_units():
    chapter = json.loads(corpus.source('hsk6').read_text())['chapters'][0]
    surfaces = [segment['text'] for segment in chapter['segments']]
    assert {'起义', '下寨', '听完', '太守'} <= set(surfaces)
    assert not {'信给', '之围', '都会'}.intersection(surfaces)
    assert any(surfaces[i:i + 4] == ['符水', '为', '人', '治病']
               for i in range(len(surfaces) - 3))
    # The independent editor retained the conventional compound 再拜.
    assert '再拜' in surfaces
    for note in chapter['grammar_overlays']:
        assert chapter['text'][note['start']:note['end']] == note['text']


def test_hsk6_weapon_designation_does_not_invent_eight_zhang_measurement():
    decisions = json.loads((corpus.DIRECTORY / 'hsk6.senses.json').read_text())
    entries = [entry for entry in decisions['entries'] if entry['headword'] == '丈八点钢矛']
    assert entries
    assert all('eight zhang' not in sense['definition'].lower()
               for entry in entries for sense in entry['senses'])


def test_hsk6_whole_token_reuses_existing_expression_identity():
    decisions = json.loads((corpus.DIRECTORY / 'hsk6.senses.json').read_text())
    active = [entry for entry in decisions['entries'] if entry['headword'] == '听完'
              and any(sense['occurrences'] for sense in entry['senses'])]
    assert len(active) == 1
    assert active[0]['id'] == 'zh-expression-ting-wan'
    assert all(sense['id'].startswith(active[0]['id'] + '-') for sense in active[0]['senses'])


@pytest.mark.parametrize('level', ['hsk5', 'hsk6'])
def test_confirmed_malformed_phrases_are_not_lexical_dictionary_tokens(level):
    chapter = json.loads(corpus.source(level).read_text())['chapters'][0]
    malformed = {'名备', '名羽', '张飞挺', '刘焉命', '刘备兵',
                 '家境贫寒', '常和乡中', '在家门上', '多奉', '多根',
                 '已死', '已顺', '已尽'}
    assert not malformed.intersection(s['text'] for s in chapter['segments'])
    # The conventional word 已经 remains whole; the policy isn't character splitting.
    assert any(s['text'] == '已经' for s in chapter['segments'])


@pytest.mark.parametrize('level', ['hsk5', 'hsk6'])
def test_complete_corrected_chapter_normalizes_for_app(level, tmp_path, monkeypatch):
    from scripts import generate_app_content_json as assets
    monkeypatch.setattr(assets, 'ASSET_ROOT', tmp_path)
    chapters = assets.chapters_from_markdown(corpus.source(level).with_name(f'{level}.md'))
    paths = assets.load_annotations(corpus.source(level), chapters, language='chinese',
                                   book_id='sanguoyanyi', level_key=level)
    assert len(paths) == 1
    output = json.loads((tmp_path / paths[0].removeprefix('assets/')).read_text())
    original = json.loads(corpus.source(level).read_text())['chapters'][0]
    assert output['text'] == original['text'] == chapters[0]['content']
    assert ''.join(s['text'] for s in output['segments']) == original['text']
    assert output['grammar_overlays'] == original['grammar_overlays']
