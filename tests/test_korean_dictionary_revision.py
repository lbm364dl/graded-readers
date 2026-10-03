import asyncio
import copy
import json
from pathlib import Path

import pytest

from pipeline import korean_dictionary as dictionary
from pipeline import korean_dictionary_revision as editorial
from pipeline.korean_agent_harness import digest, save


def entry():
    return {'id': 'word/verb', 'headword': '보다', 'kind': 'word',
            'definition_en': 'to see; look at; watch'}


def proposal():
    return {'entries': [{**entry(), 'definition_en': 'to see, look at or watch; to regard or consider'}],
            'references': [{'entry_id': 'word/verb',
                'url': 'https://krdict.korean.go.kr/kor/dicSearch/SearchView?ParaWordNo=61190',
                'paraphrase_en': 'The same verb includes visual and judgment meanings.'}]}


def test_revision_preserves_identity_and_requires_primary_evidence():
    before = {'word/verb': entry()}
    editorial.check_revision(proposal(), before)
    bad = proposal()
    bad['entries'][0]['headword'] = 'different'
    with pytest.raises(ValueError, match='identities'):
        editorial.check_revision(bad, before)
    bad = proposal()
    bad['references'][0]['url'] = 'https://example.com/dictionary'
    with pytest.raises(ValueError, match='primary'):
        editorial.check_revision(bad, before)
    bad = proposal()
    bad['references'][0]['entry_id'] = 'unrelated'
    with pytest.raises(ValueError, match='every entry'):
        editorial.check_revision(bad, before)


def test_coverage_audits_all_published_levels_and_preserves_distinct_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(dictionary, 'ROOT', tmp_path)
    chapter = {'number': 1, 'segments': [
        {'text': '보다. ', 'meaning_en': 'see', 'lexical': {'id': 'word/verb'}},
        {'text': '보다.', 'meaning_en': 'regard', 'lexical': {'id': 'word/verb'}}],
        'grammar_links': []}
    for level in ('l1', 'l2'):
        save(tmp_path / f'content/korean/book/{level}.annotations.json', {'chapters': [chapter]})
    draft = tmp_path / 'draft.json'
    save(draft, {**chapter, 'segments': [{'text': '보고', 'meaning_en': 'consider, and',
                                        'lexical_id': 'word/verb'}]})
    result = editorial.coverage({'word/verb'}, draft)
    assert len(result['files']) == 3
    assert len(result['occurrences']) == 5
    assert [use['start'] for use in result['occurrences'][:2]] == [0, 4]


@pytest.mark.parametrize('concurrent_change', [False, True])
def test_only_independently_reviewed_revision_can_promote_and_replay(tmp_path, monkeypatch, concurrent_change):
    monkeypatch.setattr(dictionary, 'ROOT', tmp_path)
    monkeypatch.setattr(dictionary, 'WORDS', tmp_path / 'words.json')
    save(dictionary.WORDS, {'reviewed': True, 'entries': [entry()]})
    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            if job == 'revision-0':
                assert kwargs['tool_profile'] == 'research'
                return proposal()
            assert job == 'revision-review-0'
            assert kwargs['tool_profile'] == 'offline'
            if concurrent_change:
                save(dictionary.WORDS, {'reviewed': True, 'entries': [entry()], 'other_change': True})
            return {'approved': True, 'issues': []}
    if concurrent_change:
        with pytest.raises(ValueError, match='changed during'):
            asyncio.run(editorial.revise(['word/verb'], tmp_path / 'run', None, runner=Runner()))
        assert json.loads(dictionary.WORDS.read_text())['other_change']
        return
    result = asyncio.run(editorial.revise(['word/verb'], tmp_path / 'run', None, runner=Runner()))
    assert result['status'] == 'updated'
    assert dictionary._registry(dictionary.WORDS)['word/verb'] == proposal()['entries'][0]
    data = json.loads(dictionary.WORDS.read_text())
    data['entries'][0]['definition_en'] = 'An unreviewed change'
    save(dictionary.WORDS, data)
    with pytest.raises(ValueError, match='differs from independent review'):
        dictionary._registry(dictionary.WORDS)


def test_publication_reuses_reviewed_predecessor_without_restoring_it(tmp_path, monkeypatch):
    from pipeline import korean_publication as publication
    monkeypatch.setattr(dictionary, 'WORDS', tmp_path / 'words.json')
    old, new = entry(), proposal()['entries'][0]
    review = {'approved': True, 'issues': []}
    record = {'before_entries': [old], 'proposal': proposal(),
              'proposal_digest': digest(proposal()), 'review': review, 'review_digest': digest(review)}
    save(dictionary.WORDS, {'reviewed': True, 'entries': [new], 'revision_reviews': [record]})
    words, grammar = {old['id']: new}, {'lesson': {'id': 'lesson', 'definition_en': 'unchanged'}}
    chapter = {'segments': [{'type': 'word', 'lexical': {'id': old['id'], 'kind': 'word'}}],
               'grammar_links': [{'entry_id': 'lesson'}]}
    original_digest = digest(publication.relevant_entries(chapter, {old['id']: old}, grammar))
    assert publication.dictionary_digest_matches(chapter, original_digest, words, grammar)
    publication.merge_dictionary_delta(words, grammar, {'words': [old], 'grammar': []})
    assert words[old['id']] == new
    assert not publication.dictionary_digest_matches(chapter, 'arbitrary', words, grammar)
    changed_grammar = {'lesson': {**grammar['lesson'], 'definition_en': 'unreviewed'}}
    assert not publication.dictionary_digest_matches(chapter, original_digest, words, changed_grammar)
    with pytest.raises(ValueError, match='overwrites'):
        publication.merge_dictionary_delta(words, grammar,
            {'words': [{**old, 'definition_en': 'not in the reviewed history'}], 'grammar': []})
    bad = json.loads(dictionary.WORDS.read_text())
    bad['revision_reviews'][0]['review']['approved'] = False
    save(dictionary.WORDS, bad)
    with pytest.raises(ValueError, match='independent review'):
        publication.dictionary_digest_matches(chapter, original_digest, words, grammar)
