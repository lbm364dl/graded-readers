import copy
import asyncio

import pytest

from pipeline.korean_prose_patches import apply_patch


def draft():
    return {'title': '제목', 'text': '앞 문장.\n\n틀린 문장. 뒤 문장. ',
            'length_reason_en': 'Preserve the complete scene.'}


def patch(edits):
    return {'title': '제목', 'length_reason_en': 'Preserve the complete scene.', 'edits': edits}


def edit(start, end, old, new):
    return {'source_start': start, 'source_end': end, 'original_text': old,
            'replacement_text': new, 'reason_en': 'Correct the reviewed issue.'}


def test_targeted_prose_replacement_preserves_all_other_text_and_inputs():
    previous = draft()
    start = previous['text'].index('틀린')
    proposal = patch([edit(start, start + 5, '틀린 문장', '고친 문장')])
    before = copy.deepcopy((previous, proposal))
    result = apply_patch(previous, proposal)
    assert result['text'] == '앞 문장.\n\n고친 문장. 뒤 문장. '
    assert (previous, proposal) == before
    assert apply_patch(previous, patch([])) == previous


def test_prose_patch_supports_explicit_missing_event_insertion_and_deletion():
    previous = draft()
    result = apply_patch(previous, patch([edit(0, 0, '', '새 사건. '),
        edit(7, 13, '틀린 문장.', '')]))
    assert result['text'] == '새 사건. 앞 문장.\n\n 뒤 문장. '


@pytest.mark.parametrize('edits', [
    [edit(7, 12, 'wrong source', '고침')],
    [edit(7, 12, '틀린 문장', '틀린 문장')],
    [edit(7, 12, '틀린 문장', '고침'), edit(8, 10, '린 ', '겹침')],
    [edit(7, 12, '틀린 문장', '고침'), edit(0, 2, '앞 ', '뒤 ')],
    [edit(0, 999, '', '고침')],
    [edit(0, 0, '', '하나'), edit(0, 0, '', '둘')],
])
def test_prose_patch_rejects_stale_ambiguous_or_broad_invalid_edits(edits):
    previous, proposal = draft(), patch(edits)
    before = copy.deepcopy((previous, proposal))
    with pytest.raises(ValueError):
        apply_patch(previous, proposal)
    assert (previous, proposal) == before


def test_prose_stage_repairs_rejected_draft_with_patches_and_replays_evidence(tmp_path):
    from pipeline.korean_agent_harness import KoreanHarness, save, read
    from pipeline.korean_prose_patches import replay
    class Runner:
        def __init__(self): self.jobs = []
        async def call(self, job, prompt, *args, **kwargs):
            self.jobs.append(job)
            if job.endswith('-patch'):
                assert 'not a regenerated chapter' in prompt
                value = patch([edit(7, 12, '틀린 문장', '고친 문장')])
            elif '-review-' in job:
                corrected = '고친 문장' in prompt
                value = {'approved': corrected, 'issues': [] if corrected else ['Repair the incorrect sentence.']}
            else:
                raise AssertionError('Unexpected whole-draft regeneration: ' + job)
            save(tmp_path/'agents'/job/'result.json', value)
            save(tmp_path/'agents'/job/'meta.json', {'return_code': 0})
            return value
    runner = Runner()
    harness = KoreanHarness(tmp_path, 3, runner=runner)
    result = asyncio.run(harness.stage('prose', 'Write a coherent scene', 'prose',
        lambda value: None, {}, initial=draft()))
    assert result['text'] == '앞 문장.\n\n고친 문장. 뒤 문장. '
    assert any(job.endswith('-patch') for job in runner.jobs)
    proposal_job = harness.stages['prose']['proposal_job']
    meta = read(tmp_path/'agents'/proposal_job/'meta.json')
    assert replay(tmp_path, meta) == result
    before = copy.deepcopy(meta)
    meta['base']['text'] += ' changed'
    with pytest.raises(ValueError, match='evidence changed'):
        replay(tmp_path, meta)
    meta = before
    p = tmp_path/'agents'/meta['patch_job']/'result.json'
    changed = read(p)
    changed['edits'][0]['replacement_text'] = 'tampered'
    save(p, changed)
    with pytest.raises(ValueError, match='evidence changed'):
        replay(tmp_path, meta)
