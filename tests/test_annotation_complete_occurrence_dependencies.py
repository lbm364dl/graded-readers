"""Structural reference projection and live shared request fixtures, not linguistics."""
import copy
import pytest
from pipeline.annotation_repair_dependencies import repair_dependency_constraints,validate_plan_dependencies
from pipeline import annotation_repairs as r
from tests.test_annotation_repairs import WorkspacePlannedRunner,Harness,_plan,_target

def _korean_candidate_gate():
 return {'focus':{'entries':[]},'title':'Fixture chapter','number':1,
  'plan':{'title':'Fixture chapter','scope_reason_en':'A one sentence validation fixture.',
   'last_source_paragraph_index':0,'beats':[{'source_paragraph_index':0,'event_en':'A fixture action.'}]},
  'level':1,'source_id':'assets/annotations/korean_l1_001.json'}

def flat():
 return {'inflected_segment_indices':[0], 'segments':[{'text':'가며','type':'word','meaning_en':'fixture','form_steps':[{'form':'가며','reading':'','label':'fixture','meaning_en':'fixture','grammar_entry_ids':['old']}]},{'text':' ','type':'punctuation'},{'text':'먹었다','type':'word','meaning_en':'fixture','form_steps':[]}], 'grammar_links':[{'segment_index':0,'entry_id':'old','context_en':'fixture','display_form':'','display_meaning_en':'','display_end_segment_index':-1},{'segment_index':0,'entry_id':'new','context_en':'fixture','display_form':'가며 먹었다','display_meaning_en':'fixture whole','display_end_segment_index':2}]}
def test_replacement_identity_complete_occurrence_is_explicit_not_old_support():
 packet=repair_dependency_constraints(flat(),'korean-flat');row=packet['dependencies'][0]
 assert packet['version']==2 and row['complete_occurrence_paths']==[]
 assert row['same_anchor_complete_occurrences']==[{'path':'/grammar_links/1','identity_path':'/grammar_links/1/entry_id','entry_id':'new','source_range':[0,6],'source_form':'가며 먹었다','display_meaning_en':'fixture whole'}]
 old=repair_dependency_constraints(flat(),'korean-flat',version=1)
 assert old['version']==1 and 'same_anchor_complete_occurrences' not in old['dependencies'][0]
@pytest.mark.parametrize('change',[{'segment_index':2},{'display_form':'먹었다'},{'display_end_segment_index':3},{'display_meaning_en':''},{'display_end_segment_index':True}])
def test_foreign_or_malformed_complete_occurrences_not_offered(change):
 value=flat();value['grammar_links'][1].update(change)
 assert repair_dependency_constraints(value,'korean-flat')['dependencies'][0]['same_anchor_complete_occurrences']==[]
def test_current_and_legacy_packets_authenticate_exact_versioned_geometry():
 value=flat();plan=_plan((_target('replace_list','/segments/0/form_steps/0/grammar_entry_ids'),_target('remove_row','/grammar_links/0')))
 for version in (1,2):
  packet=repair_dependency_constraints(value,'korean-flat',version=version);validate_plan_dependencies(plan,value,'korean-flat',packet)
  bad=copy.deepcopy(packet);bad['dependencies'][0]['owner_surface']='foreign'
  with pytest.raises(ValueError):validate_plan_dependencies(plan,value,'korean-flat',bad)
 for version in (True,0,3,None):
  with pytest.raises(ValueError):repair_dependency_constraints(value,'korean-flat',version=version)
def test_nested_v4_no_source_copy_uses_tap_aligned_range():
 value={'format':'source-span-links-annotation-v4','segments':[{'source_start':0,'source_end':2,'form_steps':flat()['segments'][0]['form_steps'],'grammar_links':[{'entry_id':'old','source_start':-1,'source_end':-1,'display_meaning_en':''},{'entry_id':'new','source_start':0,'source_end':6,'display_meaning_en':'fixture whole'}]},{'source_start':2,'source_end':3},{'source_start':3,'source_end':6}]}
 row=repair_dependency_constraints(value,'korean-v4')['dependencies'][0]['same_anchor_complete_occurrences'][0]
 assert row['path']=='/segments/0/grammar_links/1' and row['source_range']==[0,6] and row['source_form'] is None
 value['segments'][0]['grammar_links'][1]['source_end']=5
 assert repair_dependency_constraints(value,'korean-v4')['dependencies'][0]['same_anchor_complete_occurrences']==[]
@pytest.mark.parametrize('rep',['chinese-annotation','japanese-annotation'])
def test_cj_actual_contracts_do_not_gain_korean_identity_dependencies(rep):
 assert repair_dependency_constraints({'segments':flat()['segments'],'grammar_overlays':[]},rep)=={'version':2,'representation':rep,'dependencies':[]}

@pytest.mark.asyncio
@pytest.mark.parametrize('language,representation,surface',[('zh','chinese-annotation','说'),('ja','japanese-annotation','言う'),('ko','korean-flat','가며')])
async def test_actual_shared_plan_patch_boundary_receives_consistent_versioned_packet(tmp_path,language,representation,surface):
 from pipeline.annotation_edits import candidate_digest
 if language=='zh':
  value={'segments':[{'text':surface,'type':'word','pinyin':'shuō','meaning_en':'fixture'}], 'grammar_overlays':[]}
 elif language=='ja':
  value={'segments':[{'surface':surface,'type':'word','lemma':surface,'surface_kana':'いう','lemma_kana':'いう',
   'part_of_speech':'verb','conjugation_form':'dictionary form','meaning_en':'to say','grammar_candidate_key':'',
   'dictionary_key':'言う','dictionary_definition_en':'to say','form_steps':[],'story_role':'none',
   'story_importance_en':''}], 'grammar_overlays':[]}
 else:
  value={'segments':[{'text':'가며','type':'word','meaning_en':'go and','lemma':'가다',
   'lexical_kind':'vocabulary','lexical_id':'가다01/동','story_importance_en':'',
   'form_steps':[{'form':'가며','reading':'가며','label':'connective','meaning_en':'go and',
       'grammar_entry_ids':['connective-myeo']}]}], 'grammar_links':[{'segment_index':0,
      'entry_id':'connective-myeo','context_en':'fixture','display_form':'',
      'display_meaning_en':'','display_end_segment_index':-1}],
      'expression_links':[],'inflected_segment_indices':[0]}
 target=_target('set_field','/segments/0/meaning_en')
 plan=_plan((target,));patch={'base_digest':candidate_digest(value),'edits':[{'op':'set_field','path':'/segments/0/meaning_en','value':'fixture changed'}]}
 runner=WorkspacePlannedRunner(tmp_path,plan,patch);context={'chunk_text':surface}
 if language=='ko':context.update({'chunk_text':'가며','candidate_gate':_korean_candidate_gate()})
 result=await r.repair_annotation(Harness(tmp_path,runner),'fixture',value,[{'problem':'fixture','candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}],representation=representation,language=language,context=context,validate_candidate=lambda _:None)
 assert result['status']=='applied'
 for job,prompt,schema,effort,options in runner.calls:
  ctx=options['workspace_context'];assert ctx['repair_dependency_guidance_version']==4 and ctx['repair_dependency_constraints']['version']==2
  assert effort=='low'
  if job.endswith('_plan'):assert 'same_anchor_complete_occurrences' in prompt
  assert 'pair its identity target with the existing matching direct entry_id field' not in prompt

@pytest.mark.asyncio
async def test_real_shared_korean_packet_contains_other_identity_complete_source(tmp_path):
 from pipeline.annotation_edits import candidate_digest
 value={'segments':[{'text':'가며','type':'word','meaning_en':'go and','lemma':'가다',
   'lexical_kind':'vocabulary','lexical_id':'가다01/동','story_importance_en':'',
   'form_steps':[{'form':'가며','reading':'가며','label':'connective','meaning_en':'go and',
       'grammar_entry_ids':['connective-myeo']}]},
   {'text':' ','type':'punctuation','meaning_en':'','lemma':'','lexical_kind':'','lexical_id':'',
    'story_importance_en':'','form_steps':[]},
   {'text':'먹었다','type':'word','meaning_en':'ate','lemma':'먹다','lexical_kind':'vocabulary',
    'lexical_id':'먹다02/동','story_importance_en':'','form_steps':[{'form':'먹었다',
     'reading':'먹었+다','label':'past','meaning_en':'ate','grammar_entry_ids':['past-ass-eoss']}]}],
   'grammar_links':[{'segment_index':0,'entry_id':'connective-myeo','context_en':'fixture direct',
      'display_form':'','display_meaning_en':'','display_end_segment_index':-1},
     {'segment_index':0,'entry_id':'connective-go','context_en':'fixture complete occurrence',
      'display_form':'가며 먹었다','display_meaning_en':'go and ate','display_end_segment_index':2},
     {'segment_index':2,'entry_id':'past-ass-eoss','context_en':'fixture past form',
      'display_form':'','display_meaning_en':'','display_end_segment_index':-1}],
   'inflected_segment_indices':[0,2],'expression_links':[]}
 target=_target('set_field','/segments/0/meaning_en')
 runner=WorkspacePlannedRunner(tmp_path,_plan((target,)),{'base_digest':candidate_digest(value),'edits':[{**target,'value':'fixture changed'}]})
 result=await r.repair_annotation(Harness(tmp_path,runner),'fixture',value,[{'problem':'fixture','candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}],representation='korean-flat',language='ko',context={'chunk_text':'가며 먹었다','candidate_gate':_korean_candidate_gate()},validate_candidate=lambda _:None)
 assert result['status']=='applied'
 for job,prompt,schema,effort,options in runner.calls:
  row=options['workspace_context']['repair_dependency_constraints']['dependencies'][0]
  assert row['same_anchor_complete_occurrences'][0]['entry_id']=='connective-go' and row['complete_occurrence_paths']==[]
  assert 'same_anchor_complete_occurrences' in prompt

def test_nonduplicate_retained_direct_update_full_publication_gate(tmp_path):
 from pipeline.korean_dictionary import build_assets
 value=flat();value.update(text='가며 먹었다',title='Fixture',form_audit={'reviewed':True,'inflected_segment_indices':[0]})
 for row in value['segments']:
  if row['type']=='word':row['lexical']={'id':'word','kind':'proper_name'}
 value['grammar_links'][1]['entry_id']='different'
 value['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new'];value['grammar_links'][0]['entry_id']='new'
 # Flat publication normally strips the direct sentinel fields at conversion.
 value['grammar_links'][0]={k:v for k,v in value['grammar_links'][0].items() if not k.startswith('display_')}
 build_assets(value,tmp_path,word_registry={'word':{'id':'word','kind':'proper_name'}},grammar_registry={k:{'id':k} for k in ('old','new','different')},write=False)
 value['grammar_links'][1]['entry_id']='new'
 with pytest.raises(ValueError,match='duplicate anchor/entry: True'):
  build_assets(value,tmp_path,word_registry={'word':{'id':'word','kind':'proper_name'}},grammar_registry={k:{'id':k} for k in ('old','new','different')},write=False)
