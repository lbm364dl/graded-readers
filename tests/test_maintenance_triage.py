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
