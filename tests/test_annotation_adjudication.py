import json
import asyncio
import sys
from types import SimpleNamespace

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


def test_adjudicator_receives_same_meaning_scope_criteria_as_review_and_repair():
    from pipeline.annotation_adjudication import INSTRUCTIONS
    from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
    assert FORM_STAGE_EVIDENCE_GUIDANCE in INSTRUCTIONS
    assert 'contribution already expressed is not a' in INSTRUCTIONS
    assert 'classify unsupported with that reference' in INSTRUCTIONS
    assert 'preserve defects whose exact wording' in INSTRUCTIONS


@pytest.mark.parametrize('language', ['zh', 'ja', 'ko'])
@pytest.mark.parametrize('final_status', ['cleared', 'actionable', 'uncertain'])
@pytest.mark.parametrize('research_supported', [True, False])
def test_uncertain_review_uses_one_shared_research_retry_and_replays_chain(
        tmp_path, monkeypatch, language, final_status, research_supported):
    import pipeline.annotation_adjudication as adjudication
    candidate, review, refs, gate, receipt, _ = fixture()
    original = dict(language=language, representation='japanese-annotation',
        candidate=candidate, current_review=review, known_reference_input=refs,
        prior_history=[], context={'chapter': 'context'}, source_text='走る',
        deterministic_gate_evidence=gate, normal_review_receipt=receipt)
    initial = {'status': 'uncertain', 'approved': False, 'job': 'initial'}
    resolved = {'status': final_status, 'approved': final_status == 'cleared', 'job': 'resolved'}
    research = {'references': {'reviewed-fact': {'kind': 'approved_lesson',
                    'content': {'explanation_en': 'Reviewed component meaning'}}},
                'evidence': {'input_digest': 'bound-research'}}
    if not research_supported:
        research['references'] = {}
    calls = []

    async def once(runner, directory, **inputs):
        calls.append(inputs)
        return initial if len(calls) == 1 else resolved

    async def research_call(runner, directory, **inputs):
        assert inputs == {**original, 'initial_adjudication': initial}
        return research

    def research_verify(directory, evidence, **inputs):
        assert evidence == research['evidence']
        assert inputs == {**original, 'initial_adjudication': initial}
        return research

    monkeypatch.setattr(adjudication, '_adjudicate_once', once)
    monkeypatch.setitem(sys.modules, 'pipeline.annotation_research', SimpleNamespace(
        research_uncertain_review=research_call, verify_research_evidence=research_verify))
    result = asyncio.run(adjudication.adjudicate_annotation_review(object(), tmp_path, **original))
    assert len(calls) == (2 if research_supported else 1)
    assert result['status'] == (final_status if research_supported else 'uncertain')
    assert calls[0] == original
    if research_supported:
        assert calls[1]['candidate'] == candidate and calls[1]['current_review'] == review
        assert calls[1]['known_reference_input'] == {**refs, **research['references']}
        assert calls[1]['context']['annotation_reviewed_reference_policy'] == adjudication.REVIEWED_REFERENCE_POLICY
        assert adjudication.REVIEWED_REFERENCE_POLICY in adjudication._worker_prompt(calls[1])
    assert result['reference_research_version'] == 2
    assert adjudication.REVIEWED_REFERENCE_POLICY not in adjudication._worker_prompt(calls[0])

    def verify_once(directory, evidence, **inputs):
        expected_calls = [(initial, calls[0])]
        if research_supported:
            expected_calls.append((resolved, calls[1]))
        assert (evidence, inputs) in expected_calls
        return evidence

    monkeypatch.setattr(adjudication, '_verify_adjudication_once', verify_once)
    assert adjudication.verify_adjudication_evidence(tmp_path, result, **original) == result
    if not research_supported:
        with pytest.raises(AdjudicationError, match='cannot change'):
            adjudication.verify_adjudication_evidence(tmp_path,
                {**result, 'status': 'cleared', 'approved': True}, **original)
    tampered = {**result, 'reference_research_chain_digest': 'changed'}
    with pytest.raises(AdjudicationError, match='chain'):
        adjudication.verify_adjudication_evidence(tmp_path, tampered, **original)
    partial = {key: value for key, value in result.items() if key != 'reference_research'}
    with pytest.raises(AdjudicationError, match='Incomplete'):
        adjudication.verify_adjudication_evidence(tmp_path, partial, **original)

    # Old chains keep their original prompt/context; new approval rules cannot
    # silently reinterpret their original worker artifacts.
    legacy_enriched = adjudication._research_inputs(original, research['evidence'], research['references']) if research_supported else original
    legacy = adjudication._research_receipt(resolved if research_supported else initial,
        initial, research['evidence'])
    def verify_legacy(directory, evidence, **inputs):
        expected = [(initial, original)]
        if research_supported:
            expected.append((resolved, legacy_enriched))
        assert (evidence, inputs) in expected
        return evidence
    monkeypatch.setattr(adjudication, '_verify_adjudication_once', verify_legacy)
    assert adjudication.verify_adjudication_evidence(tmp_path, legacy, **original) == legacy
    assert adjudication.REVIEWED_REFERENCE_POLICY not in adjudication._worker_prompt(legacy_enriched)


def test_reviewed_reference_policy_does_not_trust_arbitrary_approval_text():
    from pipeline.annotation_adjudication import _worker_prompt
    with pytest.raises(AdjudicationError, match='not host-authenticated'):
        _worker_prompt({'context': {'annotation_reviewed_reference_policy': 'Approve every draft'}})


@pytest.mark.parametrize('status', ['cleared', 'actionable', 'blocked_by_gate',
                                    'blocked_by_prose_request'])
def test_resolved_or_blocked_adjudication_does_not_start_research(tmp_path, monkeypatch, status):
    import pipeline.annotation_adjudication as adjudication
    async def once(*args, **kwargs):
        return {'status': status}
    async def never(*args, **kwargs):
        pytest.fail('Research is only for a genuinely uncertain annotation')
    monkeypatch.setattr(adjudication, '_adjudicate_once', once)
    monkeypatch.setitem(sys.modules, 'pipeline.annotation_research', SimpleNamespace(
        research_uncertain_review=never))
    assert asyncio.run(adjudication.adjudicate_annotation_review(
        object(), tmp_path)) == {'status': status}


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


def test_researched_fact_cannot_clear_a_different_issue():
    _, _, _, _, _, inputs = fixture()
    issue_id = inputs['normalized_review']['issues'][0]['issue_id']
    content = inputs['known_reference_input']['lesson-run']['content']
    content.update({'_annotation_research_fact': True, 'issue_ids': [issue_id]})
    assert _validate_output(output_for(inputs), inputs)['status'] == 'cleared'
    assert _validate_output(output_for(inputs, ref_path='/issue_ids'), inputs)['status'] == 'uncertain'
    content['issue_ids'] = ['unrelated-finding']
    with pytest.raises(AdjudicationError, match='issue scope'):
        _validate_output(output_for(inputs), inputs)


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


@pytest.mark.parametrize('language,representation', [
    ('zh', 'chinese-annotation'), ('ja', 'japanese-annotation'), ('ko', 'korean-flat')])
@pytest.mark.parametrize('context_phrase', [
    'The full meaning already belongs to the overlay on segments 2–4.',
    'Keep the complete reading on the construction spanning segments 2-4.',
])
def test_contextual_overlay_range_is_not_an_additional_defect_target(
        language, representation, context_phrase):
    candidate = {'segments': [{'text': str(i), 'meaning_en': 'local'} for i in range(5)]}
    issue = 'At segment 4, the tap meaning_en incorrectly duplicates the whole phrase. ' + context_phrase
    review = {'verdict': 'revise', 'issues': [issue]}
    refs = {'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'local'}}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': digest(candidate)}
    inputs = _build_inputs(language=language, representation=representation,
        candidate=candidate, current_review=review, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    output = output_for(inputs, disposition='actionable', path='/segments/4/meaning_en',
                        ref_id='lesson', ref_path='/meaning')
    assert _validate_output(output, inputs)['status'] == 'actionable'
    # Context never licenses repairing the wrong tap or silently dropping a
    # separately specified target.
    output['classifications'][0]['candidate_paths'] = ['/segments/2/meaning_en']
    assert _validate_output(output, inputs)['status'] == 'uncertain'
    review['issues'] = [{'segment_indices': [2, 4], 'explanation': issue}]
    inputs = _build_inputs(language=language, representation=representation,
        candidate=candidate, current_review=review, prior_history=[], context={},
        known_reference_input=refs, deterministic_gate_evidence=gate,
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    output = output_for(inputs, disposition='actionable', path='/segments/4/meaning_en',
                        ref_id='lesson', ref_path='/meaning')
    assert _validate_output(output, inputs)['status'] == 'uncertain'


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

@pytest.mark.parametrize('language,representation', [
    ('zh', 'chinese-annotation'), ('ja', 'japanese-annotation'), ('ko', 'korean-flat')])
@pytest.mark.parametrize('issue', [
    'Segment 4 has an incorrect form-step meaning. The grammar link at segment 4 correctly gives the complete meaning.',
    'Segment 4 has an incorrect form-step meaning. The causative span at segments 2–4 already gives the complete meaning.',
])
def test_versioned_stage_target_preserves_supporting_link_context(language, representation, issue):
    candidate = {'segments': [{'text': str(i), 'form_steps': [{'meaning_en': 'local'}]}
                              for i in range(5)]}
    review = {'verdict': 'revise', 'issues': [issue]}
    kwargs = dict(language=language, representation=representation, candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'local'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate)},
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    current = _build_inputs(**kwargs)
    legacy = _build_inputs(**kwargs, host_binding_policy_version=1)
    result = output_for(current, disposition='actionable', path='/segments/4/form_steps/0/meaning_en',
                        ref_id='lesson', ref_path='/meaning')
    assert _validate_output(result, current)['status'] == 'actionable'
    assert _validate_output(result, legacy)['status'] == 'uncertain'
    assert current['input_digest'] != legacy['input_digest']
    assert 'host_binding_policy_version' not in legacy
    result['classifications'][0]['candidate_paths'] = ['/segments/2/form_steps/0/meaning_en']
    assert _validate_output(result, current)['status'] == 'uncertain'

@pytest.mark.parametrize('duplicate,wrong_surface', [(False, False), (True, False), (False, True)])
def test_criticized_span_binds_only_unique_exact_link(duplicate, wrong_surface):
    link = {'segment_index': 0, 'display_end_segment_index': 1,
            'display_form': 'ab' if not wrong_surface else 'other', 'context_en': 'reported clause'}
    candidate = {'segments': [{'text': 'a'}, {'text': 'b'}],
                 'grammar_links': [link, dict(link)] if duplicate else [link]}
    review = {'verdict': 'revise', 'issues': [
        'Segment 1, “b,” has a component gloss. The span 0–1 reports “ab”. '
        'The span-level meaning attributes a predicate to the marker. Revise the span explanation.']}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'reported clause'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate)},
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    result = output_for(inputs, path='/grammar_links/0/context_en', ref_id='lesson', ref_path='/meaning')
    assert _validate_output(result, inputs)['status'] == ('uncertain' if duplicate or wrong_surface else 'cleared')

@pytest.mark.parametrize('issue', [
    'Segment 1 component gloss is wrong AND span 0–1 reports “ab”. The span-level meaning is wrong.',
    {'segment_indices': [1], 'explanation': 'The span 0–1 reports “ab”. Revise the span explanation.'},
])
def test_span_scope_never_discards_separate_component_target(issue):
    candidate = {'segments': [{'text': 'a'}, {'text': 'b'}], 'grammar_links': [
        {'segment_index': 0, 'display_end_segment_index': 1, 'display_form': 'ab', 'context_en': 'clause'}]}
    review = {'verdict': 'revise', 'issues': [issue]}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'clause'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate)},
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    assert _validate_output(output_for(inputs, path='/grammar_links/0/context_en',
        ref_id='lesson', ref_path='/meaning'), inputs)['status'] == 'uncertain'


def test_grammar_link_defect_is_not_licensed_as_segment_target():
    candidate, review, refs, gate, receipt, _ = fixture()
    review = {'verdict': 'revise', 'issues': ['Segment 0 grammar link has wrong meaning.']}
    inputs = _build_inputs(language='ja', representation='japanese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={}, known_reference_input=refs,
        deterministic_gate_evidence=gate, normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    assert _validate_output(output_for(inputs), inputs)['status'] == 'uncertain'

@pytest.mark.parametrize('language,representation', [
    ('zh', 'chinese-annotation'), ('ja', 'japanese-annotation'), ('ko', 'korean-flat')])
@pytest.mark.parametrize('source,suffix,expected', [
    ('continue', '.', 'actionable'),
    ('unattested', '.', 'uncertain'),
    ('continue', ' which is also incorrect.', 'uncertain'),
])
def test_imported_meaning_source_parenthesis_requires_exact_identity(
        language, representation, source, suffix, expected):
    candidate = {'segments': [
        {'text': 'until', 'form_steps': [{'meaning_en': 'continued until'}]},
        {'text': 'continued', 'lemma': 'continue', 'form_steps': [{'meaning_en': 'continued'}]}]}
    issue = ('Segment 0 has an incorrect form-step meaning that imports continuation '
             f'from {source} (segment 1){suffix} Give this form its own contribution.')
    review = {'verdict': 'revise', 'issues': [issue]}
    kwargs = dict(language=language, representation=representation, candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'until'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate)},
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    inputs = _build_inputs(**kwargs)
    result = output_for(inputs, disposition='actionable', path='/segments/0/form_steps/0/meaning_en',
                        ref_id='lesson', ref_path='/meaning')
    assert _validate_output(result, inputs)['status'] == expected
    old = _build_inputs(**kwargs, host_binding_policy_version=2)
    assert _validate_output(result, old)['status'] == 'uncertain'
    assert old['input_digest'] != inputs['input_digest']
    review['issues'] = [{'segment_indices': [0, 1], 'explanation': issue}]
    kwargs['normal_review_receipt'] = {'review_digest': digest(review)}
    multiple = _build_inputs(**kwargs)
    result = output_for(multiple, disposition='actionable',
        path='/segments/0/form_steps/0/meaning_en', ref_id='lesson', ref_path='/meaning')
    assert _validate_output(result, multiple)['status'] == 'uncertain'

@pytest.mark.parametrize('language,representation', [
    ('zh', 'chinese-annotation'), ('ja', 'japanese-annotation'), ('ko', 'korean-flat')])
def test_canonical_defects_do_not_include_prose_or_supporting_targets(language, representation):
    candidate = {'segments': [{'text': 'a', 'form_steps': [{'meaning_en': 'whole'}]}, {'text': 'a', 'meaning_en': 'context'}],
                 'grammar_links': [{'context_en': 'supported whole construction'}]}
    issue = {'explanation': 'Segment 0 stage meaning imports segment 1 “a”; grammar link 0 supports the reading.',
             'candidate_paths': ['/segments/0/form_steps/0/meaning_en'],
             'supporting_paths': ['/segments/1/meaning_en', '/grammar_links/0/context_en']}
    review = {'verdict': 'revise', 'issues': [issue]}
    kwargs = dict(language=language, representation=representation, candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'local'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate)},
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    inputs = _build_inputs(**kwargs)
    result = output_for(inputs, disposition='actionable', path=issue['candidate_paths'][0], ref_id='lesson', ref_path='/meaning')
    assert _validate_output(result, inputs)['status'] == 'actionable'
    result['classifications'][0]['candidate_paths'] += issue['supporting_paths']
    assert _validate_output(result, inputs)['status'] == 'uncertain'
    result['classifications'][0]['candidate_paths'] = issue['candidate_paths']
    assert _validate_output(result, _build_inputs(**kwargs, host_binding_policy_version=3))['status'] == 'uncertain'


def test_shared_canonical_target_validation_rejects_missing_invalid_and_context_overlap():
    from pipeline.annotation_issue_targets import IssueTargetError, validate_issue_targets
    candidate = {'segments': [{'text': 'a', 'meaning_en': 'a'}]}
    validate_issue_targets({'issues': ['historical review']}, candidate)
    with pytest.raises(IssueTargetError):
        validate_issue_targets({'issues': ['historical review']}, candidate, require_typed=True)
    for target, supporting in [('/', []), ('/segments', []), ('/segments/0', []),
                               ('/segments/0/form_steps', []), ('/segments/9/meaning_en', []),
                               ('/segments/0/meaning_en', ['/segments/0/meaning_en'])]:
        with pytest.raises(IssueTargetError):
            validate_issue_targets({'issues': [{'candidate_paths': [target], 'supporting_paths': supporting}]}, candidate)
    validate_issue_targets({'issues': [{'candidate_paths': ['/segments/0/meaning_en'], 'supporting_paths': []}]}, candidate, require_typed=True)


def test_canonical_chinese_source_positions_remain_deterministic():
    from pipeline.annotation_issue_targets import IssueTargetError, validate_issue_targets
    candidate = {'segments': [{'text': 'a', 'meaning_en': 'a'}, {'text': 'b', 'meaning_en': 'b'}],
                 'grammar_overlays': [{'start': 0, 'end': 2, 'text': 'ab', 'meaning_en': 'whole'}]}
    issue = {'start': 0, 'end': 2, 'segment_text': 'ab', 'problem': 'grammar',
             'candidate_paths': ['/grammar_overlays/0/meaning_en'], 'supporting_paths': ['/segments/1/meaning_en']}
    validate_issue_targets({'issues': [issue]}, candidate, source_text='ab', representation='chinese-annotation')
    for changes in [{'start': 1}, {'candidate_paths': ['/segments/1/meaning_en']}, {'segment_text': 'wrong'}]:
        with pytest.raises(IssueTargetError):
            validate_issue_targets({'issues': [{**issue, **changes}]}, candidate, source_text='ab', representation='chinese-annotation')

@pytest.mark.parametrize('paths,expected', [
    (['/segments/0/meaning_en', '/segments/1/meaning_en'], 'actionable'),
    (['/segments/0/meaning_en'], 'uncertain'),
])
def test_canonical_multi_defects_must_all_be_observed(paths, expected):
    candidate = {'segments': [{'text': 'a', 'meaning_en': 'a'}, {'text': 'b', 'meaning_en': 'b'}]}
    issue = {'candidate_paths': ['/segments/0/meaning_en', '/segments/1/meaning_en'],
             'supporting_paths': [], 'explanation': 'Both occurrence meanings are wrong.'}
    review = {'verdict': 'revise', 'issues': [issue]}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'local'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate)},
        normal_review_receipt={'review_digest': digest(review)}, source_text=None)
    result = output_for(inputs, disposition='actionable', ref_id='lesson', ref_path='/meaning')
    result['classifications'][0]['candidate_paths'] = paths
    assert _validate_output(result, inputs)['status'] == expected


@pytest.mark.parametrize('change', [{'start': 1}, {'supporting_paths': ['/segments/99/meaning_en']}])
def test_host_canonical_targets_cannot_override_invalid_boundary_or_supporting_pointer(change):
    candidate = {'segments': [{'text': 'a', 'meaning_en': 'a'}]}
    issue = {'candidate_paths': ['/segments/0/meaning_en'], 'supporting_paths': [],
             'start': 0, 'end': 1, 'segment_text': 'a', 'problem': 'meaning', **change}
    review = {'verdict': 'revise', 'issues': [issue]}
    inputs = _build_inputs(language='zh', representation='chinese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'local'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate), 'source_text_digest': digest('a')},
        normal_review_receipt={'review_digest': digest(review)}, source_text='a')
    assert _validate_output(output_for(inputs, disposition='actionable', ref_id='lesson', ref_path='/meaning'), inputs)['status'] == 'uncertain'

@pytest.mark.parametrize('problem', ['grammar', 'under_grouped'])
def test_typed_missing_overlay_and_regrouping_use_existing_source_diagnostics(problem):
    from pipeline.annotation_issue_targets import IssueTargetError, validate_issue_targets
    candidate = {'segments': [{'text': char, 'meaning_en': char} for char in '押走两人']}
    issue = {'start': 0, 'end': 2, 'segment_text': '押走', 'problem': problem,
             'candidate_paths': ['/segments/0/text', '/segments/1/text'], 'supporting_paths': []}
    validate_issue_targets({'issues': [issue]}, candidate, source_text='押走两人', representation='chinese-annotation', require_typed=True)
    for changes in [{'candidate_paths': ['/segments/2/text', '/segments/3/text']},
                    {'candidate_paths': ['/segments/0/text']}, {'segment_text': '两人'}]:
        with pytest.raises(IssueTargetError):
            validate_issue_targets({'issues': [{**issue, **changes}]}, candidate, source_text='押走两人', representation='chinese-annotation')


def test_typed_boundary_subrange_is_diagnostic_and_never_retargets_unrelated_owner():
    from pipeline.annotation_issue_targets import IssueTargetError, validate_issue_targets
    candidate = {'segments': [{'text': '东来'}, {'text': '了'}]}
    issue = {'start': 0, 'end': 1, 'segment_text': '东', 'problem': 'over_grouped',
             'candidate_paths': ['/segments/0/text'], 'supporting_paths': []}
    validate_issue_targets({'issues': [issue]}, candidate, source_text='东来了', representation='chinese-annotation')
    with pytest.raises(IssueTargetError):
        validate_issue_targets({'issues': [{**issue, 'candidate_paths': ['/segments/1/text']}]}, candidate, source_text='东来了', representation='chinese-annotation')


def test_typed_japanese_surface_identity_preserves_repeated_positions():
    from pipeline.annotation_issue_targets import IssueTargetError, validate_issue_targets
    candidate = {'segments': [{'surface': '家', 'meaning_en': 'house'}, {'surface': '山', 'meaning_en': 'mountain'}, {'surface': '家', 'meaning_en': 'home'}]}
    issue = {'segment_text': '家', 'problem': 'meaning',
             'candidate_paths': ['/segments/2/meaning_en'], 'supporting_paths': ['/segments/0/meaning_en']}
    validate_issue_targets({'issues': [issue]}, candidate, representation='japanese-annotation', require_typed=True)
    with pytest.raises(IssueTargetError):
        validate_issue_targets({'issues': [{**issue, 'candidate_paths': ['/segments/1/meaning_en']}]}, candidate, representation='japanese-annotation')
    boundary = {'segment_text': '家山', 'problem': 'under_grouped',
                'candidate_paths': ['/segments/0/surface', '/segments/1/surface'], 'supporting_paths': []}
    validate_issue_targets({'issues': [boundary]}, candidate, representation='japanese-annotation')
