import json
from pathlib import Path

import pytest

from pipeline.annotation_adjudication import OUTPUT_SCHEMA, _build_inputs, _validate_output, digest
from pipeline.maintenance import scan_runs
from pipeline.worker_workspace import build, submit


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def adjudication_job(run, *, disposition='actionable', index=0):
    candidate = {'segments': [{'text': 'a', 'meaning_en': 'a'}, {'text': 'b', 'meaning_en': 'b'}]}
    review = {'verdict': 'revise', 'issues': ['Segment 1 has wrong occurrence meaning.']}
    receipt = {'review_digest': digest(review)}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={'source_start': index},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'b'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate)},
        normal_review_receipt=receipt, source_text=None)
    job = 'annotation-adjudication-' + inputs['input_digest']
    directory = run/'agents'/job
    context = {'annotation_adjudication': inputs,
        'annotation_adjudication_validation': {'input_field': 'annotation_adjudication'}}
    _, workspace_digest = build(directory/'workspace', 'Inspect evidence', OUTPUT_SCHEMA, context=context)
    output = {'classifications': [{'issue_id': inputs['normalized_review']['issues'][0]['issue_id'],
        'disposition': disposition, 'target_binding': 'exact', 'candidate_paths': ['/segments/0/meaning_en'],
        'evidence_refs': [{'reference_id': 'lesson', 'path': '/meaning'}],
        'reason': 'Lesson supports this value.', 'diagnosis': 'Repair the local field.'}],
        'new_issues': [], 'prose_revision_reason': ''}
    write(directory/'workspace/candidate.json', output)
    output, artifact = submit(directory/'workspace', {'candidate_path': 'candidate.json'})
    verified = _validate_output(output, inputs)
    verified.update({'job': job, 'effort': 'low', 'model': 'gpt-6-luna', 'tool_profile': 'research',
        'worker_tool_profile': 'workspace', 'normal_review_receipt': receipt,
        'normal_review_receipt_digest': inputs['normal_review_receipt_digest'],
        'verified_result_digest': digest(verified)})
    for filename, value in [('result.json', output), ('adjudication-input.json', inputs),
                            ('verified-adjudication.json', verified)]:
        write(directory/filename, value)
    write(directory/'meta.json', {'return_code': 0, 'model': 'gpt-6-luna', 'effort': 'low',
        'tool_profile': 'workspace', 'workspace_digest': workspace_digest,
        'artifact_path': artifact['artifact_path'], 'artifact_digest': artifact['artifact_digest']})
    return directory


def downgrade_clusters(report):
    return [row for row in report['clusters'] if row['category'] == 'adjudication_host_downgrade']


@pytest.mark.parametrize('disposition', ['actionable', 'unsupported'])
def test_exact_host_downgrade_observations_count_distinct_jobs_and_preserve_unknown_cause(tmp_path, disposition):
    for index in range(3):
        adjudication_job(tmp_path, index=index, disposition=disposition)
    write(tmp_path/'agents/annotation-review-99/result.json', {'approved': True, 'issues': []})
    report = scan_runs([tmp_path])
    cluster, = downgrade_clusters(report)
    assert cluster['distinct_jobs'] == 3 and cluster['trigger_worthy']
    assert cluster['unresolved_count'] == 3
    for row in cluster['evidence']:
        assert row['cause'] == 'unrecorded' and row['root_cause_status'] == 'unproven'
        assert not row['resolved']
        assert row['raw_disposition'] == disposition and row['effective_disposition'] == 'uncertain'
        assert set(row['bound_artifacts']) == {'result.json', 'verified-adjudication.json', 'adjudication-input.json'}
        for bound in row['bound_artifacts'].values():
            assert digest(json.loads(Path(bound['path']).read_text())) == bound['digest']


def test_unchanged_uncertain_and_non_adjudication_outputs_are_not_host_downgrades(tmp_path):
    adjudication_job(tmp_path, disposition='uncertain')
    write(tmp_path/'agents/other-worker/result.json', {'classifications': []})
    assert not downgrade_clusters(scan_runs([tmp_path]))


@pytest.mark.parametrize('mutation', ['raw', 'input', 'verified', 'partial', 'missing', 'symlink'])
def test_unreplayable_artifacts_never_establish_host_downgrade(tmp_path, mutation):
    directory = adjudication_job(tmp_path)
    target = directory/'result.json'
    if mutation in {'raw', 'input', 'verified'}:
        target = directory/({'raw': 'result.json', 'input': 'adjudication-input.json',
                             'verified': 'verified-adjudication.json'}[mutation])
        value = json.loads(target.read_text())
        if mutation == 'raw':
            value['classifications'][0]['reason'] = 'Tampered reason'
        elif mutation == 'input':
            value['candidate']['segments'][0]['text'] = 'changed'
        else:
            value['classifications'][0]['disposition'] = 'actionable'
        write(target, value)
    elif mutation == 'partial':
        target.write_text('{')
    elif mutation == 'missing':
        target.unlink()
    else:
        outside = tmp_path.parent/(tmp_path.name+'-external.json')
        outside.write_text(target.read_text())
        target.unlink()
        target.symlink_to(outside)
    assert not downgrade_clusters(scan_runs([tmp_path]))
