"""Portable authority contracts; real retained-receipt replays live in maintenance checks."""
from __future__ import annotations
import copy, json
from pathlib import Path
import pytest

from pipeline.annotation_edits import candidate_digest, apply_edits
from pipeline.annotation_repairs import _validate_plan, _targets, PLAN_SCHEMA, repair_annotation, replay_annotation_repair
from pipeline.annotation_repair_authority import build_authority_packet, validate_authority_packet, validate_derived_effects, validate_replay_contract, validate_plan_authority
from pipeline.annotation_repair_dependencies import repair_dependency_constraints
from pipeline.worker_workspace import build, check



def t(op, path): return {'op': op, 'path': path}
def row(*targets, boundary=False):
    return {'issue_index':0,'reason':'fixture','targets':list(targets),
        'boundary_change_needed':boundary,'boundary_reason':'primary boundary' if boundary else ''}
def plan(*rows): return {'issues':list(rows)}




def test_leaf_target_and_nested_list_operation_are_exact():
    candidate={'segments':[{'text':'X','meaning_en':'old','form_steps':[{'grammar_entry_ids':['a']}]}],
        'grammar_overlays':[]}
    issue=[{'candidate_paths':['/segments/0/meaning_en']}]
    packet=build_authority_packet(issue,candidate,'chinese-annotation',candidate_digest(candidate))
    _validate_plan(plan(row(t('set_field','/segments/0/meaning_en'))),1,candidate,
        'chinese-annotation',target_contract_version=3,target_authority=packet)
    with pytest.raises(ValueError,match='exceeds authenticated target authority'):
        _validate_plan(plan(row(t('replace_row','/segments/0'))),1,candidate,
            'chinese-annotation',target_contract_version=3,target_authority=packet)
    nested=[{'candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids']}]
    candidate['grammar_links']=[]; candidate['expression_links']=[]; candidate['inflected_segment_indices']=[]
    nested_packet=build_authority_packet(nested,candidate,'korean-flat',candidate_digest(candidate))
    _validate_plan(plan(row(t('replace_list','/segments/0/form_steps/0/grammar_entry_ids'))),1,candidate,
        'korean-flat',target_contract_version=3,dependency_constraints=repair_dependency_constraints(candidate,'korean-flat'),target_authority=nested_packet)


def test_explicit_derived_projection_target_allows_only_exact_row_pair():
    candidate={'segments':[{'text':'가','form_steps':[]}], 'grammar_links':[{'segment_index':0,
        'display_form':'가','display_meaning_en':'go','display_end_segment_index':0}], 'expression_links':[], 'inflected_segment_indices':[]}
    issues=[{'candidate_paths':['/grammar_links/0/display_form']}]
    packet=build_authority_packet(issues,candidate,'korean-flat',candidate_digest(candidate))
    good=plan(row(t('remove_row','/grammar_links/0'),t('append_row','/grammar_links')))
    _validate_plan(good,1,candidate,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(candidate,'korean-flat'),target_authority=packet)
    bad=plan(row(t('remove_row','/grammar_links/0'),t('append_row','/grammar_links'),
        t('set_field','/grammar_links/0/display_meaning_en')))
    with pytest.raises(ValueError,match='exceeds authenticated target authority'):
        _validate_plan(bad,1,candidate,'korean-flat',target_contract_version=3,
            dependency_constraints=repair_dependency_constraints(candidate,'korean-flat'),target_authority=packet)
    direct_plan=plan(row(t('set_field','/grammar_links/0/display_form')))
    # Authority v3 supports this exact direct leaf operation where the
    # representation-specific edit contract permits it; Korean flat applies its
    # own projection protection later in _validate_plan.
    validate_plan_authority(direct_plan,packet)


def test_direct_semantic_leaf_is_permitted_by_authority_on_real_japanese_shape():
    candidate={'segments':[{'surface':'行く','type':'word','lemma':'行く','surface_kana':'いく','lemma_kana':'いく',
        'part_of_speech':'verb','conjugation_form':'dictionary','meaning_en':'go','grammar_candidate_key':'',
        'dictionary_key':'行く','dictionary_definition_en':'to go','form_steps':[],'story_role':'none',
        'story_importance_en':''}], 'grammar_overlays':[]}
    issues=[{'candidate_paths':['/segments/0/meaning_en']}]
    packet=build_authority_packet(issues,candidate,'japanese-annotation',candidate_digest(candidate))
    direct_plan=plan(row(t('set_field','/segments/0/meaning_en')))
    validate_plan_authority(direct_plan,packet)
    direct={'op':'set_field','path':'/segments/0/meaning_en','value':'to travel'}
    after=apply_edits(candidate,{'base_digest':candidate_digest(candidate),'edits':[direct]},
        allowed_targets=_targets(direct_plan),representation='japanese-annotation')
    validate_derived_effects(candidate,after,direct_plan,packet,[direct])


def test_repeated_text_keeps_pointer_position_and_boundary_route_is_explicit():
    candidate={'segments':[{'text':'가','meaning_en':'one'},{'text':'가','meaning_en':'two'}],
        'grammar_overlays':[]}
    issues=[{'candidate_paths':['/segments/0/meaning_en']}]
    packet=build_authority_packet(issues,candidate,'chinese-annotation',candidate_digest(candidate))
    bad=plan(row(t('set_field','/segments/1/meaning_en')))
    with pytest.raises(ValueError,match='exceeds authenticated target authority'):
        _validate_plan(bad,1,candidate,'chinese-annotation',target_contract_version=3,target_authority=packet)
    source=[{'candidate_paths':['/segments/0/text']}]
    source_packet=build_authority_packet(source,candidate,'chinese-annotation',candidate_digest(candidate))
    _validate_plan(plan(row(boundary=True)),1,candidate,'chinese-annotation',
        target_contract_version=3,target_authority=source_packet)



@pytest.mark.parametrize('language,representation,candidate,key,surface', [
    ('zh','chinese-annotation',{'segments':[{'text':'她','meaning_en':'old'}], 'grammar_overlays':[]},'text','她'),
    ('ja','japanese-annotation',{'segments':[{'surface':'彼','meaning_en':'old','form_steps':[]}], 'grammar_overlays':[]},'surface','彼'),
    ('ko','korean-flat',{'segments':[{'text':'가','meaning_en':'old','form_steps':[]}], 'grammar_links':[], 'expression_links':[], 'inflected_segment_indices':[]},'text','가'),
])
@pytest.mark.parametrize('stage', ['typed-review','verified-adjudication'])
def test_three_language_call_shapes_share_exact_v3_authority(language,representation,candidate,key,surface,stage):
    candidate['segments'].append(copy.deepcopy(candidate['segments'][0]))
    path='/segments/0/meaning_en'
    if stage=='typed-review':
        issue={'candidate_paths':[path], 'supporting_paths':['/'+key]}
    else:
        issue={'issue_id':'verified-fixture','paths':[path],
            'target_dispositions':[{'path':path,'disposition':'actionable'}]}
    packet=build_authority_packet([issue],candidate,representation,candidate_digest(candidate))
    target=t('set_field',path)
    constraints=repair_dependency_constraints(candidate,representation)
    _validate_plan(plan(row(target)),1,candidate,representation,target_contract_version=3,
        dependency_constraints=constraints,target_authority=packet)
    bad=plan(row(t('set_field','/segments/1/meaning_en')))
    with pytest.raises(ValueError,match='exceeds authenticated target authority'):
        _validate_plan(bad,1,candidate,representation,target_contract_version=3,
            dependency_constraints=constraints,target_authority=packet)


def test_explicit_dependency_closure_is_same_issue_and_not_new_defect_authority():
    candidate={'segments':[{'text':'가','form_steps':[{'form':'가며','grammar_entry_ids':['old']}]}],
        'grammar_links':[{'segment_index':0,'entry_id':'old','context_en':'fixture','display_form':'',
            'display_meaning_en':'','display_end_segment_index':-1}],
        'expression_links':[],'inflected_segment_indices':[0]}
    issue=[{'candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids']}]
    packet=build_authority_packet(issue,candidate,'korean-flat',candidate_digest(candidate))
    permitted=plan(row(t('replace_list','/segments/0/form_steps/0/grammar_entry_ids'),
        t('set_field','/grammar_links/0/entry_id')))
    _validate_plan(permitted,1,candidate,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(candidate,'korean-flat'),target_authority=packet)
    cross_issue=plan(row(t('replace_list','/segments/0/form_steps/0/grammar_entry_ids')),
        row(t('set_field','/grammar_links/0/entry_id')))
    with pytest.raises(ValueError):
        _validate_plan(cross_issue,2,candidate,'korean-flat',target_contract_version=3,
            dependency_constraints=repair_dependency_constraints(candidate,'korean-flat'),target_authority=packet)



class Args:
    annotation_repair_effort='low'; refresh=False
class Harness:
    def __init__(self,run_dir,runner): self.run_dir=run_dir; self.runner=runner; self.args=Args()
class Runner:
    def __init__(self,run_dir,plan_value,patch): self.run_dir=run_dir; self.p=plan_value; self.patch=patch; self.calls=[]; self.prompts=[]
    async def call(self,job,prompt,schema,effort,**kw):
        self.calls.append(job)
        self.prompts.append((job,prompt,kw.get("workspace_context")))
        from pipeline.worker_workspace import build as local_build, check as local_check
        job_dir=self.run_dir/'agents'/job; ws=job_dir/'workspace'
        local_build(ws,prompt,json.loads(schema.read_text()),context=kw.get('workspace_context'))
        result=self.p if job.endswith('_plan') else self.patch
        artifact=ws/'candidate.json';artifact.write_text(json.dumps(result,ensure_ascii=False))
        local_check(ws,artifact)
        job_dir.mkdir(parents=True,exist_ok=True)
        (job_dir/'result.json').write_text(json.dumps(result,ensure_ascii=False))
        (job_dir/'meta.json').write_text(json.dumps({'return_code':0,'tool_profile':'workspace'}))
        return result


@pytest.mark.asyncio
async def test_host_patch_request_assembly_and_exact_replay_v3(tmp_path):
    candidate={'segments':[{'text':'她','type':'word','pinyin':'tā','meaning_en':'she'}],
        'grammar_overlays':[]}
    issues=[{'candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}]
    repair_plan=plan(row(t('set_field','/segments/0/meaning_en')))
    patch={'base_digest':candidate_digest(candidate),'edits':[
        {'op':'set_field','path':'/segments/0/meaning_en','value':'she (corrected fixture)'}]}
    runner=Runner(tmp_path,repair_plan,patch)
    result=await repair_annotation(Harness(tmp_path,runner),'v3-lifecycle',candidate,issues,
        representation='chinese-annotation',language='zh',context={'chunk_text':'她'},
        validate_candidate=lambda _:None)
    assert result['status']=='applied'
    meta_path=tmp_path/'agents/v3-lifecycle_assembly/meta.json'
    meta=json.loads(meta_path.read_text())
    assert meta['plan_target_contract_version']==3
    assert meta['repair_target_authority']['version']==3
    replay=replay_annotation_repair(tmp_path,'v3-lifecycle_assembly',validate_candidate=lambda _:None)
    assert replay['candidate']==result['candidate']
    tampered=copy.deepcopy(meta);tampered['repair_target_authority']['issues'][0]['paths']=['/segments/0/text']
    meta_path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError,match='target authority differs'):
        replay_annotation_repair(tmp_path,'v3-lifecycle_assembly',validate_candidate=lambda _:None)


@pytest.mark.asyncio
async def test_invalid_typed_issue_fails_closed_instead_of_v2_downgrade(tmp_path):
    candidate={'segments':[{'text':'她','type':'word','pinyin':'tā','meaning_en':'she'}], 'grammar_overlays':[]}
    issues=[{'candidate_paths':['/segments/88/meaning_en']}]
    runner=Runner(tmp_path,plan(row(t('set_field','/segments/0/meaning_en'))),
        {'base_digest':candidate_digest(candidate),'edits':[]})
    with pytest.raises(ValueError,match='not present in immutable candidate'):
        await repair_annotation(Harness(tmp_path,runner),'invalid-v3',candidate,issues,
            representation='chinese-annotation',language='zh',context={'chunk_text':'她'},
            validate_candidate=lambda _:None)
    assert not (runner.run_dir/'agents'/'invalid-v3_plan').exists()


@pytest.mark.asyncio
async def test_candidate_gate_marker_does_not_downgrade_adjudication_or_untyped_review(tmp_path):
    candidate={'segments':[{'text':'가','meaning_en':'old','form_steps':[]}],
        'grammar_links':[],'expression_links':[],'inflected_segment_indices':[]}
    context={'candidate_gate':{'focus':{},'title':'Fixture','number':1,'plan':{},'level':1,'source_id':'fixture'},
        'repair_issue_origin':'deterministic_gate','prior_review_adjudication':{'status':'actionable'}}
    runner=Runner(tmp_path,plan(row(t('set_field','/segments/0/meaning_en'))),
        {'base_digest':candidate_digest(candidate),'edits':[]})
    with pytest.raises(ValueError,match='adjudication repairs require host-verified'):
        await repair_annotation(Harness(tmp_path,runner),'untyped-adjudication',candidate,
            [{'problem':'untyped finding'}],representation='korean-flat',language='ko',
            context=context,validate_candidate=lambda _:None)
    assert runner.calls == []



@pytest.mark.asyncio
async def test_adjudication_context_cannot_fall_back_to_candidate_gate_or_review_paths(tmp_path):
    candidate={'segments':[{'text':'가','meaning_en':'old','form_steps':[]}],
        'grammar_links':[],'expression_links':[],'inflected_segment_indices':[]}
    context={'candidate_gate':{'focus':{},'title':'Fixture','number':1,'plan':{},'level':1,'source_id':'fixture'},
        'repair_issue_origin':'deterministic_gate','prior_review_adjudication':{'status':'actionable'}}
    runner=Runner(tmp_path,plan(row(t('set_field','/segments/0/meaning_en'))),
        {'base_digest':candidate_digest(candidate),'edits':[]})
    for issue in ([{'problem':'untyped diagnosis'}],
                  [{'candidate_paths':['/segments/0/meaning_en']}]):
        with pytest.raises(ValueError,match='adjudication repairs require host-verified'):
            await repair_annotation(Harness(tmp_path,runner),'unsafe-fallback',candidate,issue,
                representation='korean-flat',language='ko',context=context,validate_candidate=lambda _:None)
    assert runner.calls == []


def _coupled_identity_fixture():
    c={'segments':[{'text':'가','meaning_en':'go','form_steps':[{'form':'가며','grammar_entry_ids':['old']}]}],
       'grammar_links':[
        {'segment_index':0,'entry_id':'old','context_en':'keep me','display_form':'','display_meaning_en':'','display_end_segment_index':-1},
        {'segment_index':0,'entry_id':'other','context_en':'sibling context','display_form':'','display_meaning_en':'','display_end_segment_index':-1}],
       'expression_links':[],'inflected_segment_indices':[0]}
    issues=[{'candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids']}]
    auth=build_authority_packet(issues,c,'korean-flat',candidate_digest(c))
    planv=plan(row(t('replace_list','/segments/0/form_steps/0/grammar_entry_ids'),
        t('set_field','/grammar_links/0/entry_id')))
    return c,issues,auth,planv


def test_dependency_effect_changes_only_exact_identity_fields():
    before,issues,auth,pl=_coupled_identity_fixture()
    _validate_plan(pl,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before,'korean-flat'),target_authority=auth)
    good=copy.deepcopy(before);good['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new']
    good['grammar_links'][0]['entry_id']='new'
    dependency_edits=[{'op':'replace_list','path':'/segments/0/form_steps/0/grammar_entry_ids','value':['new']},
        {'op':'set_field','path':'/grammar_links/0/entry_id','value':'new'}]
    validate_derived_effects(before,good,pl,auth,dependency_edits)
    for bad in (
        {**copy.deepcopy(good),'grammar_links':good['grammar_links'][:1]},
        {**copy.deepcopy(good),'grammar_links':list(reversed(good['grammar_links']))},
    ):
        with pytest.raises(ValueError):
            validate_derived_effects(before,bad,pl,auth,dependency_edits)
    bad=copy.deepcopy(good);bad['grammar_links'][0]['context_en']='unrelated rewrite'
    with pytest.raises(ValueError):
        validate_derived_effects(before,bad,pl,auth,dependency_edits)
    broad=plan(row(t('replace_list','/segments/0/form_steps/0/grammar_entry_ids'),
        t('replace_list','/grammar_links')))
    with pytest.raises(ValueError,match='exceeds authenticated target authority'):
        _validate_plan(broad,1,before,'korean-flat',target_contract_version=3,
            dependency_constraints=repair_dependency_constraints(before,'korean-flat'),target_authority=auth)


def test_targeted_span_replacement_preserves_siblings_and_only_changes_target_fields():
    before={'segments':[{'text':'가'}], 'grammar_links':[
        {'segment_index':0,'entry_id':'a','context_en':'ctx','display_form':'가','display_meaning_en':'go','display_end_segment_index':0},
        {'segment_index':0,'entry_id':'b','context_en':'sibling','display_form':'가','display_meaning_en':'go','display_end_segment_index':0}],
        'expression_links':[],'inflected_segment_indices':[]}
    issues=[{'candidate_paths':['/grammar_links/0/display_form','/grammar_links/0/display_end_segment_index']}]
    auth=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    pl=plan(row(t('remove_row','/grammar_links/0'),t('append_row','/grammar_links')))
    _validate_plan(pl,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before,'korean-flat'),target_authority=auth)
    after=copy.deepcopy(before);old=after['grammar_links'].pop(0);new=copy.deepcopy(old)
    new['display_form']='가 가';new['display_end_segment_index']=1
    after['grammar_links'].append(new)
    projection_edits=[{'op':'remove_row','path':'/grammar_links/0'},
        {'op':'append_row','path':'/grammar_links','value':new}]
    validate_derived_effects(before,after,pl,auth,projection_edits)
    bad=copy.deepcopy(after);bad['grammar_links'][-1]['context_en']='changed unrelated context'
    with pytest.raises(ValueError): validate_derived_effects(before,bad,pl,auth,projection_edits)
    reordered=copy.deepcopy(after);reordered['grammar_links']=[reordered['grammar_links'][1],reordered['grammar_links'][0]]
    with pytest.raises(ValueError): validate_derived_effects(before,reordered,pl,auth,projection_edits)
    dropped=copy.deepcopy(after);dropped['grammar_links'].pop(0)
    with pytest.raises(ValueError): validate_derived_effects(before,dropped,pl,auth,projection_edits)

def test_replay_contract_blocks_v3_metadata_downgrades_but_keeps_historical_pairs():
    for meta in ({'repair_request_policy_version':None,'plan_target_contract_version':1},
                 {'repair_request_policy_version':None,'plan_target_contract_version':2},
                 {'repair_request_policy_version':3,'plan_target_contract_version':2}):
        validate_replay_contract(meta)
    validate_replay_contract({'repair_request_policy_version':3,'plan_target_contract_version':3,
        'repair_target_authority':{'version':3}})
    for meta in ({'repair_request_policy_version':3,'plan_target_contract_version':1},
                 {'repair_request_policy_version':None,'plan_target_contract_version':3},
                 {'repair_request_policy_version':3,'plan_target_contract_version':3}):
        with pytest.raises(ValueError):validate_replay_contract(meta)


@pytest.mark.asyncio
@pytest.mark.parametrize('language,representation,candidate,field', [
    ('zh','chinese-annotation',{'segments':[{'text':'她','meaning_en':'she'}],'grammar_overlays':[]},'text'),
    ('ja','japanese-annotation',{'segments':[{'surface':'彼','meaning_en':'he','form_steps':[]}],'grammar_overlays':[]},'surface'),
    ('ko','korean-flat',{'segments':[{'text':'가','meaning_en':'go','form_steps':[]}],
        'grammar_links':[],'expression_links':[],'inflected_segment_indices':[]},'text'),
])
async def test_shared_repair_api_accepts_typed_issue_binding_for_three_representations(
        tmp_path,language,representation,candidate,field):
    issue={'issue_id':'caller-fixture','paths':['/segments/0/meaning_en'],
        'target_dispositions':[{'path':'/segments/0/meaning_en','disposition':'actionable'}],
        'supporting_paths':[f'/segments/0/{field}']}
    runner=Runner(tmp_path,plan(row(boundary=True)),{})
    result=await repair_annotation(Harness(tmp_path,runner),f'{language}-caller',candidate,[issue],
        representation=representation,language=language,
        context={'chunk_text':'fixture','prior_review_adjudication':{'status':'verified'},
            'candidate_gate':{'focus':{},'title':'Fixture','number':1,'plan':{},'level':1,'source_id':'fixture'}} ,
        validate_candidate=lambda _:None)
    assert result['status']=='boundary_change_needed'
    meta=json.loads((tmp_path/'agents'/f'{language}-caller_assembly'/'meta.json').read_text())
    assert meta['plan_target_contract_version']==3
    assert meta['repair_target_authority']['issues'][0]['paths']==['/segments/0/meaning_en']






@pytest.mark.asyncio
async def test_child_v3_contract_cannot_be_hidden_by_assembly_metadata_downgrade(tmp_path):
    candidate={'segments':[{'text':'她','type':'word','pinyin':'tā','meaning_en':'she'}],
        'grammar_overlays':[]}
    issues=[{'candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}]
    patch={'base_digest':candidate_digest(candidate),'edits':[
        {'op':'set_field','path':'/segments/0/meaning_en','value':'she (corrected fixture)'}]}
    runner=Runner(tmp_path,plan(row(t('set_field','/segments/0/meaning_en'))),patch)
    await repair_annotation(Harness(tmp_path,runner),'downgrade-v3',candidate,issues,
        representation='chinese-annotation',language='zh',context={'chunk_text':'她'},
        validate_candidate=lambda _:None)
    meta_path=tmp_path/'agents/downgrade-v3_assembly/meta.json'
    original=json.loads(meta_path.read_text())
    meta=copy.deepcopy(original);meta['repair_request_policy_version']=None
    meta['plan_target_contract_version']=2;meta.pop('repair_target_authority')
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='child plan request'):
        replay_annotation_repair(tmp_path,'downgrade-v3_assembly',validate_candidate=lambda _:None)


def test_targeted_span_replacement_preserves_identical_sibling_identity_by_position():
    duplicate={'segment_index':0,'entry_id':'same','context_en':'same context',
        'display_form':'가','display_meaning_en':'go','display_end_segment_index':0}
    before={'segments':[{'text':'가'}],'grammar_links':[copy.deepcopy(duplicate),copy.deepcopy(duplicate)],
        'expression_links':[],'inflected_segment_indices':[]}
    issues=[{'candidate_paths':['/grammar_links/0/display_form']}]
    packet=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    repair_plan=plan(row(t('remove_row','/grammar_links/0'),t('append_row','/grammar_links')))
    after=copy.deepcopy(before);old=after['grammar_links'].pop(0)
    revised=copy.deepcopy(old);revised['display_form']='가 가';after['grammar_links'].append(revised)
    projection_edits=[{'op':'remove_row','path':'/grammar_links/0'},
        {'op':'append_row','path':'/grammar_links','value':revised}]
    validate_derived_effects(before,after,repair_plan,packet,projection_edits)
    tampered=copy.deepcopy(after);tampered['grammar_links'][0]['context_en']='sibling changed'
    with pytest.raises(ValueError):validate_derived_effects(before,tampered,repair_plan,packet,projection_edits)



def test_joint_dependency_removals_keep_all_unrelated_rows_and_mixed_identity_edit():
    before={'segments':[{'text':'가','form_steps':[{'form':'가며','grammar_entry_ids':['old']}]}],
        'grammar_links':[
            {'segment_index':0,'entry_id':'old','context_en':'direct one','display_form':'','display_meaning_en':'one','display_end_segment_index':-1},
            {'segment_index':0,'entry_id':'old','context_en':'direct two','display_form':'','display_meaning_en':'two','display_end_segment_index':-1},
            {'segment_index':0,'entry_id':'other','context_en':'retained sibling','display_form':'','display_meaning_en':'other','display_end_segment_index':-1}],
        'expression_links':[],'inflected_segment_indices':[0]}
    issues=[{'candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids']}]
    packet=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    repair_plan=plan(row(t('replace_list','/segments/0/form_steps/0/grammar_entry_ids'),
        t('remove_row','/grammar_links/0'),t('remove_row','/grammar_links/1')))
    deps=repair_dependency_constraints(before,'korean-flat')
    _validate_plan(repair_plan,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=deps,target_authority=packet)
    edits=[{'op':'replace_list','path':'/segments/0/form_steps/0/grammar_entry_ids','value':['new']},
        {'op':'remove_row','path':'/grammar_links/0'},{'op':'remove_row','path':'/grammar_links/1'}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(repair_plan),representation='korean-flat')
    assert after['grammar_links']==[before['grammar_links'][2]]
    validate_derived_effects(before,after,repair_plan,packet,edits)
    altered=copy.deepcopy(after);altered['grammar_links'][0]['context_en']='unauthorized sibling edit'
    with pytest.raises(ValueError):validate_derived_effects(before,altered,repair_plan,packet,edits)


def test_joint_projection_replacements_preserve_base_indexed_untargeted_sibling():
    rows=[
        {'segment_index':0,'entry_id':'first','context_en':'first context','display_form':'가','display_meaning_en':'go','display_end_segment_index':0},
        {'segment_index':0,'entry_id':'sibling','context_en':'must remain','display_form':'가','display_meaning_en':'go','display_end_segment_index':0},
        {'segment_index':0,'entry_id':'third','context_en':'third context','display_form':'가','display_meaning_en':'go','display_end_segment_index':0}]
    before={'segments':[{'text':'가','form_steps':[]}],'grammar_links':rows,
        'expression_links':[],'inflected_segment_indices':[]}
    issues=[{'candidate_paths':['/grammar_links/0/display_form','/grammar_links/2/display_form']}]
    packet=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    repair_plan=plan(row(t('remove_row','/grammar_links/0'),t('append_row','/grammar_links'),
        t('remove_row','/grammar_links/2'),t('append_row','/grammar_links')))
    _validate_plan(repair_plan,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before,'korean-flat'),target_authority=packet)
    revised_first=copy.deepcopy(rows[0]);revised_first['display_form']='가 가'
    revised_third=copy.deepcopy(rows[2]);revised_third['display_form']='가 가 가'
    edits=[{'op':'remove_row','path':'/grammar_links/0'}, {'op':'remove_row','path':'/grammar_links/2'},
        {'op':'append_row','path':'/grammar_links','value':revised_first},
        {'op':'append_row','path':'/grammar_links','value':revised_third}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(repair_plan),representation='korean-flat')
    assert after['grammar_links']==[rows[1],revised_first,revised_third]
    validate_derived_effects(before,after,repair_plan,packet,edits)
    collateral=copy.deepcopy(revised_third);collateral['context_en']='not targeted'
    bad_edits=copy.deepcopy(edits);bad_edits[3]['value']=collateral
    bad=apply_edits(before,{'base_digest':candidate_digest(before),'edits':bad_edits},
        allowed_targets=_targets(repair_plan),representation='korean-flat')
    with pytest.raises(ValueError):validate_derived_effects(before,bad,repair_plan,packet,bad_edits)
    shifted_path_edits=[*edits,{'op':'set_field','path':'/grammar_links/0/context_en','value':'new index is not original target'}]
    with pytest.raises(Exception):
        apply_edits(before,{'base_digest':candidate_digest(before),'edits':shifted_path_edits},
            allowed_targets=_targets(repair_plan),representation='korean-flat')


def test_mixed_identity_rewire_dependency_removal_and_projection_replacement_share_one_base():
    before={'segments':[{'text':'가','form_steps':[{'form':'가며','grammar_entry_ids':['old']}]}],
        'grammar_links':[
            {'segment_index':0,'entry_id':'old','context_en':'target direct','display_form':'','display_meaning_en':'pattern','display_end_segment_index':-1},
            {'segment_index':0,'entry_id':'sibling','context_en':'preserve this sibling','display_form':'','display_meaning_en':'other','display_end_segment_index':-1},
            {'segment_index':0,'entry_id':'old','context_en':'dependent direct','display_form':'','display_meaning_en':'pattern','display_end_segment_index':-1}],
        'expression_links':[],'inflected_segment_indices':[0]}
    issues=[{'candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids',
        '/grammar_links/0/display_form','/grammar_links/0/display_end_segment_index']}]
    packet=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    repair_plan=plan(row(t('replace_list','/segments/0/form_steps/0/grammar_entry_ids'),
        t('remove_row','/grammar_links/0'),t('append_row','/grammar_links'),
        t('remove_row','/grammar_links/2')))
    _validate_plan(repair_plan,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before,'korean-flat'),target_authority=packet)
    revised=copy.deepcopy(before['grammar_links'][0]);revised['entry_id']='new'
    revised['display_form']='가';revised['display_end_segment_index']=0
    edits=[{'op':'replace_list','path':'/segments/0/form_steps/0/grammar_entry_ids','value':['new']},
        {'op':'remove_row','path':'/grammar_links/0'}, {'op':'remove_row','path':'/grammar_links/2'},
        {'op':'append_row','path':'/grammar_links','value':revised}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(repair_plan),representation='korean-flat')
    assert after['grammar_links']==[before['grammar_links'][1],revised]
    validate_derived_effects(before,after,repair_plan,packet,edits)
    bad=copy.deepcopy(after);bad['grammar_links'][0]['context_en']='collateral sibling change'
    with pytest.raises(ValueError):validate_derived_effects(before,bad,repair_plan,packet,edits)


def test_mixed_dependency_removal_keeps_set_field_bound_to_original_sibling_index():
    before={'segments':[{'text':'가','form_steps':[{'form':'가며','grammar_entry_ids':['old']}]}],
        'grammar_links':[
            {'segment_index':0,'entry_id':'old','context_en':'drop direct','display_form':'','display_meaning_en':'old','display_end_segment_index':-1},
            {'segment_index':0,'entry_id':'sibling','context_en':'old context','display_form':'','display_meaning_en':'sibling','display_end_segment_index':-1},
            {'segment_index':0,'entry_id':'other','context_en':'untouched','display_form':'','display_meaning_en':'other','display_end_segment_index':-1}],
        'expression_links':[],'inflected_segment_indices':[0]}
    issues=[{'candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids']},
        {'candidate_paths':['/grammar_links/1/context_en']}]
    packet=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    repair_plan={'issues':[
        {'issue_index':0,'reason':'identity support','targets':[t('replace_list','/segments/0/form_steps/0/grammar_entry_ids'),t('remove_row','/grammar_links/0')],'boundary_change_needed':False,'boundary_reason':''},
        {'issue_index':1,'reason':'exact sibling context','targets':[t('set_field','/grammar_links/1/context_en')],'boundary_change_needed':False,'boundary_reason':''}]}
    _validate_plan(repair_plan,2,before,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before,'korean-flat'),target_authority=packet)
    edits=[{'op':'set_field','path':'/grammar_links/1/context_en','value':'revised sibling'},
        {'op':'replace_list','path':'/segments/0/form_steps/0/grammar_entry_ids','value':['new']},
        {'op':'remove_row','path':'/grammar_links/0'}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(repair_plan),representation='korean-flat')
    assert after['grammar_links'][0]['entry_id']=='sibling'
    assert after['grammar_links'][0]['context_en']=='revised sibling'
    assert after['grammar_links'][1]==before['grammar_links'][2]
    validate_derived_effects(before,after,repair_plan,packet,edits)
    wrong_index=[dict(edit) for edit in edits]
    wrong_index[0]['path']='/grammar_links/0/context_en'
    with pytest.raises(Exception):
        apply_edits(before,{'base_digest':candidate_digest(before),'edits':wrong_index},
            allowed_targets=_targets(repair_plan),representation='korean-flat')


def _stage_row_dependency_fixture():
    candidate={'segments':[{'text':'가','meaning_en':'go','form_steps':[
        {'form':'가며','meaning_en':'go and','grammar_entry_ids':['old']},
        {'form':'가서','meaning_en':'go and then','grammar_entry_ids':['sibling-stage']}]}],
       'grammar_links':[
        {'segment_index':0,'entry_id':'old','context_en':'dependent','display_form':'','display_meaning_en':'pattern','display_end_segment_index':-1},
        {'segment_index':0,'entry_id':'other','context_en':'untouched sibling','display_form':'','display_meaning_en':'other','display_end_segment_index':-1}],
       'expression_links':[],'inflected_segment_indices':[0]}
    return candidate

def test_stage_row_removal_authorizes_only_its_exact_dependent_direct_row():
    before=_stage_row_dependency_fixture()
    issues=[{'candidate_paths':['/segments/0/form_steps/0']}]
    packet=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    assert packet['dependency_authority_policy_version']==2
    planv=plan(row(t('remove_row','/segments/0/form_steps/0'),t('remove_row','/grammar_links/0')))
    deps=repair_dependency_constraints(before,'korean-flat')
    _validate_plan(planv,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=deps,target_authority=packet)
    edits=[{'op':'remove_row','path':'/segments/0/form_steps/0'},
        {'op':'remove_row','path':'/grammar_links/0'}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(planv),representation='korean-flat')
    validate_derived_effects(before,after,planv,packet,edits)
    assert after['segments'][0]['form_steps']==[before['segments'][0]['form_steps'][1]]
    assert after['grammar_links']==[before['grammar_links'][1]]

    # A field-level semantic target cannot authorize dependency cleanup.
    for issue_path in ['/segments/0/meaning_en','/segments/0/form_steps/0/meaning_en']:
        narrow=[{'candidate_paths':[issue_path]}]
        narrow_packet=build_authority_packet(narrow,before,'korean-flat',candidate_digest(before))
        unauthorized=plan(row(t('set_field',issue_path),t('remove_row','/grammar_links/0')))
        with pytest.raises(ValueError,match='exceeds authenticated target authority'):
            _validate_plan(unauthorized,1,before,'korean-flat',target_contract_version=3,
                dependency_constraints=deps,target_authority=narrow_packet)

    # A targeted stage row does not authorize a sibling row or sibling field.
    collateral=plan(row(t('remove_row','/segments/0/form_steps/0'),
        t('remove_row','/grammar_links/0'),t('set_field','/grammar_links/1/context_en')))
    with pytest.raises(ValueError,match='exceeds authenticated target authority'):
        _validate_plan(collateral,1,before,'korean-flat',target_contract_version=3,
            dependency_constraints=deps,target_authority=packet)

def test_stage_row_replacement_rebinds_only_to_new_identity_and_requires_real_change():
    before=_stage_row_dependency_fixture()
    issues=[{'candidate_paths':['/segments/0/form_steps/0']}]
    packet=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    planv=plan(row(t('replace_row','/segments/0/form_steps/0'),
        t('set_field','/grammar_links/0/entry_id')))
    deps=repair_dependency_constraints(before,'korean-flat')
    _validate_plan(planv,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=deps,target_authority=packet)
    replacement={**before['segments'][0]['form_steps'][0],
        'form':'걸으며','meaning_en':'walk and','grammar_entry_ids':['new']}
    edits=[{'op':'replace_row','path':'/segments/0/form_steps/0','value':replacement},
        {'op':'set_field','path':'/grammar_links/0/entry_id','value':'new'}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(planv),representation='korean-flat')
    validate_derived_effects(before,after,planv,packet,edits)

    wrong_value=[dict(edits[0]),{**edits[1],'value':'unrelated'}]
    wrong_after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':wrong_value},
        allowed_targets=_targets(planv),representation='korean-flat')
    with pytest.raises(ValueError,match='replacement stage identity'):
        validate_derived_effects(before,wrong_after,planv,packet,wrong_value)

    same_identity={**before['segments'][0]['form_steps'][0],
        'meaning_en':'a meaning correction without an identity change'}
    unnecessary=[{'op':'replace_row','path':'/segments/0/form_steps/0','value':same_identity},
        {'op':'set_field','path':'/grammar_links/0/entry_id','value':'sibling-stage'}]
    unnecessary_after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':unnecessary},
        allowed_targets=_targets(planv),representation='korean-flat')
    with pytest.raises(ValueError,match='actual stage identity change'):
        validate_derived_effects(before,unnecessary_after,planv,packet,unnecessary)

def test_legacy_v3_authority_packet_keeps_original_dependency_shape():
    before=_coupled_identity_fixture()[0]
    issues=[{'candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids']}]
    legacy=build_authority_packet(issues,before,'korean-flat',candidate_digest(before),
        dependency_authority_policy_version=1)
    assert 'dependency_authority_policy_version' not in legacy
    assert 'trigger_kind' not in legacy['dependency_edges'][0]
    validate_authority_packet(legacy,issues,before,'korean-flat',candidate_digest(before))
    current=build_authority_packet(issues,before,'korean-flat',candidate_digest(before))
    assert current['dependency_authority_policy_version']==2
    assert current['dependency_edges'][0]['trigger_kind']=='identity_list'

@pytest.mark.parametrize('language,representation,candidate,path,existing,new_row', [
    ('zh','chinese-annotation',
     {'segments':[{'text':'她','type':'word','pinyin':'tā','meaning_en':'she'}],
      'grammar_overlays':[{'start':0,'end':1,'text':'她','grammar_candidate_key':'existing',
          'pattern':'existing pattern','meaning_en':'existing meaning'}]},
     '/grammar_overlays',
     {'start':0,'end':1,'text':'她','grammar_candidate_key':'existing',
      'pattern':'existing pattern','meaning_en':'existing meaning'},
     {'start':0,'end':1,'text':'她','grammar_candidate_key':'new-pattern',
      'pattern':'new pattern','meaning_en':'new meaning'}),
    ('ja','japanese-annotation',
     {'segments':[{'surface':'猫','type':'word','lemma':'猫','surface_kana':'ねこ','lemma_kana':'ねこ',
          'part_of_speech':'noun','conjugation_form':'','meaning_en':'cat','grammar_candidate_key':'',
          'dictionary_key':'','dictionary_definition_en':'','form_steps':[],'story_role':'none',
          'story_importance_en':''}],
      'grammar_overlays':[{'start':0,'end':1,'surface':'猫','grammar_candidate_key':'existing',
          'pattern':'existing pattern','meaning_en':'existing meaning','head_lemma':'猫',
          'head_lemma_kana':'ねこ','form_label':'fixture','explanation_en':'existing explanation',
          'components':[{'start':0,'end':1,'surface':'猫','lemma':'猫','lemma_kana':'ねこ',
              'function_en':'fixture','lookup_kind':'none','dictionary_key':'','dictionary_definition_en':''},
              {'start':0,'end':1,'surface':'猫','lemma':'猫','lemma_kana':'ねこ',
              'function_en':'fixture','lookup_kind':'none','dictionary_key':'','dictionary_definition_en':''}]}]},
     '/grammar_overlays',
     {'start':0,'end':1,'surface':'猫','grammar_candidate_key':'existing','pattern':'existing pattern',
      'meaning_en':'existing meaning','head_lemma':'猫','head_lemma_kana':'ねこ','form_label':'fixture',
      'explanation_en':'existing explanation','components':[
          {'start':0,'end':1,'surface':'猫','lemma':'猫','lemma_kana':'ねこ','function_en':'fixture',
           'lookup_kind':'none','dictionary_key':'','dictionary_definition_en':''},
          {'start':0,'end':1,'surface':'猫','lemma':'猫','lemma_kana':'ねこ','function_en':'fixture',
           'lookup_kind':'none','dictionary_key':'','dictionary_definition_en':''}]},
     {'start':0,'end':1,'surface':'猫','grammar_candidate_key':'new-pattern','pattern':'new pattern',
      'meaning_en':'new meaning','head_lemma':'猫','head_lemma_kana':'ねこ','form_label':'fixture',
      'explanation_en':'new explanation','components':[
          {'start':0,'end':1,'surface':'猫','lemma':'猫','lemma_kana':'ねこ','function_en':'fixture',
           'lookup_kind':'none','dictionary_key':'','dictionary_definition_en':''},
          {'start':0,'end':1,'surface':'猫','lemma':'猫','lemma_kana':'ねこ','function_en':'fixture',
           'lookup_kind':'none','dictionary_key':'','dictionary_definition_en':''}]}),
    ('ko','korean-flat',
     {'segments':[{'text':'가','type':'word','meaning_en':'go','lemma':'가',
          'lexical_kind':'vocabulary','lexical_id':'fixture-go','story_importance_en':'','form_steps':[]}],
      'grammar_links':[{'segment_index':0,'entry_id':'existing','context_en':'existing context',
          'display_form':'','display_meaning_en':'','display_end_segment_index':-1}],
      'expression_links':[],'inflected_segment_indices':[]},
     '/grammar_links',
     {'segment_index':0,'entry_id':'existing','context_en':'existing context',
      'display_form':'','display_meaning_en':'','display_end_segment_index':-1},
     {'segment_index':0,'entry_id':'new','context_en':'new context',
      'display_form':'','display_meaning_en':'','display_end_segment_index':-1}),
])
def test_explicit_semantic_collection_target_can_append_without_touching_siblings(
        language,representation,candidate,path,existing,new_row):
    from jsonschema import validate
    schema_name={'zh':'annotation.schema.json','ja':'japanese-annotation.schema.json',
        'ko':'korean-annotation.schema.json'}[language]
    schema=json.loads((Path(__file__).resolve().parents[1]/'pipeline/schemas'/schema_name).read_text())
    validate(candidate,schema)
    issues=[{'issue_id':'missing-construction','candidate_paths':[path]}]
    packet=build_authority_packet(issues,candidate,representation,candidate_digest(candidate))
    assert ('append_row',path) in {tuple(target) for target in packet['issues'][0]['allowed_targets']}
    planv=plan(row(t('append_row',path)))
    _validate_plan(planv,1,candidate,representation,target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(candidate,representation),
        target_authority=packet)
    edits=[{'op':'append_row','path':path,'value':new_row}]
    after=apply_edits(candidate,{'base_digest':candidate_digest(candidate),'edits':edits},
        allowed_targets=_targets(planv),representation=representation)
    validate(after,schema)
    validate_derived_effects(candidate,after,planv,packet,edits)
    assert after['segments']==candidate['segments']
    assert after[path[1:]]==[existing,new_row]

    # An exact meaning leaf has no authority to append a neighboring row.
    if path=='/grammar_overlays':
        leaf_issue=[{'candidate_paths':[path+'/0/meaning_en']}]
        leaf_packet=build_authority_packet(leaf_issue,candidate,representation,candidate_digest(candidate))
        unauthorized=plan(row(t('set_field',path+'/0/meaning_en'),t('append_row',path)))
        with pytest.raises(ValueError,match='exceeds authenticated target authority'):
            _validate_plan(unauthorized,1,candidate,representation,target_contract_version=3,
                dependency_constraints=repair_dependency_constraints(candidate,representation),
                target_authority=leaf_packet)

    # Collection append leaves old rows in place and cannot rewrite one as collateral.
    collateral=[*edits,{'op':'set_field','path':path+'/0/'+('meaning_en' if language!='ko' else 'context_en'),
        'value':'unauthorized sibling rewrite'}]
    with pytest.raises(ValueError,match='exact issue/dependency authority'):
        validate_derived_effects(candidate,after,planv,packet,collateral)

def test_semantic_append_authority_excludes_primary_segments_and_scalar_id_lists():
    candidate={'segments':[{'text':'가','meaning_en':'go','form_steps':[
        {'form':'가며','meaning_en':'go and','grammar_entry_ids':['old']}]}],
        'grammar_links':[],'expression_links':[],'inflected_segment_indices':[0]}
    for path in ['/segments','/segments/0/form_steps/0/grammar_entry_ids','/inflected_segment_indices']:
        issue=[{'candidate_paths':[path]}]
        packet=build_authority_packet(issue,candidate,'korean-flat',candidate_digest(candidate))
        assert ('append_row',path) not in {tuple(target) for target in packet['issues'][0]['allowed_targets']}

@pytest.mark.asyncio
async def test_host_collection_append_assembles_and_replays_exact_rows(tmp_path):
    from jsonschema import validate
    schema=json.loads((Path(__file__).resolve().parents[1]/'pipeline/schemas/annotation.schema.json').read_text())
    candidate={'segments':[{'text':'她','type':'word','pinyin':'tā','meaning_en':'she'}],
        'grammar_overlays':[{'start':0,'end':1,'text':'她','grammar_candidate_key':'existing',
            'pattern':'existing pattern','meaning_en':'existing meaning'}]}
    appended={'start':0,'end':1,'text':'她','grammar_candidate_key':'new-pattern',
        'pattern':'new pattern','meaning_en':'new meaning'}
    validate(candidate,schema)
    issues=[{'candidate_paths':['/grammar_overlays']}]
    repair_plan=plan(row(t('append_row','/grammar_overlays')))
    patch={'base_digest':candidate_digest(candidate),'edits':[
        {'op':'append_row','path':'/grammar_overlays','value':appended}]}
    runner=Runner(tmp_path,repair_plan,patch)
    result=await repair_annotation(Harness(tmp_path,runner),'append-v3',candidate,issues,
        representation='chinese-annotation',language='zh',context={'chunk_text':'她'},
        validate_candidate=lambda value:validate(value,schema))
    assert result['status']=='applied'
    assert result['candidate']['segments']==candidate['segments']
    assert result['candidate']['grammar_overlays']==[candidate['grammar_overlays'][0],appended]
    meta=json.loads((tmp_path/'agents/append-v3_assembly/meta.json').read_text())
    assert meta['repair_target_authority']['dependency_authority_policy_version']==2
    replay=replay_annotation_repair(tmp_path,'append-v3_assembly',
        validate_candidate=lambda value:validate(value,schema))
    assert replay['candidate']==result['candidate']


def test_policy2_same_row_projection_pair_covers_all_explicit_semantic_targets_only():
    fixture={'candidate': {'segments': [{'text': '가', 'meaning_en': 'go', 'form_steps': []}, {'text': '며', 'meaning_en': 'and', 'form_steps': []}], 'grammar_links': [{'segment_index': 1, 'entry_id': 'same', 'context_en': 'old', 'display_form': '', 'display_meaning_en': '', 'display_end_segment_index': -1}], 'expression_links': [], 'inflected_segment_indices': []}, 'issues': [{'candidate_paths': ['/grammar_links/0/segment_index', '/grammar_links/0/context_en', '/grammar_links/0/display_form', '/grammar_links/0/display_meaning_en', '/grammar_links/0/display_end_segment_index'], 'supporting_paths': ['/grammar_links/0/entry_id']}], 'replacement': {'segment_index': 0, 'entry_id': 'same', 'context_en': 'new', 'display_form': '가며', 'display_meaning_en': 'go and', 'display_end_segment_index': 1}}
    before=fixture['candidate']; issues=fixture['issues']; rep='korean-flat'
    packet=build_authority_packet(issues,before,rep,candidate_digest(before))
    assert packet['projected_row_authority_policy_version']==2
    authority=packet['issues'][0]['row_replacement_authorities'][0]
    assert set(authority['fields'])=={'segment_index','context_en','display_form',
        'display_meaning_en','display_end_segment_index'}
    assert '/grammar_links/0/entry_id' not in authority['target_paths']
    repair_plan=plan(row(t('remove_row','/grammar_links/0'),t('append_row','/grammar_links')))
    _validate_plan(repair_plan,1,before,rep,target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before,rep),target_authority=packet)
    replacement=fixture['replacement']
    edits=[{'op':'remove_row','path':'/grammar_links/0'},
        {'op':'append_row','path':'/grammar_links','value':replacement}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(repair_plan),representation=rep)
    validate_derived_effects(before,after,repair_plan,packet,edits)
    assert after['segments']==before['segments']
    assert after['grammar_links'][0]['entry_id']=='same'

    for field in ('segment_index','context_en','display_form','display_meaning_en',
                  'display_end_segment_index'):
        incomplete=copy.deepcopy(replacement)
        incomplete[field]=before['grammar_links'][0][field]
        bad_edits=[edits[0],{**edits[1],'value':incomplete}]
        bad_after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':bad_edits},
            allowed_targets=_targets(repair_plan),representation=rep)
        with pytest.raises(ValueError,match='every explicitly targeted same-row field'):
            validate_derived_effects(before,bad_after,repair_plan,packet,bad_edits)

    # The supporting identity is preserved; changing it or an untargeted field is forbidden.
    collateral=copy.deepcopy(replacement);collateral['entry_id']='other'
    bad_edits=[edits[0],{**edits[1],'value':collateral}]
    bad_after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':bad_edits},
        allowed_targets=_targets(repair_plan),representation=rep)
    with pytest.raises(ValueError,match='one-to-one'):
        validate_derived_effects(before,bad_after,repair_plan,packet,bad_edits)

def test_policy2_same_row_pair_does_not_authorize_untargeted_semantic_sibling_fields():
    before={'segments':[{'text':'가','form_steps':[]},{'text':'며','form_steps':[]}],
        'grammar_links':[{'segment_index':1,'entry_id':'same','context_en':'old','display_form':'',
            'display_meaning_en':'','display_end_segment_index':-1}],
        'expression_links':[],'inflected_segment_indices':[]}
    issue=[{'candidate_paths':['/grammar_links/0/segment_index','/grammar_links/0/display_form',
        '/grammar_links/0/display_end_segment_index']}]
    packet=build_authority_packet(issue,before,'korean-flat',candidate_digest(before))
    repair_plan=plan(row(t('remove_row','/grammar_links/0'),t('append_row','/grammar_links')))
    _validate_plan(repair_plan,1,before,'korean-flat',target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before,'korean-flat'),target_authority=packet)
    replacement={'segment_index':0,'entry_id':'same','context_en':'changed without authority',
        'display_form':'가며','display_meaning_en':'','display_end_segment_index':1}
    edits=[{'op':'remove_row','path':'/grammar_links/0'},
        {'op':'append_row','path':'/grammar_links','value':replacement}]
    after=apply_edits(before,{'base_digest':candidate_digest(before),'edits':edits},
        allowed_targets=_targets(repair_plan),representation='korean-flat')
    with pytest.raises(ValueError,match='appended rows do not map'):
        validate_derived_effects(before,after,repair_plan,packet,edits)

@pytest.mark.parametrize('language,representation,candidate,paths,make_replacement,schema_name',[
    ('zh','chinese-annotation',
     {'segments':[{'text':'ABC','type':'word','pinyin':'','meaning_en':'fixture'}],
      'grammar_overlays':[{'start':1,'end':3,'text':'BC','grammar_candidate_key':'fixture',
          'pattern':'fixture pattern','meaning_en':'old meaning'}]},
     ['/grammar_overlays/0/start','/grammar_overlays/0/end','/grammar_overlays/0/text',
      '/grammar_overlays/0/meaning_en'],
     {'start':0,'end':2,'text':'AB','grammar_candidate_key':'fixture',
      'pattern':'fixture pattern','meaning_en':'new meaning'},'annotation.schema.json'),
    ('ja','japanese-annotation',
     {'segments':[{'surface':'ABC','type':'word','lemma':'ABC','surface_kana':'ABC','lemma_kana':'ABC',
          'part_of_speech':'fixture','conjugation_form':'','meaning_en':'fixture',
          'grammar_candidate_key':'','dictionary_key':'','dictionary_definition_en':'',
          'form_steps':[],'story_role':'none','story_importance_en':''}],
      'grammar_overlays':[{'start':1,'end':3,'surface':'BC','grammar_candidate_key':'fixture',
          'pattern':'fixture pattern','meaning_en':'old meaning','head_lemma':'AB',
          'head_lemma_kana':'AB','form_label':'fixture','explanation_en':'fixture explanation',
          'components':[{'start':0,'end':1,'surface':'A','lemma':'A','lemma_kana':'A',
              'function_en':'fixture','lookup_kind':'none','dictionary_key':'',
              'dictionary_definition_en':''},
              {'start':1,'end':2,'surface':'B','lemma':'B','lemma_kana':'B',
              'function_en':'fixture','lookup_kind':'none','dictionary_key':'',
              'dictionary_definition_en':''}]}]},
     ['/grammar_overlays/0/start','/grammar_overlays/0/end','/grammar_overlays/0/surface',
      '/grammar_overlays/0/meaning_en'],
     {'start':0,'end':2,'surface':'AB','grammar_candidate_key':'fixture',
      'pattern':'fixture pattern','meaning_en':'new meaning','head_lemma':'AB',
      'head_lemma_kana':'AB','form_label':'fixture','explanation_en':'fixture explanation',
      'components':[{'start':0,'end':1,'surface':'A','lemma':'A','lemma_kana':'A',
          'function_en':'fixture','lookup_kind':'none','dictionary_key':'',
          'dictionary_definition_en':''},
          {'start':1,'end':2,'surface':'B','lemma':'B','lemma_kana':'B',
          'function_en':'fixture','lookup_kind':'none','dictionary_key':'',
          'dictionary_definition_en':''}]},'japanese-annotation.schema.json'),
])
def test_policy2_projected_row_pair_keeps_actual_zh_ja_schema_and_nested_components(
        language,representation,candidate,paths,make_replacement,schema_name):
    from jsonschema import validate
    schema=json.loads((Path(__file__).resolve().parents[1]/'pipeline/schemas'/schema_name).read_text())
    validate(candidate,schema)
    issue=[{'candidate_paths':paths,'supporting_paths':[]}]
    packet=build_authority_packet(issue,candidate,representation,candidate_digest(candidate))
    repair_plan=plan(row(t('remove_row','/grammar_overlays/0'),t('append_row','/grammar_overlays')))
    _validate_plan(repair_plan,1,candidate,representation,target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(candidate,representation),
        target_authority=packet)
    edits=[{'op':'remove_row','path':'/grammar_overlays/0'},
        {'op':'append_row','path':'/grammar_overlays','value':make_replacement}]
    after=apply_edits(candidate,{'base_digest':candidate_digest(candidate),'edits':edits},
        allowed_targets=_targets(repair_plan),representation=representation)
    validate(after,schema)
    validate_derived_effects(candidate,after,repair_plan,packet,edits)
    assert after['segments']==candidate['segments']
    if language=='ja':
        assert after['grammar_overlays'][0]['components']==candidate['grammar_overlays'][0]['components']

@pytest.mark.asyncio
async def test_policy2_host_request_and_cache_replay_bind_mixed_row_targets(tmp_path):
    before={'segments':[{'text':'ABC','type':'word','pinyin':'ā','meaning_en':'fixture'}],
        'grammar_overlays':[{'start':1,'end':3,'text':'BC','grammar_candidate_key':'fixture',
            'pattern':'fixture pattern','meaning_en':'old meaning'}]}
    issues=[{'candidate_paths':['/grammar_overlays/0/start','/grammar_overlays/0/end',
        '/grammar_overlays/0/text','/grammar_overlays/0/meaning_en'],'supporting_paths':[]}]
    replacement={'start':0,'end':2,'text':'AB','grammar_candidate_key':'fixture',
        'pattern':'fixture pattern','meaning_en':'new meaning'}
    rep='chinese-annotation'
    repair_plan=plan(row(t('remove_row','/grammar_overlays/0'),t('append_row','/grammar_overlays')))
    patch={'base_digest':candidate_digest(before),'edits':[
        {'op':'remove_row','path':'/grammar_overlays/0'},
        {'op':'append_row','path':'/grammar_overlays','value':replacement}]}
    runner=Runner(tmp_path,repair_plan,patch);validated=[]
    result=await repair_annotation(Harness(tmp_path,runner),'projection-semantic-v2',before,issues,
        representation=rep,language='zh',context={'chunk_text':'ABC'},
        validate_candidate=lambda value:validated.append(copy.deepcopy(value)))
    assert result['status']=='applied' and result['candidate']['grammar_overlays'][0]==replacement
    assert len(validated)==1 and validated[0]==result['candidate']
    assert len(runner.prompts)==2
    assert all('row_replacement_authorities' in prompt and 'supporting path is not a repair target' in prompt
        for _,prompt,_ in runner.prompts)
    meta=json.loads((tmp_path/'agents/projection-semantic-v2_assembly/meta.json').read_text())
    assert meta['repair_target_authority']['projected_row_authority_policy_version']==2
    replay=replay_annotation_repair(tmp_path,'projection-semantic-v2_assembly',
        validate_candidate=lambda value:None)
    assert replay['candidate']==result['candidate']
