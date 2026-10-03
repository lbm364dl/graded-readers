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
