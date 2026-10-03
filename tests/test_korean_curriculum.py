from collections import Counter
from copy import deepcopy

import pytest

from pipeline import korean_curriculum as curriculum
from pipeline.korean_readability import classify_segment


def source_id(kind, number):
    return f'nikl2017-{kind}-{number:05d}'


def sample(grammar_number=21):
    chapter = {'segments': [{'text': '아이였습니다', 'type': 'word',
        'meaning_en': 'was a child', 'lexical': {'kind': 'vocabulary', 'id': '아이01/명'}}],
        'grammar_links': [{'segment_index': 0, 'entry_id': 'past-ass-eoss'}]}
    bindings = {'level_reason_en': 'One simple sentence with one reviewed transformation.', 'prose_revision_reason_en': '', 'bindings': [
        {'kind': 'vocabulary', 'entry_id': '아이01/명', 'source_ids': [source_id('vocabulary', 405)],
         'equivalence': 'listed', 'analysis_en': 'The noun meaning child, not the exclamation.', 'optional_reason_en': ''},
        {'kind': 'grammar', 'entry_id': 'past-ass-eoss', 'source_ids': [source_id('grammar', grammar_number)],
         'equivalence': 'listed', 'analysis_en': 'Synthetic grammar mapping fixture.', 'optional_reason_en': ''}]}
    return chapter, bindings


def test_curriculum_has_six_cumulative_levels_and_preserves_source_fields():
    assert Counter(e['level'] for e in curriculum.entries('vocabulary')) == {
        1: 735, 2: 1100, 3: 1655, 4: 2200, 5: 2365, 6: 2580}
    assert len(curriculum.entries('vocabulary', 2)) == 1835
    assert len(curriculum.entries('grammar', 1)) == 45
    entry = curriculum.vocabulary_candidates('있다')[0]
    assert entry['source_fields']['어휘'] == '있다01/있다02'
    assert entry['source_fields']['품사'] == '동사/형용사'


def test_candidates_keep_distinct_homonyms_and_do_not_infer_derived_grades():
    candidates = curriculum.vocabulary_candidates('아이')
    assert [(e['level'], e['source_fields']['품사']) for e in candidates] == [(1, '명사'), (3, '감탄사')]
    assert not curriculum.vocabulary_candidates('일하다')
    assert curriculum.vocabulary_candidates('일')
    assert curriculum.vocabulary_candidates('하다')


def test_new_source_words_are_available_without_merging_legacy_identities():
    from pipeline.korean_contracts import lexical_catalog
    from pipeline.korean_readability import vocabulary
    candidates = lexical_catalog()
    assert {e['id'] for e in candidates['아이']} == {'아이01/명', '아이02/감'}
    notebook = candidates['수첩'][0]
    assert notebook['curriculum_level'] == 1
    assert notebook['grade'] is None
    assert vocabulary()[notebook['id']] is None
    assert {e['pos'] for e in candidates['십만']} == {'수사', '관형사'}
    assert len({e['id'] for e in candidates['십만']}) == 2


def test_optional_grammar_keeps_its_true_grade_and_needs_reviewed_rationale():
    chapter, bindings = sample()
    grade = curriculum.evaluate_bindings(chapter, bindings)
    assert grade['passes']
    assert grade['optional_grammar_reasons'] == {}
    chapter, bindings = sample(63)  # Present noun modifier, curriculum Level 2.
    with pytest.raises(ValueError, match='optional learning rationale'):
        curriculum.evaluate_bindings(chapter, bindings)
    bindings['bindings'][1]['optional_reason_en'] = "One brief common modifier; outside this chapter's learning goals."
    grade = curriculum.evaluate_bindings(chapter, bindings)
    assert grade['passes']
    assert grade['extra_vocabulary_ratio'] == 0
    assert grade['above_level_grammar'] == {'past-ass-eoss': 2}
    assert grade['above_level_grammar_occurrences'] == {'past-ass-eoss': 1}
    # At Level 2 the same pattern is a target, not optional.
    bindings['bindings'][1]['optional_reason_en'] = ''
    assert curriculum.evaluate_bindings(chapter, bindings, level=2)['passes']
    bindings['prose_revision_reason_en'] = 'The chapter depends on too many advanced constructions; simplify these clauses.'
    with pytest.raises(ValueError, match='requires prose revision'):
        curriculum.evaluate_bindings(chapter, bindings)


def test_a_prerequisite_inside_a_listed_construction_can_use_its_grade():
    chapter, bindings = sample(65)  # Standalone prospective modifier: Level 2.
    with pytest.raises(ValueError, match='optional learning rationale'):
        curriculum.evaluate_bindings(chapter, bindings)
    bindings['bindings'][1].update(equivalence='grammatical',
        source_ids=[source_id('grammar', 42)],
        analysis_en='Synthetic occurrence: the modifier is only a prerequisite inside the Level 1 ability construction.')
    assert curriculum.evaluate_bindings(chapter, bindings)['passes']


def test_missing_unknown_and_duplicate_evidence_fail_closed():
    chapter, bindings = sample()
    missing = deepcopy(bindings)
    missing['bindings'].pop()
    with pytest.raises(ValueError, match='Incomplete'):
        curriculum.evaluate_bindings(chapter, missing)
    unknown = deepcopy(bindings)
    unknown['bindings'][0]['source_ids'] = ['invented']
    with pytest.raises(ValueError, match='unknown'):
        curriculum.evaluate_bindings(chapter, unknown)
    duplicate = deepcopy(bindings)
    duplicate['bindings'].append(duplicate['bindings'][0])
    with pytest.raises(ValueError, match='Duplicate'):
        curriculum.evaluate_bindings(chapter, duplicate)


def test_productive_binding_carries_all_prerequisites_and_uses_highest_grade():
    chapter, bindings = sample()
    row = bindings['bindings'][0]
    row['equivalence'] = 'productive'
    with pytest.raises(ValueError, match='explicit prerequisites'):
        curriculum.evaluate_bindings(chapter, bindings)
    row['source_ids'].append(source_id('vocabulary', 1439))  # 옛날, Level 2
    grade = curriculum.evaluate_bindings(chapter, bindings)
    assert grade['lexical_levels']['아이01/명'] == 2
    assert not grade['passes']


def test_app_classification_uses_curriculum_rather_than_old_a_band():
    chapter, bindings = sample()
    segment = {'type': 'word', 'lexical': {'kind': 'vocabulary', 'id': '옛날/명'}}
    evidence = {'evaluation': {'lexical_levels': {'옛날/명': 2}}}
    assert classify_segment(segment, evidence)['lookup_reason'] == 'above_level'
    assert classify_segment(segment, evidence)['matched_curriculum_level'] == 2
    assert classify_segment(chapter['segments'][0], {
        'evaluation': curriculum.evaluate_bindings(chapter, bindings)})['curriculum_status'] == 'in_level'


def test_direct_grammar_tap_is_optional_without_marking_its_lexical_base_extra():
    chapter, bindings = sample(63)
    bindings['bindings'][1]['optional_reason_en'] = 'One common short pattern.'
    evidence = {'evaluation': curriculum.evaluate_bindings(chapter, bindings)}
    segment = {'type': 'word', 'lexical': {'kind': 'grammar', 'id': 'past-ass-eoss'}}
    assert classify_segment(segment, evidence)['lookup_reason'] == 'above_level_grammar'
    assert classify_segment(segment, evidence)['matched_curriculum_level'] == 2
    assert classify_segment(chapter['segments'][0], evidence)['learning_focus'] == 'target'


def test_prompt_projection_keeps_semantic_evidence_and_distinct_occurrences():
    raw = curriculum.entries('vocabulary', 1)
    compact = curriculum.prompt_entries('vocabulary', 1)
    assert [(e['id'], e['level']) for e in compact] == [(e['id'], e['level']) for e in raw]
    for original, projected in zip(raw, compact):
        for field in ('어휘', '품사', '길잡이말'):
            if original['source_fields'][field] is not None:
                assert projected['source_fields'][field] == original['source_fields'][field]
    assert '전체 번호' not in compact[0]['source_fields']
    assert '전체 번호' in raw[0]['source_fields']  # The pinned source is untouched.
    chapter, _ = sample()
    chapter['text'] = '아이였습니다 아이였습니다'
    chapter['segments'].append(deepcopy(chapter['segments'][0]))
    view = curriculum.chapter_view(chapter)
    assert [s['segment_index'] for s in view['word_occurrences']] == [0, 1]
    assert view['grammar_links'] == chapter['grammar_links']
    assert view['text'] == chapter['text']


def test_unlisted_grammar_is_optional_without_inventing_a_grade():
    chapter, bindings = sample()
    row = bindings['bindings'][1]
    row.update(equivalence='unlisted', source_ids=[],
               analysis_en='This reviewed function has no matching curriculum row.',
               optional_reason_en='One familiar short expression; no assumed source grade.')
    grade = curriculum.evaluate_bindings(chapter, bindings)
    assert grade['grammar_levels']['past-ass-eoss'] is None
    assert grade['unlisted_grammar'] == ['past-ass-eoss']
    assert grade['above_level_grammar'] == {}
    assert grade['optional_grammar_reasons']
    segment = {'type': 'word', 'lexical': {'kind': 'grammar', 'id': 'past-ass-eoss'}}
    assert classify_segment(segment, {'evaluation': grade})['lookup_reason'] == 'unlisted_grammar'
    row['source_ids'] = [source_id('grammar', 21)]
    with pytest.raises(ValueError, match='must not imply a source grade'):
        curriculum.evaluate_bindings(chapter, bindings)


def test_unlisted_ordinary_words_count_as_extra_not_beginner():
    chapter, bindings = sample()
    bindings['bindings'][0].update(equivalence='unlisted', source_ids=[],
                                   analysis_en='Reviewed word absent from the source catalog.')
    grade = curriculum.evaluate_bindings(chapter, bindings)
    assert grade['lexical_levels']['아이01/명'] is None
    assert grade['extra_vocabulary_ratio'] == 1
    assert not grade['passes']
    assert classify_segment(chapter['segments'][0], {'evaluation': grade})['curriculum_status'] == 'unlisted'

def test_derived_word_search_includes_possible_prerequisites_without_grading():
    assert not curriculum.vocabulary_candidates('걱정하다')
    hits = curriculum.vocabulary_context_candidates('걱정하다')
    assert {'걱정', '하다01'} <= {e['source_fields']['어휘'] for e in hits}
    chapter, bindings = sample()
    bindings['bindings'][0]['source_ids'] = []
    with pytest.raises(ValueError, match='Missing'):
        curriculum.evaluate_bindings(chapter, bindings)
    # Unrelated homonyms remain candidates, not silently approved identities.
    assert len(curriculum.vocabulary_context_candidates('감사하다')) >= 2

def test_grammar_counts_keep_distinct_positions_and_deduplicate_layers():
    chapter, _ = sample()
    chapter['grammar_links'] += [
        dict(chapter['grammar_links'][0]),
        {'segment_index': 1, 'entry_id': 'past-ass-eoss'}]
    chapter['segments'].append({'type': 'word', 'lexical': {
        'kind': 'grammar', 'id': 'past-ass-eoss'}})
    assert curriculum.grammar_occurrence_counts(chapter) == {'past-ass-eoss': 2}


def test_optional_load_includes_standalone_grammar_and_changes_with_target():
    chapter, bindings = sample(63)
    bindings['bindings'][1]['optional_reason_en'] = 'One useful modifier, explained in context.'
    chapter['grammar_links'].append(dict(chapter['grammar_links'][0]))
    # A second source position has only a grammar tap, without a duplicate link.
    chapter['segments'].append({'type': 'word', 'lexical': {
        'kind': 'grammar', 'id': 'past-ass-eoss'}})
    grade = curriculum.evaluate_bindings(chapter, bindings, level=1)
    assert grade['passes']
    assert grade['grammar_levels']['past-ass-eoss'] == 2
    assert grade['optional_grammar_occurrences'] == {'past-ass-eoss': 2}
    assert grade['above_level_grammar_occurrences'] == {'past-ass-eoss': 2}

    bindings['bindings'][1]['optional_reason_en'] = ''
    grade = curriculum.evaluate_bindings(chapter, bindings, level=2)
    assert grade['passes']
    assert grade['grammar_levels']['past-ass-eoss'] == 2
    assert grade['optional_grammar_occurrences'] == {}
    assert grade['above_level_grammar_occurrences'] == {}
