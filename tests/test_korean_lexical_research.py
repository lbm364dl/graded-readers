import asyncio
import copy
import json

import pytest
from jsonschema import ValidationError

from pipeline import korean_lexical_research as research
from pipeline.korean_agent_harness import save

STANDARD_EVIDENCE = research.standard_evidence


@pytest.mark.parametrize('complete', [False, True])
def test_supplied_primary_records_avoid_redundant_research_tools(tmp_path, monkeypatch, complete):
    monkeypatch.setattr(research, 'standard_evidence', lambda headword: {
        'headword_request': headword,
        'records': [{'text': '검증하다: verified direct dictionary content.'}] if complete else []})
    profiles = []
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            profiles.append(kwargs['tool_profile'])
            if complete:
                assert 'do not call tools' in prompt
            return {'approved': True, 'issues': []} if '-review-' in job else proposal()
    registry = tmp_path / 'lexemes.json'
    asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(), registry=registry))
    assert profiles == ['offline' if complete else 'research', 'offline']
    assert research.candidates(registry)


def test_targeted_research_receives_passage_context_and_binds_it_to_review(tmp_path):
    registry = tmp_path / 'lexemes.json'
    passage = '자료를 검증했다.'
    calls = []
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            calls.append(job)
            assert 'not exhaustive dictionary enrichment' in prompt
            assert 'do not demand unrelated homonyms' in prompt
            assert json.loads(prompt.split('\nINPUT:\n', 1)[1])['source_context'] == passage
            return {'approved': True, 'issues': []} if '-review-' in job else proposal()
    asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(),
        registry=registry, source_context=passage))
    assert len(calls) == 2
    assert research.candidates(registry)
    document = json.loads(registry.read_text())
    document['reviews'][0]['source_context'] = 'Changed usage context'
    save(registry, document)
    with pytest.raises(ValueError, match='source context changed'):
        research.candidates(registry)


@pytest.fixture(autouse=True)
def offline_primary_fixture(monkeypatch):
    monkeypatch.setattr(research, 'standard_evidence', lambda headword:
        {'headword_request': headword, 'records': []})
    monkeypatch.setattr(research, 'primary_record_evidence', lambda url:
        {'primary_url': url, 'text': '검증하다: verified test verb record.'})


def proposal():
    return {'entries': [{'id': 'krdict-123/동', 'headword': '검증하다', 'pos': '동',
        'meaning_en': 'to verify', 'primary_url': 'https://krdict.korean.go.kr/kor/dicSearch/SearchView?ParaWordNo=123',
        'evidence_en': 'An attested verb in the supplied test dictionary record.'}], 'unresolved': []}


@pytest.mark.parametrize('persistent', [False, True])
def test_primary_http_interruption_is_bounded_and_never_uses_partial_text(monkeypatch, persistent):
    from http.client import IncompleteRead
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            if persistent or len(calls) == 1:
                raise IncompleteRead(b'unverified partial dictionary content')
            return 'verified complete record'.encode()
    def open_url(request, **kwargs):
        calls.append(request)
        return Response()
    monkeypatch.setattr(research, 'urlopen', open_url)
    if persistent:
        with pytest.raises(IncompleteRead): research.read_primary('https://example.test/record')
    else:
        assert research.read_primary('https://example.test/record') == 'verified complete record'
    assert len(calls) == 2


@pytest.mark.parametrize('mode', ['single', 'paged', 'interrupted'])
def test_standard_retrieval_follows_real_pagination_and_keeps_completed_records(monkeypatch, mode):
    from urllib.parse import parse_qs, urlparse
    from http.client import IncompleteRead
    calls = []
    def fetch(request):
        if isinstance(request, str):
            page = int(parse_qs(urlparse(request).query).get('pageIndex', ['1'])[0])
            calls.append(('page', page))
            if page == 2 and mode == 'interrupted':
                raise IncompleteRead(b'partial next page')
            links = '<a onclick="fnSearch(2);return false;">2</a>' if mode != 'single' else ''
            numbers = ['1'] if page == 1 else ['1', '2']
            return links + ''.join(f'<a href="/search/searchView.do?word_no={n}&searchKeywordTo=3">word</a>' for n in numbers)
        number = parse_qs(request.data.decode())['word_no'][0]
        calls.append(('record', number))
        return f'<p>Verified record {number}</p>'
    monkeypatch.setattr(research, 'read_primary', fetch)
    result = STANDARD_EVIDENCE('a homonym')
    assert [r['text'] for r in result['records']] == (
        ['Verified record 1', 'Verified record 2'] if mode == 'paged' else ['Verified record 1'])
    assert calls.count(('record', '1')) == 1
    assert calls.count(('page', 2)) == (0 if mode == 'single' else 1)
    assert ('retrieval_error' in result) == (mode == 'interrupted')


@pytest.mark.parametrize('change', ['url', 'identity', 'coverage'])
def test_researched_identity_requires_primary_record_pos_and_exact_coverage(change):
    value = proposal()
    research.check_proposal(value, ['검증하다'])
    if change == 'url':
        value['entries'][0]['primary_url'] = 'https://example.com/?ParaWordNo=123'
    elif change == 'identity':
        value['entries'][0]['id'] = 'invented/동'
    else:
        value['entries'][0]['headword'] = 'unrequested'
    with pytest.raises(ValueError):
        research.check_proposal(value, ['검증하다'])


@pytest.mark.parametrize('change', ['invalid_pos', 'extra_property'])
def test_compiled_proposal_validator_keeps_schema_rejections(change):
    value = proposal()
    if change == 'invalid_pos':
        value['entries'][0]['pos'] = 'noun'
    else:
        value['entries'][0]['unreviewed_grade'] = 1
    with pytest.raises(ValidationError):
        research.check_proposal(value, ['검증하다'])


def test_uncertain_expression_can_remain_unresolved_without_fake_lemma():
    value = {'entries': [], 'unresolved': [{'headword': 'productive phrase',
        'reason_en': 'This is a productive pattern rather than an attested dictionary headword.'}]}
    research.check_proposal(value, ['productive phrase'])
    mixed = proposal()
    mixed['unresolved'] = [{'headword': '검증하다', 'reason_en':
        'A separate claimed usage remains unverified; this does not deny the attested verb.'}]
    research.check_proposal(mixed, ['검증하다'])
    mixed['unresolved'][0]['headword'] = '검증하다 (claimed usage)'
    with pytest.raises(ValueError, match='unexpected'):
        research.check_proposal(mixed, ['검증하다'])


@pytest.mark.parametrize('failing', [False, True])
def test_large_lexical_research_batches_preserve_coverage_and_drain_siblings(tmp_path, monkeypatch, failing):
    words = [f'word-{i:02}' for i in range(25)]
    started, finished = [], []
    active, peak = 0, 0
    async def batch(headwords, *args, **kwargs):
        nonlocal active, peak
        started.append(list(headwords))
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        finished.extend(headwords)
        if failing and words[0] in headwords:
            raise ValueError('Independent review rejected this batch')
        return {'status': 'reviewed', 'entries': list(headwords), 'unresolved': []}
    monkeypatch.setattr(research, '_research_batch', batch)
    operation = research.research(words, tmp_path, runner=object(), registry=tmp_path / 'registry.json')
    if failing:
        with pytest.raises(ValueError, match='Independent review rejected'):
            asyncio.run(operation)
    else:
        assert asyncio.run(operation)['entries'] == words
    assert sorted(finished) == words
    assert sorted(w for group in started for w in group) == words
    assert all(len(group) <= 8 for group in started)
    assert peak == 3


def test_standard_dictionary_fallback_keeps_its_own_identity_namespace():
    value = proposal()
    value['entries'][0].update(id='stdict-123/동',
        primary_url='https://stdict.korean.go.kr/search/searchView.do?word_no=123')
    research.check_proposal(value, ['검증하다'])
    value['entries'][0]['id'] = 'krdict-123/동'
    with pytest.raises(ValueError, match='match a direct primary'):
        research.check_proposal(value, ['검증하다'])


def test_direct_standard_snapshot_reaches_creator_and_exact_record_reviewer(tmp_path, monkeypatch):
    url = 'https://stdict.korean.go.kr/search/searchView.do?word_no=123'
    record = {'primary_url': url, 'text': '검증하다: directly retrieved verb definition.'}
    monkeypatch.setattr(research, 'standard_evidence', lambda word:
        {'headword_request': word, 'records': [record]})
    retrieved = []
    def retrieve(reference):
        retrieved.append(reference)
        return record
    monkeypatch.setattr(research, 'primary_record_evidence', retrieve)
    class Runner:
        async def call(self, job, *args, **kwargs):
            assert 'directly retrieved verb definition' in args[0]
            if '-review-' in job:
                assert kwargs['tool_profile'] == 'offline'
                assert 'proposal_records' in args[0]
                return {'approved': True, 'issues': []}
            value = proposal()
            value['entries'][0].update(id='stdict-123/동', primary_url=url)
            return value
    result = asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(),
        registry=tmp_path / 'registry.json'))
    assert result['entries'] == ['stdict-123/동']
    assert retrieved == [url]


def test_research_requires_independent_review_and_reuses_approved_identity(tmp_path):
    registry = tmp_path / 'lexemes.json'
    class Runner:
        def __init__(self): self.calls = []
        async def call(self, job, *args, **kwargs):
            self.calls.append((job, kwargs['tool_profile']))
            if '-review-' in job:
                assert 'verified test verb record' in args[0]
                return {'approved': True, 'issues': []}
            return proposal()
    runner = Runner()
    result = asyncio.run(research.research(['검증하다'], tmp_path / 'run', runner=runner, registry=registry))
    assert result['status'] == 'reviewed'
    assert [profile for _, profile in runner.calls] == ['research', 'offline']
    entry = research.candidates(registry)[0]
    assert entry['grade'] is None
    assert entry['id'] == 'krdict-123/동'
    before = len(runner.calls)
    assert asyncio.run(research.research(['검증하다'], tmp_path / 'run', runner=runner,
        registry=registry))['status'] == 'reused'
    assert len(runner.calls) == before
    document = json.loads(registry.read_text())
    document['reviews'][0]['proposal']['entries'][0]['meaning_en'] = 'tampered'
    save(registry, document)
    with pytest.raises(ValueError, match='matching independent review'):
        research.candidates(registry)


def test_research_reuse_distinguishes_requested_homonym_from_reviewed_spelling(tmp_path):
    registry = tmp_path / 'lexemes.json'
    class Runner:
        def __init__(self): self.calls = []
        async def call(self, job, *args, **kwargs):
            self.calls.append(job)
            if '-review-' in job:
                return {'approved': True, 'issues': []}
            return proposal()
    runner = Runner()
    asyncio.run(research.research(['검증하다'], tmp_path, runner=runner, registry=registry))
    scope = {'검증하다': [{'text': '검증했다', 'meaning_en': 'a newly requested use'}]}
    before = len(runner.calls)
    asyncio.run(research.research(['검증하다'], tmp_path, runner=runner, registry=registry,
        occurrence_requests=scope))
    assert len(runner.calls) == before + 2  # Spelling alone did not bypass research/review.
    before = len(runner.calls)
    assert asyncio.run(research.research(['검증하다'], tmp_path, runner=runner, registry=registry,
        occurrence_requests=scope))['status'] == 'reused'
    assert len(runner.calls) == before
    assert research.usage_evidence(scope, registry)[0]['requested_usages'] == scope
    assert research.usage_evidence({'검증하다': [{'text': '검증했다', 'meaning_en': 'different use'}]}, registry) == []
    related = research.usage_evidence({'검증하다': [{'text': '검증했다', 'meaning_en': 'different use'}]},
        registry, related_forms=True)
    assert related[0]['requested_usages'] == scope
    assert 'matching spelling does not approve the current meaning' in related[0]['scope']
    assert research.usage_evidence({'검증하다': [{'text': '검증한다', 'meaning_en': 'different use'}]},
        registry, related_forms=True) == []
    document = json.loads(registry.read_text())
    document['reviews'][-1]['occurrence_requests']['검증하다'][0]['meaning_en'] = 'tampered'
    save(registry, document)
    with pytest.raises(ValueError, match='occurrence scope changed'):
        research.candidates(registry)


def test_mixed_scope_reuse_records_only_newly_reviewed_headwords(tmp_path):
    registry = tmp_path / 'lexemes.json'
    class Runner:
        async def call(self, job, *args, **kwargs):
            if '-review-' in job:
                return {'approved': True, 'issues': []}
            inputs = json.loads(args[0].rsplit('\nINPUT:\n', 1)[1])
            result = {'entries': [], 'unresolved': []}
            for headword in inputs['headwords']:
                entry = proposal()['entries'][0]
                number = 123 if headword == '검증하다' else 124
                entry.update(headword=headword, id=f'krdict-{number}/동',
                    primary_url=f'https://krdict.korean.go.kr/kor/dicSearch/SearchView?ParaWordNo={number}')
                result['entries'].append(entry)
            return result
    runner = Runner()
    scopes = {h: [{'text': h, 'meaning_en': 'requested use'}] for h in ['검증하다', '확인하다']}
    asyncio.run(research.research(['검증하다'], tmp_path, runner=runner, registry=registry,
        occurrence_requests=scopes))
    asyncio.run(research.research(scopes, tmp_path, runner=runner, registry=registry,
        occurrence_requests=scopes))
    last = json.loads(registry.read_text())['reviews'][-1]
    assert last['headwords'] == ['확인하다']
    assert set(last['occurrence_requests']) == {'확인하다'}
    assert len(research.candidates(registry)) == 2


def test_rejected_research_is_repaired_before_promotion(tmp_path):
    class Runner:
        async def call(self, job, *args, **kwargs):
            if '-review-' in job:
                return ({'approved': False, 'issues': ['Verify the dictionary sense.']}
                    if job.endswith('-0') else {'approved': True, 'issues': []})
            return proposal()
    result = asyncio.run(research.research(['검증하다'], tmp_path / 'run', runner=Runner(),
        registry=tmp_path / 'lexemes.json'))
    assert result['status'] == 'reviewed'
    assert research.candidates(tmp_path / 'lexemes.json')[0]['grade'] is None


def test_unresolved_primary_references_reach_independent_review(tmp_path, monkeypatch):
    url = 'https://krdict.korean.go.kr/eng/dicSearch/SearchView?ParaWordNo=66315'
    seen = []
    monkeypatch.setattr(research, 'primary_record_evidence', lambda reference:
        seen.append(reference) or {'primary_url': reference, 'text': 'Actual verb record and sleep sense.'})
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            if '-review-' in job:
                inputs = json.loads(prompt.rsplit('\nINPUT:\n', 1)[1])
                records = inputs['primary_evidence'][-1]['proposal_records']
                assert records == [{'primary_url': url, 'text': 'Actual verb record and sleep sense.'}]
                return {'approved': True, 'issues': []}
            return {'entries': [], 'unresolved': [{'headword': '잠을 이루다',
                'reason_en': f'Expression under [이루다]({url}); not a standalone lemma. See also https://example.test/claim and https://krdict.korean.go.kr/search-results.'}]}
    result = asyncio.run(research.research(['잠을 이루다'], tmp_path / 'run',
        runner=Runner(), registry=tmp_path / 'lexemes.json'))
    assert result['status'] == 'reviewed'
    assert seen == [url]  # Neither external claims nor search pages are retrieved.
    assert research.candidates(tmp_path / 'lexemes.json') == []


def test_later_stages_receive_only_requested_vocabulary_usage_evidence(monkeypatch):
    calls = []
    def evidence(usages):
        calls.append(usages)
        return [{'reviewed': True}] if usages else []
    monkeypatch.setattr(research, 'usage_evidence', evidence)
    chapter = {'segments': [
        {'text': '검증했다', 'meaning_en': 'verified', 'lexical': {'kind': 'vocabulary', 'id': 'verb'}},
        {'text': '이름', 'meaning_en': 'name', 'lexical': {'kind': 'proper_name', 'id': 'name'}},
        {'text': '.', 'type': 'punctuation'}]}
    requests = {'verb': {'headword': '검증하다'}, 'name': {'headword': '이름'}}
    assert research.chapter_usage_evidence(chapter, requests) == [{'reviewed': True}]
    assert calls[-1] == {'검증하다': [{'text': '검증했다', 'meaning_en': 'verified'}]}
    assert research.chapter_usage_evidence(chapter, {'name': requests['name']}) == []
    assert calls[-1] == {}


def test_primary_attested_word_is_ordinary_unlisted_vocabulary_not_an_exemption(monkeypatch):
    from pipeline.korean_readability import diagnostics
    from pipeline.korean_curriculum import evaluate_bindings
    chapter = {'text': '검증하다', 'segments': [{'type': 'word', 'text': '검증하다',
        'meaning_en': 'to verify', 'lexical': {'kind': 'vocabulary', 'id': 'krdict-123/동'}}],
        'annotation_audit': {'all_reviewed': True}, 'grammar_links': []}
    monkeypatch.setattr(research, 'candidates', lambda: [{'id': 'krdict-123/동'}])
    assert diagnostics(chapter)['above_beginner_ratio'] == 1
    assert diagnostics(chapter)['story_terms'] == []
    bindings = {'level_reason_en': 'An ordinary unlisted word.', 'prose_revision_reason_en': '',
        'bindings': [{'kind': 'vocabulary', 'entry_id': 'krdict-123/동', 'source_ids': [],
            'equivalence': 'unlisted', 'analysis_en': 'Verified lexeme absent from the curriculum.', 'optional_reason_en': ''}]}
    result = evaluate_bindings(chapter, bindings, level=5)
    assert result['lexical_levels']['krdict-123/동'] is None
    assert result['extra_vocabulary_ratio'] == 1
    assert not result['passes']
    chapter['segments'][0]['lexical']['id'] = 'unchecked/동'
    with pytest.raises(ValueError, match='unresolved Korean vocabulary identity'):
        diagnostics(chapter)


@pytest.mark.parametrize('error', ['Research worker did not actually use web search', 'Worker used tools outside its research role: shell'])
def test_missing_search_retries_once_without_relaxing_capability(tmp_path, monkeypatch, error):
    monkeypatch.setattr(research, 'standard_evidence', lambda word: {'records': []})
    calls = []
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            calls.append((job, kwargs['tool_profile']))
            if len(calls) == 1:
                raise ValueError(error)
            if '-review-' in job:
                return {'approved': True, 'issues': []}
            assert job.endswith('-search-retry')
            assert 'Call web search before returning' in prompt
            return proposal()
    if error.startswith('Research worker'):
        result = asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(), registry=tmp_path/'registry.json'))
        assert result['status'] == 'reviewed'
        assert [profile for _, profile in calls] == ['research', 'research', 'offline']
    else:
        with pytest.raises(ValueError, match='outside'):
            asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(), registry=tmp_path/'registry.json'))
        assert len(calls) == 1


@pytest.mark.parametrize('rejected', [True, False])
def test_research_resume_uses_rejected_checkpoint_only_as_repair_context(tmp_path, monkeypatch, rejected):
    monkeypatch.setattr(research, 'standard_evidence', lambda word: {'records': []})
    key = research.fingerprint(['검증하다'])[:16]
    proposal_dir = tmp_path / 'agents' / f'lexical-research-{key}-3'
    review_dir = tmp_path / 'agents' / f'lexical-research-review-{key}-3'
    proposal_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    save(proposal_dir / 'result.json', proposal())
    save(review_dir / 'result.json', {'approved': not rejected,
        'issues': ['Repair the unsupported lexical claim'] if rejected else []})
    if rejected:
        adjudicated_dir = review_dir.with_name(review_dir.name + '-adjudication')
        adjudicated_dir.mkdir()
        save(adjudicated_dir/'result.json', {'approved': False, 'issues': ['Adjudicated repair requirement']})
    calls = []
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            calls.append(job)
            if '-review-' in job:
                return {'approved': True, 'issues': []}
            if rejected:
                assert 'Adjudicated repair requirement' in prompt
                assert 'Repair the unsupported lexical claim' not in prompt
            else:
                assert 'Repair the unsupported lexical claim' not in prompt
            return proposal()
    result = asyncio.run(research.research(['검증하다'], tmp_path,
        runner=Runner(), registry=tmp_path/'registry.json'))
    assert calls[0] == f'lexical-research-{key}-{4 if rejected else 0}'
    assert '-review-' in calls[1]  # Every resumed proposal still gets independent review.
    assert result['status'] == 'reviewed'


def test_research_review_distinguishes_unresolved_coverage_from_missing_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(research, 'standard_evidence', lambda word: {'records': []})
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            if '-review-' in job:
                assert 'Coverage is the union of entries and unresolved' in prompt
                assert 'Review whether its unresolved reason is justified' in prompt
                return {'approved': True, 'issues': []}
            return {'entries': [], 'unresolved': [{'headword': '큰', 'reason_en': 'An inflected adjective form, not a standalone headword.'}]}
    result = asyncio.run(research.research(['큰'], tmp_path, runner=Runner(), registry=tmp_path/'registry.json'))
    assert result['entries'] == []
    assert result['unresolved'][0]['headword'] == '큰'
    with pytest.raises(ValueError, match='Missing'):
        research.check_proposal({'entries': [], 'unresolved': []}, ['큰'])


def test_research_scope_explains_internal_dictionary_hyphen_without_collapsing_spacing(tmp_path, monkeypatch):
    monkeypatch.setattr(research, 'standard_evidence', lambda word: {'records': []})
    prompts = []
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            prompts.append(prompt)
            assert 'internal hyphen in a complete headword marks its compound components' in prompt
            assert 'Do not remove actual spaces, combine separate words' in prompt
            assert 'leading/trailing affix hyphens' in prompt
            return {'approved': True, 'issues': []} if '-review-' in job else proposal()
    asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(), registry=tmp_path/'registry.json'))
    assert len(prompts) == 2


@pytest.mark.parametrize('material', [False, True])
def test_lexical_objections_are_adjudicated_without_waiving_material_errors(tmp_path, monkeypatch, material):
    monkeypatch.setattr(research, 'standard_evidence', lambda word: {'records': []})
    calls = []
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            calls.append(job)
            if job.endswith('-adjudication'):
                assert 'Local validation already establishes exact requested-spelling coverage' in prompt
                assert 'wrong-sense' in prompt
                return {'approved': not material, 'issues': ['Wrong sense'] if material else []}
            if '-review-' in job:
                return {'approved': False, 'issues': ['Proposed objection']}
            return proposal()
    registry = tmp_path/'registry.json'
    if material:
        with pytest.raises(ValueError, match='Wrong sense'):
            asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(), registry=registry))
        assert not registry.exists()
        assert len([j for j in calls if j.endswith('-adjudication')]) == 4
    else:
        asyncio.run(research.research(['검증하다'], tmp_path, runner=Runner(), registry=registry))
        assert len(calls) == 3
        document = json.loads(registry.read_text())
        assert document['reviews'][0]['initial_review']['issues'] == ['Proposed objection']
        document['reviews'][0]['initial_review']['issues'] = ['Changed objection']
        save(registry, document)
        with pytest.raises(ValueError, match='initial objections'):
            research.candidates(registry)


@pytest.mark.parametrize('display,written', [('주제-넘다', '주제넘다'), ('다음-날', '다음날'), ('다음 날', '다음 날'), ('-하다', '-하다')])
def test_primary_heading_preserves_spaces_affixes_and_exact_record_identity(display, written):
    html = f'<li id="word_123"><span class="tit_b">{display}<span class="chi_info_list"></span></span><p>Different example headword</p></li>'
    fields = research.standard_record_fields(html, 123)
    assert fields == {'record_word_no': '123', 'display_headword': display, 'written_headword': written}
    assert research.standard_record_fields(html, 456) == {}
    assert research.standard_record_fields('<p>다음 날 example only</p>', 123) == {}


def test_primary_headword_guard_rejects_component_or_spacing_mismatch_but_not_compound_marker():
    value = proposal()
    entry = value['entries'][0]
    entry['headword'] = '주제넘다'
    record = {'primary_url': entry['primary_url'], 'display_headword': '주제-넘다', 'written_headword': '주제넘다'}
    research.check_primary_headwords(value, [record])
    entry['headword'] = '주제 넘다'
    with pytest.raises(ValueError, match='Preserve actual spaces'):
        research.check_primary_headwords(value, [record])
    record.update(display_headword='주제', written_headword='주제')
    with pytest.raises(ValueError, match='component records'):
        research.check_primary_headwords(value, [record])


def test_phrase_research_supplies_exact_catalog_components_without_guessing_inflections(tmp_path, monkeypatch):
    monkeypatch.setattr(research, 'standard_evidence', lambda word: {'records': []})
    catalog = {'띄다': [{'id': '띄다01/동', 'headword': '띄다', 'pos': '동', 'meaning': '눈에 ~', 'grade': 'C'}],
        '눈': [{'id': '눈01/명', 'headword': '눈', 'pos': '명', 'meaning': 'eye', 'grade': 'A'}]}
    monkeypatch.setattr(research.contracts, 'lexical_catalog', lambda: catalog)
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            inputs = json.loads(prompt.split('\nINPUT:\n', 1)[1])
            assert inputs['known_candidates']['띄다'] == catalog['띄다']
            assert '눈' not in inputs['known_candidates']  # 눈에 is not silently analyzed as 눈.
            assert inputs['known_candidates']['눈에'] == []
            assert 'not attestation of a standalone phrase' in prompt
            if '-review-' in job:
                return {'approved': True, 'issues': []}
            return {'entries': [], 'unresolved': [{'headword': '눈에 띄다', 'reason_en': 'A phrase using the supplied lexical component 띄다01/동; not a standalone headword.'}]}
    result = asyncio.run(research.research(['눈에 띄다'], tmp_path, runner=Runner(), registry=tmp_path/'registry.json'))
    assert result['entries'] == []
