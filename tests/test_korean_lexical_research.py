import asyncio
import copy
import json

import pytest

from pipeline import korean_lexical_research as research
from pipeline.korean_agent_harness import save


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


def test_standard_dictionary_fallback_keeps_its_own_identity_namespace():
    value = proposal()
    value['entries'][0].update(id='stdict-123/동',
        primary_url='https://stdict.korean.go.kr/search/searchView.do?word_no=123')
    research.check_proposal(value, ['검증하다'])
    value['entries'][0]['id'] = 'krdict-123/동'
    with pytest.raises(ValueError, match='match a direct primary'):
        research.check_proposal(value, ['검증하다'])


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
