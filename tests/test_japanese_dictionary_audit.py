import asyncio
from copy import deepcopy

import pytest

from pipeline import japanese_dictionary_audit as audit
from pipeline import japanese_usage_dictionary as words


def fixture():
    payload = dict(word_entries=[dict(id='word')], grammar_entries=[dict(id='grammar')],
        segments=[dict(id='segment')], overlays=[dict(id='overlay')],
        occurrences=[dict(id='occurrence')])
    result = dict(word_entry_ids=['word'], grammar_entry_ids=['grammar'],
        segment_ids=['segment'], overlay_ids=['overlay'], occurrence_ids=['occurrence'],
        issues=[dict(kind='grammar', target_id='grammar', reason='Stale aside',
                     requested_change='Remove unrelated aside')])
    return payload, result


def test_audit_requires_complete_unique_coverage_and_real_targets():
    payload, result = fixture()
    audit.validate(result, payload)
    for key in ['word_entry_ids', 'grammar_entry_ids', 'segment_ids',
                'overlay_ids', 'occurrence_ids']:
        missing = deepcopy(result)
        missing[key] = []
        with pytest.raises(ValueError, match='coverage'):
            audit.validate(missing, payload)
        duplicate = deepcopy(result)
        duplicate[key] *= 2
        with pytest.raises(ValueError, match='coverage'):
            audit.validate(duplicate, payload)
    invalid = deepcopy(result)
    invalid['issues'][0]['target_id'] = 'invented'
    with pytest.raises(ValueError, match='target'):
        audit.validate(invalid, payload)


def test_short_model_ids_round_trip_to_the_complete_canonical_manifest():
    payload, result = fixture()
    compact, aliases = audit.model_inputs(payload)
    assert compact['word_entries'][0]['id'] == 'W0'
    short = deepcopy(result)
    for key, prefix in [('word_entry_ids', 'W'), ('grammar_entry_ids', 'G'),
                        ('segment_ids', 'S'), ('overlay_ids', 'O'), ('occurrence_ids', 'R')]:
        short[key] = [prefix + '0']
    short['issues'][0]['target_id'] = 'G0'
    assert audit.decode_report(short, aliases) == result
    audit.validate(audit.decode_report(short, aliases), payload)


def test_triage_must_cover_all_findings_and_keep_notes_separate(tmp_path, monkeypatch):
    payload, result = fixture()
    monkeypatch.setattr(audit, 'inputs', lambda: payload)
    monkeypatch.setattr(words, 'REQUESTS', tmp_path / 'word.json')
    monkeypatch.setattr(audit.grammar, 'REQUESTS', tmp_path / 'grammar.json')
    report = dict(reviewed=True,
        input_fingerprint=words.decision_digest(dict(policy=audit.POLICY, inputs=payload, manifest_protocol=2)),
        review_digest=words.decision_digest(result), data=result)
    decisions = dict(audit_digest=report['review_digest'], decisions=[])
    with pytest.raises(ValueError, match='explicit triage'):
        audit.queue_fixes(report, decisions)
    decisions['decisions'] = [dict(index=0, action='retain', reason='Useful generic contrast')]
    audit.queue_fixes(report, decisions)
    assert words.read(audit.grammar.REQUESTS) == []


def test_reviewed_audit_is_cached_without_repeated_agent_work(tmp_path, monkeypatch):
    from pipeline.agent_harness import CodexRunner
    payload, result = fixture()
    monkeypatch.setattr(audit, 'inputs', lambda: payload)
    monkeypatch.setattr(audit, 'REPORT', tmp_path / 'report.json')
    calls = []
    async def call(self, job, prompt, schema, effort, **options):
        assert options['tool_profile'] == 'offline'
        assert audit.grammar.ENTRY_FOCUS_POLICY in prompt
        calls.append(effort)
        return deepcopy(result)
    monkeypatch.setattr(CodexRunner, 'call', call)
    report = asyncio.run(audit.audit())
    assert calls == ['low', 'high']
    assert asyncio.run(audit.audit()) == report
    assert calls == ['low', 'high']


def test_queue_only_reviewed_current_scoped_findings(tmp_path, monkeypatch):
    payload, result = fixture()
    monkeypatch.setattr(audit, 'inputs', lambda: payload)
    monkeypatch.setattr(words, 'REQUESTS', tmp_path / 'word-requests.json')
    monkeypatch.setattr(audit.grammar, 'REQUESTS', tmp_path / 'grammar-requests.json')
    report = dict(reviewed=True,
        input_fingerprint=words.decision_digest(dict(policy=audit.POLICY, inputs=payload, manifest_protocol=2)),
        review_digest=words.decision_digest(result), data=result)
    audit.queue_fixes(report)
    first = words.read(audit.grammar.REQUESTS)
    audit.queue_fixes(report)
    assert words.read(audit.grammar.REQUESTS) == first
    assert len(first) == 1 and first[0]['entry_id'] == 'grammar'
    assert words.read(words.REQUESTS) == []
    report['input_fingerprint'] = 'stale'
    with pytest.raises(ValueError, match='stale'):
        audit.queue_fixes(report)
    assert words.read(audit.grammar.REQUESTS) == first


def test_routing_findings_do_not_silently_become_prose_edits(tmp_path, monkeypatch):
    payload, result = fixture()
    monkeypatch.setattr(audit, 'inputs', lambda: payload)
    monkeypatch.setattr(words, 'REQUESTS', tmp_path / 'word-requests.json')
    monkeypatch.setattr(audit.grammar, 'REQUESTS', tmp_path / 'grammar-requests.json')
    result['issues'] = [dict(kind='occurrence', target_id='occurrence',
        reason='Wrong grammar destination', requested_change='Correct the link')]
    report = dict(reviewed=True,
        input_fingerprint=words.decision_digest(dict(policy=audit.POLICY, inputs=payload, manifest_protocol=2)),
        review_digest=words.decision_digest(result), data=result)
    with pytest.raises(ValueError, match='explicit pipeline correction'):
        audit.queue_fixes(report)
    assert not words.REQUESTS.exists()
    assert not audit.grammar.REQUESTS.exists()
