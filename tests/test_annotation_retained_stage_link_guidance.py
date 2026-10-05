"""Structural/guidance fixtures; linguistic acceptance still needs fresh review."""
import copy
import pytest
from pipeline import annotation_repairs as repairs
from pipeline.annotation_edits import candidate_digest
from tests.test_annotation_repairs import PlannedRunner,Harness,_plan,_target,_korean_candidate_gate


def chapter():
    return {'text':'가며','title':'Fixture','segments':[{'text':'가며','type':'word','meaning_en':'fixture',
        'lexical':{'id':'fixture-word','kind':'proper_name'},
        'form_steps':[{'form':'가며','reading':'','label':'fixture form','meaning_en':'fixture complete meaning','grammar_entry_ids':['old']}]}],
        'grammar_links':[{'segment_index':0,'entry_id':'old','context_en':'fixture role'}],
        'form_audit':{'reviewed':True,'inflected_segment_indices':[0]}}


def gate(value,tmp_path):
    from pipeline.korean_dictionary import build_assets
    return build_assets(value,tmp_path,word_registry={'fixture-word':{'id':'fixture-word','kind':'proper_name'}},
        grammar_registry={key:{'id':key} for key in ('old','new','other')},write=False)


def test_retained_complete_stage_identity_and_direct_link_changes_preserve_geometry(tmp_path):
    value=chapter();gate(value,tmp_path)
    value['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new']
    value['grammar_links'][0]['entry_id']='new'
    _,asset=gate(value,tmp_path)
    assert asset['occurrences'][0]['entry_id']=='new'
    assert 'display_form' not in asset['occurrences'][0]
    assert value['grammar_links'][0]['context_en']==chapter()['grammar_links'][0]['context_en']


def test_displayed_same_retained_stage_identity_is_rejected_by_actual_gate(tmp_path):
    value=chapter();value['grammar_links'][0].update(display_form='가며',display_meaning_en='fixture whole',display_end_segment_index=0)
    with pytest.raises(ValueError,match='unexpected Korean construction stage'):gate(value,tmp_path)


def test_actual_removed_stage_can_have_supported_complete_occurrence_shape(tmp_path):
    value=chapter();value['segments'][0].pop('form_steps');value['form_audit']['inflected_segment_indices']=[]
    value['grammar_links'][0].update(display_form='가며',display_meaning_en='fixture whole',display_end_segment_index=0)
    gate(value,tmp_path)


def test_distinct_full_span_construction_is_not_forced_into_direct_stage_identity(tmp_path):
    value=chapter();value['grammar_links'].append({'segment_index':0,'entry_id':'other','context_en':'different fixture construction',
        'display_form':'가며','display_meaning_en':'fixture other whole','display_end_segment_index':0})
    gate(value,tmp_path)
    assert value['grammar_links'][1]['display_form']=='가며'


@pytest.mark.parametrize('representation',['korean-flat','korean-v4'])
def test_structural_contract_exposes_stage_identity_occurrence_rule(representation):
    rule=repairs._representation_structure_contract(representation)['stage_link_identity_rule']
    assert rule['source']=='pipeline.korean_dictionary.build_assets'
    occurrence=repairs._construction_occurrence_contract(representation)['staged_identity_occurrence']
    assert occurrence['identity_field']=='entry_id'
    if representation=='korean-flat':
        assert occurrence['direct_fields']=={'display_form':'','display_meaning_en':'','display_end_segment_index':-1}
    else:
        assert occurrence['direct_fields']=={'source_start':-1,'source_end':-1,'display_meaning_en':''}


@pytest.mark.parametrize('representation',['chinese-annotation','japanese-annotation'])
def test_other_language_contracts_do_not_import_korean_direct_link_rule(representation):
    assert repairs._representation_structure_contract(representation) is None
    assert 'staged_identity_occurrence' not in repairs._construction_occurrence_contract(representation)


@pytest.mark.asyncio
@pytest.mark.parametrize('language,representation,surface',[('zh','chinese-annotation','说'),('ja','japanese-annotation','言う'),('ko','korean-flat','가며')])
async def test_new_guidance_reaches_actual_shared_plan_and_patch_only(tmp_path,language,representation,surface):
    key='surface' if language=='ja' else 'text'
    candidate={'segments':[{key:surface,'meaning_en':'fixture old','form_steps':[]}],
               'grammar_links':[],'grammar_overlays':[],'expression_links':[],'inflected_segment_indices':[]}
    plan=_plan((_target('set_field','/segments/0/meaning_en'),))
    patch={'base_digest':candidate_digest(candidate),'edits':[{'op':'set_field','path':'/segments/0/meaning_en','value':'fixture new'}]}
    runner=PlannedRunner(tmp_path,plan,patch);context={'chunk_text':surface}
    if language=='ko':context['candidate_gate']=_korean_candidate_gate()
    result=await repairs.repair_annotation(Harness(tmp_path,runner),'fixture',candidate,[{'problem':'fixture meaning'}],representation=representation,language=language,context=context,validate_candidate=lambda _:None)
    assert result['status']=='applied'
    for _,prompt,_,effort,options in runner.calls:
        assert effort=='low' and repairs.RETAINED_STAGE_LINK_GUIDANCE in prompt
        assert options['workspace_context']['repair_explanation_guidance_version']==3
        assert options['workspace_context']['repair_dependency_guidance_version']==2
    assert 'Related issues may share the same necessary scalar/list identity target' in repairs.RETAINED_STAGE_LINK_GUIDANCE
    assert 'actual step removal or occurrence-shape change' in repairs.DEPENDENCY_CLOSURE_GUIDANCE


def test_normal_review_guidance_and_historical_binding_are_unchanged():
    from pipeline.korean_chunk_reviews import review_request
    from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
    assert repairs.RETAINED_STAGE_LINK_GUIDANCE not in FORM_STAGE_EVIDENCE_GUIDANCE
    request=review_request({'segments':[{'text':'가며'}]},'가며',{'chapter_text':'가며','source_start':0},'Review')
    assert repairs.RETAINED_STAGE_LINK_GUIDANCE not in request[1]
    assert 'repair_dependency_guidance_version' not in request[0]


def test_independent_ending_without_form_chain_remains_direct_under_actual_gate(tmp_path):
    value=chapter()
    value['segments'][0].pop('form_steps')
    value['segments'][0]['lexical']={'id':'old','kind':'grammar'}
    value['form_audit']['inflected_segment_indices']=[]
    _,asset=gate(value,tmp_path)
    assert asset['occurrences'][0]['entry_id']=='old'
    assert 'display_form' not in asset['occurrences'][0]
    rule=repairs._representation_structure_contract('korean-flat')['stage_link_identity_rule']
    assert 'without a form chain remains direct' in rule['when_not_staged']
