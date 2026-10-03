import json
import pytest
from pipeline.worker_workspace import build, verify, check


def test_workspace_keeps_distinct_complete_references_and_inputs(tmp_path):
    refs = {'format': 'lossless_reference_rows', 'columns': ['id', 'headword', 'meaning'],
            'rows': [['a/1', '같다', 'first sense'], ['a/2', '같다', 'distinct sense']]}
    prompt = 'Task\nINPUT:\n' + json.dumps({'words': refs, 'chunk_text': '글. '}) + '\nSecond task\nINPUT:\n' + json.dumps({'issues': ['repair']})
    message, signature = build(tmp_path, prompt, {'type': 'object'})
    assert len(message) < 400
    assert 'Second task' in (tmp_path/'TASK.txt').read_text()
    assert 'Tools are enabled' in (tmp_path/'TASK.txt').read_text()
    index = json.loads((tmp_path/'INDEX.json').read_text())
    word_index = json.loads((tmp_path/index[0]['path']).read_text())
    assert [json.loads((tmp_path/r['path']).read_text())['meaning'] for r in word_index] == ['first sense', 'distinct sense']
    verify(tmp_path, signature)
    (tmp_path/'candidate.json').write_text('{}')
    verify(tmp_path, signature)  # Scratch/draft output is allowed.
    check(tmp_path, tmp_path/'candidate.json')
    (tmp_path/word_index[0]['path']).write_text('{}')
    with pytest.raises(ValueError, match='input changed'):
        verify(tmp_path, signature)


def test_workspace_rejects_lossy_reference_rows(tmp_path):
    prompt = '\nINPUT:\n' + json.dumps({'words': {'format':'lossless_reference_rows', 'columns':['id','meaning'], 'rows':[['a']]}})
    with pytest.raises(ValueError, match='Malformed'):
        build(tmp_path, prompt, {})


def test_workspace_deduplicates_identical_input_blocks_but_keeps_distinct_blocks(tmp_path):
    repeated = {'issues': ['first', 'second'], 'candidate': {'segments': []}}
    different = {'issues': ['first', 'changed'], 'candidate': {'segments': []}}
    prompt = ('Plan the repairs.\nINPUT:\n' + json.dumps(repeated, ensure_ascii=False)
              + '\nKeep this intervening direction.\nINPUT:\n'
              + json.dumps(different, ensure_ascii=False)
              + '\nKeep this trailing direction.\nINPUT:\n'
              + json.dumps(repeated, ensure_ascii=False))
    _, signature = build(tmp_path, prompt, {'type': 'object'}, context=repeated)

    inventory = json.loads((tmp_path / 'INDEX.json').read_text())
    # The repeated first/third prompt blocks and appended workspace context are
    # one input; the distinct second block stays independently represented.
    assert len(inventory) == 4  # issues + candidate for each of two distinct blocks
    assert {entry['input_block'] for entry in inventory} == {1, 2}
    task = (tmp_path / 'TASK.txt').read_text()
    assert 'Keep this intervening direction.' in task
    assert 'Keep this trailing direction.' in task
    verify(tmp_path, signature)


def _reference_block(identity, surface):
    return {'chunk_text': surface, 'references': {
        'format': 'lossless_reference_rows', 'columns': ['id', 'headword'],
        'rows': [[identity, surface]]}}


def test_rebuilding_workspace_removes_only_obsolete_unchanged_managed_inputs(tmp_path):
    first, second = _reference_block('first/id', '첫'), _reference_block('second/id', '둘')
    prompt = ('Repair.\nINPUT:\n' + json.dumps(first, ensure_ascii=False)
              + '\nNext chunk.\nINPUT:\n' + json.dumps(second, ensure_ascii=False))
    build(tmp_path, prompt, {'type': 'object'})
    old = json.loads((tmp_path / 'manifest.json').read_text())['files']
    obsolete = [name for name in old if name.startswith(('inputs/02-', 'references/02-'))]
    assert obsolete and all((tmp_path / name).exists() for name in obsolete)

    scratch = tmp_path / 'inputs' / 'manual-notes.json'
    scratch.write_text('{"keep":true}', encoding='utf-8')
    candidate = tmp_path / 'candidate.json'
    candidate.write_bytes(b'{"candidate":true}')
    rejected = tmp_path / 'rejected-candidate-attempt-01.json'
    rejected.write_bytes(b'{invalid}')
    one_block_prompt = 'Repair.\nINPUT:\n' + json.dumps(first, ensure_ascii=False)
    _, signature = build(tmp_path, one_block_prompt, {'type': 'object'}, context=first)

    assert all(not (tmp_path / name).exists() for name in obsolete)
    assert scratch.read_text(encoding='utf-8') == '{"keep":true}'
    assert candidate.read_bytes() == b'{"candidate":true}'
    assert rejected.read_bytes() == b'{invalid}'
    assert {entry['input_block'] for entry in json.loads((tmp_path / 'INDEX.json').read_text())} == {1}
    verify(tmp_path, signature)


def test_rebuilding_workspace_preserves_changed_and_unsafe_old_manifest_targets(tmp_path):
    from pipeline.worker_workspace import hash_bytes

    first, second = _reference_block('first/id', '첫'), _reference_block('second/id', '둘')
    prompt = ('Repair.\nINPUT:\n' + json.dumps(first, ensure_ascii=False)
              + '\nNext chunk.\nINPUT:\n' + json.dumps(second, ensure_ascii=False))
    build(tmp_path, prompt, {'type': 'object'})
    manifest_path = tmp_path / 'manifest.json'
    old = json.loads(manifest_path.read_text())
    changed = next(name for name in old['files'] if name.startswith('inputs/02-'))
    changed_path = tmp_path / changed
    changed_path.write_text('user edited this managed input', encoding='utf-8')

    outside = tmp_path.parent / (tmp_path.name + '-outside.json')
    outside.write_text('preserve outside file', encoding='utf-8')
    unsafe_link = tmp_path / 'inputs' / 'unsafe-link.json'
    unsafe_link.symlink_to(outside)
    old['files']['inputs/../../' + outside.name] = hash_bytes(outside.read_bytes())
    old['files']['inputs/unsafe-link.json'] = hash_bytes(outside.read_bytes())
    manifest_path.write_text(json.dumps(old), encoding='utf-8')

    build(tmp_path, 'Repair.\nINPUT:\n' + json.dumps(first, ensure_ascii=False), {'type': 'object'})

    assert changed_path.read_text(encoding='utf-8') == 'user edited this managed input'
    assert unsafe_link.is_symlink()
    assert outside.read_text(encoding='utf-8') == 'preserve outside file'


def test_plain_task_and_schema_validation(tmp_path):
    _, signature = build(tmp_path, 'Inspect the supplied repository.', {'type':'object', 'required':['ok']})
    verify(tmp_path, signature)
    (tmp_path/'candidate.json').write_text('{}')
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        check(tmp_path, tmp_path/'candidate.json')


@pytest.mark.parametrize('language,surface_key', [('zh', 'text'), ('ja', 'surface')])
def test_language_specific_annotation_checks_use_authoritative_source(tmp_path, language, surface_key):
    from pipeline.agent_harness import ChapterHarness
    from pipeline.japanese_agent_harness import JapaneseChapterHarness
    # A punctuation-only fragment exercises lossless reconstruction in both
    # real validators without introducing dictionary assumptions.
    segment = {surface_key: '。', 'type': 'punctuation', 'meaning_en': ''}
    if language == 'zh':
        segment['pinyin'] = ''
    else:
        segment.update(lemma='', surface_kana='', lemma_kana='', part_of_speech='',
                       conjugation_form='', story_role='none', story_importance_en='')
    value = {'segments': [segment], 'grammar_overlays': []}
    build(tmp_path, 'Annotate the provided source.', {'type':'object'}, context={'chunk_text':'。', 'language':language})
    candidate = tmp_path/'candidate.json'
    candidate.write_text(json.dumps(value))
    assert check(tmp_path, candidate) == value
    value['segments'][0][surface_key] = '、'
    candidate.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='reconstruction'):
        check(tmp_path, candidate)


def test_submission_uses_exact_validated_file_and_detects_later_changes(tmp_path):
    from pipeline.worker_workspace import submit
    from pipeline.agent_harness import CodexRunner
    workspace = tmp_path/'workspace'
    _, signature = build(workspace, 'Return a value.', {'type':'object', 'required':['ok']})
    candidate = workspace/'candidate.json'
    candidate.write_text('{"ok":true}')
    value, artifact = submit(workspace, {'candidate_path':'candidate.json'})
    assert value == {'ok':True}
    (tmp_path/'result.json').write_text(json.dumps(value))
    meta = {'tool_profile':'workspace', 'workspace_digest':signature, **artifact}
    CodexRunner._check_tool_profile(tmp_path, 'offline', meta)
    candidate.write_text('{"ok":false}')
    with pytest.raises(ValueError, match='artifact changed'):
        CodexRunner._check_tool_profile(tmp_path, 'offline', meta)
    with pytest.raises(ValueError, match='must be in'):
        submit(workspace, {'candidate_path':'../result.json'})
    with pytest.raises(ValueError, match='does not exist'):
        submit(workspace, {'candidate_path':'missing.json'})


def test_annotation_patch_submission_applies_and_validates_derived_candidate(tmp_path, monkeypatch):
    from pipeline.annotation_edits import candidate_digest
    from pipeline.annotation_repairs import PATCH_SCHEMA
    from pipeline.worker_workspace import CandidateSubmissionError, submit

    base = {'segments': [
        {'text': '她', 'type': 'word', 'pinyin': 'tā', 'meaning_en': 'she'},
        {'text': '走', 'type': 'word', 'pinyin': 'zǒu', 'meaning_en': 'walk'},
    ], 'grammar_overlays': []}
    allowed = [{'op': 'set_field', 'path': '/segments/1/meaning_en'},
               {'op': 'set_field', 'path': '/segments/1/text'}]
    patch_context = {'chunk_text': '她走', 'language': 'zh', 'annotation_patch_validation': {
        'base_candidate': base, 'representation': 'chinese-annotation',
        'allowed_targets': allowed, 'language': 'zh', 'issues': [], 'chunk_text': '她走'}}
    workspace = tmp_path / 'patch-workspace'
    build(workspace, 'Apply this semantic patch.', PATCH_SCHEMA, context=patch_context)
    validation_context = json.loads((workspace / 'validation-context.json').read_text())
    assert validation_context[0]['annotation_patch_validation']['base_candidate'] == base

    patch = {'base_digest': candidate_digest(base), 'edits': [
        {'op': 'set_field', 'path': '/segments/1/meaning_en', 'value': 'go'}]}
    (workspace / 'candidate.json').write_text(json.dumps(patch), encoding='utf-8')
    returned, _ = submit(workspace, {'candidate_path': 'candidate.json'})
    assert returned == patch
    assert 'approved' not in returned  # Local derived validation does not replace independent review.

    boundary_patch = {'base_digest': candidate_digest(base), 'edits': [
        {'op': 'set_field', 'path': '/segments/1/text', 'value': '行'}]}
    (workspace / 'candidate.json').write_text(json.dumps(boundary_patch), encoding='utf-8')
    with pytest.raises(CandidateSubmissionError, match='protected source surface') as caught:
        submit(workspace, {'candidate_path': 'candidate.json'})
    assert caught.value.category == 'annotation_patch_contract_rejection'

    # A valid in-scope edit that fails only the downstream language gate stays
    # eligible for the single semantic replan path.
    from pipeline.agent_harness import ChapterHarness
    monkeypatch.setattr(ChapterHarness, 'annotation_contract_issues',
        staticmethod(lambda _text, _candidate: ['dependent form row is missing']))
    semantic_patch = {'base_digest': candidate_digest(base), 'edits': [
        {'op': 'set_field', 'path': '/segments/1/meaning_en', 'value': 'go'}]}
    (workspace / 'candidate.json').write_text(json.dumps(semantic_patch), encoding='utf-8')
    with pytest.raises(CandidateSubmissionError, match='dependent form row is missing') as caught:
        submit(workspace, {'candidate_path': 'candidate.json'})
    assert caught.value.category == 'derived_annotation_rejection'


def test_patch_submission_rejects_omitted_planned_issue_target(tmp_path, monkeypatch):
    from pipeline.annotation_edits import candidate_digest
    from pipeline.annotation_repairs import PATCH_SCHEMA
    from pipeline.worker_workspace import CandidateSubmissionError, submit
    from pipeline.agent_harness import ChapterHarness

    monkeypatch.setattr(ChapterHarness, 'annotation_contract_issues', staticmethod(lambda _text, _value: []))
    base = {'segments': [
        {'text': '她', 'type': 'word', 'pinyin': 'tā', 'meaning_en': 'old'},
        {'text': '走', 'type': 'word', 'pinyin': 'zǒu', 'meaning_en': 'walk'},
    ], 'grammar_overlays': []}
    issue_targets = [
        {'op': 'set_field', 'path': '/segments/0/meaning_en'},
        {'op': 'set_field', 'path': '/segments/0/pinyin'},
    ]
    plan = {'issues': [
        {'issue_index': 0, 'targets': [issue_targets[0]], 'boundary_change_needed': False},
        {'issue_index': 1, 'targets': [issue_targets[1]], 'boundary_change_needed': False},
    ]}
    patch_context = {'chunk_text': '她走', 'language': 'zh', 'annotation_patch_validation': {
        'base_candidate': base, 'representation': 'chinese-annotation',
        'allowed_targets': issue_targets, 'language': 'zh', 'chunk_text': '她走',
        'repair_plan': plan, 'target_contract_version': 1}}
    workspace = tmp_path / 'coverage-workspace'
    build(workspace, 'Apply each scoped issue.', PATCH_SCHEMA, context=patch_context)
    candidate = workspace / 'candidate.json'
    incomplete = {'base_digest': candidate_digest(base), 'edits': [
        {'op': 'set_field', 'path': issue_targets[0]['path'], 'value': 'she'}]}
    candidate.write_text(json.dumps(incomplete), encoding='utf-8')
    with pytest.raises(CandidateSubmissionError, match='planned target had no effective patch change') as caught:
        submit(workspace, {'candidate_path': 'candidate.json'})
    assert caught.value.category == 'annotation_patch_contract_rejection'

    complete = {'base_digest': candidate_digest(base), 'edits': [
        {'op': 'set_field', 'path': issue_targets[0]['path'], 'value': 'she'},
        {'op': 'set_field', 'path': issue_targets[1]['path'], 'value': 'tā (she)'},]}
    candidate.write_text(json.dumps(complete), encoding='utf-8')
    returned, _ = submit(workspace, {'candidate_path': 'candidate.json'})
    assert returned == complete


def test_plan_operation_mismatch_is_rejected_before_patch_and_corrected_by_resubmission(tmp_path):
    from pipeline.annotation_repairs import PLAN_SCHEMA
    from pipeline.worker_workspace import CandidateSubmissionError, submit

    base = {'segments': [{'text': '은혜에', 'form_steps': [
        {'form': '은혜에', 'grammar_entry_ids': ['location-e']}]}],
        'grammar_links': [], 'expression_links': [], 'inflected_segment_indices': [0]}
    context = {'candidate': base, 'representation': 'korean-flat',
        'annotation_plan_validation': {'issue_count': 1, 'target_contract_version': 1}}
    workspace = tmp_path / 'plan-workspace'
    build(workspace, 'Plan a scoped correction.', PLAN_SCHEMA, context=context)
    target_path = '/segments/0/form_steps/0/grammar_entry_ids'
    invalid = {'issues': [{'issue_index': 0, 'reason': 'Remove the unsupported identity.',
        'targets': [{'op': 'set_field', 'path': target_path}],
        'boundary_change_needed': False, 'boundary_reason': ''}]}
    candidate_path = workspace / 'candidate.json'
    candidate_path.write_text(json.dumps(invalid), encoding='utf-8')
    with pytest.raises(CandidateSubmissionError, match='use replace_list') as rejected:
        submit(workspace, {'candidate_path': 'candidate.json'})
    assert rejected.value.category == 'plan_contract_rejection'

    corrected = {'issues': [{'issue_index': 0, 'reason': 'Replace the grammar identity array.',
        'targets': [{'op': 'replace_list', 'path': target_path}],
        'boundary_change_needed': False, 'boundary_reason': ''}]}
    candidate_path.write_text(json.dumps(corrected), encoding='utf-8')
    returned, _ = submit(workspace, {'candidate_path': 'candidate.json'})
    assert returned == corrected


def _word_schema():
    return {'type': 'object', 'required': ['segments'], 'properties': {'segments': {
        'type': 'array', 'items': {'anyOf': [
            {'type': 'object', 'properties': {
                'type': {'enum': ['word']}, 'lemma': {'type': 'string'},
                'is_inflected': {'type': 'boolean'}, 'expression_links': {'type': 'array'}},
             'required': ['type', 'lemma', 'is_inflected', 'expression_links']},
            {'type': 'object', 'properties': {
                'type': {'enum': ['punctuation']}, 'lemma': {'enum': ['']}},
             'required': ['type', 'lemma']},
        ]}}}}


def _word_candidate(*, complete):
    segment = {'type': 'word', 'lemma': '안팎'}
    if complete:
        segment.update(is_inflected=False, expression_links=[])
    return {'segments': [segment]}


def _fake_workspace_codex(monkeypatch, outcomes):
    import asyncio
    from pathlib import Path

    launches = []

    class FakeStdin:
        def write(self, value):
            launches[-1]['prompt'] = value.decode('utf-8')
        async def drain(self): pass
        def close(self): pass

    class FakeProcess:
        pid = 123456
        returncode = 0
        def __init__(self): self.stdin = FakeStdin()
        async def wait(self): return 0

    async def fake_exec(*command, **kwargs):
        workspace = Path(command[command.index('-C') + 1])
        attempt = len(launches)
        launches.append({'workspace': workspace})
        if outcomes[attempt] is not None:
            if isinstance(outcomes[attempt], bytes):
                (workspace / 'candidate.json').write_bytes(outcomes[attempt])
            else:
                (workspace / 'candidate.json').write_text(json.dumps(outcomes[attempt]), encoding='utf-8')
        Path(command[command.index('-o') + 1]).write_text(
            '{"candidate_path":"candidate.json"}', encoding='utf-8')
        return FakeProcess()

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    return launches


def _plan(*indexes):
    return {'issues': [{'issue_index': index, 'reason': f'Issue {index}',
        'targets': [{'op': 'set_field', 'path': f'/segments/{index}/meaning_en'}],
        'boundary_change_needed': False, 'boundary_reason': ''} for index in indexes]}


@pytest.mark.parametrize('invalid', [_plan(0, 0, 2), _plan(0, 2), _plan(1, 0, 2)])
@pytest.mark.asyncio
async def test_codex_runner_repairs_schema_valid_but_incomplete_annotation_plan_once(
    tmp_path, monkeypatch, invalid,
):
    import asyncio
    from pipeline.agent_harness import CodexRunner
    from pipeline.annotation_repairs import PLAN_SCHEMA

    corrected = _plan(0, 1, 2)
    shared_context = {'issues': [{'id': i} for i in range(3)],
        'candidate': {'segments': []}, 'chunk_text': 'abc',
        'annotation_plan_validation': {'issue_count': 3}}
    prompt = 'Plan one repair per issue.\nINPUT:\n' + json.dumps(shared_context)
    launches = _fake_workspace_codex(monkeypatch, [invalid, corrected])
    schema = tmp_path / 'plan-schema.json'
    schema.write_text(json.dumps(PLAN_SCHEMA), encoding='utf-8')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))

    assert await runner.call('annotation-plan', prompt, schema, 'low',
        workspace_context=shared_context) == corrected
    assert len(launches) == 2
    retry_workspace = launches[1]['workspace']
    rejection = json.loads((retry_workspace / 'submission-rejection.json').read_text())
    assert rejection['category'] == 'plan_contract_rejection'
    assert 'exactly once' in rejection['diagnostic']
    assert json.loads((retry_workspace / 'rejected-candidate-attempt-01.json').read_text()) == invalid
    validation = json.loads((retry_workspace / 'validation-context.json').read_text())
    assert len(validation) == 1
    assert validation[0]['annotation_plan_validation'] == {'issue_count': 3}


@pytest.mark.asyncio
async def test_correct_annotation_plan_passes_without_correction(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner
    from pipeline.annotation_repairs import PLAN_SCHEMA

    correct = _plan(0, 1)
    context = {'issues': [{'id': 0}, {'id': 1}],
        'annotation_plan_validation': {'issue_count': 2}}
    prompt = 'Plan one repair per issue.\nINPUT:\n' + json.dumps(context)
    launches = _fake_workspace_codex(monkeypatch, [correct])
    schema = tmp_path / 'plan-schema.json'
    schema.write_text(json.dumps(PLAN_SCHEMA), encoding='utf-8')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))

    assert await runner.call('valid-annotation-plan', prompt, schema, 'low',
        workspace_context=context) == correct
    assert len(launches) == 1


@pytest.mark.asyncio
async def test_codex_runner_repairs_one_schema_rejected_candidate_and_caches_final_artifact(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner

    invalid, corrected = _word_candidate(complete=False), _word_candidate(complete=True)
    launches = _fake_workspace_codex(monkeypatch, [invalid, corrected])
    schema = tmp_path / 'schema.json'
    schema.write_text(json.dumps(_word_schema()), encoding='utf-8')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))

    assert await runner.call('candidate', 'Annotate the source.', schema, 'low') == corrected
    assert len(launches) == 2
    retry_workspace = launches[1]['workspace']
    retry_task = (retry_workspace / 'TASK.txt').read_text(encoding='utf-8')
    retry_error = json.loads((retry_workspace / 'submission-rejection.json').read_text())
    assert "'is_inflected' is a required property" in retry_error['diagnostic']
    assert "'expression_links' is a required property" in retry_error['diagnostic']
    assert "lemma' is not one of ['']" not in retry_error['diagnostic']
    assert 'independent review and publication gates remain required' in retry_task
    assert json.loads((retry_workspace / 'rejected-candidate-attempt-01.json').read_text()) == invalid

    job = tmp_path / 'agents/candidate'
    assert json.loads((job / 'workspace/rejected-candidate-attempt-01.json').read_text()) == invalid
    meta = json.loads((job / 'meta.json').read_text())
    assert meta['return_code'] == 0 and meta['recovered_after_submission_rejection'] is True
    assert meta['submission_repair']['status'] == 'repaired'
    assert json.loads((job / 'result.json').read_text()) == corrected
    # The original fingerprint now replays the final exact artifact without another launch.
    assert await runner.call('candidate', 'Annotate the source.', schema, 'low') == corrected
    assert len(launches) == 2


@pytest.mark.asyncio
async def test_codex_runner_accepts_valid_first_candidate_without_repair(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner

    valid = _word_candidate(complete=True)
    launches = _fake_workspace_codex(monkeypatch, [valid])
    schema = tmp_path / 'schema.json'
    schema.write_text(json.dumps(_word_schema()), encoding='utf-8')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))

    assert await runner.call('valid', 'Annotate the source.', schema, 'low') == valid
    assert len(launches) == 1
    meta = json.loads((tmp_path / 'agents/valid/meta.json').read_text())
    assert meta['return_code'] == 0
    assert 'submission_repair' not in meta


@pytest.mark.asyncio
async def test_codex_runner_repairs_missing_candidate_file_once(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner

    valid = _word_candidate(complete=True)
    launches = _fake_workspace_codex(monkeypatch, [None, valid])
    schema = tmp_path / 'schema.json'
    schema.write_text(json.dumps(_word_schema()), encoding='utf-8')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))

    assert await runner.call('missing-file', 'Annotate the source.', schema, 'low') == valid
    assert len(launches) == 2
    retry = launches[1]['workspace']
    record = json.loads((retry / 'submission-rejection.json').read_text())
    assert record['category'] == 'missing_candidate'
    assert record['artifact_path'] == 'candidate.json'
    assert 'does not exist' in record['diagnostic']
    assert not (retry / 'rejected-candidate-attempt-01.json').exists()


@pytest.mark.asyncio
async def test_codex_runner_preserves_malformed_candidate_bytes_for_one_repair(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner

    malformed = b'{"segments": [\xff}'
    valid = _word_candidate(complete=True)
    launches = _fake_workspace_codex(monkeypatch, [malformed, valid])
    schema = tmp_path / 'schema.json'
    schema.write_text(json.dumps(_word_schema()), encoding='utf-8')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))

    assert await runner.call('malformed-file', 'Annotate the source.', schema, 'low') == valid
    assert len(launches) == 2
    job = tmp_path / 'agents/malformed-file'
    retry = launches[1]['workspace']
    assert (job / 'workspace/rejected-candidate-attempt-01.json').read_bytes() == malformed
    assert (retry / 'rejected-candidate-attempt-01.json').read_bytes() == malformed
    record = json.loads((retry / 'submission-rejection.json').read_text())
    assert record['category'] == 'unreadable_candidate'
    assert record['artifact_sha256']
    assert 'Could not parse submitted candidate' in record['diagnostic']


@pytest.mark.asyncio
async def test_codex_runner_keeps_persistent_schema_rejection_after_one_repair(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner
    from pipeline.worker_workspace import CandidateSubmissionError

    invalid = _word_candidate(complete=False)
    launches = _fake_workspace_codex(monkeypatch, [invalid, invalid])
    schema = tmp_path / 'schema.json'
    schema.write_text(json.dumps(_word_schema()), encoding='utf-8')
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))

    with pytest.raises(CandidateSubmissionError, match='bounded correction rejected'):
        await runner.call('persistent', 'Annotate the source.', schema, 'low')
    assert len(launches) == 2
    job = tmp_path / 'agents/persistent'
    meta = json.loads((job / 'meta.json').read_text())
    assert meta['return_code'] == 1
    recovery = json.loads((job / 'submission-recovery.json').read_text())
    assert recovery['status'] == 'rejected'
    assert 'finding' not in meta
    retry_workspace = launches[1]['workspace']
    assert json.loads((retry_workspace / 'rejected-candidate-attempt-01.json').read_text()) == invalid


@pytest.mark.asyncio
async def test_enabling_tools_reuses_exact_legacy_offline_cache(tmp_path, monkeypatch):
    import asyncio
    from pipeline.agent_harness import CodexRunner, digest, CachedCallUnavailable
    schema = tmp_path/'schema.json'
    schema.write_text('{"type":"object"}')
    job = tmp_path/'agents/old-job'
    job.mkdir(parents=True)
    (job/'result.json').write_text('{"ok":true}')
    fp = digest('same task', schema.read_text(), 'gpt-6-luna', 'low')
    meta = {'model':'gpt-6-luna', 'effort':'low', 'return_code':0, 'tool_profile':'offline',
            'fingerprint':digest(fp, 'offline', 'tool-profile-v1')}
    (job/'meta.json').write_text(json.dumps(meta))
    async def unexpected(*args, **kwargs):
        raise AssertionError('Unrelated approved work must not rerun')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', unexpected)
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))
    assert await runner.call('old-job', 'same task', schema, 'low', tool_profile='offline') == {'ok':True}
    assert not (job/'workspace').exists()
    # Historical evidence keeps its actual policy, rather than being relabeled.
    (job/'events.attempt-01.jsonl').write_text(json.dumps({'type':'item.completed', 'item':{'type':'command_execution'}}))
    with pytest.raises(CachedCallUnavailable):
        await runner.call('old-job', 'same task', schema, 'low', tool_profile='offline', cache_only=True)


def test_korean_local_check_uses_full_identity_and_dictionary_gate(tmp_path):
    from pipeline.korean_annotation_chunks import SPAN_LINK_SCHEMA, SPAN_LINK_FORMAT
    value = {'format':SPAN_LINK_FORMAT, 'segments':[{
        'type':'word', 'meaning_en':'person', 'lemma':'사람', 'lexical_kind':'vocabulary',
        'lexical_id':'사람/명', 'story_importance_en':'', 'form_steps':[], 'is_inflected':False,
        'grammar_links':[], 'expression_links':[], 'source_start':0, 'source_end':2}]}
    context = {'annotation_validation': {'language':'ko', 'chunk_text':'사람',
        'focus':{'entries':[]}, 'title':'Test', 'number':1, 'plan':{'beats':[]},
        'level':3, 'source_id':'test.json'}}
    build(tmp_path, 'Annotate the given source.', SPAN_LINK_SCHEMA, context=context)
    candidate = tmp_path/'candidate.json';candidate.write_text(json.dumps(value))
    assert check(tmp_path, candidate) == value
    value['segments'][0]['lemma'] = '사람/명'
    candidate.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='Headword field contains'):
        check(tmp_path, candidate)
    # Submission retains the proposal so the pipeline's typed error routing
    # can request research/plan repair; it does not claim stage approval.
    from pipeline.worker_workspace import submit
    assert submit(tmp_path, {'candidate_path':'candidate.json'})[0] == value
