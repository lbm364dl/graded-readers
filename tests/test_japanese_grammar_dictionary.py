# Legacy untyped worker fixtures; current authenticated default has separate callback regressions.
import asyncio
from copy import deepcopy

import pytest
from jsonschema import ValidationError

from pipeline import japanese_grammar_dictionary as grammar
from pipeline import japanese_usage_dictionary as words


@pytest.fixture(autouse=True)
def isolated_pilot_scope(request, monkeypatch, tmp_path):
    # Unit fixtures model the original pilot, independently of a running migration.
    if request.node.name not in ('test_published_grammar_and_word_routes_resolve',
                                'test_reusable_patterns_do_not_catalog_separate_inflection_lessons'):
        monkeypatch.setattr(words, 'annotations', lambda: [words.read(words.ANNOTATION)])
        from pipeline import japanese_component_links
        monkeypatch.setattr(japanese_component_links, 'REGISTRY', tmp_path/'component-links.json')


def fixture(rows):
    return dict(entries=[dict(id='ja-grammar-test', title='Test', reading='',
        kind='conjugation', summary_en='Test role', explanation_en='Test explanation',
        formation=[dict(form='V', explanation_en='Verb')], notes_en=[])],
        assignments=[dict(candidate_id=r['id'], entry_ids=['ja-grammar-test'],
            context_en='Test context', **(dict(display_meaning_en='Whole form meaning',
                display_base_meaning_en='Whole base meaning')
                if 'display_context' in r or r['layer'] == 'form' else {})) for r in rows])


def test_grammar_extraction_preserves_text_boundaries_and_form_steps():
    annotation = words.read(words.ANNOTATION)
    before = deepcopy(annotation)
    sources, rows = grammar.candidates(annotation)
    assert rows and annotation == before
    for row in rows:
        assert sources[row['source']]['text'][row['start']:row['end']] == row['surface']
        assert row['annotation'].get('type') != 'punctuation'
    assert any(r['surface'] == 'は' for r in rows)
    assert sum(r['surface'] == '見ました' and r['layer'] == 'segment' for r in rows) == 1
    forms = [r for r in rows if r['surface'] == '見ました' and r['layer'] == 'form']
    assert [r['form'] for r in forms] == ['見ます', '見ました']
    assert [r['previous_form'] for r in forms] == ['見る', '見ます']


def test_exact_assignment_and_reference_validation():
    _, rows = grammar.candidates()
    data = fixture(rows)
    grammar.validate(data, rows)
    for change in ('missing', 'duplicate', 'dangling'):
        broken = deepcopy(data)
        if change == 'missing':
            broken['assignments'].pop()
        elif change == 'duplicate':
            broken['assignments'].append(broken['assignments'][0])
        else:
            broken['assignments'][0]['entry_ids'] = ['absent']
        with pytest.raises(ValueError):
            grammar.validate(broken, rows)
    broken = deepcopy(data)
    broken['entries'][0]['explanation_en'] = 'Rederived'
    with pytest.raises(ValueError, match='Previously reviewed'):
        grammar.validate(broken, rows, data['entries'])
    broken = deepcopy(data)
    broken['assignments'][0]['entry_ids'] = ['missing-a']
    broken['assignments'][1]['entry_ids'] = ['missing-b']
    with pytest.raises(ValueError, match='missing-a, missing-b'):
        grammar.validate(broken, rows)


def test_assignment_errors_report_all_missing_and_repeated_records():
    _, rows = grammar.candidates()
    selected = rows[:3]
    data = fixture(selected)
    data['assignments'] = [data['assignments'][0]] * 3
    with pytest.raises(ValueError) as caught:
        grammar.validate(data, selected)
    assert str(caught.value) == (
        'Grammar candidates must be assigned exactly once: '
        f"missing={[r['id'] for r in selected[1:]]}; "
        f"extra={[selected[0]['id']] * 2}")
    aliases = {r['id']: f'C{i}' for i, r in enumerate(selected)}
    assert grammar.routing_error(caught.value, aliases).endswith(
        "missing=['C1', 'C2']; extra=['C0', 'C0']")
    grammar.validate(fixture(selected), selected)


def test_missing_form_meanings_report_every_candidate_and_field():
    _, rows = grammar.candidates()
    selected = [r for r in rows if r['layer'] == 'form'][:3]
    data = fixture(selected)
    del data['assignments'][0]['display_meaning_en']
    del data['assignments'][0]['display_base_meaning_en']
    data['assignments'][1]['display_base_meaning_en'] = ' '
    del data['assignments'][2]['display_meaning_en']
    del data['assignments'][2]['display_base_meaning_en']
    with pytest.raises(ValueError) as caught:
        grammar.validate(data, selected, require_form_meanings=True)
    import json
    missing = json.loads(str(caught.value).split('missing fields: ', 1)[1])
    assert missing == {
        selected[0]['id']: ['display_meaning_en', 'display_base_meaning_en'],
        selected[1]['id']: ['display_base_meaning_en'],
        selected[2]['id']: ['display_meaning_en', 'display_base_meaning_en'],
    }
    grammar.validate(fixture(selected), selected, require_form_meanings=True)


def test_empty_grammar_destination_is_rejected_for_plain_verbs_and_form_steps():
    _, rows = grammar.candidates()
    plain = next(r for r in rows if r['layer'] == 'segment' and
                 r['annotation']['type'] == 'word' and
                 r['annotation']['conjugation_form'] == 'plain nonpast' and
                 not r['annotation']['form_steps'])
    form = next(r for r in rows if r['layer'] == 'form')
    for row in (plain, form):
        valid = fixture([row])
        grammar.validate(valid, [row], require_form_meanings=True)
        invalid = deepcopy(valid)
        invalid['assignments'][0]['entry_ids'] = ['']
        with pytest.raises(ValidationError):
            grammar.validate(invalid, [row], require_form_meanings=True)


def test_repair_replacement_cannot_drop_other_new_lesson_definitions():
    _, rows = grammar.candidates()
    selected = rows[:2]
    data = fixture(selected)
    first = dict(data['entries'][0], id='ja-grammar-new-first')
    second = dict(data['entries'][0], id='ja-grammar-new-second')
    data['entries'] = [first, second]
    data['assignments'][0]['entry_ids'] = [first['id']]
    data['assignments'][1]['entry_ids'] = [second['id']]
    grammar.validate(data, selected)
    partial = deepcopy(data)
    partial['entries'] = [second]
    with pytest.raises(ValueError, match='Unresolved grammar reference: ja-grammar-new-first'):
        grammar.validate(grammar.complete_entries(partial, []), selected)


def test_repair_payload_omits_approved_prose_without_losing_new_entries_or_assignments():
    _, rows = grammar.candidates()
    previous = fixture(rows)['entries']
    new = dict(previous[0], id='ja-grammar-new', title='New pattern')
    data = dict(entries=previous + [new], assignments=fixture(rows)['assignments'])
    original = deepcopy(data)
    compact = grammar.repair_payload(data, previous)
    assert compact['entries'] == [new]
    assert compact['assignments'] == data['assignments']
    assert grammar.complete_entries(compact, previous) == original
    assert data == original


def test_offline_review_and_unchanged_reuse(tmp_path, monkeypatch):
    from pipeline.agent_harness import CodexRunner
    _, rows = grammar.candidates()
    data = fixture(rows)
    calls = []
    async def call(self, job, prompt, schema, effort, **options):
        calls.append(effort)
        assert options['tool_profile'] == 'offline'
        assert grammar.ENTRY_FOCUS_POLICY in prompt
        return deepcopy(data)
    monkeypatch.setattr(CodexRunner, 'call', call)
    monkeypatch.setattr(grammar, 'REGISTRY', tmp_path / 'registry.json')
    monkeypatch.setattr(grammar, 'OUTPUT', tmp_path / 'published.json')
    monkeypatch.setattr(grammar, 'REQUESTS', tmp_path / 'requests.json')
    result = asyncio.run(grammar.update(run_dir=tmp_path / 'run', objection_accountability=None))
    assert calls == ['low', 'high']
    assert asyncio.run(grammar.update(run_dir=tmp_path / 'run', objection_accountability=None)) == result
    assert calls == ['low', 'high']
    registry = words.read(grammar.REGISTRY)
    registry['data']['entries'][0]['summary_en'] = 'Unreviewed mutation'
    words.atomic_json(grammar.REGISTRY, registry)
    with pytest.raises(ValueError, match='valid review'):
        grammar.build()


def test_scoped_grammar_edits_preserve_unrequested_lessons(tmp_path, monkeypatch):
    from pipeline.agent_harness import CodexRunner
    _, rows = grammar.candidates()
    data = fixture(rows)
    untouched = dict(data['entries'][0], id='ja-grammar-other', title='Other')
    data['entries'].append(untouched)
    monkeypatch.setattr(grammar, 'REGISTRY', tmp_path / 'registry.json')
    monkeypatch.setattr(grammar, 'OUTPUT', tmp_path / 'published.json')
    monkeypatch.setattr(grammar, 'REQUESTS', tmp_path / 'requests.json')
    words.atomic_json(grammar.REGISTRY, dict(reviewed=True,
        source_fingerprint=grammar.decision_digest(rows), data=data,
        review_digest=grammar.decision_digest(data)))
    words.atomic_json(grammar.REQUESTS, [dict(id='edit', entry_id='ja-grammar-test', reason='Clarify')])
    calls = []
    async def call(self, job, prompt, schema, effort, **options):
        calls.append(effort)
        assert grammar.ENTRY_FOCUS_POLICY in prompt
        return dict(entries=[dict(data['entries'][0], explanation_en='Clearer')], assignments=[])
    monkeypatch.setattr(CodexRunner, 'call', call)
    asyncio.run(grammar.update(objection_accountability=None))
    assert calls == ['low', 'high']
    after = words.read(grammar.REGISTRY)
    assert after['data']['entries'][1] == untouched
    assert after['data']['entries'][0]['explanation_en'] == 'Clearer'
    asyncio.run(grammar.update(objection_accountability=None))
    assert calls == ['low', 'high']


def test_new_grammar_occurrence_reuses_existing_assignments_and_lessons(tmp_path, monkeypatch):
    from pipeline.agent_harness import CodexRunner
    sources, rows = grammar.candidates()
    initial = deepcopy(rows)
    monkeypatch.setattr(grammar, 'REGISTRY', tmp_path / 'registry.json')
    monkeypatch.setattr(grammar, 'OUTPUT', tmp_path / 'published.json')
    monkeypatch.setattr(grammar, 'REQUESTS', tmp_path / 'requests.json')
    monkeypatch.setattr(grammar, 'candidates', lambda: (sources, rows))
    calls = []
    async def call(self, job, prompt, schema, effort, **options):
        calls.append(effort)
        if len(calls) <= 2:
            return fixture(initial)
        delta = fixture([rows[-1]])
        delta['entries'] = []  # Existing prose is reused locally, not reprinted.
        return delta
    monkeypatch.setattr(CodexRunner, 'call', call)
    before = asyncio.run(grammar.update(objection_accountability=None))
    rows.append(dict(rows[0], id=rows[0]['id'] + '-new'))
    after = asyncio.run(grammar.update(objection_accountability=None))
    assert calls == ['low', 'high', 'low', 'high']
    assert after['entries'] == before['entries']
    assert len(after['occurrences']) == len(before['occurrences']) + 1
    by_id = {u['id']: u for u in after['occurrences']}
    for use in before['occurrences']:
        assert by_id[use['id']] == use


def test_polite_steps_do_not_link_to_the_larger_construction():
    _, rows = grammar.candidates()
    polite = next(r for r in rows if r['layer'] == 'form' and r['form_label'] == 'polite')
    data = fixture([polite])
    data['entries'][0]['kind'] = 'construction'
    with pytest.raises(ValueError, match='inflection introduced here'):
        grammar.validate(data, [polite])


def test_merged_stage_candidates_keep_whole_forms_separate_from_tail_forms():
    _, rows = grammar.candidates()
    merged = [r for r in rows if 'display_context' in r]
    assert {r['display_surface'] for r in merged} == {'住むことにしました', '目が回りました'}
    decision = [r for r in merged if r['display_surface'] == '住むことにしました']
    assert [r['form'] for r in decision] == ['ことにします', 'ことにしました']
    assert [r['display_form'] for r in decision] == ['住むことにします', '住むことにしました']
    assert all(r['display_base_form'] == '住むことにする' for r in decision)
    idiom = [r for r in merged if r['display_surface'] == '目が回りました']
    assert [r['display_form'] for r in idiom] == ['目が回ります', '目が回りました']
    assert all(r['display_base_form'] == '目が回る' for r in idiom)
    data = fixture(merged)
    grammar.validate(data, merged)
    disagreement = deepcopy(data)
    disagreement['assignments'][1]['display_base_meaning_en'] = 'Different base meaning'
    with pytest.raises(ValueError, match='consistent reviewed meaning') as caught:
        grammar.validate(disagreement, merged)
    assert merged[0]['id'] in str(caught.value)
    assert merged[1]['id'] in str(caught.value)
    assert 'Different base meaning' in str(caught.value)
    del data['assignments'][0]['display_meaning_en']
    del data['assignments'][0]['display_base_meaning_en']
    with pytest.raises(ValueError, match='complete displayed forms'):
        grammar.validate(data, merged)


def test_noun_absence_chain_uses_the_readers_complete_display_unit():
    annotation = words.read(words.ANNOTATION.with_name('n3.annotations.json'))
    original = deepcopy(annotation)
    _, rows = grammar.candidates(annotation)
    merged = [r for r in rows if r['layer'] == 'form'
              and r.get('display_surface') == '考えがなく']
    assert len(merged) == 1
    assert merged[0]['surface'] == 'なく'
    assert merged[0]['display_form'] == '考えがなく'
    assert merged[0]['display_base_form'] == '考えがない'
    assert any(r['layer'] == 'form' and 'display_context' not in r for r in rows)
    assert annotation == original


def test_overlay_readings_are_actual_surface_not_canonical_base():
    annotation = words.read(words.ANNOTATION)
    before = deepcopy(annotation)
    _, rows = grammar.candidates(annotation)
    actual = {r['surface']: r['reading'] for r in rows if r['layer'] == 'overlay'}
    assert actual == {'どこで生まれたか': 'どこでうまれたか', '見ながら': 'みながら',
                      '置いてやれ': 'おいてやれ', '住むことにしました': 'すむことにしました'}
    assert annotation == before
    # Reading cannot be guessed by cutting kana at a kanji-character offset.
    chapter = dict(segments=[dict(surface='生まれた', surface_kana='うまれた')])
    assert grammar.surface_reading(chapter, 0, 4) == 'うまれた'
    assert grammar.surface_reading(chapter, 1, 4) == ''


def test_reused_prose_comes_from_registry_not_an_agent_echo():
    previous = fixture([])['entries']
    echo = dict(previous[0], explanation_en='Unrequested rewording')
    data = grammar.complete_entries(dict(entries=[echo], assignments=[]), previous)
    assert data['entries'] == previous
    with pytest.raises(ValueError, match='identity changed'):
        grammar.complete_entries(dict(entries=[dict(echo, title='Different function')], assignments=[]), previous)


def test_grammar_prerequisites_must_resolve_and_be_acyclic():
    data = fixture([])
    data['entries'][0]['formation'][0]['grammar_entry_id'] = 'ja-grammar-missing'
    with pytest.raises(ValueError, match='Unresolved grammar prerequisite'):
        grammar.validate(data, [])
    other = deepcopy(data['entries'][0])
    other['id'] = 'ja-grammar-other'
    other['formation'][0]['grammar_entry_id'] = 'ja-grammar-test'
    data['entries'].append(other)
    data['entries'][0]['formation'][0]['grammar_entry_id'] = 'ja-grammar-other'
    with pytest.raises(ValueError, match='Cyclic grammar prerequisites'):
        grammar.validate(data, [])
    del other['formation'][0]['grammar_entry_id']
    grammar.validate(data, [])


@pytest.mark.parametrize('change_links', [False, True])
def test_scoped_occurrence_edits_cannot_change_source_forms_or_links(tmp_path, monkeypatch, change_links):
    from pipeline.agent_harness import CodexRunner
    _, rows = grammar.candidates()
    target = next(r for r in rows if r['layer'] == 'form' and r['surface'] == '歩き')
    data = fixture(rows)
    registry = dict(data=data, applied_requests=[])
    before = deepcopy(registry)
    assignment = next(a for a in data['assignments'] if a['candidate_id'] == target['id'])
    calls = []
    async def call(self, job, prompt, schema, effort, **options):
        calls.append(effort)
        edited = dict(assignment, context_en='Links walking to the following action.',
            display_meaning_en='walking', display_base_meaning_en='walk')
        if change_links:
            edited['entry_ids'] = ['ja-grammar-other']
        return dict(entries=[], assignments=[edited])
    monkeypatch.setattr(CodexRunner, 'call', call)
    requests = [dict(id='note-edit', candidate_id=target['id'], reason='Complete linking meaning')]
    if change_links:
        with pytest.raises(ValueError, match='cannot change grammar links'):
            asyncio.run(grammar.edit_occurrence_notes(registry, rows, requests, tmp_path, 1))
        assert registry == before
    else:
        asyncio.run(grammar.edit_occurrence_notes(registry, rows, requests, tmp_path, 1))
        assert registry['data']['entries'] == before['data']['entries']
        changed = [a for a, b in zip(registry['data']['assignments'], before['data']['assignments']) if a != b]
        assert len(changed) == 1
        assert changed[0]['entry_ids'] == assignment['entry_ids']
        assert changed[0]['display_meaning_en'] == 'walking'
        assert registry['applied_requests'] == ['note-edit']
    assert calls == ['low', 'high']


def test_reusable_patterns_do_not_catalog_separate_inflection_lessons():
    entries = {e['id']: e for e in grammar.build()['entries']}
    unrelated_forms = {
        'ja-grammar-decision-koto-ni-suru': ('ことにしました',),
        'ja-grammar-teiru': ('ています', 'ていました'),
        'ja-grammar-passive': ('捨てられました',),
        'ja-grammar-te-yaru': ('やれ', '置いてやれ'),
    }
    for entry_id, forms in unrelated_forms.items():
        prose = str(entries[entry_id])
        assert not any(form in prose for form in forms), entry_id
    # Inflection lessons still explain THEIR target form; the rule must not
    # blindly remove all mentions of past or imperative morphology.
    assert 'ました' in str(entries['ja-grammar-polite-past']['formation'])
    assert 'e-row' in str(entries['ja-grammar-imperative']['formation'])
    assert 'ろ' in str(entries['ja-grammar-imperative']['formation'])


def test_published_grammar_and_word_routes_resolve():
    if not grammar.REGISTRY.exists():
        pytest.skip('Grammar pilot review not yet published')
    published = grammar.build()
    assert published == words.read(grammar.OUTPUT)
    known = {e['id'] for e in published['entries']}
    assert all(set(u['entry_ids']) <= known for u in published['occurrences'])
    dictionary = words.build()
    wa = next(e for e in dictionary['entries'] if e['headword'] == 'は')
    seeing = next(e for e in dictionary['entries'] if e['headword'] == '見る')
    assert wa['id'] in published['word_routes']
    assert seeing['id'] not in published['word_routes']
    n5 = [u for u in published['occurrences'] if
          u['source'] == 'assets/annotations/japanese_wagahai_n5_001.json']
    ga = [u for u in n5 if u['surface'] == 'が']
    assert len({tuple(u['entry_ids']) for u in ga}) == 2
    stages = [u for u in n5 if u['surface'] == '入りました' and u['layer'] == 'form']
    assert [u['form'] for u in stages[:2]] == ['入ります', '入りました']
    assert stages[0]['entry_ids'] != stages[1]['entry_ids']
    assert stages[1]['entry_ids'] == ['ja-grammar-polite-past']
    for use in published['occurrences']:
        if use['layer'] == 'form' and use['form'] == 'ことにします':
            assert use['entry_ids'] == stages[0]['entry_ids']
    complete = {u['display_form']: u for u in n5 if 'display_form' in u}
    assert complete['住むことにします']['display_meaning_en'] == 'decide to live'
    assert complete['住むことにしました']['display_meaning_en'] == 'decided to live'
    assert complete['住むことにします']['display_base_meaning_en'] == 'decide to live'
    assert 'dizzy' in complete['目が回りました']['display_meaning_en']
    assert all('display_context' not in u for u in published['occurrences'])
