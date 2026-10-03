from copy import deepcopy
import asyncio
import json
import pytest
from pipeline import japanese_usage_dictionary as words
from pipeline import japanese_grammar_dictionary as grammar


def test_all_levels_share_lexical_identities_and_keep_source_spans():
    annotations = [words.read(words.ANNOTATION.with_name(f'{l}.annotations.json'))
                   for l in words.LEVELS]
    original = deepcopy(annotations)
    sources, groups, uses, _ = words.source_data(annotations)
    assert {s['level'] for s in sources.values()} == {'N5', 'N4', 'N3', 'N2', 'N1'}
    assert sum(u.get('layer') != 'expression' for u in uses) == sum(
        sum(u.get('layer') != 'expression' for u in words.source_data(a)[2]) for a in annotations)
    assert len({g['id'] for g in groups}) == len(groups)
    for use in uses:
        assert sources[use['source']]['text'][use['start']:use['end']] == use['surface']
    cat = next(g for g in groups if g['headword'] == '猫' and g['reading'] == 'ねこ')
    assert len({u['source'] for u in cat['uses']}) == 5
    assert annotations == original


def test_all_levels_keep_independent_grammar_and_expression_source_ids():
    annotations = [words.read(words.ANNOTATION.with_name(f'{l}.annotations.json'))
                   for l in words.LEVELS]
    sources, rows = grammar.candidates(annotations)
    assert len(rows) == sum(len(grammar.candidates(a)[1]) for a in annotations)
    assert len({r['id'] for r in rows}) == len(rows)
    for row in rows:
        assert sources[row['source']]['text'][row['start']:row['end']] == row['surface']
    groups, uses, _ = words.expression_data(annotations)
    assert len({g['id'] for g in groups}) == len(groups)
    assert len({u['id'] for u in uses}) == len(uses)
    partial = [u for u in uses if u['surface'] == '日が暮れ']
    assert len(partial) == 2
    assert all(u['reading'] == '' for u in partial)


def test_one_phrase_identity_can_span_single_units_and_segmented_expressions():
    annotations = [words.read(words.ANNOTATION.with_name(f'{l}.annotations.json')) for l in words.LEVELS]
    _, groups, uses, _ = words.source_data(annotations)
    face = next(g for g in groups if g['headword'] == '顔を合わせる')
    assert {u.get('layer', 'segment') for u in face['uses']} == {'segment', 'expression'}
    assert all(g['id'] != face['id'] for g in words.expression_data(annotations)[0])
    assert len({u['id'] for u in uses}) == len(uses)


def test_expression_promotion_preserves_senses_and_retires_secondary_uses(tmp_path, monkeypatch):
    monkeypatch.setattr(words, 'DIRECTORY', tmp_path)
    monkeypatch.setattr(words, 'SENSES', tmp_path/'primary.json')
    monkeypatch.setattr(words, 'EXPRESSIONS', tmp_path/'expressions.json')
    old = dict(id='ja-face', headword='顔を合わせる', reading='かおをあわせる', kind='expression',
        senses=[dict(id='ja-face-1', definition='meet face-to-face', occurrences=['old-expression'])])
    words.atomic_json(words.EXPRESSIONS, dict(reviewed=True, entries=[old],
        review_digest=words.decision_digest([old])))
    groups = [dict(id=old['id'], headword=old['headword'], reading=old['reading'],
        uses=[dict(id='old-expression'), dict(id='new-word')])]
    async def batch(runner, rows, path, fingerprint):
        selected = words.read(path)['entries']
        assert selected == [old]
        selected = deepcopy(selected)
        selected[0]['senses'][0]['occurrences'] = [u['id'] for u in rows[0]['uses']]
        return selected
    monkeypatch.setattr(words, '_link_batch', batch)
    primary = asyncio.run(words.link(None, groups, words.SENSES, words.decision_digest(groups)))
    assert primary[0]['senses'][0]['definition'] == old['senses'][0]['definition']
    assert primary[0]['senses'][0]['id'] == old['senses'][0]['id']
    retired = asyncio.run(words.link(None, [], words.EXPRESSIONS, words.decision_digest([])))
    assert retired[0]['senses'][0]['occurrences'] == []
    assert retired[0]['senses'][0]['definition'] == old['senses'][0]['definition']


def test_lexical_batches_resume_and_reuse_unchanged_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(words, 'DIRECTORY', tmp_path)
    groups = [dict(id=f'ja-test-{i}', headword=f'word{i}', reading=f'reading{i}', uses=[])
              for i in range(35)]
    calls, fail = [], [True]
    async def batch(runner, rows, path, fingerprint):
        cached = words.read(path, {})
        if cached.get('source_fingerprint') == fingerprint:
            return cached['entries']
        calls.append([g['id'] for g in rows])
        if rows[0]['id'] == 'ja-test-16' and fail[0]:
            fail[0] = False
            raise ValueError('Interrupted batch')
        entries = [dict(id=g['id'], headword=g['headword'], reading=g['reading'],
            kind='word', senses=[dict(id=g['id']+'-1', definition='approved', occurrences=[])])
            for g in rows]
        words.atomic_json(path, dict(entries=entries, reviewed=True,
            source_fingerprint=fingerprint, review_digest=words.decision_digest(entries)))
        return entries
    monkeypatch.setattr(words, '_link_batch', batch)
    registry = tmp_path / 'senses.json'
    with pytest.raises(ValueError, match='Interrupted'):
        asyncio.run(words.link(None, groups, registry, words.decision_digest(groups)))
    assert not registry.exists()  # No partial publication.
    result = asyncio.run(words.link(None, groups, registry, words.decision_digest(groups)))
    assert len(result) == 35
    assert len(calls) == 4  # Three initial batches, only the failed one rerun.
    assert all(len(c) <= 16 for c in calls)
    assert asyncio.run(words.link(None, groups, registry, words.decision_digest(groups))) == result
    assert len(calls) == 4
    expanded = groups + [dict(id='ja-test-new', headword='new', reading='new', uses=[])]
    updated = asyncio.run(words.link(None, expanded, registry, words.decision_digest(expanded)))
    assert updated[:35] == result
    assert calls[-1] == ['ja-test-new']


def test_new_standalone_form_steps_require_reviewed_whole_meanings():
    annotation = words.read(words.ANNOTATION)
    row = next(r for r in grammar.candidates(annotation)[1]
               if r['layer'] == 'form' and 'display_context' not in r)
    original = deepcopy(row)
    modeled = grammar.model_candidate(row, include_display=True)
    assert modeled['display_form'] == row['form']
    assert modeled['display_base_form'] == row['annotation']['lemma']
    assert 'display_context' not in row and row == original
    fingerprint = grammar.candidate_fingerprint(row)
    data = dict(entries=[dict(id='ja-grammar-test', title='Test', reading='',
        kind='conjugation', summary_en='Test', explanation_en='Test',
        formation=[], notes_en=[])], assignments=[dict(candidate_id=row['id'],
        entry_ids=['ja-grammar-test'], context_en='Test',
        display_meaning_en='complete meaning', display_base_meaning_en='base meaning')])
    grammar.validate(data, [row], require_form_meanings=True)
    del data['assignments'][0]['display_meaning_en']
    del data['assignments'][0]['display_base_meaning_en']
    with pytest.raises(ValueError, match='reviewed meanings'):
        grammar.validate(data, [row], require_form_meanings=True)
    assert grammar.candidate_fingerprint(row) == fingerprint


def test_short_occurrence_ids_restore_exact_canonical_links(tmp_path):
    annotation = words.read(words.ANNOTATION)
    groups = words.source_data(annotation)[1][:2]
    seen = []
    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            compact = json.JSONDecoder().raw_decode(prompt.split('\nGROUPS:\n', 1)[1])[0]
            seen.extend(u['id'] for g in compact for u in g['uses'])
            result = dict(entries=[dict(id=g['id'], headword=g['headword'],
                reading=g['reading'], kind='word', senses=[dict(id=g['id']+'-1',
                    definition='approved', occurrences=[u['id'] for u in g['uses']])])
                for g in compact])
            if job.endswith('/review'):
                occurrences = result['entries'][0]['senses'][0]['occurrences']
                occurrences.append(occurrences[0])
            if '/repair-' in job:
                error, serialized = prompt.split('Repair the entire reviewed registry: ', 1)[1].split('\n', 1)
                assert 'missing=[]' in error and "extra=['O0']" in error
                repair = json.loads(serialized)
                assert all(oid.startswith('O') and len(oid) < 8
                    for e in repair['entries'] for s in e['senses'] for oid in s['occurrences'])
            return result
    entries = asyncio.run(words._link_batch(Runner(), groups, tmp_path/'batch.json',
        words.decision_digest(groups)))
    assert all(oid.startswith('O') and len(oid) < 8 for oid in seen)
    assert {oid for e in entries for s in e['senses'] for oid in s['occurrences']} == {
        u['id'] for g in groups for u in g['uses']}
    words.validate_entries(entries, groups)


def test_short_grammar_ids_preserve_routing_and_display_identity():
    _, rows = grammar.candidates(words.read(words.ANNOTATION))
    compact, aliases = grammar.routing_payload(rows)
    assert [row['id'] for row in compact] == [f'C{i}' for i in range(len(rows))]
    data = dict(entries=[], assignments=[dict(candidate_id=row['id'],
        entry_ids=['ja-grammar-canonical'], context_en='Reviewed context') for row in compact])
    restored = grammar.routing_ids(data, {v: k for k, v in aliases.items()})
    assert [a['candidate_id'] for a in restored['assignments']] == [r['id'] for r in rows]
    assert grammar.routing_ids(restored, aliases) == data
    assert restored['assignments'][0]['entry_ids'] == ['ja-grammar-canonical']


def test_grammar_repair_errors_use_input_aliases_without_prefix_collisions():
    aliases = {'source#step-1': 'C0', 'source#step-10': 'C1'}
    error = ValueError('source#step-10: missing meaning; source#step-1: missing base')
    assert grammar.routing_error(error, aliases) == 'C1: missing meaning; C0: missing base'
    assert grammar.routing_error('Unknown grammar entry ja-grammar-example', aliases) == (
        'Unknown grammar entry ja-grammar-example')


def test_grammar_batches_preserve_real_chains_and_exact_source_order():
    for level in words.LEVELS:
        rows = grammar.candidates(words.read(words.ANNOTATION.with_name(f'{level}.annotations.json')))[1]
        batches = list(grammar.routing_batches(rows))
        assert [row for batch in batches for row in batch] == rows
        assert all(len(batch) <= 96 for batch in batches)
        for left, right in zip(batches, batches[1:]):
            assert (left[-1]['source'], left[-1]['start']) != (right[0]['source'], right[0]['start'])
    rows = [dict(source='s', start=0, id=str(i)) for i in range(5)]
    rows.append(dict(source='s', start=10, id='last'))
    assert [len(batch) for batch in grammar.routing_batches(rows, limit=3)] == [5, 1]


def test_attested_components_share_destinations_without_merging_homographs():
    def entry(id, headword, reading, kind='word', **extra):
        return dict(id=id, headword=headword, reading=reading, kind=kind,
            meaning_guide=dict(parts=[]), **extra)
    canonical = entry('ja-suru', 'する', 'する')
    component = entry('ja-component-suru', 'する', 'する', origin='component_word')
    grammar_only = entry('ja-component-grammar', 'する', 'する',
        kind='construction', origin='component_word')
    different_reading = entry('ja-component-reading', 'する', '別', origin='component_word')
    parent = entry('ja-parent', '勉強する', 'べんきょうする')
    parent['meaning_guide']['parts'] = [
        dict(text='勉強', contribution_en='study'), dict(text='する', contribution_en='do')]
    # The differing reading is genuinely ambiguous, so never guess its link.
    result = words.attach_links(dict(entries=[canonical, component, parent]))
    assert component['superseded_by'] == canonical['id']
    assert parent['meaning_guide']['parts'][1]['entry_id'] == canonical['id']
    words.attach_links(dict(entries=[canonical, grammar_only, different_reading]))
    assert 'superseded_by' not in grammar_only
    assert 'superseded_by' not in different_reading
