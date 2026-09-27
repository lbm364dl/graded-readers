import asyncio
import copy
import json

import pytest

from pipeline import usage_dictionary as ud


@pytest.fixture
def corpus(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"level": "hsk1", "chapters": [{
        "number": 1, "text": "来。来！", "annotation_audit": {"all_reviewed": True},
        "segments": [
            {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come"},
            {"text": "。", "type": "punctuation"},
            {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come here"},
            {"text": "！", "type": "punctuation"},
        ],
    }]}))
    _, _, occurrences, fingerprint = ud.source_data(source)
    entries = ud.allocate_ids([{"headword": "来", "reading": "lái", "kind": "word",
        "senses": [{"definition": "come", "occurrences": [o["id"] for o in occurrences]}]}])
    decisions = dict(source_fingerprint=fingerprint, reviewed=True, entries=entries,
                     review_digest=ud.decision_digest(entries))
    return source, decisions


def test_build_links_repeated_uses_and_exact_sentences(corpus):
    source, decisions = corpus
    result = ud.build(decisions, source)
    assert len(result["entries"]) == 1
    assert [o["sentence"] for o in result["occurrences"]] == ["来。", "来！"]
    assert [o["start"] for o in result["occurrences"]] == [0, 2]
    assert len({o["sense_id"] for o in result["occurrences"]}) == 1


def test_restore_copied_id_typo_without_guessing_sense_meaning(corpus):
    _, decisions = corpus
    previous = decisions['entries']
    entries = copy.deepcopy(previous)
    entries[0]['id'] = 'typo'
    entries[0]['senses'][0]['id'] = 'typo-s1'
    ud.restore_unambiguous_ids(previous, entries)
    assert entries == previous
    entries[0]['senses'][0]['id'] = entries[0]['id'] + '-s99'
    ud.restore_unambiguous_ids(previous, entries)
    assert entries == previous
    entries[0]['id'] = 'typo'
    entries[0]['senses'][0]['id'] = 'typo-s1'
    entries[0]['senses'][0]['definition'] = 'different meaning'
    ud.restore_unambiguous_ids(previous, entries)
    with pytest.raises(ValueError, match='identity'):
        ud.check_identity(previous, entries)


def test_shared_metadata_guard_allows_new_links_and_senses_not_rewrites(corpus):
    _, decisions = corpus
    old = decisions['entries']
    revised = copy.deepcopy(old)
    revised[0]['senses'][0]['occurrences'] = []
    revised[0]['senses'].append({'id': revised[0]['id'] + '-s2',
                               'definition': 'another meaning', 'occurrences': []})
    assert ud.shared_metadata_errors(old, revised) == []
    revised[0]['reading'] = 'lai'
    revised[0]['kind'] = 'name'
    revised[0]['senses'][0]['definition'] = 'silently rewritten'
    errors = ud.shared_metadata_errors(old, revised)
    assert len(errors) == 3
    assert any('reading' in error for error in errors)
    assert any('kind' in error for error in errors)
    assert any('definition' in error for error in errors)


@pytest.mark.parametrize('problem', ['fingerprint_typo', 'wrong_source', 'wrong_position', 'wrong_word'])
def test_occurrence_reference_repair_is_exactly_scoped(problem):
    identity = 'assets/hsk4.json#abcdef:123'
    rows = [('听', [{'id': identity}])]
    reference = identity.replace('abcdef', 'abcxef')
    headword = '听'
    if problem == 'wrong_source':
        reference = identity.replace('hsk4', 'hsk3')
    elif problem == 'wrong_position':
        reference = reference.replace(':123', ':124')
    elif problem == 'wrong_word':
        headword = '听到'
    entries = [{'headword': headword, 'senses': [{'occurrences': [reference]}]}]
    ud.restore_unambiguous_occurrence_refs(rows, entries)
    assert entries[0]['senses'][0]['occurrences'] == [
        identity if problem == 'fingerprint_typo' else reference]


@pytest.mark.parametrize('problem', ['duplicate', 'wrong_source', 'wrong_position', 'wrong_word', 'insertion'])
def test_duplicated_fingerprint_repair_does_not_guess_occurrences(problem):
    identity = 'assets/hsk6.json#54753c68d1e4:564'
    reference = identity.replace('d1e4', 'd1d1e4')
    word = '说'
    if problem == 'wrong_source':
        reference = reference.replace('hsk6', 'hsk5')
    elif problem == 'wrong_position':
        reference = reference.replace(':564', ':565')
    elif problem == 'wrong_word':
        word = '说道'
    elif problem == 'insertion':
        reference = identity.replace('d1e4', 'd1abe4')
    entries = [dict(headword=word, senses=[dict(occurrences=[reference])])]
    ud.restore_unambiguous_occurrence_refs([('说', [dict(id=identity)])], entries)
    assert entries[0]['senses'][0]['occurrences'] == [identity if problem == 'duplicate' else reference]


def test_repair_findings_scope_ids_without_prefix_collisions():
    entries = [{'id': 'word-1', 'headword': '来'}, {'id': 'word-10', 'headword': '去'}]
    assert ud.repair_headwords(entries, ['word-10-s1: correct meaning']) == {'去'}
    assert ud.repair_headwords(entries, ['word-1-s1: correct meaning']) == {'来'}
    assert ud.repair_headwords(entries, ['unclear finding']) == {'来', '去'}


def test_review_repairs_only_affected_word_and_preserves_other_links(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    document = json.loads(source.read_text())
    chapter = document['chapters'][0]
    chapter['text'] += '去。'
    chapter['segments'] += [
        {'text': '去', 'type': 'word', 'pinyin': 'qù', 'meaning_en': 'go'},
        {'text': '。', 'type': 'punctuation'}]
    source.write_text(json.dumps(document))
    _, groups, _, fingerprint = ud.source_data(source)
    decisions['source_fingerprint'] = fingerprint
    decisions['reviewed'] = False
    decisions['entries'][0]['senses'][0]['occurrences'] = [o['id'] for o in groups['来']]
    unaffected = copy.deepcopy(decisions['entries'][0])
    affected = ud.allocate_ids([{'headword': '去', 'reading': 'qù', 'kind': 'word',
        'senses': [{'definition': 'bad definition', 'occurrences': [o['id'] for o in groups['去']]}]}])[0]
    decisions['entries'].append(affected)
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(decisions))
    repaired = copy.deepcopy(affected)
    repaired['senses'][0]['definition'] = 'go'
    repairs = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        if job in {'review-00-0', 'adjudicate-00-0'}:
            return {'approved': False, 'issues': [affected['id'] + '-s1: correct definition to go']}
        if job.startswith('repair-'):
            assert unaffected['id'] not in prompt
            repairs.append(job)
            return {'entries': [repaired]}
        return {'approved': True, 'issues': []}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    asyncio.run(ud.review(path, tmp_path, 'gpt-6-luna', source=source))
    result = json.loads(path.read_text())
    assert next(e for e in result['entries'] if e['headword'] == '来') == unaffected
    assert next(e for e in result['entries'] if e['headword'] == '去') == repaired
    assert repairs == ['repair-00-0']
    ud.build(result, source)


@pytest.mark.parametrize('error', ['duplicate', 'missing', 'unknown', 'wrong_headword'])
def test_batch_rejects_invalid_links(corpus, error):
    source, decisions = corpus
    _, groups, _, _ = ud.source_data(source)
    entries = copy.deepcopy(decisions['entries'])
    uses = entries[0]['senses'][0]['occurrences']
    if error == 'duplicate':
        uses.append(uses[0])
    elif error == 'missing':
        uses.pop()
    elif error == 'unknown':
        uses.append('unknown')
    else:
        entries[0]['headword'] = '去'
    with pytest.raises(ValueError):
        ud.check_batch_links(list(groups.items()), entries)


@pytest.mark.parametrize("mutation", ["unreviewed", "stale", "edited", "missing", "duplicate", "unknown"])
def test_rejects_invalid_publication(corpus, mutation):
    source, decisions = corpus
    uses = decisions["entries"][0]["senses"][0]["occurrences"]
    if mutation == "unreviewed":
        decisions["reviewed"] = False
    elif mutation == "stale":
        decisions["source_fingerprint"] = "old"
    elif mutation == "edited":
        decisions["entries"][0]["senses"][0]["definition"] = "wrong"
    elif mutation == "missing":
        uses.pop()
    elif mutation == "duplicate":
        uses.append(uses[0])
    elif mutation == "unknown":
        uses[0] = "nonexistent"
    if mutation in {"missing", "duplicate", "unknown"}:
        decisions["review_digest"] = ud.decision_digest(decisions["entries"])
    with pytest.raises(ValueError):
        ud.build(decisions, source)


def test_retired_sense_keeps_id_but_not_published(corpus):
    source, decisions = corpus
    entry = decisions["entries"][0]
    entry["senses"].append(dict(id=entry["id"] + "-s2", definition="retired", occurrences=[]))
    decisions["review_digest"] = ud.decision_digest(decisions["entries"])
    assert len(ud.build(decisions, source)["entries"][0]["senses"]) == 1


def test_identity_guard(corpus):
    _, decisions = corpus
    previous = decisions["entries"]
    entries = copy.deepcopy(previous)
    entries[0]["senses"][0]["definition"] = "come towards the speaker"
    ud.check_identity(previous, entries)
    entries[0]["senses"][0]["id"] += "-changed"
    with pytest.raises(ValueError, match="sense identity"):
        ud.check_identity(previous, entries)
    with pytest.raises(ValueError, match="entry identity"):
        ud.check_identity(previous, [])


def test_homonyms_receive_distinct_ids():
    entries = ud.allocate_ids([dict(headword="行", senses=[{}]), dict(headword="行", senses=[{}])])
    assert entries[0]["id"] != entries[1]["id"]


def test_update_no_source_change_makes_no_calls(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions))
    async def unexpected(*args, **kwargs):
        raise AssertionError("unnecessary agent call")
    monkeypatch.setattr("pipeline.agent_harness.CodexRunner.call", unexpected)
    asyncio.run(ud.propose(path, tmp_path, "gpt-6-luna", update=True, source=source))
    assert json.loads(path.read_text()) == decisions


@pytest.mark.parametrize('identity', ['entry', 'sense'])
def test_proposal_repairs_shared_identity_loss_before_accepting(corpus, tmp_path, monkeypatch, identity):
    source, decisions = corpus
    previous = copy.deepcopy(decisions)
    previous['source_fingerprint'] = 'new source needs matching'
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(previous))
    jobs = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        jobs.append((job, effort))
        entries = copy.deepcopy(decisions['entries'])
        if effort == 'low':
            target = entries[0] if identity == 'entry' else entries[0]['senses'][0]
            target['id'] += '-renamed'
        else:
            assert decisions['entries'][0]['id'] in prompt
            assert kwargs['tool_profile'] == 'offline'
        return {'entries': entries}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    asyncio.run(ud.propose(path, tmp_path, 'gpt-6-luna', update=True, source=source))
    expected = [('batch-00', 'low')]
    if identity == 'entry':
        expected.append(('batch-00-identity-repair', 'high'))
    assert jobs == expected
    result = json.loads(path.read_text())
    assert result['entries'] == decisions['entries']
    ud.build(result, source, require_review=False)


def test_repeated_proposal_identity_loss_preserves_existing_registry(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    decisions['source_fingerprint'] = 'changed'
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(decisions))
    original = path.read_bytes()

    async def call(*args, **kwargs):
        return {'entries': []}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    with pytest.raises(ValueError, match='entry identity'):
        asyncio.run(ud.propose(path, tmp_path, 'gpt-6-luna', update=True, source=source))
    assert path.read_bytes() == original


@pytest.mark.parametrize('problem', ['missing_use', 'unknown_word', 'scope_violation'])
def test_proposal_repairs_only_structurally_affected_headwords(corpus, tmp_path, monkeypatch, problem):
    source, decisions = corpus
    document = json.loads(source.read_text())
    chapter = document['chapters'][0]
    chapter['text'] += '去。'
    chapter['segments'] += [
        dict(text='去', type='word', pinyin='qù', meaning_en='go'),
        dict(text='。', type='punctuation')]
    source.write_text(json.dumps(document))
    _, groups, _, fingerprint = ud.source_data(source)
    good = copy.deepcopy(decisions['entries'][0])
    good['senses'][0]['occurrences'] = [o['id'] for o in groups['来']]
    other = ud.allocate_ids([dict(headword='去', reading='qù', kind='word',
        senses=[dict(definition='go', occurrences=[o['id'] for o in groups['去']])])])[0]
    previous = dict(decisions, source_fingerprint='changed', entries=[good, other])
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(previous))
    original = path.read_bytes()
    jobs = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        jobs.append(job)
        if effort == 'low':
            entries = copy.deepcopy([good, other])
            if problem == 'unknown_word':
                entries.append(dict(other, headword='unexpected'))
            else:
                entries[1]['senses'][0]['occurrences'] = []
            return dict(entries=entries)
        assert '"headword": "去"' in prompt
        if problem == 'unknown_word':
            assert '"headword": "来"' in prompt
            return dict(entries=copy.deepcopy([good, other]))
        assert '"headword": "来"' not in prompt
        if problem == 'scope_violation':
            return dict(entries=copy.deepcopy([good, other]))
        return dict(entries=copy.deepcopy([other]))

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    if problem == 'scope_violation':
        with pytest.raises(ValueError, match='unaffected headword'):
            asyncio.run(ud.propose(path, tmp_path, 'gpt-6-luna', update=True, source=source))
        assert path.read_bytes() == original
    else:
        asyncio.run(ud.propose(path, tmp_path, 'gpt-6-luna', update=True, source=source))
        saved = json.loads(path.read_text())
        assert saved['source_fingerprint'] == fingerprint
        assert next(e for e in saved['entries'] if e['headword'] == '来') == good
        ud.build(saved, source, require_review=False)
    assert jobs == ['batch-00', 'batch-00-identity-repair']


def test_proposal_scope_includes_both_sides_of_misassigned_use():
    rows = [('来', [dict(id='come-use')]), ('去', [dict(id='go-use')]),
            ('看', [dict(id='look-use')])]
    entries = ud.allocate_ids([
        dict(headword=word, senses=[dict(definition=word, occurrences=[uses[0]['id']])])
        for word, uses in rows])
    previous = copy.deepcopy(entries)
    entries[0]['senses'][0]['occurrences'].append('go-use')
    entries[1]['senses'][0]['occurrences'] = []
    assert ud.proposal_repair_headwords(previous, rows, entries) == {'来', '去'}


def test_review_repair_then_independent_approval(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    decisions["reviewed"] = False
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions))
    jobs = []
    async def call(self, job, prompt, schema, effort, **kwargs):
        assert kwargs['tool_profile'] == 'offline'
        jobs.append(job)
        assert effort == ("medium" if job.startswith("adjudicate") else "low")
        if job in {"review-00-0", "adjudicate-00-0"}:
            return {"approved": False, "issues": ["Improve definition"]}
        if job == "repair-00-0":
            return {"entries": decisions["entries"]}
        return {"approved": True, "issues": []}
    monkeypatch.setattr("pipeline.agent_harness.CodexRunner.call", call)
    asyncio.run(ud.review(path, tmp_path, "gpt-6-luna", source=source))
    assert jobs == ["review-00-0", "adjudicate-00-0", "repair-00-0", "review-00-1"]
    ud.build(json.loads(path.read_text()), source)


def test_review_reuses_frozen_identity_absent_from_local_registry(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    decisions['reviewed'] = False
    local = decisions['entries'][0]
    canonical = copy.deepcopy(local)
    canonical['id'] = 'zh-expression-existing'
    canonical['senses'][0]['id'] = canonical['id'] + '-s1'
    retired = copy.deepcopy(local)
    retired['senses'][0]['occurrences'] = []
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(decisions))

    async def call(self, job, *args, **kwargs):
        if job.startswith('repair-'):
            return dict(entries=copy.deepcopy([retired, canonical]))
        return dict(approved=True, issues=[])

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    asyncio.run(ud.review(path, tmp_path, 'gpt-6-luna', source=source,
                          frozen_entries=[canonical]))
    saved = json.loads(path.read_text())
    assert {entry['id'] for entry in saved['entries']} == {local['id'], canonical['id']}
    published = ud.build(saved, source)
    assert published['entries'][0]['id'] == canonical['id']
    assert all(use['sense_id'] == canonical['senses'][0]['id']
               for use in published['occurrences'])


@pytest.mark.parametrize('batch_size', [0, 21, True, 2.5, '5'])
def test_review_rejects_invalid_batch_sizes(tmp_path, batch_size):
    with pytest.raises(ValueError, match='batch_size'):
        asyncio.run(ud.review(tmp_path / 'missing.json', tmp_path,
                              'gpt-6-luna', batch_size=batch_size))


def test_small_review_batches_cover_every_headword(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    decisions['reviewed'] = False
    retired = ud.allocate_ids([
        dict(headword=f'old{i:02}', reading='old', kind='word',
             senses=[dict(definition='retired', occurrences=[])])
        for i in range(5)])
    decisions['entries'] = retired + decisions['entries']
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(decisions))
    jobs = []

    async def call(self, job, *args, **kwargs):
        jobs.append(job)
        return {'approved': True, 'issues': []}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    asyncio.run(ud.review(path, tmp_path, 'gpt-6-luna', source=source,
                          batch_size=2))
    assert sorted(jobs) == ['review-00-0', 'review-01-0', 'review-02-0']
    saved = json.loads(path.read_text())
    assert set(saved['reviewed_word_fingerprints']) == {
        entry['headword'] for entry in decisions['entries']}
    ud.build(saved, source)


def test_failed_review_does_not_overwrite_decisions(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    decisions["reviewed"] = False
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions))
    original = path.read_text()
    async def call(*args, **kwargs):
        return {"approved": False, "issues": ["Wrong sense"]}
    monkeypatch.setattr("pipeline.agent_harness.CodexRunner.call", call)
    with pytest.raises(ValueError, match="Review failed"):
        asyncio.run(ud.review(path, tmp_path, "gpt-6-luna", source=source, rounds=1))
    assert path.read_text() == original


def test_failed_sibling_preserves_only_successful_reviews(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    decisions['reviewed'] = False
    # Retired identities are valid registry members and precede the live word.
    retired = ud.allocate_ids([
        dict(headword=f'old{i:02}', reading='old', kind='word',
             senses=[dict(definition='retired', occurrences=[])])
        for i in range(20)])
    decisions['entries'] = retired + decisions['entries']
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(decisions))

    async def call(self, job, *args, **kwargs):
        if job.startswith('review-00'):
            return {'approved': True, 'issues': []}
        return {'approved': False, 'issues': ['Wrong sense']}

    monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call', call)
    with pytest.raises(ValueError, match='Review failed'):
        asyncio.run(ud.review(path, tmp_path, 'gpt-6-luna', source=source, rounds=1))
    saved = json.loads(path.read_text())
    assert saved['reviewed'] is False
    assert set(saved['reviewed_word_fingerprints']) == {e['headword'] for e in retired}
    assert saved['review_digest'] == ud.decision_digest(saved['entries'])
    assert saved['entries'][-20:] == retired
    ud.build(saved, source, require_review=False)
    with pytest.raises(ValueError, match='must be reviewed'):
        ud.build(saved, source)


def test_adjudicator_rejects_spurious_objection_without_repair(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions))
    jobs = []
    async def call(self, job, prompt, schema, effort, **kwargs):
        assert kwargs['tool_profile'] == 'offline'
        jobs.append(job)
        if job.startswith("review"):
            return {"approved": False, "issues": ["Acceptable variant should be different"]}
        return {"approved": True, "issues": []}
    monkeypatch.setattr("pipeline.agent_harness.CodexRunner.call", call)
    asyncio.run(ud.review(path, tmp_path, "gpt-6-luna", source=source))
    assert jobs == ["review-00-0", "adjudicate-00-0"]
    assert json.loads(path.read_text())["entries"] == decisions["entries"]


def test_update_matches_new_chapter_to_existing_sense(corpus, tmp_path, monkeypatch):
    source, decisions = corpus
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions))
    document = json.loads(source.read_text())
    chapter = copy.deepcopy(document["chapters"][0])
    chapter["number"] = 2
    document["chapters"].append(chapter)
    source.write_text(json.dumps(document))
    _, _, uses, _ = ud.source_data(source)
    async def call(self, job, prompt, schema, effort, **kwargs):
        assert kwargs['tool_profile'] == 'offline'
        assert decisions["entries"][0]["id"] in prompt
        entries = copy.deepcopy(decisions["entries"])
        entries[0]["senses"][0]["occurrences"] = [o["id"] for o in uses]
        return {"entries": entries}
    monkeypatch.setattr("pipeline.agent_harness.CodexRunner.call", call)
    asyncio.run(ud.propose(path, tmp_path, "gpt-6-luna", update=True, source=source))
    result = json.loads(path.read_text())
    assert result["reviewed"] is False
    assert result["entries"][0]["id"] == decisions["entries"][0]["id"]
    assert len(ud.build(result, source, require_review=False)["occurrences"]) == 4


def test_published_pilot_is_reproducible():
    if not ud.OUTPUT.exists():
        pytest.skip("Pilot has not yet passed review")
    from pipeline import dictionary_corpus
    payload = (dictionary_corpus.build(publish=False) if dictionary_corpus.MANIFEST.exists()
               else ud.build_published(json.loads(ud.DECISIONS.read_text())))
    assert payload == json.loads(ud.OUTPUT.read_text())
