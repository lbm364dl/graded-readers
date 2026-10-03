import asyncio
import copy
import json

import pytest

from pipeline.maintenance_triage import check_proposal, run, save


def finding(cluster_id='missing-lesson'):
    return {'cluster_id': cluster_id, 'status': 'proposed_fix', 'diagnosis': 'A repeated lesson link is absent.',
            'evidence_paths': ['worker/events.jsonl'], 'affected_languages': ['shared'],
            'change_layer': 'instructions', 'proposed_change': 'Link each stage to its actual lesson.',
            'regression_cases': [{'case': 'Missing link', 'expected_outcome': 'Rejected'},
                                 {'case': 'Valid link', 'expected_outcome': 'Retained'}],
            'verification': 'Rerun affected proposals and retained controls.', 'remaining_uncertainty': ''}


def report(count=3, recurring=True):
    return {'run_dirs': ['runs/language'], 'clusters': [{'id': 'missing-lesson', 'trigger_worthy': recurring,
            'distinct_jobs': count, 'unresolved_count': count,
            'evidence': [{'artifact_path': 'worker/events.jsonl'}]}]}


class Runner:
    def __init__(self):
        self.jobs = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        assert effort == 'low' and kwargs['tool_profile'] == 'workspace'
        assert 'Never weaken validation' in prompt
        self.jobs.append(job)
        return {'findings': [finding()]}


def test_recurring_triage_reuses_diagnosis_until_evidence_doubles(tmp_path):
    source, output = tmp_path / 'report.json', tmp_path / 'triage'
    runner = Runner()
    for count, expected in [(3, 1), (3, 1), (5, 1), (6, 2)]:
        save(source, report(count))
        asyncio.run(run(source, output, runner=runner))
        assert len(runner.jobs) == expected
    assert runner.jobs[0] != runner.jobs[1]
    assert json.loads((output / 'summary.json').read_text())['implementation_status'] == 'proposals_require_verified_fixes'


def test_one_off_incident_does_not_commission_maintenance_agent(tmp_path):
    source = tmp_path / 'report.json'
    save(source, report(1, False))
    runner = Runner()
    assert asyncio.run(run(source, tmp_path / 'triage', runner=runner))['new_diagnoses'] == []
    assert runner.jobs == []


def test_unverified_diagnosis_cannot_claim_reviewable_fix_without_contrast():
    cluster = {'id': 'missing-lesson', 'evidence': [{'artifact_path': 'worker/events.jsonl'}]}
    proposal = {'findings': [finding()]}
    assert check_proposal(proposal, cluster)['status'] == 'proposed_fix'
    for change in [{'regression_cases': []}, {'proposed_change': ''}, {'evidence_paths': []},
                   {'cluster_id': 'other'}, {'evidence_paths': ['made-up/events.jsonl']}]:
        bad = copy.deepcopy(proposal)
        bad['findings'][0].update(change)
        with pytest.raises(ValueError):
            check_proposal(bad, cluster)


def test_addressed_hypothesis_requires_current_code_tests_and_verification():
    cluster = {'id': 'missing-lesson', 'evidence': [{'artifact_path': 'worker/events.jsonl'}]}
    proposal = {'findings': [{**finding(), 'status': 'already_addressed',
        'evidence_paths': ['worker/events.jsonl', 'pipeline/maintenance.py:1',
                           'tests/test_pipeline_maintenance.py']}]}
    assert check_proposal(proposal, cluster)['status'] == 'already_addressed'
    for change in [{'verification': ''}, {'evidence_paths': ['worker/events.jsonl']},
                   {'evidence_paths': ['worker/events.jsonl', 'pipeline/maintenance.py', 'tests/made-up.py']}]:
        bad = copy.deepcopy(proposal)
        bad['findings'][0].update(change)
        with pytest.raises(ValueError, match='hypothesis needs'):
            check_proposal(bad, cluster)


def test_incident_citation_accepts_line_suffix_but_not_another_artifact():
    cluster = {'id': 'missing-lesson', 'evidence': [{'artifact_path': 'worker/events.jsonl'}]}
    proposal = {'findings': [{**finding(), 'evidence_paths': ['worker/events.jsonl:12-14']}]}
    assert check_proposal(proposal, cluster)['status'] == 'proposed_fix'
    unrelated = copy.deepcopy(proposal)
    unrelated['findings'][0]['evidence_paths'] = ['worker/other-events.jsonl:12']
    with pytest.raises(ValueError, match='actual incident artifact'):
        check_proposal(unrelated, cluster)


class PlannedRunner:
    def __init__(self, planned):
        self.planned = planned
        self.calls = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        assert effort == 'low' and kwargs['tool_profile'] == 'workspace'
        assert 'Never weaken validation' in prompt
        inputs = json.loads(prompt.split('INPUT:\n', 1)[1])
        cluster = inputs['cluster']['id']
        self.calls.append((cluster, inputs))
        return self.planned[cluster].pop(0)


def multi_report(*cluster_ids, count=3):
    return {'run_dirs': ['runs/language'], 'clusters': [
        {'id': cluster_id, 'trigger_worthy': True, 'distinct_jobs': count,
         'unresolved_count': count,
         'evidence': [{'artifact_path': f'{cluster_id}/events.jsonl'}]}
        for cluster_id in cluster_ids]}


def proposal_for(cluster_id, evidence=None):
    value = finding(cluster_id)
    value['evidence_paths'] = [evidence or f'{cluster_id}/events.jsonl']
    return {'findings': [value]}


def test_invalid_proposal_gets_one_exact_feedback_repair_and_keeps_rejection(tmp_path):
    source, output = tmp_path / 'report.json', tmp_path / 'triage'
    save(source, multi_report('cluster-a'))
    invalid = proposal_for('cluster-a', 'not-an-incident.jsonl')
    runner = PlannedRunner({'cluster-a': [invalid, proposal_for('cluster-a')]})
    result = asyncio.run(run(source, output, runner=runner, limit=1))

    assert len(runner.calls) == 2
    assert runner.calls[1][1]['validation_feedback'] == 'Maintenance diagnosis must cite an actual incident artifact'
    assert [row['finding']['cluster_id'] for row in result['new_diagnoses']] == ['cluster-a']
    state = json.loads((output / 'triage-state.json').read_text())['clusters']['cluster-a']
    assert len(state['rejected_attempts']) == 1
    assert state['rejected_attempts'][0]['validation_error'] == runner.calls[1][1]['validation_feedback']
    assert state['finding']['evidence_paths'] == ['cluster-a/events.jsonl']


def test_persistent_invalid_cluster_is_not_a_finding_and_does_not_starve_next_cluster(tmp_path):
    source, output = tmp_path / 'report.json', tmp_path / 'triage'
    save(source, multi_report('a-invalid', 'z-valid'))
    invalid = proposal_for('a-invalid', 'unrelated.jsonl')
    runner = PlannedRunner({'a-invalid': [invalid, invalid], 'z-valid': [proposal_for('z-valid')]})

    first = asyncio.run(run(source, output, runner=runner, limit=1))
    assert first['new_diagnoses'] == []
    assert first['known_clusters'] == 0
    assert first['attempted_clusters'] == 1
    state = json.loads((output / 'triage-state.json').read_text())['clusters']['a-invalid']
    assert 'finding' not in state
    assert len(state['rejected_attempts']) == 2
    assert all(row['validation_error'] == 'Maintenance diagnosis must cite an actual incident artifact'
               for row in state['rejected_attempts'])

    second = asyncio.run(run(source, output, runner=runner, limit=1))
    assert [row['finding']['cluster_id'] for row in second['new_diagnoses']] == ['z-valid']
    assert [cluster_id for cluster_id, _ in runner.calls] == ['a-invalid', 'a-invalid', 'z-valid']


def test_rejected_refresh_preserves_the_accepted_findings_exact_evidence(tmp_path):
    source, output = tmp_path / 'report.json', tmp_path / 'triage'
    save(source, multi_report('cluster-a'))
    runner = PlannedRunner({'cluster-a': [proposal_for('cluster-a')]})
    asyncio.run(run(source, output, runner=runner, limit=1))
    state_path = output / 'triage-state.json'
    accepted = json.loads(state_path.read_text())['clusters']['cluster-a']
    save(source, multi_report('cluster-a', count=6))
    invalid = proposal_for('cluster-a', 'unrelated.jsonl')
    runner.planned['cluster-a'] = [invalid, invalid]
    result = asyncio.run(run(source, output, runner=runner, limit=1))
    current = json.loads(state_path.read_text())['clusters']['cluster-a']
    for key in ('finding', 'job', 'distinct_jobs', 'input_digest', 'output_digest'):
        assert current[key] == accepted[key]
    assert current['attempted_distinct_jobs'] == 6
    assert current['last_rejected_attempt']['job'] != accepted['job']
    assert result['known_clusters'] == 1
    assert result['new_diagnoses'] == []
