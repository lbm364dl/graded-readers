import json

import pytest

from pipeline.annotation_adjudication import (
    AdjudicationError, _build_inputs, _validate_output, digest,
    normalize_review, verify_adjudication_evidence,
)


def fixture(review=None):
    candidate = {'segments': [{'text': '走る', 'meaning_en': 'run',
                               'form_steps': [{'meaning_en': 'runs'}]}]}
    review = review or {'verdict': 'revise', 'issues': ['Segment 0 occurrence meaning_en is wrong: it says "run".']}
    refs = {'lesson-run': {'kind': 'approved_lesson', 'content': {'meaning': 'run'}}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('走る')}
    receipt = {'job': 'review-1', 'review_digest': digest(review), 'input_digest': 'abc'}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[{'review': 'older'}], context={'level': 1},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt=receipt, source_text='走る')
    return candidate, review, refs, gate, receipt, inputs


def output_for(inputs, disposition='unsupported', path='/segments/0/meaning_en',
               ref_id='lesson-run', ref_path='/meaning', binding='exact'):
    row = inputs['normalized_review']['issues'][0]
    return {'classifications': [{
        'issue_id': row['issue_id'], 'disposition': disposition,
        'target_binding': binding, 'candidate_paths': [path],
        'evidence_refs': [{'reference_id': ref_id, 'path': ref_path}],
        'reason': 'The cited approved lesson supports the submitted complete meaning.',
        'diagnosis': 'Correct the occurrence meaning against the reviewed lesson.' if disposition == 'actionable' else '',
    }], 'new_issues': [], 'prose_revision_reason': ''}


def test_supported_linguistic_reference_can_clear_exact_candidate_field():
    _, _, _, _, _, inputs = fixture()
    verified = _validate_output(output_for(inputs), inputs)
    assert verified['status'] == 'cleared'
    assert verified['approved'] is True
    assert verified['classifications'][0]['candidate_observations'][0]['value'] == 'run'
    assert verified['classifications'][0]['resolved_evidence_refs'][0]['reference_kind'] == 'approved_lesson'
    assert verified['effective_review_kind'] == 'adjudicated'


def test_source_context_alone_cannot_clear_a_linguistic_objection():
    candidate, review, _, gate, receipt, inputs = fixture()
    refs = {'source': {'kind': 'source_context', 'content': {'sentence': '走る'}}}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text=None)
    output = output_for(inputs, ref_id='source', ref_path='/sentence')
    assert _validate_output(output, inputs)['status'] == 'uncertain'


def test_empty_reference_sections_are_not_substantive_linguistic_proof():
    candidate, review, _, gate, receipt, _ = fixture()
    refs = {'lesson': {'kind': 'approved_lesson', 'content': {'examples': []}}}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
    assert _validate_output(output_for(inputs, ref_id='lesson', ref_path='/examples'), inputs)['status'] == 'uncertain'
    metadata_refs = {'lesson': {'kind': 'approved_lesson', 'content': {'id': 'x', 'title': 'Past tense'}}}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=metadata_refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
    assert _validate_output(output_for(inputs, ref_id='lesson', ref_path='/title'), inputs)['status'] == 'uncertain'


def test_approved_lexical_kind_supports_only_matching_chinese_segment_type():
    candidate = {'segments': [{'text': '先', 'type': 'word', 'pinyin': 'xiān',
                               'meaning_en': 'first'}], 'grammar_overlays': []}
    review = {'verdict': 'revise', 'issues': [{
        'segment_index': 0, 'candidate_path': '/segments/0/type',
        'problem': 'wrong_type', 'explanation': 'This segment should be a particle.'}]}
    entry = {'id': 'zh-xian', 'headword': '先', 'reading': 'xiān', 'kind': 'word',
             'senses': [{'definition': 'first; before doing something else'}]}
    refs = {'lexical-entry': {'kind': 'approved_lesson', 'content': entry}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('先')}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'abc'}
    inputs = _build_inputs(language='zh', representation='chinese-annotation',
        candidate=candidate, current_review=review, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt=receipt, source_text='先')
    issue = inputs['normalized_review']['issues'][0]
    output = {'classifications': [{
        'issue_id': issue['issue_id'], 'disposition': 'unsupported',
        'target_binding': 'exact', 'candidate_paths': ['/segments/0/type'],
        'evidence_refs': [{'reference_id': 'lexical-entry', 'path': '/kind'}],
        'reason': 'The approved lexical entry identifies this exact word and reading as a word.',
        'diagnosis': ''}], 'new_issues': [], 'prose_revision_reason': ''}
    assert _validate_output(output, inputs)['status'] == 'cleared'

    # A matching category is insufficient when the approved entry refers to
    # another word or reading, or when the issue targets a different field.
    other_word = {**entry, 'headword': '前'}
    refs['lexical-entry']['content'] = other_word
    inputs = _build_inputs(language='zh', representation='chinese-annotation',
        candidate=candidate, current_review=review, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt=receipt, source_text='先')
    assert _validate_output(output, inputs)['status'] == 'uncertain'
    refs['lexical-entry']['content'] = {**entry, 'reading': 'qián'}
    inputs = _build_inputs(language='zh', representation='chinese-annotation',
        candidate=candidate, current_review=review, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt=receipt, source_text='先')
    assert _validate_output(output, inputs)['status'] == 'uncertain'

    # One entry cannot clear a separate category finding on another segment.
    two_segments = {'segments': [candidate['segments'][0], {
        'text': '来', 'type': 'particle', 'pinyin': 'lái', 'meaning_en': 'come'}],
        'grammar_overlays': []}
    two_issue = {'verdict': 'revise', 'issues': [{
        'segment_indices': [0, 1], 'problem': 'wrong_type',
        'explanation': 'Segments 0 and 1 have incorrect types.'}]}
    two_gate = {'passed': True, 'issues': [], 'candidate_digest': digest(two_segments),
                'source_text_digest': digest('先来')}
    two_receipt = {'job': 'review-two', 'review_digest': digest(two_issue), 'input_digest': 'ghi'}
    inputs = _build_inputs(language='zh', representation='chinese-annotation',
        candidate=two_segments, current_review=two_issue, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=two_gate,
        normal_review_receipt=two_receipt, source_text='先来')
    two_row = inputs['normalized_review']['issues'][0]
    two_output = {'classifications': [{
        'issue_id': two_row['issue_id'], 'disposition': 'unsupported',
        'target_binding': 'exact',
        'candidate_paths': ['/segments/0/type', '/segments/1/type'],
        'evidence_refs': [{'reference_id': 'lexical-entry', 'path': '/kind'}],
        'reason': 'This lexical entry confirms the type.', 'diagnosis': ''}],
        'new_issues': [], 'prose_revision_reason': ''}
    assert _validate_output(two_output, inputs)['status'] == 'uncertain'

    refs['lexical-entry']['content'] = entry
    meaning_issue = {'verdict': 'revise', 'issues': [{
        'segment_index': 0, 'candidate_path': '/segments/0/meaning_en',
        'problem': 'meaning', 'explanation': 'This meaning is wrong.'}]}
    meaning_receipt = {'job': 'review-meaning', 'review_digest': digest(meaning_issue), 'input_digest': 'def'}
    inputs = _build_inputs(language='zh', representation='chinese-annotation',
        candidate=candidate, current_review=meaning_issue, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt=meaning_receipt, source_text='先')
    meaning_row = inputs['normalized_review']['issues'][0]
    meaning_output = {'classifications': [{
        'issue_id': meaning_row['issue_id'], 'disposition': 'unsupported',
        'target_binding': 'exact', 'candidate_paths': ['/segments/0/meaning_en'],
        'evidence_refs': [{'reference_id': 'lexical-entry', 'path': '/kind'}],
        'reason': 'The lexical category supports this explanation.', 'diagnosis': ''}],
        'new_issues': [], 'prose_revision_reason': ''}
    assert _validate_output(meaning_output, inputs)['status'] == 'uncertain'


def test_lexical_kind_metadata_cannot_stand_in_for_an_approved_entry():
    candidate = {'segments': [{'text': '先', 'type': 'word', 'pinyin': 'xiān',
                               'meaning_en': 'first'}], 'grammar_overlays': []}
    review = {'verdict': 'revise', 'issues': [{
        'segment_index': 0, 'candidate_path': '/segments/0/type',
        'problem': 'wrong_type', 'explanation': 'This segment should be a particle.'}]}
    refs = {'review-metadata': {'kind': 'approved_lesson', 'content': {
        'id': 'review', 'title': 'Word', 'kind': 'word'}}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('先')}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'abc'}
    inputs = _build_inputs(language='zh', representation='chinese-annotation',
        candidate=candidate, current_review=review, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt=receipt, source_text='先')
    issue = inputs['normalized_review']['issues'][0]
    output = {'classifications': [{
        'issue_id': issue['issue_id'], 'disposition': 'unsupported',
        'target_binding': 'exact', 'candidate_paths': ['/segments/0/type'],
        'evidence_refs': [{'reference_id': 'review-metadata', 'path': '/kind'}],
        'reason': 'The reference says word.', 'diagnosis': ''}],
        'new_issues': [], 'prose_revision_reason': ''}
    assert _validate_output(output, inputs)['status'] == 'uncertain'


def test_occurrence_meaning_cannot_be_cleared_by_pointing_at_form_step():
    _, _, _, _, _, inputs = fixture()
    bad = output_for(inputs, path='/segments/0/form_steps/0/meaning_en')
    verified = _validate_output(bad, inputs)
    assert verified['status'] == 'uncertain'
    assert not verified['approved']


def test_occurrence_target_wins_over_incidental_form_step_contrast():
    candidate, _, refs, gate, receipt, _ = fixture()
    review = {'verdict': 'revise', 'issues': [
        'Segment 0 occurrence meaning_en may be consistent with form step 0, but the occurrence gloss is wrong: "run".']}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'abc'}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
    assert _validate_output(output_for(inputs, path='/segments/0/form_steps/0/meaning_en'), inputs)['status'] == 'uncertain'


def test_quoted_source_surface_binds_unique_segment_and_duplicate_is_ambiguous():
    candidate, _, refs, _, receipt, _ = fixture()
    candidate['segments'][0]['text'] = '走る'
    review = {'verdict': 'revise', 'issues': ['The gloss on the source token “走る” is wrong: "run".']}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'abc'}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('猫')}
    inputs = _build_inputs(language='zh', representation='chinese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text=None)
    assert _validate_output(output_for(inputs), inputs)['status'] == 'cleared'
    candidate['segments'].append({'text': '走る', 'meaning_en': 'run'})
    gate['candidate_digest'] = digest(candidate)
    inputs = _build_inputs(language='zh', representation='chinese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text=None)
    assert _validate_output(output_for(inputs), inputs)['status'] == 'uncertain'


def test_exact_review_offsets_disambiguate_repeated_primary_tap_surface():
    candidate = {'segments': [
        {'text': '甲', 'meaning_en': 'first'},
        {'text': '乙', 'meaning_en': 'middle'},
        {'text': '甲', 'meaning_en': 'last'},
    ]}
    review = {'verdict': 'revise', 'issues': [
        {'start': 2, 'end': 3, 'segment_text': '甲', 'problem': 'meaning',
         'explanation': 'The second occurrence meaning is wrong.', 'suggested_fix': 'Check it.'}]}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'x'}
    refs = {'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'last'}}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('甲乙甲')}
    inputs = _build_inputs(language='zh', representation='chinese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='甲乙甲')
    paths = ['/segments/2/meaning_en']
    output = output_for(inputs, path=paths[0], ref_id='lesson', ref_path='/meaning')
    assert _validate_output(output, inputs)['status'] == 'cleared'
    output['classifications'][0]['candidate_paths'] = ['/segments/0/meaning_en']
    assert _validate_output(output, inputs)['status'] == 'uncertain'
    invalid_review = {'verdict': 'revise', 'issues': [
        {'start': 1, 'end': 2, 'segment_text': '甲', 'problem': 'meaning',
         'explanation': 'Wrong source span.', 'suggested_fix': 'Check it.'}]}
    receipt = {'job': 'review', 'review_digest': digest(invalid_review), 'input_digest': 'y'}
    inputs = _build_inputs(language='zh', representation='chinese-annotation', candidate=candidate,
        current_review=invalid_review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='甲乙甲')
    output = output_for(inputs, path='/segments/2/meaning_en', ref_id='lesson', ref_path='/meaning')
    assert _validate_output(output, inputs)['status'] == 'uncertain'


def test_segment_and_related_grammar_link_anchors_allow_two_exact_paths():
    candidate = {'segments': [{'text': '甲', 'meaning_en': 'x'}],
                 'grammar_links': [{'meaning_en': 'y'}]}
    review = {'verdict': 'revise', 'issues': ['Segment 0 and grammar link 0 meanings are acceptable.']}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'x'}
    refs = {'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'reviewed'}}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('猫')}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text=None)
    row = inputs['normalized_review']['issues'][0]
    output = {'classifications': [{'issue_id': row['issue_id'], 'disposition': 'unsupported',
        'target_binding': 'exact', 'candidate_paths': ['/segments/0/meaning_en', '/grammar_links/0/meaning_en'],
        'evidence_refs': [{'reference_id': 'lesson', 'path': '/meaning'}],
        'reason': 'The approved lesson supports both linked meanings.', 'diagnosis': ''}],
        'new_issues': [], 'prose_revision_reason': ''}
    assert _validate_output(output, inputs)['status'] == 'cleared'


def test_japanese_surface_field_supports_unique_but_not_duplicated_positions():
    candidate = {'segments': [{'surface': '猫', 'meaning_en': 'cat', 'form_steps': []}]}
    review = {'verdict': 'revise', 'issues': [{'segment_text': '猫', 'problem': 'meaning',
        'explanation': 'This occurrence gloss says "cat".', 'suggested_fix': 'Check the meaning.'}]}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'x'}
    refs = {'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'cat'}}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('猫')}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='猫')
    output = output_for(inputs, path='/segments/0/meaning_en', ref_id='lesson', ref_path='/meaning')
    assert _validate_output(output, inputs)['status'] == 'cleared'
    candidate['segments'].append({'surface': '猫', 'meaning_en': 'cat', 'form_steps': []})
    gate['candidate_digest'] = digest(candidate)
    gate['source_text_digest'] = digest('猫猫')
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='猫猫')
    assert _validate_output(output_for(inputs, path='/segments/0/meaning_en', ref_id='lesson',
                                       ref_path='/meaning'), inputs)['status'] == 'uncertain'


def test_japanese_incomplete_intermediate_form_binds_to_form_step_not_occurrence_gloss():
    candidate = {'segments': [{'surface': '猫', 'meaning_en': 'cat',
        'form_steps': [{'form': '猫だ', 'meaning_en': 'is a cat'}]}]}
    review = {'verdict': 'revise', 'issues': [{'segment_text': '猫', 'problem': 'conjugation',
        'explanation': 'The intermediate form is an incomplete stage despite its plausible gloss.',
        'suggested_fix': 'Use a complete form.'}]}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'x'}
    refs = {'policy': {'kind': 'explicit_review_policy', 'content': {'rule': 'Stages must be complete forms.'}}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('猫')}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='猫')
    output = output_for(inputs, disposition='actionable',
        path='/segments/0/form_steps/0/meaning_en', ref_id='policy', ref_path='/rule')
    assert _validate_output(output, inputs)['status'] == 'actionable'
    output['classifications'][0]['candidate_paths'] = ['/segments/0/meaning_en']
    assert _validate_output(output, inputs)['status'] == 'uncertain'


def test_issue_without_anchor_or_exact_candidate_observation_stays_uncertain():
    candidate, _, refs, gate, receipt, _ = fixture()
    review = {'verdict': 'revise', 'issues': ['This part is linguistically wrong.']}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'abc'}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
    assert _validate_output(output_for(inputs), inputs)['status'] == 'uncertain'


def test_explicit_segment_anchor_requires_exact_index_and_multi_anchor_coverage():
    candidate, _, refs, gate, receipt, _ = fixture()
    review = {'verdict': 'revise', 'issues': ['Segments 0 and 1 have incorrect occurrence meaning_en values.']}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'abc'}
    candidate['segments'].append({'text': '歩く', 'meaning_en': 'walk'})
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate)}
    inputs = _build_inputs(language='zh', representation='chinese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text=None)
    row = inputs['normalized_review']['issues'][0]
    output = {'classifications': [{
        'issue_id': row['issue_id'], 'disposition': 'unsupported', 'target_binding': 'exact',
        'candidate_paths': ['/segments/0/meaning_en'],
        'evidence_refs': [{'reference_id': 'lesson-run', 'path': '/meaning'}],
        'reason': 'The reviewed lesson supports these meanings.', 'diagnosis': ''}],
        'new_issues': [], 'prose_revision_reason': ''}
    assert _validate_output(output, inputs)['status'] == 'uncertain'
    output['classifications'][0]['candidate_paths'] = [
        '/segments/0/meaning_en', '/segments/1/meaning_en']
    assert _validate_output(output, inputs)['status'] == 'cleared'


def test_segment_and_stage_indices_must_bind_the_same_nested_path():
    candidate = {'segments': [
        {'text': '甲', 'form_steps': [{'meaning_en': 'wrong stage'}, {'meaning_en': 'target stage'}]},
        {'text': '乙', 'form_steps': [{'meaning_en': 'other'}, {'meaning_en': 'unrelated stage'}]},
    ]}
    review = {'verdict': 'revise', 'issues': [
        {'segment_index': 0, 'stage_index': 1, 'message': 'Segment 0 form step 1 meaning is incorrect.'}]}
    refs = {'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'lesson'}}}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'x'}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate)}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text=None)
    row = inputs['normalized_review']['issues'][0]
    def result(paths):
        return {'classifications': [{'issue_id': row['issue_id'], 'disposition': 'unsupported',
            'target_binding': 'exact', 'candidate_paths': paths,
            'evidence_refs': [{'reference_id': 'lesson', 'path': '/meaning'}],
            'reason': 'The approved lesson supports the linked stage.', 'diagnosis': ''}],
            'new_issues': [], 'prose_revision_reason': ''}
    assert _validate_output(result(['/segments/0/form_steps/1/meaning_en']), inputs)['status'] == 'cleared'
    assert _validate_output(result(['/segments/0/form_steps/0/meaning_en',
                                    '/segments/1/form_steps/1/meaning_en']), inputs)['status'] == 'uncertain'


def test_structured_issue_field_path_is_exact_not_merely_same_segment():
    candidate, _, refs, gate, _, _ = fixture()
    review = {'verdict': 'revise', 'issues': [{'field_path': '/segments/0/meaning_en',
                                                'message': 'The occurrence meaning is wrong: "run".'}]}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'x'}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
    assert _validate_output(output_for(inputs, path='/segments/0/form_steps/0/meaning_en'), inputs)['status'] == 'uncertain'


def test_preflight_blocks_prose_and_failed_gate_without_model_job(tmp_path):
    import asyncio
    from pipeline.annotation_adjudication import adjudicate_annotation_review

    class NeverCall:
        model = 'gpt-6-luna'
        benchmark_effort = None
        legacy_tool_restrictions = False
        async def call(self, *args, **kwargs):
            raise AssertionError('A blocked preflight must not launch a model job')

    candidate, review, refs, gate, receipt, _ = fixture()
    gate['passed'] = False
    blocked = asyncio.run(adjudicate_annotation_review(NeverCall(), tmp_path / 'gate',
        language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る'))
    assert blocked['status'] == 'blocked_by_gate' and blocked['preflight_block']
    prose_review = {'approved': False, 'issues': ['Segment 0 occurrence meaning_en is wrong: "run".'],
                    'prose_revision_reason_en': 'Revise the passage prose.'}
    receipt = {'job': 'review', 'review_digest': digest(prose_review), 'input_digest': 'x'}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
            'source_text_digest': digest('走る')}
    blocked = asyncio.run(adjudicate_annotation_review(NeverCall(), tmp_path / 'prose',
        language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=prose_review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る'))
    assert blocked['status'] == 'blocked_by_prose_request' and blocked['preflight_block']


def test_nonannotation_representation_is_rejected_before_any_worker_call(tmp_path):
    candidate, review, refs, gate, receipt, _ = fixture()
    with pytest.raises(AdjudicationError, match='Unsupported annotation representation'):
        _build_inputs(language='ja', representation='japanese-scene-text', candidate=candidate,
            current_review=review, prior_history=[], context={}, known_reference_input=refs,
            deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')


def test_contract_finding_and_gate_for_another_candidate_cannot_be_overridden():
    candidate = {'segments': [{'text': '走る', 'meaning_en': 'run'}]}
    review = {'verdict': 'revise', 'issues': [{'problem': 'contract', 'segment_index': 0,
                                               'message': 'Source boundary mismatch'}]}
    refs = {'policy': {'kind': 'explicit_review_policy', 'content': {'rule': 'example'}}}
    receipt = {'job': 'review', 'review_digest': digest(review), 'input_digest': 'x'}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate)}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text=None)
    output = output_for(inputs, path='/segments/0/meaning_en', ref_id='policy', ref_path='/rule')
    with pytest.raises(AdjudicationError, match='contract'):
        _validate_output(output, inputs)
    gate['candidate_digest'] = digest({'different': True})
    new_review = {'verdict': 'revise', 'issues': ['Segment 0 occurrence meaning_en is wrong: "run".']}
    new_receipt = {'job': 'review-2', 'review_digest': digest(new_review), 'input_digest': 'y'}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=new_review,
        prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=new_receipt, source_text=None)
    assert _validate_output(output_for(inputs, ref_id='policy', ref_path='/rule'), inputs)['status'] == 'blocked_by_gate'


def test_prose_request_and_uncertain_issue_prevent_clear_even_if_other_issue_actionable():
    review = {'approved': False, 'issues': ['Segment 0 occurrence meaning_en is wrong: "run".'],
              'prose_revision_reason_en': 'The chapter prose needs revision.'}
    _, _, _, _, _, inputs = fixture(review)
    output = output_for(inputs, disposition='actionable')
    output['prose_revision_reason'] = ''
    assert _validate_output(output, inputs)['status'] == 'blocked_by_prose_request'
    output['classifications'][0]['target_binding'] = 'unbound'
    assert _validate_output(output, inputs)['status'] == 'blocked_by_prose_request'


def test_actionable_diagnosis_keeps_exact_prior_path_history_unverified():
    candidate, review, refs, gate, receipt, _ = fixture()
    history = [{'stage': 'repair-1', 'annotation': candidate,
                'review': {'verdict': 'revise', 'issues': [
                    'Segment 0 occurrence meaning_en does not match the lesson.']}}]
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=history, context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
    verified = _validate_output(output_for(inputs, disposition='actionable'), inputs)
    diagnosis = verified['repair_diagnoses'][0]
    assert diagnosis['path_history'][0]['disposition'] == 'prior_review_unverified'
    assert diagnosis['path_history'][0]['path_values'] == [
        {'path': '/segments/0/meaning_en', 'original_value': 'run', 'current_value': 'run'}]
    assert 'not linguistic proof' in diagnosis['path_history'][0]['evidence']


def test_review_shapes_preserve_ko_prose_and_japanese_verdict():
    ko = normalize_review('ko', {'approved': False, 'issues': ['x'],
                                  'prose_revision_reason_en': 'revise prose'})
    ja = normalize_review('ja', {'verdict': 'revise', 'issues': ['x']})
    assert ko['prose_requests'] == [('prose_revision_reason_en', 'revise prose')]
    assert ja['verdict'] == 'revise'


def test_gate_must_bind_the_same_source_text():
    _, _, _, _, _, inputs = fixture()
    inputs['deterministic_gate_evidence']['source_text_digest'] = digest('different')
    assert _validate_output(output_for(inputs), inputs)['status'] == 'blocked_by_gate'


def test_replay_rejects_changed_candidate_review_history_and_symlink(tmp_path):
    # Build a genuine confined workspace/profile record without launching a model.
    candidate, review, refs, gate, receipt, inputs = fixture()
    gate['source_text_digest'] = digest('走る')
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[{'review': 'older'}], context={'level': 1},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt=receipt, source_text='走る')
    output = output_for(inputs)
    run_dir = tmp_path / 'run'
    job = f"annotation-adjudication-{inputs['input_digest']}"
    job_dir = run_dir / 'agents' / job
    job_dir.mkdir(parents=True)
    verified = _validate_output(output, inputs)
    from pipeline.annotation_adjudication import OUTPUT_SCHEMA
    from pipeline.worker_workspace import build, submit
    workspace_context = {'annotation_adjudication': inputs,
                         'annotation_adjudication_validation': {'input_field': 'annotation_adjudication'}}
    _, workspace_digest = build(job_dir / 'workspace', 'Adjudicate.\nINPUT:\n' + json.dumps(workspace_context),
                                OUTPUT_SCHEMA, context=workspace_context)
    candidate_file = job_dir / 'workspace' / 'candidate.json'
    candidate_file.write_text(json.dumps(output), encoding='utf-8')
    submitted, artifact = submit(job_dir / 'workspace', {'candidate_path': 'candidate.json'})
    verified = _validate_output(submitted, inputs)
    verified.update({'job': job, 'effort': 'low', 'model': 'gpt-6-luna', 'tool_profile': 'research',
                     'worker_tool_profile': 'workspace',
                     'normal_review_receipt': receipt,
                     'normal_review_receipt_digest': inputs['normal_review_receipt_digest'],
                     'verified_result_digest': digest(verified)})
    (job_dir / 'adjudication-input.json').write_text(json.dumps(inputs), encoding='utf-8')
    (job_dir / 'result.json').write_text(json.dumps(submitted), encoding='utf-8')
    (job_dir / 'verified-adjudication.json').write_text(json.dumps(verified), encoding='utf-8')
    (job_dir / 'meta.json').write_text(json.dumps({'return_code': 0, 'model': 'gpt-6-luna',
        'effort': 'low', 'tool_profile': 'workspace', 'workspace_digest': workspace_digest,
        'artifact_path': artifact['artifact_path'], 'artifact_digest': artifact['artifact_digest']}), encoding='utf-8')
    got = verify_adjudication_evidence(run_dir, verified, language='ja', representation='japanese-annotation',
            candidate=candidate, current_review=review, prior_history=[{'review': 'older'}],
            context={'level': 1}, known_reference_input=refs,
            deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
    assert got['approved']
    with pytest.raises(AdjudicationError, match='identity'):
        verify_adjudication_evidence(run_dir, verified, language='ja', representation='japanese-annotation',
                candidate={'changed': True}, current_review=review, prior_history=[{'review': 'older'}],
                context={'level': 1}, known_reference_input=refs,
                deterministic_gate_evidence=gate, normal_review_receipt=receipt, source_text='走る')
