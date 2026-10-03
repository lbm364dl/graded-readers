import asyncio
import copy
import json

import pytest

from pipeline import usage_dictionary as lexical
from pipeline import chinese_reading_units as reading
from pipeline import expression_dictionary as expressions


def test_expression_response_schema_requires_every_declared_entry_field():
    schema = json.loads(expressions.SCHEMA.read_text())
    entry = schema['properties']['entries']['items']
    assert set(entry['required']) == set(entry['properties'])
    assert 'name' in entry['properties']['kind']['enum']


def test_duplicate_expression_error_identifies_existing_entry_and_senses(data):
    base, units, decisions = copy.deepcopy(data)
    expression = decisions['entries'][0]
    existing = dict(expression, id='zh-existing-word')
    existing['senses'] = [dict(existing['senses'][0], id='zh-existing-word-s1')]
    base['entries'].append(existing)
    decisions['input_fingerprint'] = expressions.fingerprint(base, units)
    with pytest.raises(ValueError, match='duplicates') as error:
        expressions.extend(base, units, decisions)
    assert expression['headword'] in str(error.value)
    assert 'zh-existing-word' in str(error.value)
    assert 'zh-existing-word-s1' in str(error.value)


def test_reassigned_expression_error_identifies_both_headwords(data):
    _, _, decisions = copy.deepcopy(data)
    proposed = copy.deepcopy(decisions)
    old = proposed['entries'][0]
    original_word = old['headword']
    old['headword'] = '不同写法'
    with pytest.raises(ValueError, match='reassigned') as error:
        expressions.preserve_approved_entries(decisions['entries'], proposed)
    assert old['id'] in str(error.value)
    assert original_word in str(error.value)
    assert '不同写法' in str(error.value)
    assert 'distinct identity' in str(error.value)


def test_unknown_binding_error_supplies_local_approved_identities(data):
    base, units, decisions = copy.deepcopy(data)
    binding = decisions['bindings'][0]
    candidate = next(c for c in expressions.candidates(base, units)
                     if c['id'] == binding['candidate_id'])
    occurrence = next(o for o in base['occurrences']
                      if o['source'] == candidate['source']
                      and o['start'] == candidate['start'])
    binding.update(entry_id='invented-id', sense_id='invented-sense')
    decisions['review_digest'] = lexical.decision_digest({
        key: decisions[key] for key in ('entries', 'bindings')})
    with pytest.raises(ValueError, match='Unknown expression') as error:
        expressions.extend(base, units, decisions)
    assert occurrence['entry_id'] in str(error.value)
    assert occurrence['sense_id'] in str(error.value)
    assert candidate['surface'] in str(error.value)


@pytest.fixture
def data():
    base = lexical.build(json.loads(lexical.DECISIONS.read_text()))
    units = reading.build(json.loads(reading.DECISIONS.read_text()))
    decisions = json.loads(expressions.DECISIONS.read_text())
    return base, units, decisions


def test_layered_headword_keeps_underlying_entries_and_source(data):
    base, units, decisions = data
    payload = expressions.extend(*data)
    by_word = {e['headword']: e for e in payload['entries']}
    assert '看到' in by_word and '看到了' not in by_word and '来了' not in by_word
    kandao = by_word['看到']
    assert kandao['base_entry_id'] == by_word['看']['id']
    assert [c['entry_id'] for c in kandao['components']] == [by_word['看']['id'], by_word['到']['id']]
    use = next(o for o in payload['occurrences'] if o['entry_id'] == kandao['id'])
    assert payload['sources'][use['source']]['text'][use['start']:use['end']] == '看到'
    binding = next(b for b in payload['reading_bindings'] if b['surface'] == '看到了')
    assert binding['occurrence_id'] == use['id']
    assert payload['occurrences'][:len(base['occurrences'])] == base['occurrences']
    assert len(payload['reading_bindings']) == len(expressions.candidates(base, units))


def test_simple_aspect_uses_existing_entry_without_duplicate_example(data):
    payload = expressions.extend(*data)
    binding = next(b for b in payload['reading_bindings'] if b['surface'] == '来了')
    use = next(o for o in payload['occurrences'] if o['id'] == binding['occurrence_id'])
    assert use['surface'] == '来'
    assert 'layer' not in use


def test_existing_word_can_bind_a_different_levels_split_surface(data):
    base, units, decisions = data
    shared = next(e for e in decisions['entries'] if e['headword'] == '看到')
    base['entries'].append(dict(copy.deepcopy(shared), kind='word'))
    decisions['entries'] = [e for e in decisions['entries'] if e['id'] != shared['id']]
    decisions['input_fingerprint'] = expressions.fingerprint(base, units)
    decisions['review_digest'] = lexical.decision_digest({k: decisions[k] for k in ('entries', 'bindings')})
    result = expressions.extend(base, units, decisions)
    assert result['occurrences'][:len(base['occurrences'])] == base['occurrences']
    binding = next(b for b in result['reading_bindings'] if b['surface'] == '看到了')
    use = next(o for o in result['occurrences'] if o['id'] == binding['occurrence_id'])
    assert use['entry_id'] == shared['id'] and use['surface'] == '看到'
    assert sum(e['headword'] == '看到' for e in result['entries']) == 1


@pytest.mark.parametrize('headword,right,allowed', [('开', 1, True), ('开得', 2, False)])
def test_partial_carrier_adds_usage_but_cannot_relabel_whole_token(headword, right, allowed):
    asset, text = 'source', '开得正盛'
    entries = [dict(id='inflected', headword='开得', reading='kāi de', kind='construction',
                    senses=[dict(id='inflected-s1', definition='bloom in a state')]),
               dict(id='state', headword='正盛', reading='zhèng shèng', kind='word',
                    senses=[dict(id='state-s1', definition='in full bloom')]),
               dict(id='carrier', headword=headword, reading='kāi', kind='word',
                    senses=[dict(id='carrier-s1', definition='bloom')])]
    occurrences = [dict(id='use0', source=asset, segment_index=0, start=0, end=2,
                        surface='开得', entry_id='inflected', sense_id='inflected-s1',
                        sentence=text, sentence_start=0),
                   dict(id='use1', source=asset, segment_index=1, start=2, end=4,
                        surface='正盛', entry_id='state', sense_id='state-s1',
                        sentence=text, sentence_start=0)]
    base = dict(entries=entries, sources={asset: dict(text=text)}, occurrences=occurrences)
    units = dict(sources={asset: dict(text=text, segments=[['开得'], ['正盛']], units=[
        dict(first_segment=0, last_segment=1, text=text, pinyin='kāi de zhèng shèng',
             meaning_en='be in full bloom', explanation_en='Degree complement describes blooming.')])})
    binding = dict(candidate_id=expressions.candidates(base, units)[0]['id'],
                   entry_id='carrier', sense_id='carrier-s1', canonical_start=0, canonical_end=right)
    decisions = dict(entries=[], bindings=[binding], input_fingerprint=expressions.fingerprint(base, units))
    if allowed:
        result = expressions.extend(base, units, decisions, require_review=False)
        assert result['occurrences'][:2] == occurrences
        assert result['occurrences'][-1]['surface'] == headword
        assert result['occurrences'][-1]['entry_id'] == 'carrier'
    else:
        with pytest.raises(ValueError, match='conflicts with reviewed lexical sense'):
            expressions.extend(base, units, decisions, require_review=False)


@pytest.mark.parametrize('surface,expected', [('看到了', (0, 1)), ('看看', (0, 99))])
def test_span_restoration_does_not_guess_repeated_substrings(data, monkeypatch, surface, expected):
    base, units, _ = data
    carrier = next(e for e in base['entries'] if e['headword'] == '看')
    monkeypatch.setattr(expressions, 'candidates', lambda *_: [dict(id='test', surface=surface)])
    binding = dict(candidate_id='test', entry_id=carrier['id'], sense_id=carrier['senses'][0]['id'],
                   canonical_start=0, canonical_end=99)
    proposed = dict(entries=[], bindings=[binding])
    expressions.restore_unambiguous_spans(base, units, proposed)
    assert (binding['canonical_start'], binding['canonical_end']) == expected
    assert binding['sense_id'] == carrier['senses'][0]['id']


@pytest.mark.parametrize('kind', ['name', 'word', 'expression', 'construction'])
def test_canonical_layer_preserves_declared_classification(data, kind):
    base, units, decisions = data
    # Exercise classification transport, independently of semantic review.
    decisions['entries'][0]['kind'] = kind
    decisions['review_digest'] = lexical.decision_digest({k: decisions[k] for k in ('entries', 'bindings')})
    payload = expressions.extend(base, units, decisions)
    entry = next(e for e in payload['entries'] if e['id'] == decisions['entries'][0]['id'])
    assert entry['kind'] == kind


def test_canonical_layer_rejects_unknown_classification(data):
    base, units, decisions = data
    decisions['entries'][0]['kind'] = 'unknown'
    with pytest.raises(ValueError, match='Invalid canonical entry kind'):
        expressions.extend(base, units, decisions, require_review=False)


def test_restores_immutable_entries_without_guessing_new_bindings(data):
    _, _, decisions = data
    old = decisions['entries']
    proposed = copy.deepcopy(decisions)
    proposed['entries'][0]['components'][0]['role'] = 'unrequested rewrite'
    proposed['entries'][0]['senses'][0]['definition'] = 'unrequested rewrite'
    additional = dict(id=old[0]['id'] + '-new', definition='new coverage')
    proposed['entries'][0]['senses'].append(additional)
    proposed['entries'].pop()
    restored = expressions.preserve_approved_entries(old, proposed)
    expected = copy.deepcopy(old)
    expected[0]['senses'].append(additional)
    assert restored['entries'] == expected
    assert restored['bindings'] == proposed['bindings']
    assert proposed['entries'][0]['senses'][0]['definition'] == 'unrequested rewrite'
    proposed['entries'][0]['headword'] = 'a different identity'
    with pytest.raises(ValueError, match='ID was reassigned'):
        expressions.preserve_approved_entries(old, proposed)


def test_compact_construction_keeps_noun_reading_and_linked_parts(data):
    payload = expressions.extend(*data)
    entries = {e['headword']: e for e in payload['entries']}
    entry = entries['有难']
    assert 'nàn' in entry['reading']
    assert entry['base_entry_id'] == entries['有']['id']
    assert [c['entry_id'] for c in entry['components']] == [
        entries['有']['id'], entries['难']['id']]
    use = next(o for o in payload['occurrences'] if o['entry_id'] == entry['id'])
    assert use['surface'] == '有难'
    assert '国家有难' in use['sentence']


@pytest.mark.parametrize('problem', ['stale', 'unreviewed', 'missing', 'duplicate', 'span', 'sense', 'base', 'component'])
def test_invalid_links_fail_closed(data, problem):
    base, units, decisions = data
    if problem == 'stale':
        decisions['input_fingerprint'] = 'old'
    elif problem == 'unreviewed':
        decisions['reviewed'] = False
    elif problem == 'missing':
        decisions['bindings'].pop()
    elif problem == 'duplicate':
        decisions['bindings'].append(copy.deepcopy(decisions['bindings'][0]))
    elif problem == 'span':
        decisions['bindings'][0]['canonical_end'] = 3
    elif problem == 'sense':
        decisions['bindings'][0]['sense_id'] = 'missing'
    elif problem == 'base':
        decisions['entries'][0]['base_entry_id'] = 'missing'
    else:
        decisions['entries'][0]['components'].pop()
    decisions['review_digest'] = lexical.decision_digest({k: decisions[k] for k in ('entries', 'bindings')})
    with pytest.raises(ValueError):
        expressions.extend(base, units, decisions)


def test_unchanged_update_reuses_ids_and_makes_no_agent_calls(data, tmp_path, monkeypatch):
    base, units, decisions = data
    path = tmp_path / 'expressions.json'
    path.write_text(json.dumps(decisions))
    async def unexpected(*args, **kwargs):
        raise AssertionError('Unchanged input must not call agents')
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', unexpected)
    assert asyncio.run(expressions.update(base, units, path)) == expressions.extend(*data)


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_invalid_review_gets_one_repair_before_publication(data, tmp_path, monkeypatch, repair_succeeds):
    base, units, decisions = data
    previous = dict(decisions, input_fingerprint='stale')
    path = tmp_path / 'expressions.json'
    original = json.dumps(previous)
    path.write_text(original)
    valid = {k: decisions[k] for k in ('entries', 'bindings')}
    invalid = copy.deepcopy(valid)
    invalid['bindings'][0]['sense_id'] = 'missing'
    calls = []

    async def call(self, name, prompt, schema, effort):
        calls.append(name)
        if name in {'repair', 'repair-2'}:
            assert 'Validation rejected' in prompt
            assert effort == 'high'
            return valid if repair_succeeds else invalid
        return invalid

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    if repair_succeeds:
        assert asyncio.run(expressions.update(base, units, path)) == expressions.extend(*data)
    else:
        with pytest.raises(ValueError, match='sense'):
            asyncio.run(expressions.update(base, units, path))
        assert path.read_text() == original
    assert calls == ['propose', 'review', 'repair'] + ([] if repair_succeeds else ['repair-2'])


@pytest.mark.parametrize('batch_size', [0, -1, True, 2.5, '30'])
def test_expression_batch_size_rejects_invalid_values(data, tmp_path, batch_size):
    base, units, _ = data
    path = tmp_path / 'absent.json'
    with pytest.raises(ValueError, match='positive integer'):
        asyncio.run(expressions.update(base, units, path, batch_size=batch_size))
    assert not path.exists()


def test_expression_batches_do_not_reuse_unreviewed_registry(data, tmp_path):
    base, units, decisions = data
    path = tmp_path / 'expressions.json'
    previous = dict(decisions, reviewed=False)
    original = json.dumps(previous)
    path.write_text(original)
    with pytest.raises(ValueError, match='unreviewed'):
        asyncio.run(expressions.update(base, units, path, batch_size=2))
    assert path.read_text() == original


@pytest.mark.parametrize('interrupt', [False, True])
def test_bounded_expression_batches_reuse_completed_bindings(data, tmp_path, monkeypatch, interrupt):
    base, units, decisions = data
    previous = dict(decisions, input_fingerprint='stale', bindings=[], candidate_fingerprints={})
    previous['review_digest'] = lexical.decision_digest({key: previous[key] for key in ('entries', 'bindings')})
    path = tmp_path / 'expressions.json'
    path.write_text(json.dumps(previous))
    sizes = []
    fail_once = interrupt

    async def call(self, name, prompt, schema, effort):
        nonlocal fail_once
        request = json.JSONDecoder().raw_decode(prompt.split('\nINPUT:\n', 1)[1])[0]
        ids = {candidate['id'] for candidate in request['candidates']}
        if name == 'propose':
            sizes.append(len(ids))
            if fail_once and len(sizes) == 2:
                fail_once = False
                raise RuntimeError('interrupted batch')
        return dict(entries=copy.deepcopy(decisions['entries']), bindings=[
            copy.deepcopy(binding) for binding in decisions['bindings'] if binding['candidate_id'] in ids])

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    if interrupt:
        with pytest.raises(RuntimeError, match='interrupted batch'):
            asyncio.run(expressions.update(base, units, path, batch_size=2))
        partial = json.loads(path.read_text())
        assert len(partial['bindings']) == 2
        assert partial['reviewed']
    result = asyncio.run(expressions.update(base, units, path, batch_size=2))
    assert all(size <= 2 for size in sizes)
    saved = json.loads(path.read_text())
    assert len(result['reading_bindings']) == len(expressions.candidates(base, units))
    assert {binding['candidate_id'] for binding in saved['bindings']} == {
        candidate['id'] for candidate in expressions.candidates(base, units)}
    assert saved['entries'] == decisions['entries']
    assert saved['input_fingerprint'] == expressions.fingerprint(base, units)
    before = len(sizes)
    assert asyncio.run(expressions.update(base, units, path, batch_size=2)) == result
    assert len(sizes) == before
