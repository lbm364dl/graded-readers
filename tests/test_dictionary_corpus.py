import asyncio
import copy
import json

import pytest

from pipeline import dictionary_corpus as corpus
from pipeline import usage_dictionary as lexical
from pipeline import expression_dictionary as expressions
from pipeline import chinese_reading_units as reading
from pipeline import dictionary_meaning_guides as guides
from pipeline.chinese_translation_policy import CHINESE_TRANSLATION_POLICY


def sample(level, entry_id='word', definition='come'):
    return dict(sources={level: {'text': '来'}}, entries=[dict(
        id=entry_id, headword='来', reading='lái', kind='word',
        senses=[dict(id=entry_id + '-s1', definition=definition)])],
        occurrences=[dict(id=level+'-use', entry_id=entry_id, sense_id=entry_id+'-s1')])


def test_guide_only_plan_never_updates_lexical_layers(tmp_path, monkeypatch):
    registry = tmp_path / 'guides.json'
    registry.write_text(json.dumps(dict(reviewed=True, guides=[],
                                       review_digest=lexical.decision_digest([]))))
    requests = tmp_path / 'requests.json'
    requests.write_text('[]')
    monkeypatch.setattr(corpus, 'GUIDES', registry)
    monkeypatch.setattr(corpus, 'REQUESTS', requests)
    calls = []

    def source_input(**kwargs):
        calls.append(kwargs)
        return dict(entries=[], occurrences=[], sources={}), dict(sources={})

    monkeypatch.setattr(corpus, 'editorial_input', source_input)
    report = corpus.build(update=True, editorial_only=True, plan=True,
                          publish=False, levels=['hsk1'])
    assert report['editorial_jobs'] == []
    assert len(calls) == 1
    assert calls[0]['update'] is False


def test_shared_entries_gain_examples_not_duplicates():
    merged = corpus.merge_dictionaries([sample('hsk1'), sample('hsk2')])
    assert len(merged['entries']) == 1
    assert len(merged['occurrences']) == 2
    assert len(merged['entries'][0]['senses']) == 1


def test_observed_readings_keep_canonical_identity_and_exclude_inflected_surfaces():
    dictionary = sample('hsk1')
    entry = dictionary['entries'][0]
    original = copy.deepcopy(entry)
    dictionary['occurrences'][0].update(surface='来', reading='lai')
    dictionary['occurrences'].append(dict(entry_id=entry['id'], surface='来了', reading='láile'))
    corpus.record_observed_readings(dictionary)
    assert entry['observed_readings'] == ['lai', 'lái']
    assert {k: entry[k] for k in original} == original


def test_linking_cannot_rewrite_shared_metadata_but_can_add_a_sense():
    known = sample('hsk1')
    decisions = dict(reviewed=True, entries=copy.deepcopy(known['entries']))
    entry = decisions['entries'][0]
    entry['kind'] = 'particle'
    entry['senses'][0]['definition'] = 'unnecessary rewrite'
    entry['senses'][0]['occurrences'] = ['hsk2-use']
    entry['senses'].append(dict(id='word-s2', definition='new meaning', occurrences=['new-use']))
    pinned = corpus.preserve_shared_metadata(decisions, known)
    assert pinned['reviewed'] is False
    assert pinned['entries'][0]['kind'] == 'word'
    assert pinned['entries'][0]['senses'][0] == dict(id='word-s1', definition='come', occurrences=['hsk2-use'])
    assert pinned['entries'][0]['senses'][1] == entry['senses'][1]


def test_new_shared_sense_is_copied_empty_without_rederiving_approved_uses():
    known = sample('hsk1')
    decisions = dict(reviewed=True, entries=copy.deepcopy(known['entries']))
    decisions['entries'][0]['senses'][0]['occurrences'] = ['existing-use']
    decisions['review_digest'] = lexical.decision_digest(decisions['entries'])
    known['entries'][0]['senses'].append(dict(id='word-s2', definition='another approved meaning'))
    pinned = corpus.preserve_shared_metadata(decisions, known)
    assert pinned['reviewed'] is True
    assert pinned['entries'][0]['senses'][0]['occurrences'] == ['existing-use']
    assert pinned['entries'][0]['senses'][1]['occurrences'] == []
    assert pinned['review_digest'] == lexical.decision_digest(pinned['entries'])
    assert corpus.preserve_shared_metadata(pinned, known) == pinned


def test_changed_shared_metadata_invalidates_only_affected_headword():
    known = sample('hsk1')
    second = copy.deepcopy(known['entries'][0])
    second.update(id='other', headword='去', reading='qù')
    second['senses'] = [dict(id='other-s1', definition='go')]
    known['entries'].append(second)
    decisions = dict(reviewed=True, entries=copy.deepcopy(known['entries']))
    for entry in decisions['entries']:
        entry['senses'][0]['occurrences'] = [entry['id'] + '-use']
    decisions['review_digest'] = lexical.decision_digest(decisions['entries'])
    known['entries'][0]['kind'] = 'construction'
    pinned = corpus.preserve_shared_metadata(decisions, known)
    assert pinned['reviewed'] is False
    assert set(pinned['reviewed_word_fingerprints']) == {'去'}
    assert pinned['review_digest'] == lexical.decision_digest(pinned['entries'])
    assert pinned['entries'][0]['senses'][0]['occurrences'] == ['word-use']


def test_new_ids_are_allocated_without_touching_existing_ids():
    known = sample('hsk1')['entries']
    proposed = copy.deepcopy(known) + [dict(id='zh-无效', headword='新', senses=[dict(id='无效', definition='new')])]
    proposed[0]['senses'].append(dict(id='also-无效', definition='new meaning'))
    allocated = lexical.allocate_new_ids(proposed, known)
    assert allocated[0]['id'] == 'word'
    assert allocated[0]['senses'][0]['id'] == 'word-s1'
    assert allocated[0]['senses'][1]['id'] == 'word-s2'
    assert allocated[1]['id'].isascii()
    assert allocated[1]['senses'][0]['id'].startswith(allocated[1]['id'] + '-')


def test_merge_preserves_new_senses_without_rewriting_old_ones():
    first, second = sample('hsk1'), sample('hsk2')
    second['entries'][0]['senses'].append(dict(id='word-s2', definition='a new meaning'))
    assert len(corpus.merge_dictionaries([first, second])['entries'][0]['senses']) == 2
    second['entries'][0]['senses'][0]['definition'] = 'silently rewritten'
    with pytest.raises(ValueError, match='Conflicting shared sense'):
        corpus.merge_dictionaries([first, second])


def test_hsk2_source_ids_are_distinct_and_source_is_unchanged():
    path = corpus.source('hsk2')
    before = path.read_bytes()
    sources, _, occurrences, _ = lexical.source_data(path)
    assert all('hsk2' in key for key in sources)
    assert all(s['level'] == 2 for s in sources.values())
    assert all('hsk2' in o['id'] for o in occurrences)
    assert path.read_bytes() == before


@pytest.mark.parametrize('level', ['hsk3', 'hsk4', 'hsk5', 'hsk6'])
def test_higher_level_chapter_one_has_complete_dictionary_source_evidence(level):
    path = corpus.source(level)
    before = path.read_bytes()
    document = json.loads(before)
    sources, groups, occurrences, _ = lexical.source_data(path)
    assert {s['chapter'] for s in sources.values()} == {1}
    assert {s['level'] for s in sources.values()} == {int(level[3:])}
    assert len(occurrences) == sum(s['type'] != 'punctuation'
                                  for c in document['chapters'] for s in c['segments'])
    assert set(groups) == {o['surface'] for o in occurrences}
    assert all(sources[o['source']]['text'][o['start']:o['end']] == o['surface']
               for o in occurrences)
    assert path.read_bytes() == before


def test_default_rebuild_retains_published_higher_levels(tmp_path, monkeypatch):
    manifest = tmp_path / 'corpus.json'
    manifest.write_text(json.dumps(dict(levels=['hsk1', 'hsk2', 'hsk3', 'hsk4'])))
    monkeypatch.setattr(corpus, 'MANIFEST', manifest)
    assert corpus.selected_levels() == ('hsk1', 'hsk2', 'hsk3', 'hsk4')
    assert corpus.selected_levels(['hsk4', 'hsk1']) == ('hsk1', 'hsk4')
    with pytest.raises(ValueError, match='supported'):
        corpus.selected_levels(['hsk1', 'hsk7'])
    assert corpus.selected_levels(['hsk6', 'hsk5', 'hsk1']) == ('hsk1', 'hsk5', 'hsk6')


def test_seed_reuses_known_identity_without_fabricating_occurrences(tmp_path):
    path = tmp_path / 'senses.json'
    source = corpus.source('hsk2')
    known = lexical.build_published(json.loads(lexical.DECISIONS.read_text()))
    corpus.seed_senses(known, path, source)
    seeded = json.loads(path.read_text())
    by_id = {e['id']: e for e in known['entries']}
    for entry in seeded['entries']:
        old = by_id[entry['id']]
        assert entry['headword'] == old['headword']
        assert [{k: s[k] for k in ('id', 'definition')} for s in entry['senses']] == old['senses']
        assert all(not s['occurrences'] for s in entry['senses'])
    original = path.read_bytes()
    corpus.seed_senses(sample('unrelated'), path, source)
    assert path.read_bytes() == original


def test_expression_registry_can_become_a_lexical_entry_in_another_text():
    base = lexical.build(json.loads(lexical.DECISIONS.read_text()))
    units = reading.build(json.loads(reading.DECISIONS.read_text()))
    decisions = json.loads(expressions.DECISIONS.read_text())
    old = expressions.extend(base, units, decisions)
    promoted = copy.deepcopy(next(e for e in old['entries'] if e['headword'] == '看到'))
    base['entries'].append(promoted)
    decisions['input_fingerprint'] = expressions.fingerprint(base, units)
    result = expressions.extend(base, units, decisions)
    assert len([e for e in result['entries'] if e['headword'] == '看到']) == 1
    assert len(result['occurrences']) == len(old['occurrences'])


def test_unchanged_candidates_reuse_bindings_when_other_corpus_data_changes(tmp_path, monkeypatch):
    base = lexical.build(json.loads(lexical.DECISIONS.read_text()))
    units = reading.build(json.loads(reading.DECISIONS.read_text()))
    decisions = json.loads(expressions.DECISIONS.read_text())
    decisions['candidate_fingerprints'] = expressions.candidate_fingerprints(base, units)
    base['sources']['other'] = {'text': 'A newly indexed source with no grouped units'}
    path = tmp_path / 'expressions.json'
    path.write_text(json.dumps(decisions))
    async def unexpected(*args, **kwargs):
        raise AssertionError('Existing bindings must not be re-derived')
    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', unexpected)
    result = asyncio.run(expressions.update(base, units, path))
    assert len(result['reading_bindings']) == len(decisions['bindings'])


def test_translation_evidence_policy_reaches_explanation_and_linker_roles():
    from pipeline import dictionary_editor
    for policy in (lexical.POLICY, reading.POLICY, guides.POLICY, dictionary_editor.RESEARCH_POLICY):
        assert CHINESE_TRANSLATION_POLICY in policy
    assert 'conventional literary translation is not textual evidence' in CHINESE_TRANSLATION_POLICY


def test_known_gloss_corrections_are_narrow_and_preserve_all_word_boundaries():
    audit = json.loads((corpus.DIRECTORY / 'contextual-gloss-corrections.json').read_text())
    assert audit['segmentation_changed'] is False
    assert audit['sense_assignments_changed'] is False
    for level in ('hsk1', 'hsk2'):
        annotation = json.loads(corpus.source(level).read_text())
        for chapter in annotation['chapters']:
            assert ''.join(s['text'] for s in chapter['segments']) == chapter['text']
            for segment in chapter['segments']:
                if segment['text'] == '喝酒':
                    assert segment['meaning_en'] == 'drink alcohol'
    annotation = json.loads(corpus.source('hsk2').read_text())
    part = annotation['chapters'][0]['segments'][531]
    assert (part['text'], part['pinyin']) == ('下来', 'xiàlai')
    assert 'alive' not in part['meaning_en']


def test_published_full_corpus_reuses_existing_guides_and_links_every_hsk2_occurrence():
    if not corpus.MANIFEST.exists():
        pytest.skip('Full HSK2 corpus not published yet')
    manifest = json.loads(corpus.MANIFEST.read_text())
    assert manifest['hsk2_scope'] == 'full'
    published = json.loads(lexical.OUTPUT.read_text())
    published_ids = {o['id'] for o in published['occurrences']}
    for level in manifest['levels']:
        _, _, uses, _ = lexical.source_data(corpus.source(level))
        assert {o['id'] for o in uses} <= published_ids
    units = json.loads(reading.OUTPUT.read_text())
    for asset, source in published['sources'].items():
        assert asset in units['sources']
        assert units['sources'][asset]['text'] == source['text']
    assert all(entry.get('meaning_guide') for entry in published['entries'])
    old = {r['entry_id']: r for r in json.loads(guides.DECISIONS.read_text())['guides']}
    new = {r['entry_id']: r for r in json.loads(corpus.GUIDES.read_text())['guides']}
    # Expanded levels legitimately add senses to familiar HSK1 words. Require
    # reuse for every unchanged coverage input, not a fixed pilot-era count.
    current, _ = corpus.editorial_input(levels=manifest['levels'])
    eligible = {
        item['entry']['id'] for item in guides.inputs(current)
        if item['entry']['id'] in old
        and guides.input_fingerprint(item) == old[item['entry']['id']]['input_fingerprint']
    }
    reused = set()
    for eid, row in old.items():
        if new[eid]['input_fingerprint'] == row['input_fingerprint']:
            assert new[eid] == row
            reused.add(eid)
    assert reused == eligible
    entry = next(e for e in published['entries'] if e['headword'] == '喝酒')
    assert old[entry['id']] == new[entry['id']]
    shared = [o for o in published['occurrences'] if o['entry_id'] == entry['id']]
    assert {1, 2} <= {published['sources'][o['source']]['level'] for o in shared}
    assert len({o['sense_id'] for o in shared}) == 1
