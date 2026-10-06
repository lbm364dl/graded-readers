from copy import deepcopy

import pytest

from pipeline.lexical_boundary_migration import corrected_chapter, migrate_links
from pipeline.usage_dictionary import decision_digest


def test_exact_name_predicate_split_retains_prose_and_later_indices():
    chapter = dict(text='张飞挺矛', segments=[
        dict(text='张飞挺', type='word', pinyin='Zhāng Fēi tǐng', meaning_en='Zhang Fei brandishes',
             composition_en='obsolete combined explanation', subsegments=[{'text': '张飞'}]),
        dict(text='矛', type='word', pinyin='máo', meaning_en='spear')], grammar_overlays=[])
    revision = dict(first_segment=0, last_segment=0, text='张飞挺', segments=[
        dict(text='张飞', type='name', pinyin='Zhāng Fēi', meaning_en='Zhang Fei'),
        dict(text='挺', type='word', pinyin='tǐng', meaning_en='brandish')],
        grammar_explanation_en='张飞 is the subject; 挺 is the verb.')
    original = deepcopy(chapter)
    result, indices = corrected_chapter(chapter, [revision], 'hsk5')
    assert chapter == original
    assert result['text'] == chapter['text']
    assert [s['text'] for s in result['segments']] == ['张飞', '挺', '矛']
    assert indices == {0: 0, 1: 2}
    assert result['segments'][0]['lookup_reason'] == 'proper_name'
    assert result['segments'][0]['composition_en'] == ''
    assert result['segments'][1]['subsegments'] == []
    assert result['segments'][2] == chapter['segments'][1]
    note = result['grammar_overlays'][0]
    assert result['text'][note['start']:note['end']] == note['text']
    revision['text'] = '张飞拿'
    with pytest.raises(ValueError, match='changes source'):
        corrected_chapter(chapter, [revision], 'hsk5')


def test_exact_compound_merge_and_overlapping_edits_fail_closed():
    chapter = dict(text='五原', segments=[
        dict(text='五', type='word', pinyin='wǔ', meaning_en='five'),
        dict(text='原', type='word', pinyin='yuán', meaning_en='plain')])
    revision = dict(first_segment=0, last_segment=1, text='五原', segments=[
        dict(text='五原', type='name', pinyin='Wǔyuán', meaning_en='Wuyuan')])
    result, indices = corrected_chapter(chapter, [revision], 'hsk6')
    assert [s['text'] for s in result['segments']] == ['五原']
    assert indices == {0: 0, 1: 0}
    with pytest.raises(ValueError, match='overlapping'):
        corrected_chapter(chapter, [revision, revision], 'hsk6')


def occurrence(identity, start, end, surface, reading, gloss):
    return dict(id=identity, source='test', start=start, end=end, surface=surface,
                reading=reading, gloss=gloss, sentence='名备说。', sentence_start=0)


def test_merging_a_story_term_preserves_its_contextual_explanation():
    story = dict(story_term='孝廉', story_term_meaning_en='recommended candidate',
                 story_term_importance_en='Marks entry into official service.')
    chapter = dict(text='孝廉', segments=[
        dict(text='孝', type='word', pinyin='xiào', meaning_en='filial piety', **story),
        dict(text='廉', type='word', pinyin='lián', meaning_en='integrity', **story)])
    revision = dict(first_segment=0, last_segment=1, text='孝廉', segments=[
        dict(text='孝廉', type='word', pinyin='xiàolián', meaning_en='recommended candidate')])
    result, _ = corrected_chapter(chapter, [revision], 'hsk5')
    assert all(result['segments'][0][key] == value for key, value in story.items())
    assert result['segments'][0]['learning_focus'] == 'lookup'


def test_rebase_keeps_only_valid_unaffected_approvals_and_retires_old_bundle():
    entries = [dict(id='bundle', headword='名备', reading='míng Bèi', kind='word',
                    senses=[dict(id='bundle-s1', definition='named Bei', occurrences=['old0'])]),
               dict(id='say', headword='说', reading='shuō', kind='word',
                    senses=[dict(id='say-s1', definition='say', occurrences=['old1'])])]
    decisions = dict(entries=entries, reviewed=True, review_digest=decision_digest(entries))
    old = [occurrence('old0', 0, 2, '名备', 'míng Bèi', 'named Bei'),
           occurrence('old1', 2, 3, '说', 'shuō', 'say')]
    new = [occurrence('new0', 0, 1, '名', 'míng', 'be named'),
           occurrence('new1', 1, 2, '备', 'Bèi', 'Bei'),
           occurrence('new2', 2, 3, '说', 'shuō', 'say')]
    result, unlinked, mapping = migrate_links(decisions, old, new, 'new-fingerprint', {'名备', '名', '备'})
    assert mapping == {'old1': 'new2'}
    assert result['entries'][0]['senses'][0]['occurrences'] == []
    assert result['entries'][1]['senses'][0]['occurrences'] == ['new2']
    assert set(result['reviewed_word_fingerprints']) == {'说'}
    assert [o['surface'] for o in unlinked] == ['名', '备']
    assert result['reviewed'] is False
    assert decisions['entries'][1]['senses'][0]['occurrences'] == ['old1']
    changed = deepcopy(new)
    changed[-1]['gloss'] = 'tell'
    with pytest.raises(ValueError, match='changed linguistic evidence'):
        migrate_links(decisions, old, changed, 'new-fingerprint', {'名备', '名', '备'})
    with pytest.raises(ValueError, match='Unrelated occurrence disappeared'):
        migrate_links(decisions, old, new[:-1], 'new-fingerprint', {'名备', '名', '备'})
