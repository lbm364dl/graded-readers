import asyncio
import copy
import json

import pytest
from jsonschema import ValidationError

from pipeline.annotation_review_ledger import (
    LedgerProtocolError,
    prepare_saved_smoke_case,
    review_finding_ledger,
    verify_ledger_output,
)


def snapshots():
    original = {'segments': [{'form_steps': [{'form': '태어났', 'label': 'past',
                                              'meaning_en': 'was born'}]}]}
    current = copy.deepcopy(original)
    return original, current


def finding(original=None, finding_id='f1', text='The displayed stage is incomplete.'):
    original = original or snapshots()[0]
    return {'finding_id': finding_id, 'text': text, 'original_candidate': original}


def observation(path, value):
    return {'path': path, 'observed_value': value}


def ledger(current, *, disposition='resolved', original=None, changed=True,
           ledger_clear=None, new_issues=None, repair_diagnoses=None, prose=''):
    original = original or snapshots()[0]
    original_value = original['segments'][0]['form_steps'][0]['meaning_en']
    current_value = current['segments'][0]['form_steps'][0]['meaning_en']
    findings = [{'finding_id': 'f1', 'disposition': disposition,
        'original_observations': [observation('/segments/0/form_steps/0/meaning_en', original_value)],
        'current_observations': [observation('/segments/0/form_steps/0/meaning_en', current_value)],
        'reason': 'The complete form meaning was corrected.' if disposition == 'resolved'
                  else 'The review conclusion is recorded.',
        'evidence': 'The exact form chain and supplied lesson support this conclusion.'}]
    new_issues = new_issues or []
    if repair_diagnoses is None:
        repair_diagnoses = []
        if disposition == 'unresolved':
            repair_diagnoses = [{'finding_ids': ['f1'], 'new_issue_ids': [],
                'diagnosis': 'The stage still gives the incomplete form as its meaning.',
                'observations': [observation('/segments/0/form_steps/0/meaning_en', current_value)]}]
    if ledger_clear is None:
        ledger_clear = disposition != 'unresolved' and not new_issues and not prose
    return {'ledger_clear': ledger_clear, 'findings': findings, 'new_issues': new_issues,
            'repair_diagnoses': repair_diagnoses, 'prose_revision_reason_en': prose}


def test_resolved_requires_exact_changed_field_and_produces_approval():
    original, current = snapshots()
    current['segments'][0]['form_steps'][0]['meaning_en'] = 'was born in this complete form'
    verified = verify_ledger_output(ledger(current), current_candidate=current,
        historical_findings=[finding(original)], context={'language': 'ko'})
    assert verified['approved'] is False
    assert verified['ledger_clear'] is True
    assert verified['requires_independent_review'] is True
    assert verified['findings'][0]['observed_change'] is True


def test_unchanged_field_cannot_be_claimed_resolved():
    original, current = snapshots()
    with pytest.raises(LedgerProtocolError, match='without a relevant changed field'):
        verify_ledger_output(ledger(current), current_candidate=current,
            historical_findings=[finding(original)])


def test_unsupported_is_distinct_from_resolved_and_requires_evidence():
    original, current = snapshots()
    output = ledger(current, disposition='unsupported', ledger_clear=True)
    result = verify_ledger_output(output, current_candidate=current,
        historical_findings=[finding(original)])
    assert result['approved'] is False
    assert result['ledger_clear'] is True
    assert result['findings'][0]['observed_change'] is False
    output['findings'][0]['evidence'] = ''
    with pytest.raises(ValidationError):
        verify_ledger_output(output, current_candidate=current,
            historical_findings=[finding(original)])


def test_unresolved_finding_has_actionable_current_diagnosis_and_stays_unapproved():
    original, current = snapshots()
    output = ledger(current, disposition='unresolved', ledger_clear=False)
    verified = verify_ledger_output(output, current_candidate=current,
        historical_findings=[finding(original)])
    assert not verified['approved'] and not verified['ledger_clear']
    assert verified['repair_diagnoses'][0]['finding_ids'] == ['f1']
    output['repair_diagnoses'][0]['diagnosis'] = finding(original)['text']
    with pytest.raises(LedgerProtocolError, match='copy historical'):
        verify_ledger_output(output, current_candidate=current,
            historical_findings=[finding(original)])


def test_comparable_unresolved_findings_can_share_one_current_repair_diagnosis():
    original, current = snapshots()
    old_findings = [finding(original), finding(original, 'f2', 'The same stage is incomplete.')]
    output = ledger(current, disposition='unresolved', ledger_clear=False)
    second = copy.deepcopy(output['findings'][0])
    second['finding_id'] = 'f2'
    output['findings'].append(second)
    output['repair_diagnoses'][0]['finding_ids'] = ['f1', 'f2']
    verified = verify_ledger_output(output, current_candidate=current,
        historical_findings=old_findings)
    assert len(verified['repair_diagnoses']) == 1
    assert verified['repair_diagnoses'][0]['finding_ids'] == ['f1', 'f2']


@pytest.mark.parametrize('damage', ['omission', 'duplicate', 'wrong_id'])
def test_historical_finding_ids_must_be_reconciled_exactly(damage):
    original, current = snapshots()
    findings = [finding(original), finding(original, 'f2', 'A second historical defect.')]
    current['segments'][0]['form_steps'][0]['meaning_en'] = 'born'
    output = ledger(current, original=original)
    second = copy.deepcopy(output['findings'][0])
    second['finding_id'] = 'f2'
    output['findings'].append(second)
    if damage == 'omission':
        output['findings'].pop()
    elif damage == 'duplicate':
        output['findings'][1]['finding_id'] = 'f1'
    else:
        output['findings'][1]['finding_id'] = 'f3'
    with pytest.raises(LedgerProtocolError, match='cover every historical'):
        verify_ledger_output(output, current_candidate=current, historical_findings=findings)


@pytest.mark.parametrize('damage', ['wrong_value', 'missing_pointer', 'invalid_escape'])
def test_candidate_observations_are_bound_to_immutable_snapshots(damage):
    original, current = snapshots()
    current['segments'][0]['form_steps'][0]['meaning_en'] = 'was born fully'
    output = ledger(current)
    if damage == 'wrong_value':
        output['findings'][0]['current_observations'][0]['observed_value'] = 'a guessed value'
    elif damage == 'missing_pointer':
        output['findings'][0]['current_observations'][0]['path'] = '/segments/8'
    else:
        output['findings'][0]['current_observations'][0]['path'] = '/segments/0/~2bad'
    with pytest.raises(LedgerProtocolError):
        verify_ledger_output(output, current_candidate=current,
            historical_findings=[finding(original)])


def test_new_finding_requires_evidence_and_repair_diagnosis():
    original, current = snapshots()
    current['segments'][0]['form_steps'][0]['meaning_en'] = 'was born fully'
    issue = {'issue_id': 'new-1', 'issue': 'The form label is inconsistent.',
        'observations': [observation('/segments/0/form_steps/0/label', 'past')],
        'reason': 'The label does not fit the observed form.', 'evidence': 'The supplied chain.'}
    output = ledger(current, new_issues=[issue], ledger_clear=False,
        repair_diagnoses=[{'finding_ids': [], 'new_issue_ids': ['new-1'],
            'diagnosis': 'The current form label conflicts with the reviewed step.',
            'observations': [observation('/segments/0/form_steps/0/label', 'past')]}])
    verified = verify_ledger_output(output, current_candidate=current,
        historical_findings=[finding(original)])
    assert verified['approved'] is False
    assert verified['ledger_clear'] is False
    output['repair_diagnoses'][0]['new_issue_ids'] = []
    with pytest.raises(LedgerProtocolError, match='Repair diagnosis'):
        verify_ledger_output(output, current_candidate=current,
            historical_findings=[finding(original)])


def test_prose_change_blocks_an_otherwise_approved_ledger():
    original, current = snapshots()
    current['segments'][0]['form_steps'][0]['meaning_en'] = 'was born fully'
    output = ledger(current, ledger_clear=True)
    with pytest.raises(LedgerProtocolError, match='contradicts'):
        verify_ledger_output(output, current_candidate=current,
            historical_findings=[finding(original)], prose_before='source A', prose_after='source B')


def test_tools_enabled_runner_receives_luna_low_contract_and_saves_verifiable_evidence(tmp_path):
    original, current = snapshots()
    current['segments'][0]['form_steps'][0]['meaning_en'] = 'was born fully'

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            assert job.startswith('annotation-finding-ledger-')
            assert effort in {'low', 'medium'}
            assert kwargs['tool_profile'] == 'research'
            assert 'every historical finding' in prompt
            assert 'review-policy-test' in prompt
            assert 'annotation_finding_ledger' in kwargs['workspace_context']
            return ledger(current, ledger_clear=True)

    result = asyncio.run(review_finding_ledger(Runner(), tmp_path,
        current_candidate=current, historical_findings=[finding(original)],
        context={'language': 'ko', 'review_instructions': 'review-policy-test'}))
    assert result['approved'] is False and result['ledger_clear']
    assert list((tmp_path / 'agents').glob('*/verified-ledger.json'))


def test_saved_smoke_adapter_preserves_each_finding_candidate_and_current_artifact(tmp_path):
    old, revised = snapshots()
    revised['segments'][0]['form_steps'][0]['meaning_en'] = 'was born fully'
    cases = [{'name': 'saved-case', 'level': 4, 'old_review': {'issues': ['Old finding.']},
        'review_inputs': {'annotation': old, 'text': 'unchanged prose', 'context': {'language': 'ko'}}}]
    cases_path = tmp_path / 'cases.json'
    cases_path.write_text(json.dumps(cases), encoding='utf-8')
    run_dir = tmp_path / 'saved-case'
    review_dir = run_dir / 'agents/review-a'
    review_dir.mkdir(parents=True)
    round_input = {'annotation': revised, 'text': 'unchanged prose', 'context': {'language': 'ko'}}
    (review_dir / 'review-input.json').write_text(json.dumps(round_input), encoding='utf-8')
    from pipeline.annotation_review_ledger import canonical_digest
    summary = [{'case': 'saved-case', 'input_digest': 'base-input-digest', 'reviews': [{
        'round': 0, 'candidate_digest': canonical_digest(revised),
        'evidence': {'job': 'review-a'},
        'review': {'issues': ['Fresh finding.']} }]}]
    summary_path = tmp_path / 'summary.json'
    summary_path.write_text(json.dumps(summary), encoding='utf-8')
    current_path = tmp_path / 'assembly-result.json'
    current_path.write_text(json.dumps(revised), encoding='utf-8')
    output_path = tmp_path / 'ledger-input.json'
    prepare_saved_smoke_case(cases_path, summary_path, 'saved-case', current_path, output_path)
    prepared = json.loads(output_path.read_text(encoding='utf-8'))
    assert [row['finding_id'] for row in prepared['historical_findings']] == [
        'historical-000', 'round-000-000']
    assert len(prepared['historical_candidates_by_digest']) == 2
    assert prepared['current_candidate'] == revised
    assert prepared['prose_before'] == prepared['prose_after'] == 'unchanged prose'
    assert 'learner difficulty' in prepared['review_instructions']
    assert prepared['historical_findings'][0]['original_context'] == {'language': 'ko'}


def test_effort_and_instruction_digest_separate_cached_job_identities(tmp_path):
    original, current = snapshots()
    current['segments'][0]['form_steps'][0]['meaning_en'] = 'was born fully'
    observed = []

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            observed.append((job, effort, prompt))
            assert kwargs['tool_profile'] == 'research'
            return ledger(current, ledger_clear=True)

    for effort in ('low', 'medium'):
        asyncio.run(review_finding_ledger(Runner(), tmp_path / effort,
            current_candidate=current, historical_findings=[finding(original)],
            context={'review_instructions': 'language policy evidence'}, effort=effort))
    assert observed[0][1] == 'low' and observed[1][1] == 'medium'
    assert observed[0][0] != observed[1][0]
    assert all('language policy evidence' in row[2] for row in observed)
