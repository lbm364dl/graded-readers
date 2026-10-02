import asyncio
import copy
import json
from pathlib import Path

import pytest

from pipeline import korean_contracts as contracts
from pipeline import korean_dictionary as dictionary
from pipeline import korean_publication as publication
from pipeline import korean_sources as sources
from pipeline.korean_agent_harness import KoreanHarness, check_prose, digest, validate_delta


def manual_chapter():
    return json.loads(Path('tests/fixtures/korean_manual_annotations.json').read_text())['chapters'][0]


def protocol_curriculum_bindings(chapter):
    """Synthetic reviewer outputs for cache/replay tests, not linguistic data.

    Real source grading and contrasting grammar cases are tested separately in
    test_korean_curriculum.py. These tests mock every model review already.
    """
    required = {('vocabulary', s['lexical']['id']) for s in chapter['segments']
                if s.get('lexical', {}).get('kind') == 'vocabulary'}
    required |= {('grammar', link['entry_id']) for link in chapter['grammar_links']}
    return {'level_reason_en': 'Synthetic level-review fixture.', 'prose_revision_reason_en': '', 'bindings': [{'kind': kind, 'entry_id': identity,
        'source_ids': [f'nikl2017-{kind}-00001'], 'equivalence': 'listed',
        'analysis_en': 'Synthetic curriculum-review protocol fixture.', 'optional_reason_en': ''}
        for kind, identity in sorted(required)]}


def test_chapter_quantity_is_not_a_prose_gate_but_empty_work_is_rejected():
    from jsonschema import validate
    short = {'title': '도착', 'text': '아이가 왔습니다.',
             'length_reason_en': 'This source scene contains only the arrival; further detail would be invented.'}
    long = {**short, 'text': manual_chapter()['text'] * 8,
            'length_reason_en': 'Synthetic quantity fixture; narrative quality is assessed by independent review.'}
    assert len(long['text']) > 250
    assert len(contracts.sentence_inventory(long['text'])) > 9
    for value in (short, long):
        validate(value, contracts.PROSE)
        check_prose(value)
    for field in ('title', 'text', 'length_reason_en'):
        with pytest.raises(ValueError, match='explained length decision'):
            check_prose({**short, field: ' '})


def test_source_coverage_has_no_two_paragraph_quota_and_records_reason():
    from jsonschema import validate
    plan = {'title': '가족', 'scope_reason_en': 'All three paragraphs advance the family scene using beginner wording.', 'last_source_paragraph_index': 2,
            'beats': [{'source_paragraph_index': i, 'event_en': f'Event {i}'} for i in range(3)]}
    validate(plan, contracts.PLAN)
    bound = contracts.bind_plan(plan, '하나\n\n둘\n\n셋', 0)
    assert len(bound['beats']) == 3
    assert bound['scope_reason_en'] == plan['scope_reason_en']


def test_explicit_grammar_bindings_unify_equivalents_and_preserve_contrasts():
    value = {'segments': [{'lexical_kind': 'grammar', 'lexical_id': 'condition-b',
                          'form_steps': [{'grammar_entry_ids': ['condition-a']}], 'text': 'unchanged'}],
             'grammar_links': [{'entry_id': x} for x in ('condition-a', 'condition-b', 'subject-i-ga')]}
    rows = [{'draft_id': 'condition-a', 'entry_id': 'condition-a'},
            {'draft_id': 'condition-b', 'entry_id': 'condition-a'}]
    mapped = contracts.bind_grammar_identities(value, {'bindings': rows}, {'subject-i-ga'})
    assert mapped['segments'][0]['lexical_id'] == 'condition-a'
    assert mapped['segments'][0]['form_steps'][0]['grammar_entry_ids'] == ['condition-a']
    assert mapped['grammar_links'][-1]['entry_id'] == 'subject-i-ga'
    assert mapped['segments'][0]['text'] == value['segments'][0]['text']
    assert value['segments'][0]['lexical_id'] == 'condition-b'
    distinct = [{'draft_id': x, 'entry_id': x} for x in ('condition-a', 'condition-b')]
    assert contracts.bind_grammar_identities(value, {'bindings': distinct}, {'subject-i-ga'}) == value
    with pytest.raises(ValueError, match='only new draft identities'):
        contracts.bind_grammar_identities(value, {'bindings': rows + [{'draft_id': 'subject-i-ga', 'entry_id': 'condition-a'}]}, {'subject-i-ga'})
    with pytest.raises(ValueError, match='canonical identity'):
        contracts.bind_grammar_identities(value, {'bindings': [{**r, 'entry_id': 'invented'} for r in rows]}, {'subject-i-ga'})


def test_annotation_slicing_and_review_repair_selection_preserve_other_chunks():
    from pipeline.korean_agent_harness import normalize_existing
    chapter = manual_chapter()
    raw = normalize_existing(chapter, dictionary._registry(dictionary.WORDS))
    texts = contracts.annotation_chunks(chapter['text'])
    chunks = contracts.slice_annotations(raw, texts)
    assert contracts.combine_annotations(chunks, texts) == raw
    selected = contracts.repair_selection({'repairs': [{'chunk_index': 2, 'issues': ['Correct the reviewed occurrence meaning.']}]}, len(texts))
    assert set(selected) == {2}
    for rows in ([{'chunk_index': 0, 'issues': ['bad']}],
                 [{'chunk_index': 1, 'issues': ['bad']}] * 2,
                 [{'chunk_index': len(texts) + 1, 'issues': ['bad']}]):
        with pytest.raises(ValueError, match='escapes chapter coverage'):
            contracts.repair_selection({'repairs': rows}, len(texts))


def test_agent_source_boundary_can_extend_beyond_reference_scene():
    manifest, reference, _ = sources.load_unit(1)
    source = (sources.DIRECTORY / manifest['text_file']).read_text().rstrip('\n')
    plan = {'title': '가족과 대화', 'scope_reason_en': 'Retain the connected conversation.',
            'last_source_paragraph_index': 16,
            'beats': [{'source_paragraph_index': 16, 'event_en': 'Closing conversation'}]}
    bound = contracts.bind_plan(plan, source, 0)
    unit = {'number': 1, **bound['scope'], 'label': plan['title']}
    assert unit['end'] > reference['end']
    _, _, selected = sources.load_selected_unit(unit)
    assert selected == source[:unit['end']]
    with pytest.raises(ValueError, match='complete original paragraphs'):
        sources.load_selected_unit({**unit, 'end': unit['end'] - 1})
    plan['last_source_paragraph_index'] = 0
    with pytest.raises(ValueError, match='invalid paragraph'):
        contracts.bind_plan(plan, source, 0)


def test_publication_rejects_overlapping_or_skipped_chapter_scopes(tmp_path, monkeypatch):
    chapter = manual_chapter()
    chapter['source_alignment']['unit'] = {'number': 1, 'start': 0, 'end': 100, 'label': 'test'}
    second = copy.deepcopy(chapter)
    second['number'] = 2
    second['source_alignment']['unit']['number'] = 2
    for number in (1, 2):
        path = tmp_path / f'chapter-{number:03d}'
        path.mkdir()
        (path / 'report.json').write_text('{}')
    monkeypatch.setattr(publication, 'verify_run', lambda path: (
        chapter if path.name == 'chapter-001' else second, {}, {}, {}))
    for start in (0, 103):
        second['source_alignment']['unit']['start'] = start
        with pytest.raises(ValueError, match='overlap or skip'):
            publication.publish(tmp_path)


@pytest.mark.parametrize('prose_revision,worker_failure,recover_partial', [(False, False, False), (False, True, False), (True, False, False), ('technical_failure', False, False), (False, False, True), (False, False, 'rejected_worker')])
def test_annotation_repairs_only_failed_chunk_and_reuses_other_sentences(tmp_path, prose_revision, worker_failure, recover_partial):
    from pipeline.korean_agent_harness import normalize_existing, save, UnannotatableProseError
    chapter = manual_chapter()
    raw = normalize_existing(chapter, dictionary._registry(dictionary.WORDS))
    texts = contracts.annotation_chunks(chapter['text'])
    chunks = contracts.slice_annotations(raw, texts)
    revised = copy.deepcopy(raw)
    revised['segments'] = revised['segments'][2:]
    revised['grammar_links'] = [{**link, 'segment_index': link['segment_index'] - 2,
        'display_end_segment_index': link['display_end_segment_index'] - 2
        if link['display_end_segment_index'] != -1 else -1}
        for link in raw['grammar_links'] if link['segment_index'] >= 2]
    revised['inflected_segment_indices'] = [i - 2 for i in raw['inflected_segment_indices'] if i >= 2]
    revised_text = ''.join(s['text'] for s in revised['segments'])
    revised_chunks = contracts.slice_annotations(revised, contracts.annotation_chunks(revised_text))
    class Runner:
        def __init__(self): self.jobs = []
        async def call(self, job, *args, **kwargs):
            self.jobs.append(job)
            value = await self.respond(job)
            save(tmp_path / 'agents' / job / 'result.json', value)
            save(tmp_path / 'agents' / job / 'meta.json', {'return_code': 0})
            return value
        async def respond(self, job):
            if prose_revision and job == 'annotation-review-0':
                return {'approved': False, 'issues': ['First sentence needs deliberate prose review.']}
            if prose_revision and job == 'annotation-1-repair-plan':
                if prose_revision == 'technical_failure':
                    raise ValueError('Invalid annotation repair evidence')
                return {'repairs': [], 'prose_revision_reason_en': 'Revise the first sentence deliberately'}
            if job.endswith('-reuse-plan'):
                return {'reused_chunks': [{'old_chunk_index': i, 'new_chunk_index': i} for i in range(2, len(chunks) + 1)]}
            if '-review-' in job: return {'approved': True, 'issues': []}
            if job.startswith('plan-'):
                return {'title': '제목', 'scope_reason_en': 'Test scene decision', 'last_source_paragraph_index': 0,
                        'beats': [{'source_paragraph_index': 0, 'event_en': 'Test setup'}]}
            if job.startswith('lexical-plan-'):
                return {'entries': [
                    {'id': 'hong-gildong', 'headword': '홍길동', 'kind': 'proper_name', 'aliases': ['홍길동'], 'role_en': 'central child'},
                    {'id': '벼슬/명', 'headword': '벼슬', 'kind': 'story_term', 'aliases': ['벼슬'], 'role_en': 'historical office'}]}
            if job.startswith('prose-'):
                return {'title': '제목', 'text': revised_text if 'revision1' in job else chapter['text'], 'length_reason_en': 'Test source-scene extent'}
            if job.startswith('curriculum-'):
                adopted = revised if 'revision1' in job else raw
                canonical = {'segments': [dict(s, lexical={'kind': s['lexical_kind'], 'id': s['lexical_id']})
                    for s in adopted['segments']], 'grammar_links': adopted['grammar_links']}
                return protocol_curriculum_bindings(canonical)
            if '-chunk-' in job:
                number, attempt = map(int, job.split('-chunk-')[1].split('-'))
                if worker_failure and number == 1 and attempt == 0:
                    raise ValueError('Worker used tools outside its offline role')
                value = copy.deepcopy((revised_chunks if 'revision1' in job else chunks)[number - 1])
                if not prose_revision and number == 1 and attempt == 0:
                    next(s for s in value['segments'] if s['lexical_kind'] == 'vocabulary')['lexical_id'] = 'invented'
                return value
            if job.startswith('sentence-help-'):
                return {'sentences': [{**row, 'selected': False, 'reason_en': 'Synthetic selection fixture',
                                      'translation_en': '', 'parts': []}
                                     for row in contracts.sentence_inventory(revised_text if prose_revision else chapter['text'])]}
            raise AssertionError(job)
    runner = Runner()
    if recover_partial:
        # An earlier process completed workers but never saved the parent
        # assembly: one valid proposal and one invalid identity to repair.
        bad = copy.deepcopy(chunks[0])
        next(s for s in bad['segments'] if s['lexical_kind'] == 'vocabulary')['lexical_id'] = 'invented'
        for index, value in ((1, bad), (2, chunks[1])):
            job = f'annotation-0-chunk-{index:03d}-0'
            save(tmp_path / 'agents' / job / 'result.json', value)
            save(tmp_path / 'agents' / job / 'meta.json', {'return_code': 0})
        if recover_partial == 'rejected_worker':
            (tmp_path / 'agents' / 'annotation-0-chunk-002-0' / 'events.attempt-01.jsonl').write_text(json.dumps({'item': {'type': 'mcp_tool_call'}}) + '\n')
    if prose_revision == 'technical_failure':
        with pytest.raises(ValueError, match='Invalid annotation repair evidence'):
            asyncio.run(KoreanHarness(tmp_path, 1, runner=runner).run())
        assert not any('prose-revision' in job for job in runner.jobs)
        return
    assert asyncio.run(KoreanHarness(tmp_path, 1, runner=runner).run())['status'] == 'complete'
    jobs = [job for job in runner.jobs if '-chunk-' in job]
    assert jobs.count('annotation-0-chunk-001-0') == (0 if recover_partial else 1)
    assert jobs.count('annotation-0-chunk-001-1') == (0 if prose_revision else 1)
    assert all(jobs.count(f'annotation-0-chunk-{i:03d}-0') == (0 if recover_partial and i == 2 else 1) for i in range(2, len(chunks) + 1))
    assert len(jobs) == len(chunks) + (-1 if recover_partial else 1) + (1 if recover_partial == 'rejected_worker' else 0)
    if recover_partial:
        assert 'annotation-review-0' in runner.jobs  # Recovery is never approval.
        if recover_partial == 'rejected_worker':
            assert 'annotation-0-chunk-002-1' in runner.jobs
    if prose_revision:
        assert [job for job in jobs if 'revision1' in job] == ['annotation-revision1-0-chunk-001-0']


def test_form_reading_can_show_pronunciation_but_form_must_match_tap(tmp_path):
    chapter = manual_chapter()
    step = chapter['segments'][7]['form_steps'][-1]
    step['reading'] = '사랃씀니다'
    _, grammar = dictionary.build_assets(chapter, tmp_path, write=False)
    assert any(form['reading'] == '사랃씀니다' for form in grammar['forms'])
    step['form'] = '살았어요'
    with pytest.raises(ValueError, match='does not end at tap surface'):
        dictionary.build_assets(chapter, tmp_path, write=False)


def test_korean_pronunciation_note_is_optional_but_complete_meaning_is_required(tmp_path):
    chapter = manual_chapter()
    step = chapter['segments'][7]['form_steps'][-1]
    step['reading'] = ''
    dictionary.build_assets(chapter, tmp_path, write=False)
    step['meaning_en'] = ''
    with pytest.raises(ValueError, match='complete-form transformation'):
        dictionary.build_assets(chapter, tmp_path, write=False)


def test_transformations_require_one_grammar_destination():
    from jsonschema import validate, ValidationError
    step = manual_chapter()['segments'][7]['form_steps'][0]
    validate(step, contracts.STEP)
    for ids in ([], ['past-ass-eoss', 'polite-seumnida']):
        with pytest.raises(ValidationError):
            validate({**step, 'grammar_entry_ids': ids}, contracts.STEP)


@pytest.mark.parametrize('identity,lemma', [('똑같다/형', '똑같다'), ('똑같이/부', '똑같이')])
def test_annotation_review_receives_attested_identity_grades_and_budget(tmp_path, identity, lemma):
    import json
    from pipeline.korean_agent_harness import normalize_existing
    raw = normalize_existing(manual_chapter(), dictionary._registry(dictionary.WORDS))
    word = {**raw['segments'][0], 'text': '똑같이', 'lexical_id': identity,
            'lemma': lemma, 'lexical_kind': 'vocabulary', 'form_steps': []}
    value = {'segments': [word], 'grammar_links': [], 'inflected_segment_indices': []}
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            if 'review' not in job: return value
            data = json.loads(prompt.split('\nINPUT:\n', 1)[1])
            evidence = data['vocabulary_evidence']
            expected = {'똑같이/부': 'B'}
            if identity == '똑같다/형': expected['똑같다/형'] = 'A'
            assert {entry['id']: entry['grade'] for entry in evidence['entries']} == expected
            assert evidence['max_non_beginner_ratio'] == 0.10
            assert data['context']['linguistic_reference']['reviewed']
            return {'approved': True, 'issues': []}
    assert asyncio.run(KoreanHarness(tmp_path, 1, runner=Runner()).stage(
        'annotation', 'task', 'annotation', lambda _: None, {})) == value


def test_resume_uses_latest_reviewed_prose_not_largest_revision_number(tmp_path):
    from pipeline.korean_agent_harness import latest_prose_revision, save
    assert latest_prose_revision(tmp_path) == 0
    value = {'title': '제목', 'text': '아이가 왔습니다.', 'length_reason_en': 'A complete test scene.'}
    for revision, attempt, date, accepted in [(2, 0, '2026-01-01', True),
                                             (1, 3, '2026-01-02', True),
                                             (2, 1, '2026-01-03', False)]:
        job = tmp_path / 'agents' / f'prose-revision{revision}-{attempt}'
        review = tmp_path / 'agents' / f'prose-revision{revision}-review-{attempt}'
        save(job / 'result.json', value)
        save(job / 'meta.json', {'return_code': 0})
        save(review / 'result.json', {'approved': accepted, 'issues': [] if accepted else ['Unresolved issue']})
        save(review / 'meta.json', {'return_code': 0, 'ended_at': date})
    assert latest_prose_revision(tmp_path) == 1
    save(tmp_path / 'agents/prose-revision1-3/meta.json', {'return_code': 1})
    assert latest_prose_revision(tmp_path) == 2


def test_sentence_help_can_end_inside_punctuation_but_cannot_split_words(tmp_path):
    from pipeline.korean_sentence_breakdowns import build
    chapter = {'text': '좋습니다. 다음입니다.', 'segments': [
        {'text': '좋습니다', 'type': 'word'}, {'text': '. ', 'type': 'punctuation'},
        {'text': '다음입니다', 'type': 'word'}, {'text': '.', 'type': 'punctuation'}]}
    item = {'source': 'test', 'start': 0, 'sentence': '좋습니다.',
            'translation_en': 'It is good.', 'parts': [
                {'text': '좋습니다.', 'explanation_en': 'A polite statement.'}]}
    data = {'schema_version': 1, 'reviewed': True, 'breakdowns': [item]}
    assert build(chapter, tmp_path, source_id='test', data=data, write=False)['breakdowns'] == [item]
    item['parts'] = [{'text': '좋', 'explanation_en': 'Invalid split'},
                     {'text': '습니다.', 'explanation_en': 'Invalid split'}]
    with pytest.raises(ValueError, match='splits a tap'):
        build(chapter, tmp_path, source_id='test', data=data, write=False)


def test_pinned_source_is_complete_and_detects_changed_text(tmp_path):
    manifest, unit, source = sources.load_unit(1)
    assert 'ᄒᆞ' in source or len(source) > 100
    assert len((sources.DIRECTORY / 'original.txt').read_text()) > 30000
    for name in ('source.json', 'original.txt', 'original.wiki', 'source-notes.json'):
        (tmp_path / name).write_bytes((sources.DIRECTORY / name).read_bytes())
    assert sources.load_unit(1, tmp_path) == (manifest, unit, source)
    with (tmp_path / 'original.txt').open('a') as stream:
        stream.write('changed')
    with pytest.raises(ValueError, match='stale'):
        sources.load_unit(1, tmp_path)
    with pytest.raises(ValueError, match='not been mapped'):
        sources.load_unit(3)


def test_source_plan_binds_exact_unicode_paragraphs_and_rejects_reordering():
    plan = {'title': '제목', 'beats': [{'source_paragraph_index': 1, 'event_en': 'event'}]}
    bound = contracts.bind_plan(plan, '첫 문단\n\n둘째 문단', 10)
    assert bound['beats'][0]['quote'] == '둘째 문단'
    assert bound['beats'][0]['start'] == 16
    plan['beats'].append({'source_paragraph_index': 0, 'event_en': 'event'})
    with pytest.raises(ValueError, match='reorders'):
        contracts.bind_plan(plan, '첫 문단\n\n둘째 문단', 10)


def test_dictionary_delta_reuses_approved_entries_and_allows_only_missing():
    old = {'id': 'one', 'headword': '하나', 'kind': 'word', 'definition_en': 'one'}
    new = {'id': 'two', 'headword': '둘', 'kind': 'word', 'definition_en': 'two'}
    requests = {entry['id']: {k: entry[k] for k in ('headword', 'kind')} for entry in (old, new)}
    merged, _ = validate_delta({'words': [new], 'grammar': []}, requests, set(), {'one': old}, {})
    assert merged['one'] is old
    assert merged['two'] == new
    with pytest.raises(ValueError, match='only new'):
        validate_delta({'words': [old, new], 'grammar': []}, requests, set(), {'one': old}, {})
    with pytest.raises(ValueError, match='changed a lexical identity'):
        validate_delta({'words': [{**new, 'headword': '셋'}], 'grammar': []}, requests, set(), {'one': old}, {})


def test_sentence_help_covers_all_sentences_but_leaves_simple_sentence_alone():
    chapter = {'text': '아이가 왔습니다. 아이가 온 것을 보았습니다.'}
    inventory = contracts.sentence_inventory(chapter['text'])
    rows = [{**row, 'selected': False, 'reason_en': 'simple', 'translation_en': '', 'parts': []} for row in inventory]
    rows[1].update(selected=True, reason_en='embedded clause', translation_en='I saw that the child came.',
                   parts=[{'text': inventory[1]['sentence'], 'explanation_en': 'Object clause and main verb.'}])
    result = contracts.selected_breakdowns({'sentences': rows}, chapter, 'source')
    assert len(result['breakdowns']) == 1
    assert result['breakdowns'][0]['start'] == inventory[1]['start']
    rows[0]['translation_en'] = 'The child came.'
    with pytest.raises(ValueError, match='unsolicited'):
        contracts.selected_breakdowns({'sentences': rows}, chapter, 'source')
    with pytest.raises(ValueError, match='every sentence'):
        contracts.selected_breakdowns({'sentences': rows[:1]}, chapter, 'source')


def test_independent_review_rejection_repairs_and_only_approval_completes(tmp_path):
    class Runner:
        def __init__(self):
            self.jobs = []
        async def call(self, job, prompt, *args, **kwargs):
            self.jobs.append(job)
            if job == 'prose-review-0':
                return {'approved': False, 'issues': ['Unsupported people group']}
            if '-review-' in job:
                assert 'Unsupported people group' in prompt or job.endswith('1')
                return {'approved': True, 'issues': []}
            return {'title': '제목', 'text': 'bad' if job.endswith('0') else 'fixed', 'length_reason_en': 'Test editorial decision'}
    runner = Runner()
    harness = KoreanHarness(tmp_path, 1, runner=runner)
    result = asyncio.run(harness.stage('prose', 'task', 'prose', lambda _: None, {}))
    assert result['text'] == 'fixed'
    assert runner.jobs == ['prose-0', 'prose-review-0', 'prose-1', 'prose-review-1']
    assert harness.stages['prose']['review_job'] == 'prose-review-1'


def test_dictionary_publication_keeps_both_chapters_and_exact_form_routes(tmp_path):
    first = manual_chapter()
    second = copy.deepcopy(first)
    second['number'] = 2
    words, grammar = dictionary.build_collection([first, second], tmp_path)
    assert len(words['sources']) == len(grammar['sources']) == 2
    assert len(words['occurrences']) == 2 * len(dictionary.build_assets(first, tmp_path, write=False)[0]['occurrences'])
    assert {form['source'] for form in grammar['forms']} == set(grammar['sources'])
    assert all(form['meaning_en'] and len(form['grammar_entry_ids']) == 1 for form in grammar['forms'])
    with pytest.raises(ValueError, match='duplicate'):
        dictionary.build_collection([first, first], tmp_path)


def test_publication_rejects_incomplete_run_before_writing(tmp_path):
    run = tmp_path / 'chapter-001'
    run.mkdir()
    (run / 'report.json').write_text(json.dumps({'status': 'complete', 'edition': sources.EDITION, 'artifacts': {}}))
    with pytest.raises(ValueError, match='artifact coverage'):
        publication.publish(tmp_path)


def test_publication_requires_curriculum_review_and_replays_exact_bindings(tmp_path):
    from pipeline.korean_agent_harness import read, save
    make_reviewed_run(tmp_path)
    assert publication.verify_run(tmp_path)[0]['curriculum']['evaluation']['source_sha256']
    chapter = read(tmp_path / 'chapter.json')
    chapter['curriculum']['bindings']['bindings'][0]['analysis_en'] = 'Changed after approval'
    save(tmp_path / 'chapter.json', chapter)
    report = read(tmp_path / 'report.json')
    report['artifacts']['chapter.json'] = sources.sha((tmp_path / 'chapter.json').read_bytes())
    save(tmp_path / 'report.json', report)
    with pytest.raises(ValueError, match='differs from reviewed proposals'):
        publication.verify_run(tmp_path)
    make_reviewed_run(tmp_path)
    report = read(tmp_path / 'report.json')
    del report['stages']['curriculum']
    save(tmp_path / 'report.json', report)
    with pytest.raises(ValueError, match='coverage or policy is stale'):
        publication.verify_run(tmp_path)


def test_publication_replays_grammar_binding_job_and_rejects_changed_result(tmp_path):
    from pipeline.korean_agent_harness import read, save
    make_reviewed_run(tmp_path)
    chapter = read(tmp_path / 'chapter.json')
    raw = read(tmp_path / 'agents/annotation/result.json')
    lineage, first = [], 0
    for i, text in enumerate(contracts.annotation_chunks(chapter['text'])):
        last, length = first, 0
        while length < len(text):
            length += len(raw['segments'][last]['text'])
            last += 1
        chunk = {'segments': raw['segments'][first:last],
                 'grammar_links': [{**link, 'segment_index': link['segment_index'] - first,
                    'display_end_segment_index': link['display_end_segment_index'] - first
                    if link['display_end_segment_index'] != -1 else -1}
                    for link in raw['grammar_links'] if first <= link['segment_index'] < last],
                 'inflected_segment_indices': [index - first for index in raw['inflected_segment_indices'] if first <= index < last]}
        job = f'raw-chunk-{i}'
        save(tmp_path / 'agents' / job / 'result.json', chunk)
        save(tmp_path / 'agents' / job / 'meta.json', {'return_code': 0})
        lineage.append({'job': job, 'text': text, 'digest': digest(chunk)})
        first = last
    bindings = {'bindings': []}
    save(tmp_path / 'agents/binding/result.json', bindings)
    save(tmp_path / 'agents/binding/meta.json', {'return_code': 0})
    save(tmp_path / 'agents/annotation/meta.json', {'return_code': 0, 'kind': 'annotation_assembly',
         'chunks': lineage, 'grammar_binding_job': 'binding', 'grammar_binding_digest': digest(bindings),
         'approved_grammar_ids': sorted(dictionary._registry(dictionary.GRAMMAR))})
    assert publication.verify_run(tmp_path)[0] == chapter
    save(tmp_path / 'agents/binding/result.json', {'bindings': [{'draft_id': 'changed', 'entry_id': 'changed'}]})
    with pytest.raises(ValueError, match='bindings changed after annotation review'):
        publication.verify_run(tmp_path)


def test_completed_run_cache_avoids_calls_but_changed_dictionary_invalidates(tmp_path, monkeypatch):
    chapter = manual_chapter()
    _, unit, _ = sources.load_unit(1)
    words = dictionary._registry(dictionary.WORDS)
    grammar = dictionary._registry(dictionary.GRAMMAR)
    report = {'previous_chapters_digest': digest(''), 'source_unit': unit,
              'dictionary_digest': digest(publication.relevant_entries(chapter, words, grammar)),
              'existing_input_digest': None}
    (tmp_path / 'report.json').write_text(json.dumps(report))
    monkeypatch.setattr(publication, 'verify_run', lambda _: (chapter, {'words': [], 'grammar': []}, {}, {}))
    class NoCalls:
        async def call(self, *args, **kwargs):
            raise AssertionError('model called')
    harness = KoreanHarness(tmp_path, 1, runner=NoCalls())
    assert asyncio.run(harness.run()) == report
    harness.words['아버지/명'] = {**harness.words['아버지/명'], 'definition_en': 'changed definition'}
    with pytest.raises(AssertionError, match='model called'):
        asyncio.run(harness.run())
    assert not (tmp_path / 'report.json').exists()


def make_reviewed_run(run, level=1):
    from pipeline.korean_agent_harness import POLICY, normalize_existing, save
    chapter = manual_chapter()
    manifest, unit, source = sources.load_unit(1)
    plan = {'title': '제목', 'scope_reason_en': 'Coherent family setup; omit unrelated detail.', 'last_source_paragraph_index': 0, 'beats': [{'source_paragraph_index': 0, 'event_en': 'Family setup'}]}
    bound = contracts.bind_plan(plan, source, unit['start'])
    unit = {'number': 1, **bound['scope'], 'label': plan['title']}
    prose = {'title': '제목', 'text': chapter['text'], 'length_reason_en': 'The family scene reaches its supported conflict.'}
    annotation = normalize_existing(chapter, dictionary._registry(dictionary.WORDS))
    focus = {'entries': [
        {'id': 'hong-gildong', 'headword': '홍길동', 'kind': 'proper_name', 'aliases': ['홍길동'], 'role_en': 'central child'},
        {'id': '벼슬/명', 'headword': '벼슬', 'kind': 'story_term', 'aliases': ['벼슬'], 'role_en': 'historical office'}]}
    chapter = contracts.canonical_annotation(annotation, prose, 1, sources.EDITION, bound, focus, level=level)
    from pipeline.korean_curriculum import evaluate_bindings
    bindings = protocol_curriculum_bindings(chapter)
    chapter['curriculum'] = {'bindings': bindings, 'evaluation': evaluate_bindings(chapter, bindings, level=level)}
    old = json.loads(Path('tests/fixtures/korean_manual_breakdowns.json').read_text())
    rows = []
    for sentence in contracts.sentence_inventory(chapter['text']):
        match = next((b for b in old['breakdowns'] if b['start'] == sentence['start']), None)
        rows.append({**sentence, 'selected': bool(match), 'reason_en': 'test decision',
                     'translation_en': match['translation_en'] if match else '',
                     'parts': match['parts'] if match else []})
    help_output = {'sentences': rows}
    from pipeline.korean_levels import source_id, LEVEL_POLICY
    help_data = contracts.selected_breakdowns(help_output, chapter, source_id(chapter))
    report = {'status': 'complete', 'number': 1, 'edition': sources.EDITION,
              'source_sha256': manifest['text_sha256'], 'source_notes_sha256': manifest.get('notes_sha256'), 'source_unit': unit,
              'policy_sha256': sources.sha(POLICY.read_bytes()),
              'linguistic_reference_sha256': sources.sha((sources.ROOT / 'data/korean/linguistic-reference.json').read_bytes()),
              'stages': {}, 'artifacts': {}}
    if level > 1:
        report.update(target_level=level, level_policy_sha256=sources.sha(LEVEL_POLICY.read_bytes()))
    for name, value in {'plan': plan, 'lexical-plan': focus, 'prose': prose, 'annotation': annotation,
                        'sentence-help': help_output, 'curriculum': bindings}.items():
        review = {'approved': True, 'issues': []}
        save(run / 'agents' / name / 'result.json', value)
        save(run / 'agents' / name / 'meta.json', {'return_code': 0})
        save(run / 'agents' / (name + '-review') / 'result.json', review)
        save(run / 'agents' / (name + '-review') / 'meta.json',
             {'return_code': 0, 'model': 'test', 'effort': 'test', 'fingerprint': 'test'})
        report['stages'][name] = {'proposal_job': name, 'review_job': name + '-review',
                                  'output_digest': digest(value), 'review_digest': digest(review), 'approved': True}
    report['stages']['dictionary'] = {'reused': True, 'approved': True,
        'registry_digest': digest(publication.relevant_entries(chapter, dictionary._registry(dictionary.WORDS), dictionary._registry(dictionary.GRAMMAR)))}
    for name, value in {'chapter.json': chapter, 'dictionary-delta.json': {'words': [], 'grammar': []},
                        'sentence-breakdowns.json': help_data, 'source-plan.json': bound, 'lexical-plan.json': focus}.items():
        save(run / name, value)
        report['artifacts'][name] = sources.sha((run / name).read_bytes())
    save(run / 'report.json', report)
    return report


def test_reviewed_artifacts_must_match_actual_proposals_even_if_hash_updated(tmp_path):
    from pipeline.korean_agent_harness import read, save
    report = make_reviewed_run(tmp_path)
    publication.verify_run(tmp_path)
    chapter = read(tmp_path / 'chapter.json')
    chapter['segments'][0]['meaning_en'] = 'unreviewed meaning'
    save(tmp_path / 'chapter.json', chapter)
    report['artifacts']['chapter.json'] = sources.sha((tmp_path / 'chapter.json').read_bytes())
    save(tmp_path / 'report.json', report)
    with pytest.raises(ValueError, match='differs from reviewed proposals'):
        publication.verify_run(tmp_path)


def test_review_rejection_cannot_be_published(tmp_path):
    from pipeline.korean_agent_harness import save
    report = make_reviewed_run(tmp_path)
    rejected = {'approved': False, 'issues': ['unsupported source detail']}
    save(tmp_path / 'agents/prose-review/result.json', rejected)
    report['stages']['prose']['review_digest'] = digest(rejected)
    save(tmp_path / 'report.json', report)
    with pytest.raises(ValueError, match='no approved completed evidence'):
        publication.verify_run(tmp_path)


@pytest.mark.parametrize('changed', [False, True])
def test_publication_binds_reviewed_linguistic_reference(tmp_path, monkeypatch, changed):
    make_reviewed_run(tmp_path)
    reference = tmp_path / 'linguistic-reference.json'
    reference.write_bytes(publication.LINGUISTIC_REFERENCE.read_bytes() + (b'\n' if changed else b''))
    monkeypatch.setattr(publication, 'LINGUISTIC_REFERENCE', reference)
    if changed:
        with pytest.raises(ValueError, match='policy is stale'):
            publication.verify_run(tmp_path)
    else:
        publication.verify_run(tmp_path)


def test_form_chain_needs_word_base_but_plain_grammar_tap_is_valid(tmp_path):
    chapter = manual_chapter()
    dictionary.build_assets(chapter, tmp_path, write=False)
    segment = chapter['segments'][7]
    segment['lexical'] = {'kind': 'grammar', 'id': segment['form_steps'][-1]['grammar_entry_ids'][0]}
    with pytest.raises(ValueError, match='attested lexical base'):
        dictionary.build_assets(chapter, tmp_path, write=False)


def test_unannotatable_prose_returns_to_prose_instead_of_inventing_ids(tmp_path):
    from pipeline.korean_agent_harness import UnannotatableProseError
    class Runner:
        def __init__(self):
            self.jobs = []
        async def call(self, job, *args, **kwargs):
            self.jobs.append(job)
            return {'title': '제목', 'text': 'literary word', 'length_reason_en': 'Test editorial decision'}
    runner = Runner()
    harness = KoreanHarness(tmp_path, 1, runner=runner)
    def reject(_):
        raise UnannotatableProseError('ordinary word absent')
    with pytest.raises(UnannotatableProseError):
        asyncio.run(harness.stage('prose', 'task', 'prose', reject, {}))
    assert runner.jobs == ['prose-0']
    assert harness.stages == {}


def test_approval_with_remaining_issues_is_not_approval(tmp_path):
    class Runner:
        async def call(self, job, *args, **kwargs):
            if 'review' in job:
                return {'approved': True, 'issues': ['unresolved complete meaning']}
            return {'title': '제목', 'text': 'text', 'length_reason_en': 'Test editorial decision'}
    harness = KoreanHarness(tmp_path, 1, runner=Runner())
    with pytest.raises(ValueError, match='failed review'):
        asyncio.run(harness.stage('prose', 'task', 'prose', lambda _: None, {}))
    assert harness.stages == {}


def test_reconstruction_error_identifies_missing_whitespace_without_changing_text():
    segments = [{'text': '한 문장.'}, {'text': '다음 문장.'}]
    with pytest.raises(ValueError, match='Unicode offset 5'):
        contracts.check_reconstruction(segments, '한 문장.\n다음 문장.')
    contracts.check_reconstruction([segments[0], {'text': '\n'}, segments[1]], '한 문장.\n다음 문장.')


def test_dictionary_rejects_same_length_unattested_surface(tmp_path):
    chapter = manual_chapter()
    chapter['segments'][0]['text'] = '다른말'
    assert len(chapter['segments'][0]['text']) == len('옛날에')
    with pytest.raises(ValueError, match='do not reconstruct'):
        dictionary.build_assets(chapter, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('approved_review', [False, True])
def test_partial_stage_reuses_actual_cached_review_only_for_matching_context(tmp_path, approved_review):
    from pipeline.agent_harness import CodexRunner, digest as runner_digest
    from pipeline.korean_agent_harness import REVIEW_POLICY, payload, save
    value = {'title': '제목', 'text': 'reviewed output', 'length_reason_en': 'Test editorial decision'}
    context = {'source': 'same source'}
    harness = KoreanHarness(tmp_path, 1, runner=CodexRunner(tmp_path, 'test-model', asyncio.Semaphore(1)))
    prompt = harness.policy + '\n' + REVIEW_POLICY + payload(stage='prose', task='task', task_inputs={}, context=context, output=value)
    fingerprint = runner_digest(prompt, contracts.schema_path('review').read_text(), 'test-model', 'high')
    fingerprint = runner_digest(fingerprint, 'offline', 'tool-profile-v1')
    save(tmp_path / 'agents/prose-4/result.json', value)
    save(tmp_path / 'agents/prose-review-4/result.json', {'approved': approved_review,
         'issues': [] if approved_review else ['Concrete wording correction']})
    save(tmp_path / 'agents/prose-review-4/meta.json', {'return_code': 0, 'fingerprint': fingerprint})
    save(tmp_path / 'agents/prose-3/result.json', value)
    save(tmp_path / 'agents/prose-review-3/result.json', {'approved': False, 'issues': ['Older stale finding']})
    save(tmp_path / 'agents/prose-review-3/meta.json', {'return_code': 0, 'fingerprint': 'obsolete-context'})
    original = harness.runner.call
    async def cache_only(job, *args, **kwargs):
        if not kwargs.get('cache_only'):
            if not approved_review and job == 'prose-5':
                assert 'Concrete wording correction' in args[0]
                return value
            if not approved_review and job == 'prose-review-5':
                return {'approved': True, 'issues': []}
            raise AssertionError('new model work required')
        return await original(job, *args, **kwargs)
    harness.runner.call = cache_only
    assert asyncio.run(harness.stage('prose', 'task', 'prose', lambda _: None, context)) == value
    assert harness.stages['prose']['proposal_job'] == ('prose-4' if approved_review else 'prose-5')
    with pytest.raises(AssertionError, match='new model work required'):
        asyncio.run(harness.stage('prose', 'task', 'prose', lambda _: None, {'source': 'changed source'}))


def test_chunk_assembly_preserves_text_and_rebases_exact_links():
    first = {'segments': [{'text': '가'}, {'text': ' '}],
             'grammar_links': [{'segment_index': 0, 'display_end_segment_index': 0}],
             'inflected_segment_indices': [0]}
    second = {'segments': [{'text': '나'}, {'text': '.'}],
              'grammar_links': [{'segment_index': 0, 'display_end_segment_index': -1}],
              'inflected_segment_indices': [0]}
    combined = contracts.combine_annotations([first, second], ['가 ', '나.'])
    assert ''.join(s['text'] for s in combined['segments']) == '가 나.'
    assert combined['grammar_links'] == [
        {'segment_index': 0, 'display_end_segment_index': 0},
        {'segment_index': 2, 'display_end_segment_index': -1}]
    assert combined['inflected_segment_indices'] == [0, 2]
    second['grammar_links'][0]['display_end_segment_index'] = 2
    with pytest.raises(ValueError, match='escapes its source'):
        contracts.combine_annotations([first, second], ['가 ', '나.'])


def test_sentence_chunks_include_every_separator_and_quoted_sentence():
    text = '아이가 왔습니다.\n"안녕하세요." 그는 웃었습니다.'
    chunks = contracts.annotation_chunks(text)
    assert ''.join(chunks) == text
    assert chunks[0].endswith('\n')
    assert chunks[1] == '"안녕하세요." '
    assert chunks[-1] == '그는 웃었습니다.'


def test_lexical_plan_reuses_names_and_only_exempts_essential_nonbeginner_words():
    from pipeline.korean_agent_harness import validate_focus
    words = dictionary._registry(dictionary.WORDS)
    catalog = contracts.lexical_catalog()
    profile = {'id': 'hong-gildong', 'headword': '홍길동', 'kind': 'proper_name',
               'aliases': ['홍길동', '길동'], 'role_en': 'central child'}
    servant = {'id': catalog['하인'][0]['id'], 'headword': '하인', 'kind': 'story_term',
               'aliases': ['하인'], 'role_en': 'his mother’s status causes the central conflict'}
    validate_focus({'entries': [profile, servant]}, words, catalog)
    with pytest.raises(ValueError, match='canonical NIKL identity'):
        validate_focus({'entries': [profile, {**servant, 'id': 'invented-servant'}]}, words, catalog)
    with pytest.raises(ValueError, match='Reuse the approved identity'):
        validate_focus({'entries': [{**profile, 'id': 'duplicate-child'}]}, words, catalog)
    beginner = {'id': '아버지/명', 'headword': '아버지', 'kind': 'story_term',
                'aliases': ['아버지'], 'role_en': 'father matters to the plot'}
    with pytest.raises(ValueError, match='beginner word cannot'):
        validate_focus({'entries': [beginner]}, {}, catalog)


def test_source_placeholder_notes_prevent_invented_given_names():
    from pipeline.korean_agent_harness import validate_focus
    notes = json.loads((sources.DIRECTORY / 'source-notes.json').read_text())
    good = notes['notes'][0]['profile']
    validate_focus({'entries': [good]}, {}, contracts.lexical_catalog(), notes)
    bad = {**good, 'headword': '홍뫼', 'aliases': ['홍뫼']}
    with pytest.raises(ValueError, match='literal placeholder name'):
        validate_focus({'entries': [bad]}, {}, contracts.lexical_catalog(), notes)


@pytest.mark.parametrize('actual', [2, None])
def test_optional_grammar_metadata_covers_every_occurrence_without_changing_entries(tmp_path, actual):
    chapter = manual_chapter()
    bindings = protocol_curriculum_bindings(chapter)
    advanced = chapter['grammar_links'][0]['entry_id']
    for binding in bindings['bindings']:
        if binding['kind'] == 'grammar' and binding['entry_id'] == advanced:
            binding.update(source_ids=['nikl2017-grammar-00063'] if actual else [],
                           equivalence='listed' if actual else 'unlisted',
                           optional_reason_en='A brief common pattern helps this scene; optional learning at Level 1.')
    from pipeline.korean_curriculum import evaluate_bindings
    chapter['curriculum'] = {'bindings': bindings,
                            'evaluation': evaluate_bindings(chapter, bindings)}
    registry = dictionary._registry(dictionary.GRAMMAR)
    original = copy.deepcopy(registry)
    _, asset = dictionary.build_assets(chapter, tmp_path, grammar_registry=registry, write=False)
    optional = [use for use in asset['occurrences'] if use['entry_id'] == advanced]
    assert len(optional) == sum(link['entry_id'] == advanced for link in chapter['grammar_links'])
    assert all(use['optional_for_level'] and use['matched_curriculum_level'] == actual
               and use['target_curriculum_level'] == 1 for use in optional)
    assert all(not use['optional_for_level'] and use['matched_curriculum_level'] == 1
               for use in asset['occurrences'] if use['entry_id'] != advanced)
    assert registry == original  # A contextual exception does not rewrite a lesson.


def test_changed_policy_repairs_latest_cached_draft_after_old_attempt_budget(tmp_path):
    from pipeline.korean_agent_harness import save
    draft = {'title': '제목', 'text': '아이가 왔습니다.', 'length_reason_en': 'A coherent brief scene.'}
    save(tmp_path / 'agents/prose-8/result.json', draft)
    save(tmp_path / 'agents/prose-review-8/result.json', {'approved': False, 'issues': ['Old policy']})
    class Runner:
        def __init__(self): self.jobs = []
        async def call(self, job, prompt, *args, **kwargs):
            self.jobs.append(job)
            if job == 'prose-review-8':
                return {'approved': False, 'issues': ['A concrete wording repair.']}
            if job == 'prose-9':
                assert 'A concrete wording repair.' in prompt
                return draft
            if job == 'prose-review-9':
                return {'approved': True, 'issues': []}
            raise AssertionError(job)
    runner = Runner()
    harness = KoreanHarness(tmp_path, 1, runner=runner)
    assert asyncio.run(harness.stage('prose', 'Current policy', 'prose', check_prose, {})) == draft
    assert runner.jobs == ['prose-review-8', 'prose-9', 'prose-review-9']


def test_optional_grammar_still_requires_independent_whole_chapter_review(tmp_path):
    from pipeline.korean_agent_harness import UnannotatableProseError
    chapter = manual_chapter()
    bindings = protocol_curriculum_bindings(chapter)
    for binding in bindings['bindings']:
        if binding['kind'] == 'grammar':
            binding.update(source_ids=['nikl2017-grammar-00063'],
                           optional_reason_en='Synthetic implausible exception for rejection testing.')
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            if job == 'curriculum-0': return bindings
            if job == 'curriculum-review-0':
                assert 'computed_curriculum_evaluation' in prompt
                assert 'above_level_grammar_occurrences' in prompt
                return {'approved': False, 'issues': ['Optional labels do not justify this chapter depending on many advanced constructions. Revise prose.']}
            if job == 'curriculum-1':
                assert 'many advanced constructions' in prompt
                return {**bindings, 'prose_revision_reason_en': 'Simplify the core constructions across the chapter.'}
            raise AssertionError(job)
    from pipeline.korean_curriculum import evaluate_bindings
    def check(value):
        if value['prose_revision_reason_en']:
            raise UnannotatableProseError(value['prose_revision_reason_en'])
        evaluate_bindings(chapter, value)
    harness = KoreanHarness(tmp_path, 1, runner=Runner())
    with pytest.raises(UnannotatableProseError, match='Simplify the core'):
        asyncio.run(harness.stage('curriculum', 'Review the whole chapter honestly.',
            'curriculum', check, {'chapter': chapter}))
    assert 'curriculum' not in harness.stages


def test_unplanned_name_error_supplies_catalog_evidence_without_creating_exemptions():
    from pipeline.korean_agent_harness import annotation_requests
    value = {'segments': [{'text': '조선에', 'type': 'word', 'lemma': '조선',
        'lexical_id': 'invented-name', 'lexical_kind': 'proper_name'}], 'grammar_links': []}
    with pytest.raises(ValueError, match='조선05/고'):
        annotation_requests(value, contracts.lexical_catalog(), {'entries': []}, {})
    value['segments'][0].update(lexical_id='조선05/고', lexical_kind='vocabulary')
    requests, _ = annotation_requests(value, contracts.lexical_catalog(), {'entries': []}, {})
    assert requests['조선05/고']['kind'] == 'word'


def test_complete_construction_can_start_at_an_uninflected_prefix(tmp_path):
    chapter = {'number': 1, 'title': 'Negative phrase', 'text': '안 들었습니다.',
        'segments': [
            {'text': '안', 'type': 'word', 'meaning_en': 'not',
             'lexical': {'id': 'negative-word', 'kind': 'vocabulary'}},
            {'text': ' ', 'type': 'punctuation', 'meaning_en': ''},
            {'text': '들었습니다', 'type': 'word', 'meaning_en': 'heard',
             'lexical': {'id': 'hear-word', 'kind': 'vocabulary'}, 'form_steps': [
                 {'form': '들었다', 'reading': '', 'label': 'Past', 'meaning_en': 'heard',
                  'grammar_entry_ids': ['past']},
                 {'form': '들었습니다', 'reading': '', 'label': 'Formal polite', 'meaning_en': 'heard',
                  'grammar_entry_ids': ['formal']}]},
            {'text': '.', 'type': 'punctuation', 'meaning_en': ''}],
        'grammar_links': [
            {'segment_index': 0, 'entry_id': 'negation', 'context_en': 'Negates hearing.',
             'display_form': '안 들었습니다', 'display_meaning_en': 'did not hear', 'display_end_segment_index': 2},
            {'segment_index': 2, 'entry_id': 'past', 'context_en': 'Past hearing.'},
            {'segment_index': 2, 'entry_id': 'formal', 'context_en': 'Formal polite statement.'}],
        'form_audit': {'reviewed': True, 'inflected_segment_indices': [2]}}
    words = {identity: {'id': identity, 'kind': 'word', 'headword': headword,
                       'definition_en': meaning} for identity, headword, meaning in
             [('negative-word', '안', 'not'), ('hear-word', '듣다', 'to hear')]}
    grammar = {identity: {'id': identity} for identity in ('negation', 'past', 'formal')}
    _, asset = dictionary.build_assets(chapter, tmp_path, word_registry=words,
                                      grammar_registry=grammar, write=False)
    phrase = asset['occurrences'][0]
    assert phrase['surface'] == '안'  # Tap boundaries stay unchanged.
    assert phrase['display_form'] == '안 들었습니다'
    assert phrase['display_meaning_en'] == 'did not hear'
    # A source mismatch still fails; prefix support is not an unchecked span.
    chapter['grammar_links'][0]['display_form'] = '들었습니다'
    with pytest.raises(ValueError, match='construction stage'):
        dictionary.build_assets(chapter, tmp_path, word_registry=words,
                                grammar_registry=grammar, write=False)


def test_review_payload_keeps_unique_inputs_and_does_not_duplicate_context():
    from pipeline.korean_agent_harness import review_task, payload
    prompt = 'Instructions.' + payload(source={'text': 'same source'}, words=['one'], limit=10)
    prompt += ' End instructions.' + payload(repair='Fix the identified error')
    instructions, unique = review_task(prompt, {'source': {'text': 'same source'}, 'approved_words': ['one']})
    assert instructions == 'Instructions. End instructions.'
    assert unique == {'limit': 10, 'repair': 'Fix the identified error'}
    # A different source is evidence, even with the same field name.
    _, unique = review_task(payload(source='different'), {'source': 'original'})
    assert unique == {'source': 'different'}


def test_review_dedup_does_not_confuse_equal_scalar_values_with_equal_roles():
    from pipeline.korean_agent_harness import review_task, payload
    _, unique = review_task(payload(target_level=1, pending=[]), {'story_term_budget': 1, 'other': []})
    assert unique == {'target_level': 1, 'pending': []}


def test_annotation_resume_recovers_reviewed_assembly_and_preserves_issues(tmp_path):
    from pipeline.korean_agent_harness import annotation_reuse_candidate, normalize_existing, save
    chapter = manual_chapter()
    value = normalize_existing(chapter, dictionary._registry(dictionary.WORDS))
    for job, approved, ended in [
        ('annotation-5', True, '2026-10-02T01:00:00Z'),
        ('annotation-revision1-0', True, '2026-10-02T02:00:00Z'),
        ('annotation-revision2-0', False, '2026-10-02T03:00:00Z'),
    ]:
        directory = tmp_path / 'agents' / job
        save(directory / 'result.json', value)
        save(directory / 'meta.json', {'return_code': 0, 'kind': 'annotation_assembly',
                                      'chunks': [{'text': chapter['text']}]})
        prefix, attempt = job.rsplit('-', 1)
        review = tmp_path / 'agents' / f'{prefix}-review-{attempt}'
        save(review / 'result.json', {'approved': approved, 'issues': [] if approved else ['Wrong contextual meaning.']})
        save(review / 'meta.json', {'ended_at': ended})
    candidate = annotation_reuse_candidate(tmp_path)
    assert candidate[0] == 'annotation-revision1-0'
    assert candidate[1]['text'] == chapter['text']
    # An incomplete chunk job cannot replace full assembled evidence.
    save(tmp_path / 'agents/annotation-revision2-0-chunk-001-0/meta.json', {'return_code': 0})
    assert annotation_reuse_candidate(tmp_path)[0] == candidate[0]
    for job in ('annotation-5', 'annotation-revision1-0'):
        (tmp_path / 'agents' / job / 'meta.json').unlink()
    candidate = annotation_reuse_candidate(tmp_path)
    assert candidate[0] == 'annotation-revision2-0'
    assert candidate[2]['issues'] == ['Wrong contextual meaning.']


def test_semantic_form_repair_preserves_neutral_root_and_other_occurrences(tmp_path):
    from pipeline.korean_agent_harness import normalize_existing
    raw = normalize_existing(manual_chapter(), dictionary._registry(dictionary.WORDS))
    target = next(i for i, s in enumerate(raw['segments'])
                  if s['text'] == '살았습니다')
    bad = copy.deepcopy(raw)
    bad['segments'][target]['form_steps'][-1]['meaning_en'] = 'live'
    roots = copy.deepcopy(dictionary._registry(dictionary.WORDS))
    class Runner:
        async def call(self, job, *args, **kwargs):
            if job == 'annotation-0':
                return bad
            if job == 'annotation-review-0':
                return {'approved': False, 'issues': ['Final observed form 살았습니다 loses past time.']}
            if job == 'annotation-1':
                assert 'loses past time' in args[0]
                return raw
            if job == 'annotation-review-1':
                return {'approved': True, 'issues': []}
            raise AssertionError(job)
    harness = KoreanHarness(tmp_path, 1, runner=Runner())
    result = asyncio.run(harness.stage('annotation', 'Preserve complete contextual meanings.',
        'annotation', lambda value: contracts.check_reconstruction(
            value['segments'], manual_chapter()['text']), {'words': list(roots.values())}))
    assert result == raw
    assert result['segments'][target]['form_steps'][-1]['meaning_en'] != 'live'
    assert result['segments'][target]['form_steps'][0] == raw['segments'][target]['form_steps'][0]
    assert harness.words == roots
    assert harness.stages['annotation']['review_job'] == 'annotation-review-1'


@pytest.mark.parametrize('level', [2, 3, 4, 5, 6])
def test_reviewed_run_binds_actual_target_and_source_identity(tmp_path, level):
    from pipeline.korean_agent_harness import read, save
    from pipeline.korean_levels import source_id
    report = make_reviewed_run(tmp_path, level=level)
    chapter, _, help_data, proof = publication.verify_run(tmp_path)
    assert chapter['target_level'] == level
    assert chapter['curriculum']['evaluation']['target_level'] == level
    assert proof['target_level'] == level
    assert all(row['source'] == source_id(chapter) for row in help_data['breakdowns'])
    report['target_level'] = 1
    save(tmp_path / 'report.json', report)
    with pytest.raises(ValueError, match='target differs'):
        publication.verify_run(tmp_path)


def test_higher_target_policy_is_explicit_and_does_not_relabel_beginner(tmp_path):
    from pipeline.korean_levels import source_id
    beginner = KoreanHarness(tmp_path / 'one', 1, runner=object())
    advanced = KoreanHarness(tmp_path / 'five', 1, runner=object(), level=5)
    assert 'RUN TARGET: TOPIK 5' in advanced.policy
    assert 'for a TOPIK 5 learner' in advanced.review_policy
    assert 'Do not merely relabel' in advanced.policy
    assert 'RUN TARGET:' not in beginner.policy
    assert source_id({'number': 1}) != source_id({'number': 1, 'target_level': 5})
    with pytest.raises(ValueError, match='level must be'):
        KoreanHarness(tmp_path / 'bad', 1, runner=object(), level=7)


def test_publication_preserves_other_editions_and_merges_exact_source_routes(tmp_path, monkeypatch):
    run = tmp_path / 'chapter-001'
    make_reviewed_run(run, level=2)
    from pipeline import korean_dictionary, korean_sentence_breakdowns
    original = publication.app_content.build_language
    observed = {}

    def capture(language, levels):
        entries = original(language, levels)
        assert {e['level'] for e in entries} == {1, 2}
        assets = publication.app_content.ASSET_ROOT
        words = json.loads((assets / 'usage_dictionary_ko.json').read_text())
        grammar = json.loads((assets / 'grammar_dictionary_ko.json').read_text())
        help_data = json.loads((assets / 'korean_sentence_breakdowns.json').read_text())
        assert set(words['sources']) == set(grammar['sources']) == set(help_data['sources'])
        assert {row['level'] for row in words['sources'].values()} == {'TOPIK 1', 'TOPIK 2'}
        assert len({row['id'] for row in words['entries']}) == len(words['entries'])
        for entry in entries:
            for chapter in entry['chapters']:
                data = json.loads((assets / chapter['annotationAsset'].removeprefix('assets/')).read_text())
                assert all(s['target_curriculum_level'] == entry['level'] for s in data['segments'])
        observed['checked'] = True
        return entries

    before = (publication.CONTENT / 'l1.annotations.json').read_bytes()
    monkeypatch.setattr(publication.app_content, 'build_language', capture)
    result = publication.publish(tmp_path, promote=False)
    assert result['level'] == 2
    assert observed['checked']
    assert (publication.CONTENT / 'l1.annotations.json').read_bytes() == before


def test_preparation_checkpoint_stops_before_annotation_and_cannot_publish(tmp_path, monkeypatch):
    from pipeline.korean_agent_harness import read
    fixture = tmp_path / 'fixture'
    make_reviewed_run(fixture, level=3)
    prepared = tmp_path / 'prepared' / 'chapter-001'
    harness = KoreanHarness(prepared, 1, runner=object(), level=3, stop_after='prose')
    stages = []

    async def reviewed_stage(name, prompt, schema, check, context, **kwargs):
        stages.append(name)
        value = read(fixture / 'agents' / name / 'result.json')
        check(value)
        harness.stages[name] = {'approved': True}
        return value

    monkeypatch.setattr(harness, 'stage', reviewed_stage)
    result = asyncio.run(harness.run())
    assert stages == ['plan', 'lexical-plan', 'prose']
    assert result['status'] == 'prepared'
    assert result['target_level'] == 3
    assert read(prepared / 'preparation.json')['prose']['text']
    assert not (prepared / 'report.json').exists()
    with pytest.raises(ValueError, match='No accepted'):
        publication.publish(prepared.parent, promote=False)
    assert KoreanHarness(prepared, 1, runner=object(), level=3).stop_after is None


def test_annotation_batches_preserve_sentences_separators_and_long_clauses():
    text = '첫 문장입니다.\n\n두 번째입니다. 세 번째 문장입니다! 마지막입니다.'
    sentences = contracts.annotation_chunks(text)
    batches = contracts.annotation_chunks(text, batch_characters=len(sentences[0] + sentences[1]))
    assert len(batches) < len(sentences)
    assert ''.join(batches) == text
    assert batches[0] == sentences[0] + sentences[1]
    positions = {0}
    for sentence in sentences:
        positions.add(max(positions) + len(sentence))
    offset = 0
    for batch in batches:
        offset += len(batch)
        assert offset in positions
    # The budget controls job grouping, never truncates or splits a sentence.
    assert contracts.annotation_chunks(text, batch_characters=1) == sentences
    with pytest.raises(ValueError, match='nonnegative'):
        contracts.annotation_chunks(text, batch_characters=-1)


def test_batched_assembly_is_replayed_and_rejects_a_changed_partition_policy(tmp_path):
    from pipeline.korean_agent_harness import read, save, normalize_existing
    report = make_reviewed_run(tmp_path, level=4)
    chapter = read(tmp_path / 'chapter.json')
    raw = normalize_existing(chapter, dictionary._registry(dictionary.WORDS))
    job = 'annotation'
    chunk = job + '-chunk-001-0'
    save(tmp_path / 'agents' / chunk / 'result.json', raw)
    save(tmp_path / 'agents' / chunk / 'meta.json', {'return_code': 0})
    assembly = {'return_code': 0, 'kind': 'annotation_assembly',
        'batch_characters': len(chapter['text']),
        'chunks': [{'job': chunk, 'text': chapter['text'], 'digest': digest(raw)}]}
    save(tmp_path / 'agents' / job / 'meta.json', assembly)
    publication.verify_run(tmp_path)
    assembly['batch_characters'] = 1
    save(tmp_path / 'agents' / job / 'meta.json', assembly)
    with pytest.raises(ValueError, match='chunks do not match'):
        publication.verify_run(tmp_path)


def test_lexical_search_candidates_preserve_homonyms_without_claiming_grades():
    from pipeline.korean_agent_harness import lexical_candidates
    candidates = lexical_candidates(['쉬다', '쉬다', 'not a dictionary headword'], contracts.lexical_catalog())
    assert {row['id'] for row in candidates} == {'쉬다03/동', '쉬다04/동'}
    assert len(candidates) == 2
    assert all(set(row) == {'id', 'headword', 'pos', 'meaning'} for row in candidates)
    raw = contracts.lexical_catalog()['자기']
    assert {row['pos'] for row in lexical_candidates(['자기'], {'자기': raw})} == {'대', '명'}


def test_repeated_complete_forms_report_the_real_error_without_inventing_stems(tmp_path):
    chapter = manual_chapter()
    dictionary.build_assets(chapter, tmp_path, write=False)
    steps = chapter['segments'][7]['form_steps']
    steps.append(copy.deepcopy(steps[-1]))
    with pytest.raises(ValueError, match='distinct complete form.*not a repeated stage.*bare-stem'):
        dictionary.build_assets(chapter, tmp_path, write=False)
    steps.pop()
    dictionary.build_assets(chapter, tmp_path, write=False)


def test_curriculum_checkpoint_defers_dictionary_until_shared_entries_are_ready(tmp_path, monkeypatch):
    from pipeline.korean_agent_harness import read
    fixture = tmp_path / 'fixture'
    make_reviewed_run(fixture, level=3)
    prepared = tmp_path / 'prepared' / 'chapter-001'
    class CandidateRunner:
        async def call(self, job, *args, **kwargs):
            if job == 'prose-readiness-review-revision0':
                return {'approved': True, 'issues': []}
            assert job == 'annotation-lexical-candidates-revision0'
            return {'headwords': []}
    harness = KoreanHarness(prepared, 1, runner=CandidateRunner(), level=3, stop_after='curriculum')
    stages = []
    async def reviewed_stage(name, prompt, schema, check, context, **kwargs):
        stages.append(name)
        value = read(fixture / 'agents' / name / 'result.json')
        check(value)
        harness.stages[name] = {'approved': True}
        return value
    monkeypatch.setattr(harness, 'stage', reviewed_stage)
    result = asyncio.run(harness.run())
    assert stages == ['plan', 'lexical-plan', 'prose', 'annotation', 'curriculum']
    assert result['checkpoint'] == 'curriculum'
    assert result['chapter']['curriculum']['evaluation']['target_level'] == 3
    assert not (prepared / 'report.json').exists()
    with pytest.raises(ValueError, match='No accepted'):
        publication.publish(prepared.parent, promote=False)


@pytest.mark.parametrize('repaired', [False, True])
def test_partial_review_repair_recovers_new_proposals_not_rejected_data(tmp_path, repaired):
    from pipeline.korean_agent_harness import read, save, normalize_existing
    fixture = tmp_path / 'fixture'
    make_reviewed_run(fixture)
    chapter = read(fixture / 'chapter.json')
    raw = normalize_existing(chapter, dictionary._registry(dictionary.WORDS))
    chunks = contracts.slice_annotations(raw, contracts.annotation_chunks(chapter['text']))
    run = tmp_path / 'run'
    candidate = copy.deepcopy(chunks[0])
    if repaired:
        next(s for s in candidate['segments'] if s['type'] == 'word')['meaning_en'] = 'improved contextual meaning'
    save(run / 'agents/annotation-1-chunk-001-0/result.json', candidate)
    save(run / 'agents/annotation-1-chunk-001-0/meta.json', {'return_code': 0})
    class Runner:
        def __init__(self): self.jobs = []
        async def call(self, job, *args, **kwargs):
            self.jobs.append(job)
            if job == 'annotation-review-0':
                value = {'approved': False, 'issues': ['Clarify the first sentence contextual meaning.']}
            elif '-review-' in job:
                value = {'approved': True, 'issues': []}
            elif job.endswith('-repair-plan'):
                value = {'repairs': [{'chunk_index': 1, 'issues': ['Clarify contextual meaning.']}],
                    'prose_revision_reason_en': '', 'dictionary_revision_entry_ids': []}
            elif '-chunk-' in job:
                number = int(job.split('-chunk-')[1].split('-')[0])
                value = copy.deepcopy(chunks[number - 1])
                if job.startswith('annotation-1-'):
                    next(s for s in value['segments'] if s['type'] == 'word')['meaning_en'] = 'improved contextual meaning'
            else:
                stage = job.rsplit('-', 1)[0]
                value = read(fixture / 'agents' / stage / 'result.json')
            save(run / 'agents' / job / 'result.json', value)
            save(run / 'agents' / job / 'meta.json', {'return_code': 0})
            return value
    runner = Runner()
    result = asyncio.run(KoreanHarness(run, 1, runner=runner).run())
    assert result['status'] == 'complete'
    assert 'annotation-review-1' in runner.jobs
    fresh = [j for j in runner.jobs if j.startswith('annotation-1-chunk-001-')]
    assert fresh == ([] if repaired else ['annotation-1-chunk-001-1'])


def test_primary_lexical_reference_distinguishes_expression_frame_from_lemma():
    from pipeline.korean_agent_harness import LEXICAL_REFERENCE, read
    note = next(n for n in read(LEXICAL_REFERENCE)['entries'] if n['sense_id'] == 'krdict-62210-32')
    assert note['headword'] == '나다'
    assert note['expression'] == '혼이 나다'
    assert note['meaning_en'] == 'to be scolded'
    assert note['entry_id'] in {e['id'] for e in contracts.lexical_catalog()[note['headword']]}
    assert not contracts.lexical_catalog().get(note['expression'])
    assert note['primary_url'].startswith('https://krdict.korean.go.kr/')


def test_story_dictionary_tag_does_not_exempt_an_ordinary_occurrence(tmp_path):
    from pipeline.korean_agent_harness import annotation_requests
    from pipeline.korean_readability import diagnostics
    from pipeline.korean_curriculum import evaluate_bindings
    words = dictionary._registry(dictionary.WORDS)
    raw = {'segments': [{'text': '벼슬', 'type': 'word', 'meaning_en': 'official post',
        'lemma': '벼슬', 'lexical_kind': 'vocabulary', 'lexical_id': '벼슬/명',
        'story_importance_en': '', 'form_steps': []}], 'grammar_links': [], 'inflected_segment_indices': []}
    requests, _ = annotation_requests(raw, contracts.lexical_catalog(), {'entries': []}, words)
    assert requests['벼슬/명']['kind'] == 'story_term'  # Preserve the immutable registry tag.
    chapter = contracts.canonical_annotation(raw, {'title': 'title', 'text': '벼슬'}, 1,
        sources.EDITION, {'beats': []}, {'entries': []}, level=5)
    assert diagnostics(chapter)['story_terms'] == []
    dictionary.build_assets(chapter, tmp_path, write=False)
    bindings = {'level_reason_en': 'One ordinary unlisted word.', 'prose_revision_reason_en': '',
        'bindings': [{'kind': 'vocabulary', 'entry_id': '벼슬/명', 'source_ids': [],
            'equivalence': 'unlisted', 'analysis_en': 'Approved historical noun absent from the baseline.', 'optional_reason_en': ''}]}
    grade = evaluate_bindings(chapter, bindings, level=5)
    assert grade['lexical_levels']['벼슬/명'] is None
    assert grade['extra_vocabulary_ratio'] == 1
    assert not grade['passes']  # The dictionary's old tag grants no exemption.
    assert dictionary.lexical_kind_matches('story_term', 'vocabulary')
    assert not dictionary.lexical_kind_matches('proper_name', 'vocabulary')


@pytest.mark.parametrize('accepted', [True, False])
def test_early_level_screen_repairs_overload_without_replacing_final_review(tmp_path, accepted):
    from pipeline.korean_agent_harness import UnannotatableProseError
    prose = {'title': 'title', 'text': 'source-aligned prose'}
    before = copy.deepcopy(prose)
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            assert job == 'prose-readiness-review-revision0'
            assert 'not final publication approval' in prompt
            assert 'few useful higher-level patterns are allowed' in prompt
            assert 'Unlisted patterns are not automatically advanced' in prompt
            assert kwargs['tool_profile'] == 'offline'
            return {'approved': accepted, 'issues': [] if accepted else ['Simplify this essential advanced construction.']}
    harness = KoreanHarness(tmp_path, 1, runner=Runner(), level=2)
    if accepted:
        asyncio.run(harness.check_prose_readiness(prose, 0))
    else:
        with pytest.raises(UnannotatableProseError, match='Early curriculum screen'):
            asyncio.run(harness.check_prose_readiness(prose, 0))
    assert prose == before
    assert harness.stages == {}  # Final occurrence-bound gates remain mandatory.


@pytest.mark.parametrize('with_reference', [True, False])
def test_curriculum_review_receives_only_applicable_primary_lexical_evidence(tmp_path, with_reference):
    import re
    from pipeline.korean_curriculum import vocabulary_context_candidates, evaluate_bindings
    lemma, identity, pos = ('나다', '나다01/동', '동사') if with_reference else ('아이', '아이01/명', '명사')
    source = next(e for e in vocabulary_context_candidates(lemma)
        if re.sub(r'\d+$', '', e['source_fields']['어휘']) == lemma and e['source_fields']['품사'] == pos)
    chapter = {'text': lemma, 'segments': [{'type': 'word', 'text': lemma,
        'meaning_en': 'reviewed lexical meaning',
        'lexical': {'kind': 'vocabulary', 'id': identity}}], 'grammar_links': []}
    bindings = {'level_reason_en': 'A single in-level lexical identity.', 'prose_revision_reason_en': '',
        'bindings': [{'kind': 'vocabulary', 'entry_id': identity, 'source_ids': [source['id']],
            'equivalence': 'listed', 'analysis_en': 'The source lexeme and POS match.', 'optional_reason_en': ''}]}
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            if job == 'curriculum-0': return bindings
            assert job == 'curriculum-review-0'
            assert ('primary_lexical_reference' in prompt) == with_reference
            if with_reference:
                assert 'krdict-62210-32' in prompt
                assert 'not curriculum grades' in prompt
            return {'approved': True, 'issues': []}
    harness = KoreanHarness(tmp_path, 1, runner=Runner())
    result = asyncio.run(harness.stage('curriculum', 'Review lexical and curriculum evidence.',
        'curriculum', lambda value: evaluate_bindings(chapter, value),
        {'chapter': chapter, 'word_requests': {identity: {'headword': lemma, 'kind': 'word'}}}))
    assert result == bindings


@pytest.mark.parametrize('namespace', ['krdict', 'stdict'])
def test_primary_candidate_identity_is_copied_without_legacy_id_guessing(namespace):
    from pipeline.korean_agent_harness import annotation_requests, LexicalIdentityError
    identity = f'{namespace}-123/명'
    raw = {'segments': [{'type': 'word', 'text': '말', 'meaning_en': 'reviewed noun sense',
        'lemma': '말', 'lexical_kind': 'vocabulary', 'lexical_id': identity,
        'story_importance_en': '', 'form_steps': []}], 'grammar_links': [], 'inflected_segment_indices': []}
    catalog = {'말': [{'id': identity, 'headword': '말', 'pos': '명', 'meaning': 'reviewed noun sense', 'grade': None}]}
    requests, _ = annotation_requests(raw, catalog, {'entries': []}, {})
    assert requests == {identity: {'headword': '말', 'kind': 'word'}}
    raw['segments'][0]['lexical_id'] = '말/명'
    with pytest.raises(LexicalIdentityError, match='Copy the matching candidate ID verbatim') as failure:
        annotation_requests(raw, catalog, {'entries': []}, {})
    assert failure.value.headwords == {'말'}


@pytest.mark.parametrize('has_candidate', [False, True])
def test_missing_occurrence_headword_requests_research_but_wrong_id_keeps_candidates(has_candidate):
    from pipeline.korean_agent_harness import annotation_requests, LexicalIdentityError, MissingLexicalIdentityError
    raw = {'segments': [{'type': 'word', 'text': '불평하다', 'meaning_en': 'complain',
        'lemma': '불평하다', 'lexical_kind': 'vocabulary', 'lexical_id': 'unverified-id',
        'story_importance_en': '', 'form_steps': []}], 'grammar_links': [], 'inflected_segment_indices': []}
    catalog = {'불평하다': [{'id': 'krdict-123/동', 'headword': '불평하다',
        'pos': '동', 'meaning': 'complain', 'grade': None}]} if has_candidate else {}
    with pytest.raises(LexicalIdentityError) as failure:
        annotation_requests(raw, catalog, {'entries': []}, {})
    assert failure.value.headwords == {'불평하다'}
    assert isinstance(failure.value, MissingLexicalIdentityError) == (not has_candidate)
    if has_candidate:
        assert 'krdict-123/동' in str(failure.value)
