import copy
import json
from types import SimpleNamespace

import pytest

from pipeline.annotation_reference_carry import CARRY_FIELD, ResearchCarryError
from pipeline.annotation_reference_carry_callers import (
    bind_lifecycle_carry, register_chunk_positions, validate_carry_context,
)


@pytest.fixture(params=[('zh', 'chinese-annotation', 'text'), ('ja', 'japanese-annotation', 'surface')])
def carried(request, tmp_path, monkeypatch):
    import pipeline.annotation_reference_carry as shared
    from pipeline.annotation_adjudication import normalize_review
    language, representation, surface = request.param
    harness = SimpleNamespace(run_dir=tmp_path)
    register_chunk_positions(harness, ['猫', '猫'], parent_text='猫猫')
    context = {'annotation_source_position': harness._annotation_source_positions[0]}
    candidate = {'segments': [{surface: '猫', 'meaning_en': 'cat'}], 'grammar_overlays': []}
    review = {'verdict': 'revise', 'issues': [{
        'explanation': 'Investigate the occurrence meaning.',
        'candidate_paths': ['/segments/0/meaning_en'], 'supporting_paths': []}]}
    issue_id = normalize_review(language, review)['issues'][0]['issue_id']
    original = {'language': language, 'representation': representation,
        'candidate': candidate, 'source_text': '猫', 'context': context, 'current_review': review}
    research = {'references': {'original-fact': {'kind': 'approved_lesson',
        'content': {'fact': 'The lexical item names a cat.', 'issue_ids': [issue_id]}}}}
    monkeypatch.setattr(shared, '_authenticate', lambda descriptor: (original, research))
    carry = {'version': 1, 'sources': [{'run_relpath': 'runs/retained',
        'adjudication_job': 'original-adjudication', 'receipt_digest': 'a' * 64}]}
    shared.register_carried_research(tmp_path, carry, candidate=candidate, source_text='猫',
        language=language, representation=representation, context=context)
    return harness, candidate, review, language, representation


def test_registered_carry_survives_runtime_restart_and_semantic_repair(carried):
    harness, original, review, language, representation = carried
    derived = copy.deepcopy(original)
    derived['segments'][0]['meaning_en'] = 'the cat'
    context, refs = bind_lifecycle_carry(harness, 0, '猫', derived,
        language=language, representation=representation, current_review=review)
    assert context[CARRY_FIELD]['facts'][0]['candidate_paths'] == ['/segments/0/meaning_en']
    assert len(refs) == 1
    rebound = next(iter(refs.values()))
    assert rebound['content']['_annotation_research_fact'] is True
    assert context['annotation_source_position']['source_start'] == 0
    # The repeated sibling has identical text but a different authenticated position.
    other, refs = bind_lifecycle_carry(harness, 1, '猫', derived,
        language=language, representation=representation, current_review=review)
    assert other == {} and refs == {}
    bare = SimpleNamespace(run_dir=harness.run_dir,
        _annotation_research_carries=harness._annotation_research_carries)
    assert bind_lifecycle_carry(bare, 0, '猫', derived,
        language=language, representation=representation) == ({}, {})
    wrong = copy.deepcopy(context)
    wrong[CARRY_FIELD]['facts'][0]['reference']['content']['fact'] = 'Invented claim'
    with pytest.raises(ResearchCarryError, match='packet differs'):
        validate_carry_context(harness.run_dir, wrong, candidate=derived, source_text='猫',
            language=language, representation=representation)
    incomplete = copy.deepcopy(context)
    del incomplete['annotation_source_position']['parent_text_digest']
    with pytest.raises(ResearchCarryError, match='complete source position'):
        validate_carry_context(harness.run_dir, incomplete, candidate=derived, source_text='猫',
            language=language, representation=representation)


def test_chunk_registration_requires_exact_immutable_parent():
    with pytest.raises(ResearchCarryError, match='immutable parent'):
        register_chunk_positions(SimpleNamespace(), ['猫', '猫'], parent_text='猫 犬')


def test_normal_publication_replays_authenticated_packet_and_rejects_changed_claim(carried):
    from pipeline.annotation_publication import bind_review_job, normal_review_receipt, verify_normal_review_receipt
    from pipeline.worker_workspace import build
    harness, candidate, _, language, representation = carried
    context, _ = bind_lifecycle_carry(harness, 0, '猫', candidate,
        language=language, representation=representation)
    context['annotation_issue_targets_validation'] = {
        'candidate': candidate, 'source_text': '猫', 'representation': representation, 'require_typed': True}
    root = harness.run_dir / 'agents/annotations/chunk_0000/review'
    root.mkdir(parents=True)
    _, fingerprint = build(root / 'workspace', 'Review exact inputs.', {}, context=context)
    result = {'verdict': 'pass', 'issues': []}
    (root / 'result.json').write_text(json.dumps(result))
    (root / 'meta.json').write_text(json.dumps({'return_code': 0, 'fingerprint': 'input',
        'model': 'gpt-6-luna', 'effort': 'low', 'tool_profile': 'workspace', 'workspace_digest': fingerprint}))
    bind_review_job(harness.run_dir, 'annotations/chunk_0000/review', candidate=candidate, source_text='猫')
    receipt = normal_review_receipt(harness.run_dir, ['annotations/chunk_0000/review'], result, candidate, '猫')
    assert verify_normal_review_receipt(harness.run_dir, receipt, review=result,
        candidate=candidate, source_text='猫', expected_children=1) == [result]
    fields = json.loads((root / 'workspace/INDEX.json').read_text())
    field = next(row for row in fields if row['field'] == CARRY_FIELD)
    file = root / 'workspace' / field['path']
    packet = json.loads(file.read_text())
    packet['facts'][0]['reference']['content']['fact'] = 'Changed claim'
    file.write_text(json.dumps(packet))
    with pytest.raises(ValueError):
        verify_normal_review_receipt(harness.run_dir, receipt, review=result,
            candidate=candidate, source_text='猫', expected_children=1)

@pytest.mark.asyncio
async def test_both_language_review_producers_receive_same_authenticated_fact_packet(carried):
    from argparse import Namespace
    from pipeline.annotation_reference_carry import CARRIED_RESEARCH_GUIDANCE
    harness, candidate, _, language, representation = carried
    calls = []
    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            calls.append((prompt, kwargs['workspace_context']))
            return {'verdict': 'pass', 'issues': []}
    if language == 'zh':
        from tests.test_agent_harness import annotation_harness
        target = annotation_harness(Runner())
        # Producer columns are required by the Chinese linguistic contract.
        candidate['segments'][0].update(type='word', pinyin='māo')
    else:
        from pipeline.japanese_agent_harness import JapaneseChapterHarness
        target = object.__new__(JapaneseChapterHarness)
        target.args = Namespace(annotation_review_effort='low', refresh=False)
        target.runner = Runner()
    target.run_dir = harness.run_dir
    target._annotation_source_positions = harness._annotation_source_positions
    # The fixture source is the same immutable object, including producer metadata.
    assert (await target.review_annotation(0, '猫', candidate, 'derived'))['verdict'] == 'pass'
    assert len(calls) == (1 if language == 'zh' else 2)
    for prompt, context in calls:
        assert CARRIED_RESEARCH_GUIDANCE in prompt
        assert len(context[CARRY_FIELD]['facts']) == 1
        assert context['annotation_source_position']['parent_text_digest'] == target._annotation_source_positions[0]['parent_text_digest']


def test_current_research_cache_eligibility_accounts_for_consumed_terminal_facts(monkeypatch, tmp_path):
    import pipeline.annotation_reference_carry_callers as callers
    fact = {'source': {'receipt_digest': 'a' * 64}, 'original_reference_id': 'original',
            'candidate_paths': ['/segments/0/meaning_en'], 'reference': {
        'kind': 'approved_lesson', 'content': {'fact': 'Exact supported claim', 'issue_ids': ['old']}}}
    packet = {'facts': [fact]}
    current = {'packet': packet}
    monkeypatch.setattr(callers, 'load_carried_research', lambda *a, **k: current['packet'])
    monkeypatch.setattr(callers, 'bind_carried_research', lambda run, carry, **k: {'packet': carry})
    monkeypatch.setattr(callers, 'carry_from_adjudication', lambda run, evidence, existing: evidence['consumed'])
    kwargs = dict(candidate={}, source_text='猫', language='ja', representation='japanese-annotation', context={}, old_context={})
    assert not callers.current_carry_eligibility(tmp_path, **kwargs)
    assert callers.current_carry_eligibility(tmp_path, **{**kwargs, 'old_context': {CARRY_FIELD: packet}})
    terminal = {'consumed': copy.deepcopy(packet)}
    terminal['consumed']['facts'][0]['reference']['content']['issue_ids'] = ['new']
    assert callers.current_carry_eligibility(tmp_path, **kwargs, terminal_evidence=terminal)
    current['packet'] = {'facts': [fact, {**fact, 'reference': {'content': {'fact': 'Additional foreign fact'}}}]}
    assert not callers.current_carry_eligibility(tmp_path, **kwargs, terminal_evidence=terminal)
    same_claim_foreign_receipt = copy.deepcopy(fact)
    same_claim_foreign_receipt['source']['receipt_digest'] = 'b' * 64
    current['packet'] = {'facts': [same_claim_foreign_receipt]}
    assert not callers.current_carry_eligibility(tmp_path, **kwargs, terminal_evidence=terminal)
    current['packet'] = {}
    assert callers.current_carry_eligibility(tmp_path, **kwargs)
    current['packet'] = None
    assert callers.current_carry_eligibility(tmp_path, **kwargs)


def test_current_research_guard_authenticates_saved_and_terminal_packets(carried, monkeypatch):
    import pipeline.annotation_reference_carry_callers as callers
    harness, candidate, _, language, representation = carried
    context, _ = bind_lifecycle_carry(harness, 0, '猫', candidate, language=language, representation=representation)
    kwargs = dict(candidate=candidate, source_text='猫', language=language,
                  representation=representation, context=context, old_context=context)
    assert callers.current_carry_eligibility(harness.run_dir, **kwargs)
    forged = copy.deepcopy(context)
    forged[CARRY_FIELD]['facts'][0]['reference']['content']['fact'] = 'Forged'
    with pytest.raises(ResearchCarryError):
        callers.current_carry_eligibility(harness.run_dir, **{**kwargs, 'old_context': forged})
    with pytest.raises(ResearchCarryError):
        callers.current_carry_eligibility(harness.run_dir, **{**kwargs, 'context': forged})
    def corrupt(*args):
        raise ResearchCarryError('Terminal research authentication failed')
    monkeypatch.setattr(callers, 'carry_from_adjudication', corrupt)
    with pytest.raises(ResearchCarryError, match='Terminal research'):
        callers.current_carry_eligibility(harness.run_dir, **kwargs, terminal_evidence={})


def test_explicit_current_packet_without_manifest_requires_consumed_evidence(monkeypatch, tmp_path):
    import pipeline.annotation_reference_carry_callers as callers
    packet = {'facts': [{'source': {'receipt_digest': 'a'*64}, 'original_reference_id': 'fact',
        'candidate_paths': ['/segments/0/meaning_en'], 'reference': {'content': {'fact': 'Claim'}}}]}
    monkeypatch.setattr(callers, 'load_carried_research', lambda *a, **k: None)
    monkeypatch.setattr(callers, 'bind_carried_research', lambda run, carry, **k: {'packet': carry})
    monkeypatch.setattr(callers, 'carry_from_adjudication', lambda *a: packet)
    kwargs = dict(candidate={}, source_text='猫', language='ja', representation='japanese-annotation',
        context={CARRY_FIELD: packet}, old_context={})
    assert not callers.current_carry_eligibility(tmp_path, **kwargs)
    assert callers.current_carry_eligibility(tmp_path, **kwargs, terminal_evidence={})


def test_explicit_and_manifest_packets_both_require_exact_consumed_sources(monkeypatch, tmp_path):
    import pipeline.annotation_reference_carry_callers as callers
    def packet(receipt):
        return {'facts': [{'source': {'receipt_digest': receipt*64}, 'original_reference_id': 'fact',
            'candidate_paths': ['/segments/0/meaning_en'], 'reference': {'content': {'fact': 'Same claim'}}}]}
    first, second = packet('a'), packet('b')
    monkeypatch.setattr(callers, 'load_carried_research', lambda *a, **k: second)
    monkeypatch.setattr(callers, 'bind_carried_research', lambda run, carry, **k: {'packet': carry})
    kwargs = dict(candidate={}, source_text='猫', language='ja', representation='japanese-annotation',
        context={CARRY_FIELD: first}, old_context={CARRY_FIELD: first})
    assert not callers.current_carry_eligibility(tmp_path, **kwargs)
    monkeypatch.setattr(callers, 'carry_from_adjudication', lambda *a: {'facts': first['facts']+second['facts']})
    assert callers.current_carry_eligibility(tmp_path, **kwargs, terminal_evidence={})
