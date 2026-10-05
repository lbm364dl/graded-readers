"""Synthetic replay contracts, not worker receipts or linguistic approvals."""
import copy
import json
import pytest
from pipeline.annotation_repairs import digest, replay_annotation_repair
from pipeline.annotation_edits import candidate_digest
from pipeline.annotation_repair_authority import build_authority_packet


def retained_fixture(root, version=2):
    base = {'segments': [{'text': '她', 'type': 'word', 'pinyin': 'tā',
                          'meaning_en': 'she'}], 'grammar_overlays': []}
    issues = [{'problem': 'fixture gloss', 'candidate_paths': ['/segments/0/meaning_en'],
               'supporting_paths': []}]
    target = {'op': 'set_field', 'path': '/segments/0/meaning_en'}
    plan = {'issues': [{'issue_index': 0, 'reason': 'fixture', 'targets': [target],
                       'boundary_change_needed': False, 'boundary_reason': ''}]}
    patch = {'base_digest': candidate_digest(base), 'edits': [{**target, 'value': 'she (fixture)'}]}
    derived = copy.deepcopy(base)
    derived['segments'][0]['meaning_en'] = 'she (fixture)'
    meta = {'return_code': 0, 'kind': 'annotation_patch_assembly', 'status': 'applied',
            'representation': 'chinese-annotation', 'base': base,
            'base_digest': candidate_digest(base), 'issues': issues,
            'issue_digest': digest(issues), 'plan_job': 'fixture_plan',
            'plan_digest': digest(plan), 'patch_job': 'fixture_patch',
            'patch_digest': digest(patch), 'result_digest': candidate_digest(derived),
            'repair_request_policy_version': 3, 'plan_target_contract_version': version,
            'target_contract_version': 1, 'complete_stage_instruction_policy_version': 2}
    if version == 3:
        meta['repair_target_authority'] = build_authority_packet(
            issues, base, 'chinese-annotation', candidate_digest(base))
    for job, value, metadata in [('fixture_plan', plan, {'return_code': 0}),
                                  ('fixture_patch', patch, {'return_code': 0}),
                                  ('fixture_assembly', derived, meta)]:
        folder = root / 'agents' / job
        folder.mkdir(parents=True)
        (folder / 'result.json').write_text(json.dumps(value))
        (folder / 'meta.json').write_text(json.dumps(metadata))
    return derived


def test_historical_request3_plan2_without_workspace_keeps_replay_contract(tmp_path):
    derived = retained_fixture(tmp_path)
    result = replay_annotation_repair(tmp_path, 'fixture_assembly', validate_candidate=lambda _: None)
    assert result['status'] == 'applied' and result['candidate'] == derived


def test_new_target3_without_workspace_remains_rejected(tmp_path):
    retained_fixture(tmp_path, version=3)
    with pytest.raises(ValueError, match='missing authenticated workspace request'):
        replay_annotation_repair(tmp_path, 'fixture_assembly', validate_candidate=lambda _: None)


def test_partial_historical_workspace_is_not_ignored(tmp_path):
    retained_fixture(tmp_path)
    folder = tmp_path / 'agents/fixture_plan/workspace'
    folder.mkdir()
    (folder / 'INDEX.json').write_text('[]')
    with pytest.raises(ValueError, match='missing authenticated workspace request'):
        replay_annotation_repair(tmp_path, 'fixture_assembly', validate_candidate=lambda _: None)


def test_downgraded_plan_with_retained_v3_authority_is_rejected(tmp_path):
    retained_fixture(tmp_path, version=3)
    path = tmp_path / 'agents/fixture_assembly/meta.json'
    meta = json.loads(path.read_text())
    meta['plan_target_contract_version'] = 2
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='authority cannot downgrade'):
        replay_annotation_repair(tmp_path, 'fixture_assembly', validate_candidate=lambda _: None)


def test_deleted_bound_workspace_and_downgraded_assembly_remain_rejected(tmp_path):
    import shutil
    from pipeline.annotation_repairs import PLAN_SCHEMA
    from pipeline.worker_workspace import build, check
    from pipeline.annotation_repair_dependencies import repair_dependency_constraints
    retained_fixture(tmp_path, version=3)
    assembly = tmp_path / 'agents/fixture_assembly/meta.json'
    meta = json.loads(assembly.read_text())
    child = tmp_path / 'agents/fixture_plan'
    workspace = child / 'workspace'
    _, bound_digest = build(workspace, 'Synthetic binding fixture', PLAN_SCHEMA, context={
        'candidate': meta['base'], 'issues': meta['issues'],
        'representation': meta['representation'],
        'repair_target_authority': meta['repair_target_authority'],
        'repair_dependency_constraints': repair_dependency_constraints(meta['base'], meta['representation']),
        'annotation_plan_validation': {'issue_count': 1, 'target_contract_version': 3}})
    artifact = workspace / 'candidate.json'
    artifact.write_text((child / 'result.json').read_text())
    check(workspace, artifact)
    (child / 'meta.json').write_text(json.dumps({'return_code': 0,
                                               'workspace_digest': bound_digest}))
    assert replay_annotation_repair(tmp_path, 'fixture_assembly',
                                   validate_candidate=lambda _: None)['status'] == 'applied'
    shutil.rmtree(workspace)
    meta['plan_target_contract_version'] = 2
    meta.pop('repair_target_authority')
    assembly.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='missing authenticated workspace request'):
        replay_annotation_repair(tmp_path, 'fixture_assembly', validate_candidate=lambda _: None)
