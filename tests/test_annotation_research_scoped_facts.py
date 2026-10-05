"""Research claim approval is distinct from mixed-target candidate adjudication."""
import asyncio
import copy

import pytest

import pipeline.annotation_research as research
from pipeline.annotation_adjudication import normalize_review
from tests.test_annotation_research import FakeRunner, _allow_initial_replay


def mixed_request():
    candidate = {'segments': [{'text': '놓고', 'lemma': '놓다',
        'meaning_en': 'left the desk in the position produced by pushing it',
        'form_steps': [{'form': '놓고', 'meaning_en': 'leave it in that position, then'}]}],
        'grammar_links': [{'segment_index': 0,
            'context_en': 'The result remains while the auxiliary links to the following action.'}]}
    targets = ['/segments/0/meaning_en', '/segments/0/form_steps/0/meaning_en', '/grammar_links/0/context_en']
    issue = {'candidate_paths': targets, 'supporting_paths': ['/segments/0/text'],
        'explanation': 'The occurrence gloss, form-step meaning and context are defective.'}
    review = {'approved': False, 'issues': [issue], 'prose_revision_reason_en': ''}
    identity = normalize_review('ko', review)['issues'][0]['issue_id']
    source = '놓고'
    return {'language': 'ko', 'representation': 'korean-flat', 'candidate': candidate,
        'current_review': review, 'prior_history': [], 'context': {}, 'source_text': source,
        'deterministic_gate_evidence': {'passed': True, 'issues': [],
            'candidate_digest': research._digest(candidate), 'source_text_digest': research._digest(source)},
        'initial_adjudication': {'status': 'uncertain', 'approved': False,
            'prose_requests': [], 'prose_revision_reason': '', 'classifications': [{
                'issue_id': identity, 'disposition': 'uncertain', 'candidate_paths': targets,
                'reason': 'One occurrence scope is defective; the other fields may be acceptable.'}]},
        'known_reference_input': {'auxiliary': {'kind': 'approved_lesson', 'content': {
            'definition_en': 'The auxiliary contributes completion and retention of the result; the full construction includes the preceding action.'}}},
        'normal_review_receipt': {'review_digest': research._digest(review)}}


def supported_output(request, fact):
    identity = request['initial_adjudication']['classifications'][0]['issue_id']
    return {'findings': [{'issue_id': identity, 'status': 'supported', 'fact': fact,
        'citations': [{'reference_id': 'auxiliary', 'path': '/definition_en'}], 'gap': ''}]}


@pytest.mark.parametrize('fact', [
    'The preceding action belongs to the complete construction rather than the auxiliary occurrence gloss; retained-result contributions in the form step and context remain supported.',
    'The preceding action belongs to the complete construction rather than the auxiliary occurrence gloss; this fact does not settle a separate unestablished contribution.',
])
def test_useful_cited_mixed_or_partial_fact_preserves_candidate_and_every_current_target(tmp_path, monkeypatch, fact):
    _allow_initial_replay(monkeypatch)
    request = mixed_request()
    original = copy.deepcopy(request)
    runner = FakeRunner(tmp_path, research_output=supported_output(request, fact),
        review_output={'approved': True, 'issues': []})
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result['status'] == 'approved'  # Scoped research evidence only.
    assert request == original
    assert result['evidence']['inputs']['candidate'] == original['candidate']
    assert result['evidence']['inputs']['current_review'] == original['current_review']
    assert result['evidence']['inputs']['initial_adjudication']['status'] == 'uncertain'
    tasks = result['evidence']['inputs']['uncertainty_tasks']['tasks']
    assert tasks[0]['candidate_paths'] == original['current_review']['issues'][0]['candidate_paths']
    assert len(tasks[0]['candidate_observations']) == 3
    assert len(result['references']) == 1
    reference = next(iter(result['references'].values()))
    assert reference['issue_ids'] == [tasks[0]['issue_id']]
    assert reference['content']['candidate_digest'] == research._digest(original['candidate'])
    assert reference['content']['review_digest'] == research._digest(original['current_review'])
    assert result['evidence']['version'] == result['evidence']['inputs']['research_policy_version'] == 7
    assert research.verify_research_evidence(tmp_path, result['evidence'], **request) == result
    assert 'effective_review_kind' not in result


@pytest.mark.parametrize('fact,objection', [
    ('A past ending marks previous time.', 'The fact and citation do not address any assigned field.'),
    ('The auxiliary alone expresses every preceding action.', 'The cited source does not support the asserted auxiliary meaning.'),
])
def test_independent_rejection_of_irrelevant_or_unsupported_claim_still_blocks_references(tmp_path, monkeypatch, fact, objection):
    _allow_initial_replay(monkeypatch)
    request = mixed_request()
    runner = FakeRunner(tmp_path, research_output=supported_output(request, fact),
        review_output={'approved': False, 'issues': [objection]})
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result['status'] == 'unresolved' and not result['references']
    assert result['evidence']['review_output']['issues'] == [objection]
    assert request['initial_adjudication']['status'] == 'uncertain'
    assert research.verify_research_evidence(tmp_path, result['evidence'], **request) == result


def test_no_supported_fact_retains_explicit_gap_even_if_critic_accepts_honest_research(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = mixed_request()
    identity = request['initial_adjudication']['classifications'][0]['issue_id']
    output = {'findings': [{'issue_id': identity, 'status': 'unresolved',
        'fact': '', 'citations': [], 'gap': 'No supplied evidence establishes the disputed contribution.'}]}
    runner = FakeRunner(tmp_path, research_output=output,
        review_output={'approved': True, 'issues': []})
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result['status'] == 'unresolved' and not result['references']
    assert result['evidence']['resolved_research']['unresolved'][0]['gap'] == output['findings'][0]['gap']
    assert request['initial_adjudication']['status'] == 'uncertain'


@pytest.mark.parametrize('version', [2, 3, 4, 5])
def test_v6_scope_clarification_does_not_change_historical_policy_prompts(version):
    inputs = research._build_inputs(**mixed_request(), policy_version=version)
    for prompt in [research._research_prompt(inputs), research._review_prompt(inputs)]:
        assert research.SCOPED_FACT_REVIEW_GUIDANCE not in prompt
    fresh = research._build_inputs(**mixed_request())
    for prompt in [research._research_prompt(fresh), research._review_prompt(fresh)]:
        assert research.SCOPED_FACT_REVIEW_GUIDANCE in prompt
        assert 'Do not require every original target to' in prompt
        assert 'neither clears any' in prompt
        assert 'Still reject unrelated facts' in prompt
    assert fresh['research_policy_digest'] != inputs['research_policy_digest']
    assert fresh['review_policy_digest'] != inputs['review_policy_digest']
