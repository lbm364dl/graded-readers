import copy

import pytest
from jsonschema.exceptions import ValidationError

from pipeline.annotation_adjudication import (
    AdjudicationError, OUTPUT_SCHEMA, _build_inputs, _output_schema, _validate_output,
    _versioned_instructions, digest,
)

PATHS = ['/segments/0/meaning_en', '/segments/0/form_steps/0/meaning_en', '/grammar_links/0/context_en']


def scoped_case(dispositions):
    candidate = {'segments': [{'text': '하고', 'meaning_en': 'and',
                               'form_steps': [{'meaning_en': 'make/let, connecting onward'}]}],
                 'grammar_links': [{'context_en': 'Causative predicate followed by the connector.',
                                    'entry_id': 'causative', 'segment_index': 0}], 'inflected_segment_indices': [0]}
    issue = {'candidate_paths': PATHS, 'supporting_paths': ['/grammar_links/0/entry_id'],
             'explanation': 'Occurrence and stage and grammatical context claim.'}
    review = {'verdict': 'revise', 'issues': [issue]}
    inputs = _build_inputs(language='ko', representation='korean-flat', candidate=candidate,
        current_review=review, prior_history=[], context={}, source_text='하고',
        known_reference_input={'lesson': {'kind': 'approved_lesson', 'content': {'meaning': 'make/let, connecting onward'}}},
        deterministic_gate_evidence={'passed': True, 'issues': [], 'candidate_digest': digest(candidate), 'source_text_digest': digest('하고')},
        normal_review_receipt={'review_digest': digest(review)}, host_binding_policy_version=6)
    refs = [{'reference_id': 'lesson', 'path': '/meaning'}]
    output = {'classifications': [{'issue_id': inputs['normalized_review']['issues'][0]['issue_id'],
        'disposition': 'actionable', 'target_binding': 'exact', 'candidate_paths': PATHS,
        'evidence_refs': refs, 'reason': 'Evaluate each exact field separately.', 'diagnosis': 'Summary only.',
        'target_dispositions': [{'path': path, 'disposition': disposition, 'target_binding': 'exact',
            'evidence_refs': refs if disposition != 'uncertain' else [],
            'reason': 'This exact value is contradicted.' if disposition == 'actionable' else 'This exact value is supported or unresolved.',
            'diagnosis': 'Retain the complete causative predicate meaning.' if disposition == 'actionable' else ''}
            for path, disposition in zip(PATHS, dispositions)]}], 'new_issues': [], 'prose_revision_reason': ''}
    return inputs, output


@pytest.mark.parametrize('dispositions,status,repair_indices', [
    (['actionable', 'unsupported', 'unsupported'], 'actionable', [0]),
    (['actionable'] * 3, 'actionable', [0, 1, 2]),
    (['unsupported'] * 3, 'cleared', []),
    (['actionable', 'uncertain', 'unsupported'], 'uncertain', [0]),
])
def test_per_target_aggregation_preserves_supported_neighbors(dispositions, status, repair_indices):
    inputs, output = scoped_case(dispositions)
    before = copy.deepcopy(inputs)
    verified = _validate_output(output, inputs)
    assert verified['status'] == status
    assert verified['approved'] == (status == 'cleared')
    row = verified['classifications'][0]
    assert [target['disposition'] for target in row['target_dispositions']] == dispositions
    assert len(row['candidate_observations']) == 3
    for target in row['target_dispositions']:
        assert target['candidate_observations'][0]['path'] == target['path']
    assert [path for diagnosis in verified['repair_diagnoses'] for path in diagnosis['paths']] == [PATHS[index] for index in repair_indices]
    assert inputs == before


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'supporting', 'missing_list'])
def test_per_target_coverage_cannot_drop_or_add_defect_authority(mutation):
    inputs, output = scoped_case(['actionable', 'unsupported', 'unsupported'])
    targets = output['classifications'][0]['target_dispositions']
    if mutation == 'missing':
        targets.pop()
    elif mutation == 'duplicate':
        targets.append(copy.deepcopy(targets[0]))
    elif mutation == 'supporting':
        targets[-1]['path'] = '/grammar_links/0/entry_id'
    else:
        output['classifications'][0].pop('target_dispositions')
    with pytest.raises((AdjudicationError, ValidationError)):
        _validate_output(output, inputs)


@pytest.mark.parametrize('index', [0, 1, 2])
def test_each_decisive_target_requires_its_own_linguistic_evidence(index):
    inputs, output = scoped_case(['actionable', 'unsupported', 'unsupported'])
    output['classifications'][0]['target_dispositions'][index]['evidence_refs'] = []
    verified = _validate_output(output, inputs)
    assert verified['status'] == 'uncertain'
    assert verified['classifications'][0]['target_dispositions'][index]['disposition'] == 'uncertain'
    assert PATHS[index] not in [path for row in verified['repair_diagnoses'] for path in row['paths']]


def test_invalid_original_target_set_cannot_be_rescued_by_per_target_votes():
    inputs, output = scoped_case(['unsupported'] * 3)
    output['classifications'][0]['candidate_paths'] = PATHS[:1]
    output['classifications'][0]['target_dispositions'] = output['classifications'][0]['target_dispositions'][:1]
    assert _validate_output(output, inputs)['status'] == 'uncertain'


def test_version6_contract_does_not_change_historical_schema_or_prompt():
    for version in range(1, 5):
        assert _output_schema(version) == OUTPUT_SCHEMA
    assert 'target_dispositions' not in _output_schema(5)['properties']['classifications']['items']['properties']
    assert 'target_dispositions' not in _versioned_instructions(5)
    assert 'target_dispositions' in _output_schema(6)['properties']['classifications']['items']['required']
    assert 'only actionable targets' in _versioned_instructions(6)


@pytest.mark.asyncio
async def test_mixed_target_diagnosis_routes_only_bad_field_through_shared_repair(tmp_path):
    import json
    from types import SimpleNamespace
    from pipeline.annotation_edits import candidate_digest
    from pipeline.annotation_repairs import repair_annotation
    inputs, output = scoped_case(['actionable', 'unsupported', 'unsupported'])
    diagnoses = _validate_output(output, inputs)['repair_diagnoses']
    original = copy.deepcopy(inputs['candidate'])

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            context = kwargs['workspace_context']
            assert context['issues'] == diagnoses
            assert context['issues'][0]['paths'] == PATHS[:1]
            assert len(context['issues'][0]['target_dispositions']) == 1
            value = ({'issues': [{'issue_index': 0, 'reason': 'Correct only the occurrence gloss.',
                'targets': [{'op': 'set_field', 'path': PATHS[0]}],
                'boundary_change_needed': False, 'boundary_reason': ''}]} if job.endswith('_plan') else
                {'base_digest': candidate_digest(original), 'edits': [
                    {'op': 'set_field', 'path': PATHS[0], 'value': 'make/let, connecting onward'}]})
            directory = tmp_path / 'agents' / job
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'result.json').write_text(json.dumps(value))
            (directory / 'meta.json').write_text(json.dumps({'return_code': 0}))
            return value

    def unchanged_supported_fields(candidate):
        assert candidate['segments'][0]['text'] == original['segments'][0]['text']
        assert candidate['segments'][0]['form_steps'] == original['segments'][0]['form_steps']
        assert candidate['grammar_links'] == original['grammar_links']

    harness = SimpleNamespace(run_dir=tmp_path, runner=Runner(), args=SimpleNamespace())
    result = await repair_annotation(harness, 'mixed-target', original, diagnoses,
        language='ko', representation='korean-flat',
        context={'candidate_gate': {'focus': {}, 'title': 'Test', 'number': 1, 'plan': {}, 'level': 4, 'source_id': 'test'}},
        validate_candidate=unchanged_supported_fields)
    assert result['status'] == 'applied'
    unchanged_supported_fields(result['candidate'])
    assert result['candidate']['segments'][0]['meaning_en'] == 'make/let, connecting onward'
    assert inputs['candidate'] == original


def test_policy6_receipt_replays_exact_per_target_evidence_without_model(tmp_path):
    import json
    from pipeline.annotation_adjudication import _verify_adjudication_once
    from pipeline.worker_workspace import build, submit
    inputs, output = scoped_case(['actionable', 'unsupported', 'unsupported'])
    job = 'annotation-adjudication-' + inputs['input_digest']
    directory = tmp_path / 'agents' / job
    context = {'annotation_adjudication': inputs,
               'annotation_adjudication_validation': {'input_field': 'annotation_adjudication'}}
    _, workspace_digest = build(directory / 'workspace', 'Evaluate each target', _output_schema(6), context=context)
    (directory / 'workspace/candidate.json').write_text(json.dumps(output))
    submitted, artifact = submit(directory / 'workspace', {'candidate_path': 'candidate.json'})
    core = _validate_output(submitted, inputs)
    evidence = {**core, 'job': job, 'effort': 'low', 'model': 'gpt-6-luna',
                'tool_profile': 'research', 'worker_tool_profile': 'workspace',
                'normal_review_receipt': inputs['normal_review_receipt'],
                'normal_review_receipt_digest': inputs['normal_review_receipt_digest'],
                'verified_result_digest': digest(core)}
    meta = {'return_code': 0, 'model': 'gpt-6-luna', 'effort': 'low',
            'tool_profile': 'workspace', 'workspace_digest': workspace_digest,
            'artifact_path': artifact['artifact_path'], 'artifact_digest': artifact['artifact_digest']}
    for name, value in [('adjudication-input.json', inputs), ('result.json', submitted),
                        ('verified-adjudication.json', evidence), ('meta.json', meta)]:
        (directory / name).write_text(json.dumps(value))
    kwargs = {key: inputs[key] for key in ('language', 'representation', 'candidate', 'current_review',
        'prior_history', 'context', 'known_reference_input', 'deterministic_gate_evidence',
        'normal_review_receipt', 'source_text')}
    assert _verify_adjudication_once(tmp_path, evidence, **kwargs) == evidence
    altered = copy.deepcopy(evidence)
    altered['classifications'][0]['target_dispositions'][1]['disposition'] = 'actionable'
    with pytest.raises(AdjudicationError, match='replay exactly'):
        _verify_adjudication_once(tmp_path, altered, **kwargs)
