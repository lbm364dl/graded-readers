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


def grammar_entry():
    return {'id': 'test-pattern', 'title_en': 'Linking form', 'pattern': '-게',
            'explanation_en': 'This pattern links a description to an action.'}


def grammar_proposal(before=None):
    before = before or grammar_entry()
    return {'entries': [{**before, 'title_en': 'Adverbial or linking form',
                         'explanation_en': 'This form describes how an action happens or links an action to a following predicate when supported by the construction.'}],
            'references': [{'entry_id': before['id'],
                'url': 'https://krdict.korean.go.kr/kor/dicSearch/SearchView?ParaWordNo=15205',
                'paraphrase_en': 'The source attests the relevant connective use.'}]}


def grammar_chapter(text='가게 간다.'):
    return {'number': 1, 'segments': [{'type': 'word', 'text': text, 'meaning_en': 'go',
        'lexical': {'id': 'word/go', 'kind': 'word'},
        'form_steps': [{'form': text, 'label': 'Linking form', 'reading': '',
                        'meaning_en': 'go in this form', 'grammar_entry_ids': ['test-pattern']}]}],
        'grammar_links': [{'segment_index': 0, 'entry_id': 'test-pattern',
                           'context_en': 'Links the action to the following predicate.'}]}


def setup_grammar_revision(tmp_path, monkeypatch, *, runner=None):
    monkeypatch.setattr(dictionary, 'ROOT', tmp_path)
    monkeypatch.setattr(dictionary, 'GRAMMAR', tmp_path / 'grammar.json')
    old = grammar_entry()
    save(dictionary.GRAMMAR, {'reviewed': True, 'entries': [old, {
        'id': 'untouched', 'title_en': 'Untouched', 'pattern': '-고',
        'explanation_en': 'An unrelated existing lesson.'}]})
    source = tmp_path / 'content/korean/l1/chapter.annotations.json'
    save(source, {'chapters': [grammar_chapter()]})
    return old, source


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


def test_grammar_coverage_records_distinct_taps_steps_and_links_and_ignores_unmatched_whitespace(tmp_path, monkeypatch):
    monkeypatch.setattr(dictionary, 'ROOT', tmp_path)
    chapter = grammar_chapter()
    chapter['segments'].insert(0, {'text': ' ', 'meaning_en': 'space',
                                  'lexical': {'id': 'space', 'kind': 'word'}})
    chapter['segments'][1]['lexical'] = {'id': 'test-pattern', 'kind': 'grammar'}
    chapter['grammar_links'][0]['segment_index'] = 1
    save(tmp_path / 'content/korean/l1/a.annotations.json', {'chapters': [chapter]})
    draft = tmp_path / 'draft.annotations.json'
    save(draft, {'chapters': [grammar_chapter(), grammar_chapter()]})
    result = editorial.coverage({'test-pattern'}, [draft], 'grammar')
    assert {row['occurrence_kind'] for row in result['occurrences']} == {
        'grammar_tap', 'form_step', 'grammar_link'}
    assert len(result['occurrences']) == 7
    assert len(result['chapter_digests']) == 1
    assert result['published_files'] == [str(tmp_path / 'content/korean/l1/a.annotations.json')]


@pytest.mark.parametrize('concurrent_change', ['none', 'existing_source', 'new_source'])
def test_reviewed_grammar_revision_preserves_identity_and_requires_complete_current_coverage(
        tmp_path, monkeypatch, concurrent_change):
    old, source = setup_grammar_revision(tmp_path, monkeypatch)

    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            assert kwargs.get('reasoning_effort', 'low') == 'low'
            assert kwargs['tool_profile'] == 'research'
            if job == 'revision-0':
                return grammar_proposal(old)
            assert job == 'revision-review-0'
            assert 'use tools to check primary references' in prompt
            if concurrent_change == 'existing_source':
                save(source, {'chapters': [grammar_chapter('다르게 간다.')]})
            elif concurrent_change == 'new_source':
                save(tmp_path / 'content/korean/l2/new.annotations.json',
                     {'chapters': [grammar_chapter('새롭게 간다.')]})
            return {'approved': True, 'issues': []}

    if concurrent_change != 'none':
        with pytest.raises(ValueError, match='coverage changed'):
            asyncio.run(editorial.revise([old['id']], tmp_path / 'run', None,
                runner=Runner(), entry_kind='grammar'))
        return
    result = asyncio.run(editorial.revise([old['id']], tmp_path / 'run', None,
        runner=Runner(), entry_kind='grammar'))
    assert result == {'status': 'updated', 'entry_kind': 'grammar',
                      'entries': [old['id']], 'audited_occurrences': 2}
    current = dictionary._registry(dictionary.GRAMMAR)
    assert current[old['id']] == grammar_proposal(old)['entries'][0]
    assert current['untouched']['explanation_en'] == 'An unrelated existing lesson.'
    record = json.loads(dictionary.GRAMMAR.read_text())['grammar_revision_reviews'][0]
    assert record['coverage']['published_files'] == [str(source)]
    assert dictionary._registry(dictionary.GRAMMAR) == current


def test_grammar_predecessor_compatibility_is_chapter_scoped_and_history_is_verified(tmp_path, monkeypatch):
    from pipeline import korean_publication as publication
    old, source = setup_grammar_revision(tmp_path, monkeypatch)

    class Runner:
        async def call(self, job, prompt, *args, **kwargs):
            if job == 'revision-0':
                return grammar_proposal(old)
            return {'approved': True, 'issues': []}

    asyncio.run(editorial.revise([old['id']], tmp_path / 'run', None,
        runner=Runner(), entry_kind='grammar'))
    current = dictionary._registry(dictionary.GRAMMAR)
    chapter = grammar_chapter()
    words = {'word/go': {'id': 'word/go', 'headword': '가다', 'kind': 'word',
                         'definition_en': 'to go'}}
    old_digest = digest(publication.relevant_entries(chapter, words,
        {old['id']: old, 'untouched': current['untouched']}))
    assert publication.dictionary_digest_matches(chapter, old_digest, words, current)
    publication.merge_dictionary_delta(words, current,
        {'words': [], 'grammar': [old]}, chapter)
    assert current[old['id']] == grammar_proposal(old)['entries'][0]

    # A new chapter was not in the reviewed coverage: only its current entry is valid.
    new_chapter = grammar_chapter('새 문장이다.')
    unreviewed_old_digest = digest(publication.relevant_entries(new_chapter, words,
        {old['id']: old, 'untouched': current['untouched']}))
    assert not publication.dictionary_digest_matches(new_chapter, unreviewed_old_digest,
                                                      words, current)
    with pytest.raises(ValueError, match='overwrites'):
        publication.merge_dictionary_delta(words, current,
            {'words': [], 'grammar': [old]}, new_chapter)
    # Appending a new chapter later does not invalidate history for the unchanged old chapter.
    save(source, {'chapters': [chapter, new_chapter]})
    assert dictionary._registry(dictionary.GRAMMAR)[old['id']] == current[old['id']]
    assert publication.dictionary_digest_matches(chapter, old_digest, words, current)

    bad = json.loads(dictionary.GRAMMAR.read_text())
    bad['grammar_revision_reviews'][0]['proposal']['entries'][0]['pattern'] = 'changed'
    save(dictionary.GRAMMAR, bad)
    with pytest.raises(ValueError, match='matching independent review'):
        dictionary._registry(dictionary.GRAMMAR)
