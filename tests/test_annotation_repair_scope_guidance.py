"""Shared repair instruction propagation; linguistic acceptance uses run smokes."""
import pytest
from tests.test_annotation_repairs import PlannedRunner, Harness, _korean_candidate_gate
from pipeline.annotation_edits import candidate_digest
from pipeline import annotation_repairs as module

@pytest.mark.asyncio
@pytest.mark.parametrize('language,representation,surface',[('zh','chinese-annotation','说'),('ja','japanese-annotation','と言って'),('ko','korean-flat','말하며')])
@pytest.mark.parametrize('concrete_action',[False,True])
async def test_shared_plan_patch_scope_guidance_and_correct_contrast(tmp_path,language,representation,surface,concrete_action):
    key='surface' if language=='ja' else 'text'
    candidate={'segments':[{key:surface,'meaning_en':'that P, as a reported statement','form_steps':[]},{key:'X','meaning_en':'saying P','form_steps':[]}], 'grammar_links':[] if language=='ko' else [],'grammar_overlays':[]}
    if language=='ko':candidate.update(inflected_segment_indices=[],expression_links=[])
    plan={'issues':[{'issue_index':0,'reason':'Only the proposition-only field lacks the supported reporting connective; the reporting event field is already a correct contrast.','targets':[{'op':'set_field','path':'/segments/0/meaning_en'}],'boundary_change_needed':False,'boundary_reason':''}]}
    patch={'base_digest':candidate_digest(candidate),'edits':[{'op':'set_field','path':'/segments/0/meaning_en','value':'while saying P'}]}
    runner=PlannedRunner(tmp_path,plan,patch)
    context={'source_evidence':{'construction':'reporting with a simultaneous matrix action','exact_source_action':'she left' if concrete_action else None,'action_scope':'following clause, outside the reporting tap'}}
    if language=='ko':context['candidate_gate']=_korean_candidate_gate()
    result=await module.repair_annotation(Harness(tmp_path,runner),'scope',candidate,[{'problem':'The proposition-only field omits a reporting connective contribution.'}],representation=representation,language=language,context=context,validate_candidate=lambda value:None)
    assert result['status']=='applied'
    assert result['candidate']['segments'][1]==candidate['segments'][1]
    for _,prompt,_,effort,options in runner.calls:
        assert module.SCHEMATIC_SOURCE_MEANING_GUIDANCE in prompt
        assert options['workspace_context']['repair_explanation_guidance_version']==3
        assert options['workspace_context']['source_evidence']==context['source_evidence']
        assert effort=='low'
