import copy
import pytest
from jsonschema import ValidationError
from pipeline.annotation_adjudication import _build_inputs, _validate_output, digest, AdjudicationError


def payload(version, targets, supporting=None):
    candidate = {'segments': [{'text': 'a', 'meaning_en': 'bad'}, {'text': 'a', 'meaning_en': 'context'}]}
    review = {'verdict': 'revise', 'issues': ['Segment 0 occurrence meaning_en is wrong.']}
    inputs = _build_inputs(language='zh', representation='chinese-annotation', candidate=candidate,
        current_review=review, prior_history=[], context={},
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'good'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate),
                                     'source_text_digest': digest('aa')},
        normal_review_receipt={'review_digest': digest(review)}, source_text='aa',
        host_binding_policy_version=version)
    row = {'issue_id': 'new', 'issue': 'Wrong occurrence meaning.', 'target_binding': 'exact',
           'candidate_paths': targets, 'reason': 'Incorrect meaning.', 'diagnosis': 'Use the supported meaning.',
           'evidence_refs': [{'reference_id': 'lesson', 'path': '/meaning'}]}
    if supporting is not None:
        row['supporting_paths'] = supporting
    classification = {'issue_id': inputs['normalized_review']['issues'][0]['issue_id'],
        'disposition': 'actionable', 'target_binding': 'exact',
        'candidate_paths': ['/segments/0/meaning_en'],
        'evidence_refs': [{'reference_id': 'lesson', 'path': '/meaning'}],
        'reason': 'Incorrect meaning.', 'diagnosis': 'Use the supported meaning.'}
    return inputs, {'classifications': [classification], 'new_issues': [row], 'prose_revision_reason': ''}


@pytest.mark.parametrize('version', [1, 2, 3, 4])
def test_historical_new_issue_container_result_replays_unchanged(version):
    inputs, output = payload(version, ['/segments'])
    result = _validate_output(output, inputs)
    assert result['status'] == 'actionable'
    assert result['repair_diagnoses'][-1]['paths'] == ['/segments']


@pytest.mark.parametrize('target', ['/segments', '/segments/0', '/segments/99/meaning_en'])
def test_policy5_rejects_container_or_unresolved_new_issue(target):
    inputs, output = payload(5, [target], [])
    with pytest.raises(AdjudicationError):
        _validate_output(output, inputs)


def test_policy5_valid_scalar_keeps_support_out_of_repair_scope():
    inputs, output = payload(5, ['/segments/0/meaning_en'], ['/segments/1/meaning_en'])
    result = _validate_output(output, inputs)
    assert result['status'] == 'actionable'
    assert result['repair_diagnoses'][-1]['paths'] == ['/segments/0/meaning_en']
    output['new_issues'][0]['supporting_paths'] = ['/segments/0/meaning_en']
    with pytest.raises(AdjudicationError, match='disjoint'):
        _validate_output(output, inputs)


def test_policy5_requires_explicit_support_array_but_policy4_schema_is_exact():
    inputs, output = payload(5, ['/segments/0/meaning_en'])
    with pytest.raises(ValidationError):
        _validate_output(output, inputs)
    inputs, output = payload(4, ['/segments/0/meaning_en'], [])
    with pytest.raises(ValidationError):
        _validate_output(output, inputs)


def test_policy5_confined_workspace_receipt_replays_exact_support_scope(tmp_path):
    import json
    from pipeline.annotation_adjudication import _output_schema, _worker_prompt, verify_adjudication_evidence
    from pipeline.worker_workspace import build, submit

    inputs, output = payload(5, ['/segments/0/meaning_en'], ['/segments/1/meaning_en'])
    run_dir = tmp_path / 'run'
    job = f"annotation-adjudication-{inputs['input_digest']}"
    job_dir = run_dir / 'agents' / job
    job_dir.mkdir(parents=True)
    workspace_context = {'annotation_adjudication': inputs,
        'annotation_adjudication_validation': {'input_field': 'annotation_adjudication'}}
    _, workspace_digest = build(job_dir / 'workspace', _worker_prompt(inputs),
                                _output_schema(5), context=workspace_context)
    candidate_file = job_dir / 'workspace' / 'candidate.json'
    candidate_file.write_text(json.dumps(output), encoding='utf-8')
    submitted, artifact = submit(job_dir / 'workspace', {'candidate_path': 'candidate.json'})
    verified = _validate_output(submitted, inputs)
    verified.update({'job': job, 'effort': 'low', 'model': 'gpt-6-luna', 'tool_profile': 'research',
        'worker_tool_profile': 'workspace', 'normal_review_receipt': inputs['normal_review_receipt'],
        'normal_review_receipt_digest': inputs['normal_review_receipt_digest'],
        'verified_result_digest': digest(verified)})
    for name, value in [('adjudication-input.json', inputs), ('result.json', submitted),
                        ('verified-adjudication.json', verified), ('meta.json', {
        'return_code': 0, 'model': 'gpt-6-luna', 'effort': 'low', 'tool_profile': 'workspace',
        'workspace_digest': workspace_digest, 'artifact_path': artifact['artifact_path'],
        'artifact_digest': artifact['artifact_digest']})]:
        (job_dir / name).write_text(json.dumps(value), encoding='utf-8')
    replay_args = {key: inputs[key] for key in (
        'language', 'representation', 'candidate', 'current_review', 'prior_history', 'context',
        'known_reference_input', 'deterministic_gate_evidence', 'normal_review_receipt', 'source_text')}
    assert verify_adjudication_evidence(run_dir, verified, **replay_args) == verified
    assert verified['new_issues'][0]['supporting_paths'] == ['/segments/1/meaning_en']
    assert verified['repair_diagnoses'][-1]['paths'] == ['/segments/0/meaning_en']

    tampered_evidence = copy.deepcopy(verified)
    tampered_evidence['new_issues'][0]['supporting_paths'] = []
    with pytest.raises(AdjudicationError, match='replay exactly'):
        verify_adjudication_evidence(run_dir, tampered_evidence, **replay_args)
    altered_result = copy.deepcopy(submitted)
    altered_result['new_issues'][0]['supporting_paths'] = []
    (job_dir / 'result.json').write_text(json.dumps(altered_result), encoding='utf-8')
    with pytest.raises(ValueError):
        verify_adjudication_evidence(run_dir, verified, **replay_args)


def test_policy5_prompt_explicit_targets_override_legacy_index_guidance():
    from pipeline.annotation_adjudication import INSTRUCTIONS, _versioned_instructions, _worker_prompt
    for version in (1, 2, 3, 4):
        assert _versioned_instructions(version) == INSTRUCTIONS
    inputs, _ = payload(5, ['/segments/0/meaning_en'], ['/segments/1/meaning_en'])
    prompt = _worker_prompt(inputs)
    assert 'candidate_paths is the complete defect target set' in prompt
    assert 'supporting_paths and other prose mentions are context only' in prompt
    assert 'takes precedence over legacy prose-index binding guidance above' in prompt
    assert inputs['instructions_digest'] == digest(_versioned_instructions(5))


@pytest.mark.parametrize('version', [1, 2, 3, 4, 5])
def test_policy5_new_issue_research_fact_scope_guard_preserves_history(version):
    inputs, output = payload(version, ['/segments/0/meaning_en'], [] if version == 5 else None)
    # Leave the current classification's general lesson intact, so only the
    # separately discovered finding exercises the research scope contract.
    inputs['known_reference_input']['noun-fact'] = {'kind': 'approved_lesson', 'content': {
        '_annotation_research_fact': True, 'issue_ids': ['existing-noun-finding'],
        'meaning': 'A fact approved only for the existing noun finding.'}}
    output['new_issues'][0]['evidence_refs'] = [{'reference_id': 'noun-fact', 'path': '/meaning'}]
    if version == 5:
        with pytest.raises(AdjudicationError, match='new-issue scope'):
            _validate_output(output, inputs)
        inputs['known_reference_input']['noun-fact']['content']['issue_ids'] = ['new']
        assert _validate_output(output, inputs)['status'] == 'actionable'
        output['new_issues'][0]['evidence_refs'] = [{'reference_id': 'lesson', 'path': '/meaning'}]
        assert _validate_output(output, inputs)['status'] == 'actionable'
    else:
        assert _validate_output(output, inputs)['status'] == 'actionable'
