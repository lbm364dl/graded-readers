import asyncio
import json

import pytest

from pipeline import component_words as words
from pipeline.usage_dictionary import decision_digest


def test_recursive_word_entries_stop_at_characters_and_publish_reproducibly(tmp_path, monkeypatch):
    corpus = dict(entries=[dict(id='parent', headword='甲乙丙丁', meaning_guide=dict(parts=[
        dict(text='甲乙丙', contribution_en='word component'), dict(text='丁', contribution_en='character')]))],
        occurrences=[dict(id='original')], reading_bindings=[dict(id='original-binding')])
    registry, guide_path = tmp_path / 'words.json', tmp_path / 'guides.json'
    seeds = []
    async def call(self, job, prompt, schema, effort, **kwargs):
        headword = '甲乙丙' if '"headword": "甲乙丙"' in prompt else '甲乙'
        seeds.append(headword)
        assert kwargs['tool_profile'] == 'offline'
        return dict(headword=headword, reading='test', kind='word', definition='test definition')
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    async def guide_update(dictionary, path, **kwargs):
        rows = []
        for item in words.guides.inputs(dictionary):
            headword = item['entry']['headword']
            parts = [headword[:-1], headword[-1]]
            rows.append(dict(entry_id=item['entry']['id'], structure='compositional',
                             explanation_en='Reusable word explanation', caveat_en='',
                             parts=[dict(text=p, contribution_en='meaning') for p in parts],
                             input_fingerprint=words.guides.input_fingerprint(item)))
        decisions = dict(reviewed=True, guides=rows, review_digest=decision_digest(rows))
        path.write_text(json.dumps(decisions))
        return words.guides.extend(dictionary, decisions)
    monkeypatch.setattr(words.guides, 'update', guide_update)
    result = asyncio.run(words.expand(corpus, update=True, registry_path=registry, guides_path=guide_path))
    assert {e['headword'] for e in result['entries']} == {'甲乙丙丁', '甲乙丙', '甲乙'}
    assert seeds == ['甲乙丙', '甲乙丙', '甲乙', '甲乙']  # propose/review once per word
    assert result['occurrences'] == corpus['occurrences']
    assert result['reading_bindings'] == corpus['reading_bindings']
    seeds.clear()
    assert asyncio.run(words.expand(corpus, registry_path=registry, guides_path=guide_path)) == result
    assert not seeds


def test_missing_word_fails_closed_without_editorial_update(tmp_path):
    corpus = dict(entries=[dict(headword='甲乙丙', id='parent', meaning_guide=dict(parts=[
        dict(text='甲乙', contribution_en='word'), dict(text='丙', contribution_en='char')]))])
    with pytest.raises(ValueError, match='rerun'):
        asyncio.run(words.expand(corpus, registry_path=tmp_path / 'absent'))
