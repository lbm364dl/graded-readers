import copy
import json
from pathlib import Path

import pytest

from pipeline.annotation_diagnostic_critic import build_contract, build_review_context, validate_review
from pipeline.worker_workspace import build, submit, CandidateSubmissionError

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'runs/korean-topik4/chapter-001'


def finding():
    return {'issue_id': 'identity', 'candidate_path': '/segments/2/lexical_id',
            'status': 'supported', 'fact': 'The selected identity differs from the cited sense.',
            'citations': [{'reference_id': 'record', 'path': '/definition_en'}], 'gap': ''}


def review():
    return {'approved': True, 'task_reviews': [
        {'task_id': 'identity', 'candidate_path': '/segments/2/lexical_id',
         'assessment': 'relevant', 'rationale': 'The cited evidence establishes an identity defect.',
         'citation_checks': [{'reference_id': 'record', 'path': '/definition_en',
                              'relevant': True, 'reason': 'Supports the research fact.'}]}]}


@pytest.mark.parametrize('language', ['zh', 'ja', 'ko'])
def test_supported_defect_fact_can_pass_research_gate_in_each_language(tmp_path, language):
    facts = [finding()]
    request = {'language': language, 'diagnostic_critic_policy_version': 1,
               'research_result': {'findings': facts}, 'critic_contract': build_contract(facts)}
    context = {'annotation_assigned_diagnostic_review': request,
               'annotation_diagnostic_critic_validation': {'input_field': 'annotation_assigned_diagnostic_review'}}
    build(tmp_path, 'Review evidence.', {'type': 'object'}, context=context)
    good = review()
    bad = copy.deepcopy(good)
    bad['task_reviews'][0]['citation_checks'].append(
        {'reference_id': 'context-only', 'path': '/definition_en', 'relevant': True, 'reason': 'Extra context.'})
    (tmp_path / 'candidate.json').write_text(json.dumps(bad))
    with pytest.raises(CandidateSubmissionError, match='every exact research citation once') as exc:
        submit(tmp_path, {'candidate_path': 'candidate.json'})
    assert exc.value.category == 'annotation_diagnostic_critic_rejection'
    (tmp_path / 'candidate.json').write_text(json.dumps(good))
    accepted, _ = submit(tmp_path, {'candidate_path': 'candidate.json'})
    assert accepted == good
    assert request['critic_contract']['candidate_approval'] is None


def test_mismatched_evidence_remains_rejected_without_normalizing_verdict():
    rejected = review()
    rejected['approved'] = False
    rejected['task_reviews'][0]['assessment'] = 'evidence_mismatch'
    validate_review(rejected, [finding()])
    rejected['approved'] = True
    with pytest.raises(ValueError, match='Global critic approval conflicts'):
        validate_review(rejected, [finding()])


def test_changed_contract_or_duplicate_citation_cannot_pass(tmp_path):
    facts = [finding()]
    request = {'diagnostic_critic_policy_version': 1, 'research_result': {'findings': facts},
               'critic_contract': build_contract(facts)}
    from pipeline.annotation_diagnostic_critic import validate_submission
    request['critic_contract']['citation_matrix'][0]['candidate_path'] = '/segments/3/lexical_id'
    with pytest.raises(ValueError, match='contract differs'):
        validate_submission(review(), request)
    duplicated = review()
    duplicated['task_reviews'][0]['citation_checks'] *= 2
    with pytest.raises(ValueError, match='every exact research citation once'):
        validate_review(duplicated, facts)


def test_actual_extra_citation_reproducer_and_contrasting_payload():
    original = PILOT / 'annotation-diagnostic-tasks/268a26b803d285a8b0fa311b2db391f2aab20da48d54ed488b2552906dfa1669/diagnostic-result.json'
    writer = PILOT / 'agents/annotation-diagnostic-research-6d4b9db2c56a50521c941a9339018c79848b533b598b2606c85f77b82c1899ff/result.json'
    critic = PILOT / 'agents/annotation-diagnostic-review-6185be6cc37e9b7d1c4f677cbbd31191285e613b5bcc1af180cb6b39e97d78f1/result.json'
    if not all(p.is_file() for p in (original, writer, critic)):
        pytest.skip('Retained pilot artifacts unavailable; portable contrasting tests still run')
    old = json.loads(original.read_text())
    facts = copy.deepcopy(old['research_result']['findings'])
    facts = [json.loads(writer.read_text())['findings'][0] if f['issue_id'] == 'month-lexical-identity' else f for f in facts]
    raw = json.loads(critic.read_text())
    with pytest.raises(ValueError, match='every exact research citation once'):
        validate_review(raw, facts)
    # Test structural validation only; do not produce or save a corrected receipt.
    contrast = copy.deepcopy(raw)
    target = next(r for r in contrast['task_reviews'] if r['task_id'] == 'stage88-idiomatic-meaning')
    expected = next(f['citations'] for f in facts if f['issue_id'] == target['task_id'])
    target['citation_checks'] = [c for c in target['citation_checks'] if
                               {'reference_id': c['reference_id'], 'path': c['path']} in expected]
    validate_review(contrast, facts)
    assert contrast['approved'] is False


@pytest.mark.parametrize('language', ['zh', 'ja', 'ko'])
def test_current_findings_have_a_separate_indexed_file_from_same_target_history(tmp_path, language):
    facts = [finding()]
    current_fact = facts[0]['fact']
    request = {'diagnostic_critic_policy_version': 1,
               'research_result': {'findings': facts}, 'critic_contract': build_contract(facts),
               'assigned_diagnostic_inputs': {'language': language, 'candidate': {'text': 'source'},
                   'tasks': [{'task_id': 'identity', 'candidate_paths': ['/segments/2/lexical_id']}]},
               'successor_history': {'prior_finding': {**finding(), 'fact': 'SUPERSEDED wrong occurrence'},
                                     'prior_critic': {'approved': False}}}
    context = build_review_context(request)
    from pipeline.annotation_diagnostic_critic import WORKSPACE_ENTRY_GUIDANCE
    build(tmp_path, WORKSPACE_ENTRY_GUIDANCE, {'type': 'object'}, context=context)
    index = json.loads((tmp_path/'INDEX.json').read_text())
    by_field = {r['field']: tmp_path/r['path'] for r in index}
    primary = json.loads(by_field['annotation_assigned_diagnostic_review'].read_text())
    assert primary['research_result']['findings'][0]['fact'] == current_fact
    assert 'assigned_diagnostic_inputs' not in primary and 'successor_history' not in primary
    assert 'SUPERSEDED' not in by_field['annotation_assigned_diagnostic_review'].read_text()
    assert json.loads(by_field['diagnostic_task_source_context'].read_text()) == request['assigned_diagnostic_inputs']
    assert json.loads(by_field['diagnostic_review_history'].read_text()) == request['successor_history']
    assert 'research_result.findings first' in (tmp_path/'TASK.txt').read_text()
    (tmp_path/'candidate.json').write_text(json.dumps(review()))
    accepted, _ = submit(tmp_path, {'candidate_path': 'candidate.json'})
    assert accepted['approved'] is True
    context['diagnostic_review_history']['prior_finding']['fact'] = 'changed copy'
    assert request['successor_history']['prior_finding']['fact'] == 'SUPERSEDED wrong occurrence'
