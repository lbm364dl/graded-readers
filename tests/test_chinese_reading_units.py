import asyncio
import copy
import json

import pytest

from pipeline import chinese_reading_units as units
from pipeline.usage_dictionary import SOURCE


def test_hsk4_potential_complement_keeps_adverb_and_clause_particle_outside():
    source = units.ROOT / 'content/chinese/sanguoyanyi/hsk4.annotations.json'
    chapter = json.loads(source.read_text())['chapters'][0]
    segments = chapter['segments']
    assert [s['text'] for s in segments[1392:1397]] == ['城', '快', '守', '不住', '了']
    registry = json.loads((units.ROOT / 'content/lexicon/hsk4.reading-units.json').read_text())
    units.build(registry, source)
    predicate = next(u for u in registry['chapters']['1'] if u['text'] == '守不住')
    assert (predicate['first_segment'], predicate['last_segment']) == (1394, 1395)
    assert 'potential complement' in predicate['explanation_en']
    assert segments[1394]['character_hsk_level'] == 4


@pytest.fixture
def chapter():
    return json.loads(SOURCE.read_text())['chapters'][0]


def unit():
    return dict(first_segment=1, last_segment=3, text='看到了', pinyin='kàn dàole',
                meaning_en='saw', explanation_en='Seeing succeeded; 了 marks completion.')


def test_bound_predicate_preserves_lexical_segments(chapter):
    original = copy.deepcopy(chapter)
    units.validate_units(chapter, [unit()])
    assert chapter == original


def test_large_chapter_batches_preserve_global_indices_and_sentence_boundaries():
    chapter = {'segments': [
        {'text': text, 'type': 'punctuation' if text == '。' else 'word'}
        for _ in range(180) for text in ('看', '到', '了', '。')]}
    ranges = units.chapter_ranges(chapter)
    assert len(ranges) > 1
    assert [i for first, last in ranges for i in range(first, last)] == list(range(720))
    assert all(chapter['segments'][last - 1]['text'] == '。' for _, last in ranges)
    assert all(chapter['segments'][first]['text'] == '看' for first, _ in ranges)


def test_large_update_reviews_each_batch_and_reassembles_global_spans(tmp_path, monkeypatch):
    segments = [
        {'text': text, 'type': 'punctuation' if text == '。' else 'word',
         'pinyin': '', 'meaning_en': 'part'}
        for _ in range(180) for text in ('看', '到', '了', '。')]
    chapter = {'number': 1, 'text': ''.join(s['text'] for s in segments),
               'annotation_audit': {'all_reviewed': True}, 'segments': segments}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'level': 'hsk3', 'chapters': [chapter]}))
    original = source.read_bytes()
    calls = []

    async def call(self, name, prompt, schema, effort, *, tool_profile):
        assert tool_profile == 'offline'
        rows, _ = json.JSONDecoder().raw_decode(prompt.split('\nINPUT:\n')[1])
        first = rows[0]['index']
        assert rows[-1]['index'] >= first + 2
        calls.append((name, first))
        return {'units': [dict(unit(), first_segment=first, last_segment=first + 2)]}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    decisions = tmp_path / 'units.json'
    asyncio.run(units.update(decisions, None, source))
    payload = units.build(json.loads(decisions.read_text()), source)
    accepted = next(iter(payload['sources'].values()))['units']
    assert [u['first_segment'] for u in accepted] == [first for first, _ in units.chapter_ranges(chapter)]
    assert len(calls) == 2 * len(accepted)
    assert sum(name.startswith('review-') for name, _ in calls) == len(accepted)
    assert source.read_bytes() == original


@pytest.mark.parametrize('problem', ['overlap', 'range', 'punctuation', 'text', 'empty'])
def test_rejects_invalid_units(chapter, problem):
    candidate = unit()
    proposal = [candidate]
    if problem == 'overlap':
        proposal.append(copy.deepcopy(candidate))
    elif problem == 'range':
        candidate['last_segment'] = 999
    elif problem == 'punctuation':
        candidate['last_segment'] = 7
    elif problem == 'text':
        candidate['text'] = '看见了'
    else:
        candidate['meaning_en'] = ''
    with pytest.raises(ValueError):
        units.validate_units(chapter, proposal)


def test_published_pilot_keeps_reported_forms_whole():
    decisions = json.loads(units.DECISIONS.read_text())
    payload = units.build(decisions)
    published = json.loads(units.OUTPUT.read_text())
    assert all(published['sources'][key] == value for key, value in payload['sources'].items())
    forms = {u['text'] for s in payload['sources'].values() for u in s['units']}
    assert {'看到了', '来了', '看着', '有难'} <= forms
    assert not any('国家' in form for form in forms)
    assert forms.isdisjoint({'进店', '做同一件事', '去打', '有了五百多人'})
    construction = next(u for s in payload['sources'].values() for u in s['units']
                        if u['text'] == '有难')
    assert 'nàn' in construction['pinyin']
    assert 'noun' in construction['explanation_en'].lower()
    assert not any(any(word in form for word in ['张飞', '黄巾军', '一起']) for form in forms)
    decisions['chapters']['1'][0]['meaning_en'] = 'unreviewed change'
    with pytest.raises(ValueError, match='review'):
        units.build(decisions)


def test_stale_source_is_not_published():
    decisions = json.loads(units.DECISIONS.read_text())
    decisions['source_fingerprint'] = 'stale'
    with pytest.raises(ValueError, match='Stale'):
        units.build(decisions)


@pytest.mark.parametrize('mode', ['prepare', 'apply', 'unrelated', 'fragment'])
def test_coverage_audit_preserves_snapshot_and_extends_only_complete_units(tmp_path, monkeypatch, mode):
    texts = ['来', '了', '。', '看', '见', '了', '。']
    chapter = {'number': 1, 'text': ''.join(texts),
        'annotation_audit': {'all_reviewed': True}, 'segments': [
            {'text': text, 'type': 'punctuation' if text == '。' else 'word',
             'pinyin': '', 'meaning_en': 'part'} for text in texts]}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'level': 'hsk3', 'chapters': [chapter]}))
    _, _, _, fingerprint = units.source_data(source)
    first = dict(unit(), first_segment=0, last_segment=1, text='来了')
    second = dict(unit(), first_segment=3, last_segment=4, text='看见')
    chapters = {'1': [first, second]}
    previous = dict(schema_version=1, policy_version=units.POLICY_VERSION,
        source_fingerprint=fingerprint, reviewed=True, chapters=chapters,
        review_digest=units.decision_digest(chapters))
    path = tmp_path / 'units.json'
    path.write_text(json.dumps(previous))
    original = path.read_bytes()
    addition = dict(unit(), first_segment=3, last_segment=5, text='看见了')
    if mode == 'unrelated':
        addition = first
    elif mode == 'fragment':
        addition = dict(addition, first_segment=4, text='见了')

    async def call(self, name, prompt, schema, effort, *, tool_profile):
        assert effort == 'high' and tool_profile == 'offline'
        return {'units': [addition]}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    if mode in {'unrelated', 'fragment'}:
        with pytest.raises(ValueError, match=mode):
            asyncio.run(units.review_coverage(path, source, apply=True))
        assert path.read_bytes() == original
    else:
        result = asyncio.run(units.review_coverage(path, source, apply=mode == 'apply'))
        assert result['chapters']['1'] == [first, addition]
        assert result['coverage_review']['previous_digest'] == previous['review_digest']
        if mode == 'prepare':
            assert path.read_bytes() == original
        else:
            assert json.loads(path.read_text()) == result
        units.build(result, source)


@pytest.mark.parametrize('valid_review', [True, False])
def test_review_repairs_invalid_proposals_but_invalid_review_never_publishes(
        tmp_path, monkeypatch, valid_review):
    invalid = dict(unit(), last_segment=1)
    calls = []

    async def call(self, name, prompt, schema, effort, *, tool_profile):
        assert tool_profile == 'offline'
        calls.append(name)
        if name.startswith('review'):
            assert 'Proposal validation failed' in prompt
            assert 'omit single-segment units' in prompt
            return {'units': [unit() if valid_review else invalid]}
        return {'units': [invalid]}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    decisions = tmp_path / 'decisions.json'
    output = tmp_path / 'asset.json'
    if valid_review:
        asyncio.run(units.update(decisions, output))
        assert json.loads(output.read_text()) == units.build(json.loads(decisions.read_text()))
    else:
        with pytest.raises(ValueError, match='range'):
            asyncio.run(units.update(decisions, output))
        assert not decisions.exists()
        assert not output.exists()
    assert calls == ['propose-1', 'review-1']
