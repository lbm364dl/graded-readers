import asyncio
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import pytest
from pipeline import annotation_reference_carry as carry
from pipeline.annotation_adjudication import normalize_review


def fixture_data(start=0, parent='아이 아이 아이'):
    source = '아이 아이 아이'
    candidate = {'segments': [{'text': t, 'meaning_en': 'child' if t != ' ' else '',
                  'lexical_id': 'child' if t != ' ' else '', 'form_steps': []}
                 for t in ['아이', ' ', '아이', ' ', '아이']], 'grammar_links': []}
    review = {'approved': False, 'issues': [{'explanation': 'Exact meaning issue',
        'candidate_paths': ['/segments/2/meaning_en'], 'supporting_paths': []}],
        'prose_revision_reason_en': ''}
    identity = normalize_review('ko', review)['issues'][0]['issue_id']
    original = {'language': 'ko', 'representation': 'korean-flat', 'candidate': candidate,
        'current_review': review, 'context': {'chapter_text': parent, 'source_start': start},
        'source_text': source}
    research = {'inputs': {}, 'references': {'original-fact': {'kind': 'approved_lesson',
        'content': {'_annotation_research_fact': True, 'issue_ids': [identity],
                    'fact': 'A cited reusable fact', 'citations': [{'source': 'primary'}]},
        'issue_ids': [identity]}}}
    descriptor = {'run_relpath': 'runs/exact', 'adjudication_job': 'annotation-adjudication-exact',
                  'receipt_digest': 'a'*64}
    return original, research, {'version': 1, 'sources': [descriptor]}


def bind(original, envelope, **changes):
    args = dict(candidate=original['candidate'], source_text=original['source_text'],
        language='ko', representation='korean-flat', context=original['context'],
        current_review=original['current_review'])
    args.update(changes)
    return carry.bind_carried_research(Path('.'), envelope, **args)


def test_semantic_repair_keeps_fact_and_rebinds_only_exact_current_targets(monkeypatch):
    original, research, envelope = fixture_data()
    monkeypatch.setattr(carry, '_authenticate', lambda descriptor: (original, research))
    changed = copy.deepcopy(original['candidate']); changed['segments'][2]['meaning_en'] = 'corrected meaning'
    review = copy.deepcopy(original['current_review']); review['issues'][0]['explanation'] = 'A fresh exact objection'
    bound = bind(original, envelope, candidate=changed, current_review=review)
    assert len(bound['packet']['facts']) == len(bound['references']) == 1
    content = next(iter(bound['references'].values()))['content']
    assert content['issue_ids'] == [normalize_review('ko', review)['issues'][0]['issue_id']]
    assert content['carried_candidate_paths'] == ['/segments/2/meaning_en']
    other = copy.deepcopy(review); other['issues'][0]['candidate_paths'] = ['/segments/4/meaning_en']
    assert not bind(original, envelope, candidate=changed, current_review=other)['references']
    assert 'never candidate approval' in bound['packet']['scope']


def test_repeated_position_and_boundary_merge_do_not_carry_same_owner_index(monkeypatch):
    original, research, envelope = fixture_data()
    monkeypatch.setattr(carry, '_authenticate', lambda descriptor: (original, research))
    assert bind(original, envelope)['packet']['facts']
    merged = copy.deepcopy(original['candidate'])
    merged['segments'] = [dict(merged['segments'][0], text='아이 아이'),
                           merged['segments'][1], merged['segments'][2]]
    assert ''.join(s['text'] for s in merged['segments']) == original['source_text']
    assert bind(original, envelope, candidate=merged) == {'packet': {}, 'references': {}}


def test_cross_position_identity_language_and_tampered_packet_fail_closed(monkeypatch):
    original, research, envelope = fixture_data()
    monkeypatch.setattr(carry, '_authenticate', lambda descriptor: (original, research))
    context = {'chapter_text': 'prefix'+original['source_text'], 'source_start': 6}
    assert not bind(original, envelope, context=context)['packet']
    changed = copy.deepcopy(original['candidate']);changed['segments'][2]['lexical_id'] = 'different homonym'
    assert not bind(original, envelope, candidate=changed)['packet']
    assert not bind(original, envelope, language='ja', current_review=None)['packet']
    packet = bind(original, envelope)['packet']; packet['facts'][0]['reference']['content']['fact'] = 'forged'
    with pytest.raises(carry.ResearchCarryError, match='packet differs'):
        bind(original, packet)


@pytest.mark.parametrize('position', [True, -1, '0'])
def test_korean_position_reconstructs_actual_parent_and_rejects_boolean_offsets(monkeypatch, position):
    original, research, envelope = fixture_data()
    monkeypatch.setattr(carry, '_authenticate', lambda descriptor: (original, research))
    with pytest.raises(carry.ResearchCarryError, match='position'):
        bind(original, envelope, context={'chapter_text': original['source_text'], 'source_start': position})


def test_explicit_position_requires_complete_typed_source_and_parent_binding():
    with pytest.raises(carry.ResearchCarryError):
        carry._position({'annotation_source_position': {'chunk_index': 0}}, 'text')
    position = {'chunk_index': 0, 'source_start': 0, 'source_text_digest': carry._digest('text'),
                'parent_text_digest': carry._digest('wrong')}
    with pytest.raises(carry.ResearchCarryError, match='parent'):
        carry._position({'annotation_source_position': position, 'chapter_text': 'text'}, 'text')


def test_relative_absolute_resume_and_frozen_packet_independent_of_index(tmp_path, monkeypatch):
    original, research, envelope = fixture_data()
    monkeypatch.setattr(carry, '_authenticate', lambda descriptor: (original, research))
    monkeypatch.chdir(tmp_path)
    kwargs = {k: original[k] for k in ('candidate', 'source_text', 'language', 'representation', 'context')}
    carry.register_carried_research(Path('run'), envelope, **kwargs)
    assert carry.load_carried_research(Path('run'), **kwargs) == envelope
    assert carry.load_carried_research(tmp_path/'run', **kwargs) == envelope
    packet = bind(original, envelope)['packet']
    (tmp_path/'run/annotation-research/run-carry-sources.json').unlink()
    assert bind(original, packet)['packet'] == packet


def test_concurrent_different_positions_and_same_position_union_retained(tmp_path, monkeypatch):
    original, research, envelope = fixture_data()
    sources = {}
    for index in range(3):
        o = copy.deepcopy(original);o['context'] = {'chapter_text': original['source_text']*2, 'source_start': 0 if index < 2 else len(original['source_text'])}
        descriptor = dict(envelope['sources'][0], receipt_digest=str(index)*64)
        sources[descriptor['receipt_digest']] = o
    monkeypatch.setattr(carry, '_authenticate', lambda descriptor: (sources[descriptor['receipt_digest']], research))
    def register(index):
        o=sources[str(index)*64];e={'version':1,'sources':[dict(envelope['sources'][0],receipt_digest=str(index)*64)]}
        return carry.register_carried_research(tmp_path, e, **{k:o[k] for k in ('candidate','source_text','language','representation','context')})
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(register, range(3)))
    document=json.loads((tmp_path/'annotation-research/run-carry-sources.json').read_text())
    assert sorted(len(row['carry']['sources']) for row in document['scopes'].values()) == [1,2]


def test_lock_hardlink_rejected(tmp_path, monkeypatch):
    original,research,envelope=fixture_data();monkeypatch.setattr(carry,'_authenticate',lambda descriptor:(original,research))
    directory=tmp_path/'annotation-research';directory.mkdir();origin=tmp_path/'origin';origin.write_text('')
    os.link(origin,directory/'.run-carry-sources.lock')
    with pytest.raises(carry.ResearchCarryError,match='single-link'):
        carry.register_carried_research(tmp_path,envelope,**{k:original[k] for k in ('candidate','source_text','language','representation','context')})


def test_missing_original_receipts_are_explicit_error_and_unknown_version_rejected(tmp_path):
    descriptor={'run_relpath':'runs/no-such-carry-proof','adjudication_job':'annotation-adjudication-missing','receipt_digest':'a'*64}
    with pytest.raises(carry.ResearchCarryError,match='authenticate'):
        carry._authenticate(descriptor)
    with pytest.raises(carry.ResearchCarryError,match='version'):
        carry._sources({'version':99,'sources':[]})


def test_repair_invalid_carry_rejected_before_model_call(tmp_path):
    from pipeline.annotation_repairs import repair_annotation
    calls=[]
    class Runner:
        async def call(self,*args,**kwargs):calls.append(args);raise AssertionError('paid')
    harness=SimpleNamespace(run_dir=tmp_path,runner=Runner())
    with pytest.raises(carry.ResearchCarryError):
        asyncio.run(repair_annotation(harness,'repair',{'segments':[]},['issue'],language='ja',
            representation='japanese-annotation',context={'chunk_text':'source',carry.CARRY_FIELD:{'version':99,'sources':[]}},validate_candidate=lambda v:None))
    assert calls==[]


def test_range_review_guidance_is_scoped_to_new_carry_inputs_not_legacy_fingerprints():
    from pipeline.korean_chunk_reviews import review_request
    candidate = {'segments': [], 'grammar_links': []}
    legacy_inputs, legacy_prompt, legacy_id = review_request(candidate, '', {}, 'policy')
    new_inputs, new_prompt, new_id = review_request(candidate, '', {carry.CARRY_FIELD: {'version': 1, 'sources': []}}, 'policy')
    assert carry.SOURCE_SPAN_REVIEW_GUIDANCE not in legacy_prompt
    assert carry.SOURCE_SPAN_REVIEW_GUIDANCE in new_prompt
    assert 'included attachment is valid' in new_prompt
    assert 'outside-span attachment' in new_prompt
    assert legacy_id != new_id
    assert carry.CARRY_FIELD not in legacy_inputs['context']
