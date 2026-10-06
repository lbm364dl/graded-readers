"""Request/gate fixtures test structure, not linguistic approval."""
import copy
import json
import pytest
from jsonschema import validate, ValidationError
from pipeline import annotation_repairs as repairs
from pipeline.annotation_repair_dependencies import repair_dependency_constraints
from pipeline.annotation_edits import candidate_digest
from pipeline.worker_workspace import build, check
from tests.test_annotation_repairs import _plan, _target, _candidate, Harness, PlannedRunner, WorkspacePlannedRunner


def korean():
    return {'segments': [
        {'text': '가며', 'type': 'word', 'meaning_en': 'going', 'form_steps': [
            {'form': '가며', 'reading': '', 'label': 'fixture', 'meaning_en': 'going', 'grammar_entry_ids': ['old']}]},
        {'text': '가며', 'type': 'word', 'meaning_en': 'another occurrence', 'form_steps': []}],
        'grammar_links': [{'segment_index': 0, 'entry_id': 'old', 'context_en': 'fixture',
                          'display_form': '', 'display_meaning_en': '', 'display_end_segment_index': -1}],
        'expression_links': [], 'inflected_segment_indices': [0]}


ID = '/segments/0/form_steps/0/grammar_entry_ids'
LINK = '/grammar_links/0/entry_id'


def validate_plan(candidate, targets, version=2):
    repairs._validate_plan(_plan(tuple(targets)), 1, candidate, 'korean-flat',
                          target_contract_version=version,
                          dependency_constraints=repair_dependency_constraints(candidate, 'korean-flat') if version == 2 else None)


@pytest.mark.parametrize('operation,path', [
    ('replace_list', ID), ('remove_row', '/segments/0/form_steps/0'),
    ('replace_row', '/segments/0/form_steps/0'), ('replace_list', '/segments/0/form_steps'),
    ('replace_row', '/segments/0')])
def test_new_plan_rejects_omitted_sole_support_dependency(operation, path):
    with pytest.raises(ValueError, match='sole direct-link support'):
        validate_plan(korean(), [_target(operation, path)])


def test_same_issue_link_identity_target_is_structurally_complete():
    validate_plan(korean(), [_target('replace_list', ID), _target('set_field', LINK)])
    with pytest.raises(ValueError, match='same issue'):
        candidate=korean()
        plan=_plan((_target('replace_list', ID),), (_target('set_field', LINK),))
        repairs._validate_plan(plan, 2, candidate, 'korean-flat', target_contract_version=2)


def test_meaning_only_and_retained_stage_need_no_link_edit():
    candidate=korean()
    validate_plan(candidate, [_target('set_field', '/segments/0/form_steps/0/meaning_en')])
    candidate['segments'][0]['form_steps'].append(copy.deepcopy(candidate['segments'][0]['form_steps'][0]))
    validate_plan(candidate, [_target('replace_list', ID)])


def test_unrelated_repeated_tap_does_not_support_or_cover_owner():
    candidate=korean();candidate['segments'][1]['form_steps']=copy.deepcopy(candidate['segments'][0]['form_steps'])
    candidate['grammar_links'].append({**candidate['grammar_links'][0], 'segment_index': 1})
    with pytest.raises(ValueError, match=LINK):
        validate_plan(candidate, [_target('replace_list', ID), _target('set_field', '/grammar_links/1/entry_id')])


def test_complete_occurrence_does_not_validate_unsupported_empty_direct_row(tmp_path):
    from pipeline.korean_dictionary import build_assets
    candidate=korean();candidate['grammar_links'].append({
        'segment_index': 0, 'entry_id': 'old', 'context_en': 'complete fixture',
        'display_form': '가며가며', 'display_meaning_en': 'complete fixture', 'display_end_segment_index': 1})
    constraints=repair_dependency_constraints(candidate, 'korean-flat')
    assert constraints['dependencies'][0]['complete_occurrence_paths']==['/grammar_links/1']
    with pytest.raises(ValueError, match='sole direct-link support'):
        validate_plan(candidate, [_target('replace_list', ID)])
    validate_plan(candidate, [_target('replace_list', ID), _target('remove_row', '/grammar_links/0')])
    # Actual publication gate still rejects the empty row after old stage loss.
    derived=copy.deepcopy(candidate);derived['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new']
    for row in derived['segments']:row['lexical']={'id':'fixture-word','kind':'proper_name'}
    chapter={'text':'가며가며','segments':derived['segments'],'grammar_links':[
        {k:v for k,v in derived['grammar_links'][0].items() if not k.startswith('display_')},
        {**derived['grammar_links'][1],'segment_index':1,'display_form':'가며','display_end_segment_index':1}]}
    with pytest.raises(ValueError, match='lacks complete form'):
        build_assets(chapter,tmp_path,word_registry={'fixture-word':{'id':'fixture-word','kind':'proper_name'}},grammar_registry={'old':{'id':'old'},'new':{'id':'new'}},write=False)


def test_actual_nested_v4_dependencies_use_source_ranges():
    candidate={'format':'source-span-links-annotation-v4','segments':[
        {'source_start':0,'source_end':2,'form_steps':korean()['segments'][0]['form_steps'],
         'grammar_links':[{'entry_id':'old','source_start':-1,'source_end':-1}], 'expression_links':[]}]}
    constraints=repair_dependency_constraints(candidate,'korean-v4')
    assert constraints['dependencies'][0]['owner_source_range']==[0,2]
    assert constraints['dependencies'][0]['direct_link_identity_paths']==['/segments/0/grammar_links/0/entry_id']
    from pipeline.annotation_repair_dependencies import validate_plan_dependencies
    with pytest.raises(ValueError):validate_plan_dependencies(_plan((_target('replace_list',ID),)),candidate,'korean-v4')
    validate_plan_dependencies(_plan((_target('replace_list',ID),_target('set_field','/segments/0/grammar_links/0/entry_id'))),candidate,'korean-v4')


@pytest.mark.parametrize('representation',['chinese-annotation','japanese-annotation'])
def test_other_real_schema_paths_do_not_infer_korean_stage_relations(representation):
    assert repair_dependency_constraints({'segments':[],'grammar_overlays':[]},representation)['dependencies']==[]


def test_legacy_plan_still_replays_without_new_constraint():
    validate_plan(korean(), [_target('replace_list', ID)], version=1)


def test_dynamic_patch_schema_binds_exact_base_and_does_not_overwrite_other_request(tmp_path):
    first=_candidate();second=copy.deepcopy(first);second['segments'][0]['meaning_en']='changed fixture'
    _,a=repairs._schemas(tmp_path,candidate=first,job='repair-one')
    _,b=repairs._schemas(tmp_path,candidate=second,job='repair-two')
    assert a!=b
    schema=json.loads(a.read_text());other=json.loads(b.read_text())
    assert schema['properties']['base_digest']['const']==candidate_digest(first)
    assert other['properties']['base_digest']['const']==candidate_digest(second)
    patch={'base_digest':candidate_digest(first),'edits':[]};validate(patch,schema)
    patch['base_digest']=candidate_digest(second)
    with pytest.raises(ValidationError):validate(patch,schema)
    validate(patch,repairs.PATCH_SCHEMA)  # Historical generic schema unchanged.


def test_canonical_workspace_patch_gate_checks_base_schema_and_plan(tmp_path):
    candidate=_candidate();plan=_plan((_target('set_field','/segments/0/meaning_en'),))
    allowed=repairs._targets(plan);base=candidate_digest(candidate)
    _,schema_path=repairs._schemas(tmp_path,candidate=candidate,job='fixture')
    context={'candidate':candidate,'base_digest':base,'repair_plan':plan,'allowed_targets':allowed,
        'annotation_patch_validation':{'base_candidate':candidate,'representation':'chinese-annotation',
          'language':'zh','chunk_text':'她走','allowed_targets':allowed,'repair_plan':plan,
          'target_contract_version':1,'request_binding_policy_version':3}}
    workspace=tmp_path/'workspace';build(workspace,'Fixture only',json.loads(schema_path.read_text()),context=context)
    artifact=workspace/'candidate.json';artifact.write_text(json.dumps({'base_digest':base,'edits':[
        {'op':'set_field','path':'/segments/0/meaning_en','value':'she (fixture)'}]}))
    check(workspace,artifact)
    index=json.loads((workspace/'INDEX.json').read_text());path=workspace/next(row['path'] for row in index if row['field']=='base_digest')
    path.write_text(json.dumps('0'*64))
    with pytest.raises(ValueError,match='canonical immutable'):check(workspace,artifact)

@pytest.mark.asyncio
@pytest.mark.parametrize('language,representation,surface',[('zh','chinese-annotation','说'),('ja','japanese-annotation','猫'),('ko','korean-flat','사람')])
async def test_shared_three_language_requests_bind_schema_and_new_plan_policy(tmp_path,language,representation,surface):
    from tests.test_annotation_repairs import _korean_candidate_gate
    # Each workspace validator receives its real language schema, never a union.
    if language == 'zh':
        candidate={'segments':[{'text':surface,'type':'word','pinyin':'shuō',
                               'meaning_en':'say'}], 'grammar_overlays':[]}
    elif language == 'ja':
        candidate={'segments':[{'surface':surface,'type':'word','lemma':'猫',
            'surface_kana':'ねこ','lemma_kana':'ねこ','part_of_speech':'noun',
            'conjugation_form':'uninflected','meaning_en':'cat','grammar_candidate_key':'',
            'dictionary_key':'猫','dictionary_definition_en':'cat','form_steps':[],
            'story_role':'none','story_importance_en':''}], 'grammar_overlays':[]}
    else:
        candidate={'segments':[{'text':surface,'type':'word','lemma':'사람',
            'lexical_kind':'vocabulary','lexical_id':'사람/명','meaning_en':'person',
            'story_importance_en':'','form_steps':[]}], 'grammar_links':[],
            'expression_links':[], 'inflected_segment_indices':[]}
    plan=_plan((_target('set_field','/segments/0/meaning_en'),))
    patch={'base_digest':candidate_digest(candidate),'edits':[{'op':'set_field','path':'/segments/0/meaning_en','value':'fixture corrected'}]}
    runner=WorkspacePlannedRunner(tmp_path,plan,patch)
    context={'chunk_text':surface}
    if language=='ko':
        context['candidate_gate']=_korean_candidate_gate()
        context['candidate_gate']['focus']={'entries':[]}
        context['candidate_gate']['plan']['beats']=[]
    result=await repairs.repair_annotation(Harness(tmp_path,runner),'fixture-repair',candidate,[{
        'problem':'Fixture meaning correction','candidate_paths':['/segments/0/meaning_en'],
        'supporting_paths':[]}],representation=representation,language=language,context=context,validate_candidate=lambda _:None)
    assert result['status']=='applied'
    assert runner.calls[0][4]['workspace_context']['annotation_plan_validation']['target_contract_version']==3
    assert runner.calls[0][4]['workspace_context']['repair_dependency_constraints']==repair_dependency_constraints(candidate,representation)
    patch_schema=json.loads(runner.calls[1][2].read_text())
    assert patch_schema['properties']['base_digest']['const']==candidate_digest(candidate)
    assert runner.calls[1][4]['workspace_context']['annotation_patch_validation']['request_binding_policy_version']==3
    meta=json.loads((tmp_path/'agents/fixture-repair_assembly/meta.json').read_text())
    assert meta['repair_request_policy_version']==3 and meta['plan_target_contract_version']==3
    authority=runner.calls[0][4]['workspace_context']['repair_target_authority']
    assert runner.calls[0][4]['workspace_context']['annotation_plan_validation']['target_contract_version']==3
    assert authority['version']==3
    assert authority['issues'][0]['paths']==['/segments/0/meaning_en']
    assert authority['issues'][0]['allowed_targets']==[['set_field','/segments/0/meaning_en']]
    assert repairs.replay_annotation_repair(tmp_path,'fixture-repair_assembly',validate_candidate=lambda _:None)['candidate']==result['candidate']


def test_worker_plan_gate_rejects_missing_or_tampered_dependency_packet(tmp_path):
    candidate=korean();constraints=repair_dependency_constraints(candidate,'korean-flat')
    context={'candidate':candidate,'representation':'korean-flat','repair_dependency_constraints':constraints,
             'annotation_plan_validation':{'issue_count':1,'target_contract_version':2}}
    workspace=tmp_path/'plan-workspace';build(workspace,'Fixture only',repairs.PLAN_SCHEMA,context=context)
    artifact=workspace/'candidate.json';artifact.write_text(json.dumps(_plan((_target('replace_list',ID),))))
    with pytest.raises(ValueError,match='sole direct-link'):check(workspace,artifact)
    artifact.write_text(json.dumps(_plan((_target('replace_list',ID),_target('set_field',LINK)))))
    check(workspace,artifact)
    index=json.loads((workspace/'INDEX.json').read_text());row=next(row for row in index if row['field']=='repair_dependency_constraints')
    (workspace/row['path']).write_text(json.dumps({**constraints,'dependencies':[]}))
    with pytest.raises(ValueError,match='immutable candidate'):check(workspace,artifact)
    (workspace/'INDEX.json').write_text(json.dumps([item for item in index if item['field']!='repair_dependency_constraints']))
    with pytest.raises(ValueError,match='requires typed'):check(workspace,artifact)
