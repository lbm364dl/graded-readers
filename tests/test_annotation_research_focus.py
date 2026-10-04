"""Regression proposal: apply with research-focus-v4.patch before normal CI."""
import pytest

import pipeline.annotation_research as research
from pipeline.annotation_adjudication import normalize_review


def request_inputs():
    candidate = {'segments': [
        {'text': '가고', 'meaning_en': 'go and'},
        {'text': '사내의', 'meaning_en': 'of a man'}], 'grammar_overlays': []}
    issues = [
        {'explanation': 'Investigate the connective.',
         'candidate_paths': ['/segments/0/meaning_en'], 'supporting_paths': []},
        {'explanation': 'Investigate the noun contribution.',
         'candidate_paths': ['/segments/1/meaning_en'],
         'supporting_paths': ['/segments/1/text']}]
    review = {'approved': False, 'issues': issues, 'prose_revision_reason_en': ''}
    normalized = normalize_review('ko', review)['issues']
    initial = {'status': 'uncertain', 'approved': False,
        'prose_requests': [], 'prose_revision_reason': '',
        'classifications': [
            {'issue_id': normalized[0]['issue_id'], 'disposition': 'unsupported',
             'candidate_paths': issues[0]['candidate_paths'], 'reason': 'The rule already supports this connective.'},
            {'issue_id': normalized[1]['issue_id'], 'disposition': 'uncertain',
             'candidate_paths': issues[1]['candidate_paths'],
             'reason': 'An authoritative lexical source for the noun is missing.',
             'diagnosis': 'Investigate the exact noun contribution.'}]}
    source = '가고사내의'
    return {'language': 'ko', 'representation': 'korean-flat',
        'candidate': candidate, 'current_review': review, 'prior_history': [],
        'context': {'source_id': 'fixture'}, 'source_text': source,
        'deterministic_gate_evidence': {'passed': True, 'issues': [],
            'candidate_digest': research._digest(candidate),
            'source_text_digest': research._digest(source)},
        'initial_adjudication': initial,
        'known_reference_input': {
            'grammar:connective': {'kind': 'approved_lesson',
                'content': {'explanation_en': 'Connects actions with and.'}},
            'lexical-reference': {'kind': 'primary_source',
                'content': {'primary_url': 'https://krdict.korean.go.kr/eng/dicSearch/SearchView?ParaWordNo=62210'}}},
        'normal_review_receipt': {'review_digest': research._digest(review)}}


def test_focused_packet_excludes_other_findings_and_binds_exact_values_for_both_workers():
    request = request_inputs()
    inputs = research._build_inputs(**request)
    packet = inputs['uncertainty_tasks']
    assert len(packet['tasks']) == 1
    task = packet['tasks'][0]
    assert task['current_review_issue'] == request['current_review']['issues'][1]
    assert task['candidate_paths'] == ['/segments/1/meaning_en']
    assert task['candidate_observations'] == [{'path': '/segments/1/meaning_en', 'value': 'of a man'}]
    assert task['supporting_observations'] == [{'path': '/segments/1/text', 'value': '사내의'}]
    assert 'lexical source' in task['initial_uncertainty_reason']
    assert packet['supplied_primary_origins'] == ['https://krdict.korean.go.kr']
    assert packet['other_review_issues_are_context_only'] is True
    for role in ['research', 'review']:
        context = research._worker_context(role, inputs)
        assert context['annotation_uncertainty_tasks'] == packet
        # Full inspectable source/history and references remain available.
        assert context['annotation_uncertainty_research'] == inputs
    for prompt in [research._research_prompt(inputs), research._review_prompt(inputs)]:
        assert research.SCOPED_RESEARCH_GUIDANCE in prompt
        assert 'another construction is not support' in prompt
        assert 'another headword' in prompt


def test_supported_grammar_issue_gets_its_own_packet_when_it_is_the_uncertain_target():
    request = request_inputs()
    rows = request['initial_adjudication']['classifications']
    rows[0]['disposition'] = 'uncertain'
    rows[0]['reason'] = 'Need an exact connective formation rule.'
    rows[1]['disposition'] = 'unsupported'
    inputs = research._build_inputs(**request)
    tasks = inputs['uncertainty_tasks']['tasks']
    assert len(tasks) == 1
    assert tasks[0]['candidate_paths'] == ['/segments/0/meaning_en']
    assert tasks[0]['candidate_observations'][0]['value'] == 'go and'
    assert 'connective formation' in tasks[0]['initial_uncertainty_reason']


def test_typed_issue_target_wins_over_mismatched_uncertain_classification_scope():
    request = request_inputs()
    rows = request['initial_adjudication']['classifications']
    rows[0]['disposition'] = 'uncertain'
    rows[0]['candidate_paths'] = ['/segments/1/meaning_en']
    rows[0]['reason'] = 'The host could not bind the proposed classification target.'
    rows[1]['disposition'] = 'unsupported'
    task = research._build_inputs(**request)['uncertainty_tasks']['tasks'][0]
    assert task['candidate_paths'] == ['/segments/0/meaning_en']
    assert task['candidate_observations'] == [{'path': '/segments/0/meaning_en', 'value': 'go and'}]
    assert task['initial_classification_candidate_paths'] == ['/segments/1/meaning_en']
    assert task['initial_uncertainty_reason'] == rows[0]['reason']


def test_historical_untyped_issue_uses_saved_classification_targets():
    request = request_inputs()
    request['current_review']['issues'][0] = 'Investigate the meaning of the first segment.'
    rows = request['initial_adjudication']['classifications']
    rows[0]['issue_id'] = normalize_review('ko', request['current_review'])['issues'][0]['issue_id']
    rows[0]['disposition'] = 'uncertain'
    rows[1]['disposition'] = 'unsupported'
    request['normal_review_receipt']['review_digest'] = research._digest(request['current_review'])
    task = research._build_inputs(**request)['uncertainty_tasks']['tasks'][0]
    assert task['current_review_issue'] == request['current_review']['issues'][0]
    assert task['candidate_paths'] == rows[0]['candidate_paths']
    assert task['candidate_observations'] == [{'path': '/segments/0/meaning_en', 'value': 'go and'}]


def test_unknown_uncertain_identity_is_not_silently_rebound_to_another_issue():
    request = request_inputs()
    request['initial_adjudication']['classifications'][1]['issue_id'] = 'unbound-id'
    with pytest.raises(research.AnnotationResearchError, match='not bound'):
        research._build_inputs(**request)


def test_relevant_direct_primary_page_can_be_requested_only_for_unresolved_target():
    inputs = research._build_inputs(**request_inputs())
    identity = inputs['issue_ids'][0]
    output = {'findings': [{'issue_id': identity, 'status': 'unresolved',
        'fact': '', 'citations': [], 'gap': 'Need the target noun entry.'}],
        'source_requests': [{'url': 'https://krdict.korean.go.kr/eng/dicSearch/SearchView?ParaWordNo=12345',
            'issue_ids': [identity], 'reason': 'Inspect the independently located noun entry.'}]}
    facts, unresolved = research._validate_research_output(output, inputs['issue_ids'], inputs['known_reference_input'])
    assert not facts and unresolved
    refs = {'grammar:connective': inputs['known_reference_input']['grammar:connective']}
    with pytest.raises(research.AnnotationResearchError):
        research._validate_research_output(output, inputs['issue_ids'], refs)
    output['findings'][0].update(status='supported', gap='', fact='Connects actions with and.',
        citations=[{'reference_id': 'grammar:connective', 'path': '/explanation_en'}])
    with pytest.raises(research.AnnotationResearchError):
        research._validate_research_output(output, inputs['issue_ids'], inputs['known_reference_input'])


@pytest.mark.parametrize('version', [2, 3])
def test_historical_policy_inputs_and_worker_packets_are_unchanged(version):
    inputs = research._build_inputs(**request_inputs(), policy_version=version)
    assert 'uncertainty_tasks' not in inputs
    context = research._worker_context('review', inputs)
    assert set(context) == {'annotation_uncertainty_research', 'annotation_uncertainty_research_role'}
    assert research.SCOPED_RESEARCH_GUIDANCE not in research._research_prompt(inputs)
    assert research.SCOPED_RESEARCH_GUIDANCE not in research._review_prompt(inputs)
