import asyncio
import copy
import json
from pathlib import Path

import pytest

from pipeline import korean_contracts as contracts
from pipeline import korean_dictionary as dictionary
from pipeline import korean_publication as publication
from pipeline import korean_sources as sources
from pipeline.korean_agent_harness import KoreanHarness, digest, validate_delta


def manual_chapter():
    return json.loads(Path('tests/fixtures/korean_manual_annotations.json').read_text())['chapters'][0]


def test_form_reading_can_show_pronunciation_but_form_must_match_tap(tmp_path):
    chapter = manual_chapter()
    step = chapter['segments'][7]['form_steps'][-1]
    step['reading'] = '사랃씀니다'
    _, grammar = dictionary.build_assets(chapter, tmp_path, write=False)
    assert any(form['reading'] == '사랃씀니다' for form in grammar['forms'])
    step['form'] = '살았어요'
    with pytest.raises(ValueError, match='does not end at tap surface'):
        dictionary.build_assets(chapter, tmp_path, write=False)


def test_transformations_require_one_grammar_destination():
    from jsonschema import validate, ValidationError
    step = manual_chapter()['segments'][7]['form_steps'][0]
    validate(step, contracts.STEP)
    for ids in ([], ['past-ass-eoss', 'polite-seumnida']):
        with pytest.raises(ValidationError):
            validate({**step, 'grammar_entry_ids': ids}, contracts.STEP)


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
            return {'title': '제목', 'text': 'bad' if job.endswith('0') else 'fixed'}
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


def make_reviewed_run(run):
    from pipeline.korean_agent_harness import POLICY, normalize_existing, save
    chapter = manual_chapter()
    manifest, unit, source = sources.load_unit(1)
    plan = {'title': '제목', 'beats': [{'source_paragraph_index': 0, 'event_en': 'Family setup'}]}
    bound = contracts.bind_plan(plan, source, unit['start'])
    prose = {'title': '제목', 'text': chapter['text']}
    annotation = normalize_existing(chapter, dictionary._registry(dictionary.WORDS))
    focus = {'entries': [
        {'id': 'hong-gildong', 'headword': '홍길동', 'kind': 'proper_name', 'aliases': ['홍길동'], 'role_en': 'central child'},
        {'id': '벼슬/명', 'headword': '벼슬', 'kind': 'story_term', 'aliases': ['벼슬'], 'role_en': 'historical office'}]}
    chapter = contracts.canonical_annotation(annotation, prose, 1, sources.EDITION, bound, focus)
    old = json.loads(Path('tests/fixtures/korean_manual_breakdowns.json').read_text())
    rows = []
    for sentence in contracts.sentence_inventory(chapter['text']):
        match = next((b for b in old['breakdowns'] if b['start'] == sentence['start']), None)
        rows.append({**sentence, 'selected': bool(match), 'reason_en': 'test decision',
                     'translation_en': match['translation_en'] if match else '',
                     'parts': match['parts'] if match else []})
    help_output = {'sentences': rows}
    help_data = contracts.selected_breakdowns(help_output, chapter, dictionary.SOURCE)
    report = {'status': 'complete', 'number': 1, 'edition': sources.EDITION,
              'source_sha256': manifest['text_sha256'], 'source_notes_sha256': manifest.get('notes_sha256'), 'source_unit': unit,
              'policy_sha256': sources.sha(POLICY.read_bytes()), 'stages': {}, 'artifacts': {}}
    for name, value in {'plan': plan, 'lexical-plan': focus, 'prose': prose, 'annotation': annotation, 'sentence-help': help_output}.items():
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
            return {'title': '제목', 'text': 'literary word'}
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
            return {'title': '제목', 'text': 'text'}
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


def test_partial_stage_reuses_actual_cached_review_only_for_matching_context(tmp_path):
    from pipeline.agent_harness import CodexRunner, digest as runner_digest
    from pipeline.korean_agent_harness import REVIEW_POLICY, payload, save
    value = {'title': '제목', 'text': 'reviewed output'}
    context = {'source': 'same source'}
    harness = KoreanHarness(tmp_path, 1, runner=CodexRunner(tmp_path, 'test-model', asyncio.Semaphore(1)))
    prompt = harness.policy + '\n' + REVIEW_POLICY + payload(stage='prose', task='task', context=context, output=value)
    fingerprint = runner_digest(prompt, contracts.schema_path('review').read_text(), 'test-model', 'high')
    fingerprint = runner_digest(fingerprint, 'offline', 'tool-profile-v1')
    save(tmp_path / 'agents/prose-4/result.json', value)
    save(tmp_path / 'agents/prose-review-4/result.json', {'approved': True, 'issues': []})
    save(tmp_path / 'agents/prose-review-4/meta.json', {'return_code': 0, 'fingerprint': fingerprint})
    original = harness.runner.call
    async def cache_only(job, *args, **kwargs):
        if not kwargs.get('cache_only'):
            raise AssertionError('new model work required')
        return await original(job, *args, **kwargs)
    harness.runner.call = cache_only
    assert asyncio.run(harness.stage('prose', 'task', 'prose', lambda _: None, context)) == value
    assert harness.stages['prose']['proposal_job'] == 'prose-4'
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
    servant = {'id': 'servant-hain', 'headword': '하인', 'kind': 'story_term',
               'aliases': ['하인'], 'role_en': 'his mother’s status causes the central conflict'}
    validate_focus({'entries': [profile, servant]}, words, catalog)
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
