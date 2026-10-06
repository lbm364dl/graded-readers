"""Setup-only selection receipts exercise binding, never linguistic approval."""
import copy,json,asyncio
from types import SimpleNamespace
from jsonschema import ValidationError
import pytest
from tests.test_annotation_run_lessons import fixture,overlay_fixture
from tests.test_annotation_reusable_run_knowledge import fresh
from tests.test_annotation_research import _write_job
from pipeline import annotation_run_knowledge_selection as selector
from pipeline.annotation_run_lessons import authenticated_run_lesson_catalog,RunLessonError


def inputs(fixture):
 dst,candidate,context,packet=fresh(fixture)
 candidate['segments'][0].update(type='word',lemma='fixture',lexical_kind='vocabulary',story_importance_en='')
 candidate.update(grammar_links=[{'segment_index':0,'entry_id':'old-pattern','context_en':'Fixture role','display_form':'','display_meaning_en':'','display_end_segment_index':-1}],inflected_segment_indices=[0],expression_links=[])
 catalog=authenticated_run_lesson_catalog(dst,[fixture[4]['lessons'][0]],language='ko')
 return {'version':1,'run_dir':str(dst),'candidate':candidate,'source_text':'깊다며','language':'ko','representation':'korean-flat','context':context,'catalog':catalog,'published_ids':[],'legal_targets':selector.legal_targets(candidate,'ko','korean-flat',{'new-pattern'}),'base_record':None}

def selection(value):
 return {'base_digest':selector.digest(value['candidate']),'applications':[{'source_key':selector._source_key(value['catalog']['sources'][0]),'lesson_id':'new-pattern','target_path':value['legal_targets'][0],'reason':'Setup-only selected formation.'}],'no_selection_reason':''}

def test_explicit_selection_changes_only_identity_and_matching_direct_link(fixture):
 value=inputs(fixture);value['candidate']['grammar_links']=[{'segment_index':0,'entry_id':'old-pattern','context_en':'unchanged','display_form':'','display_end_segment_index':-1}]
 current_finding={'candidate_paths':['/segments/0/meaning_en'],'explanation':'Retained separate finding'}
 value['context']['current_review']={'issues':[current_finding]}
 updated,packets,edits=selector.normalized_candidate(value,selection(value))
 assert [row['path'] for row in edits]==[value['legal_targets'][0],'/grammar_links/0/entry_id']
 restored=copy.deepcopy(updated);restored['segments'][0]['form_steps'][0]['grammar_entry_ids']=['old-pattern'];restored['grammar_links'][0]['entry_id']='old-pattern'
 assert restored==value['candidate']
 assert value['context']['current_review']['issues']==[current_finding]
 assert packets[0]['lessons'][0]['expected_context']==fixture[4]['lessons'][0]['expected_context']

@pytest.mark.parametrize('change',['base','source-key','identity','path','position','catalog'])
def test_invalid_selection_rejected_before_normalization(fixture,change):
 value=inputs(fixture);chosen=selection(value)
 if change=='base':chosen['base_digest']='bad'
 if change=='source-key':chosen['applications'][0]['source_key']='foreign'
 if change=='identity':chosen['applications'][0]['lesson_id']='foreign'
 if change=='path':chosen['applications'][0]['target_path']='/segments/0/meaning_en'
 if change=='position':value['context']['annotation_source_position']['source_start']=0
 if change=='catalog':value['catalog']['lessons'][0]['explanation_en']='tampered'
 with pytest.raises((ValueError,ValidationError)):selector.normalized_candidate(value,chosen)

def test_none_selection_never_changes_candidate_or_approves(fixture):
 value=inputs(fixture);chosen=selection(value);chosen['applications']=[];chosen['no_selection_reason']='No supported use.'
 result,packets,edits=selector.normalized_candidate(value,chosen)
 assert result==value['candidate'] and packets==edits==[]

@pytest.mark.parametrize('language,representation',[('zh','chinese-annotation'),('ja','japanese-annotation')])
def test_cj_uses_actual_overlay_identity_targets(language,representation):
 candidate={'segments':[],'grammar_overlays':[{'grammar_candidate_key':'draft'},{'grammar_candidate_key':'approved'}]}
 assert selector.legal_targets(candidate,language,representation,{'approved'})==['/grammar_overlays/0/grammar_candidate_key','/grammar_overlays/1/grammar_candidate_key']
 assert selector.legal_targets({'segments':[{'form_steps':[{'grammar_entry_ids':['wrong-schema']}]}],'grammar_overlays':[]},language,representation,set())==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('other_owner',[False,True])
async def test_shared_paid_request_boundary_and_offline_normalization_replay(fixture,monkeypatch,other_owner):
 value=inputs(fixture);catalog=value['catalog'];root=fixture[1];calls=[]
 text='깊다며'
 if other_owner:
  text='깊다며 아이'
  value['candidate']['segments'] += [{'text':' ','type':'punctuation','meaning_en':'','lemma':'','lexical_kind':'','lexical_id':'','story_importance_en':'','form_steps':[]},{'text':'아이','type':'word','meaning_en':'child','lemma':'아이','lexical_kind':'vocabulary','lexical_id':'child-old','story_importance_en':'','form_steps':[]}]
  value['context']['chapter_text']='new parent '+text+' tail'
  value['context']['annotation_source_position'].update(parent_text_digest=selector.digest(value['context']['chapter_text']),source_text_digest=selector.digest(text))
 monkeypatch.setattr(selector,'registered_catalog',lambda *_:catalog)
 class Runner:
  model='gpt-6-luna';benchmark_effort='low';legacy_tool_restrictions=False
  async def call(self,job,prompt,schema,effort,**kwargs):
   assert effort=='low' and kwargs['tool_profile']=='workspace'
   request=kwargs['workspace_context']['annotation_run_knowledge_selection'];chosen=selection(request)
   _write_job(root,job,prompt,schema.read_text(),kwargs['workspace_context'],chosen)
   calls.append(job);return chosen
 _write_job(root,'setup-base','setup-only producer','{}',{},value['candidate'])
 from pipeline.korean_agent_harness import digest as korean_digest
 base_record={'job':'setup-base','text':text,'digest':korean_digest(value['candidate'])}
 harness=SimpleNamespace(run_dir=root,runner=Runner(),grammar={},_annotation_source_positions={13:value['context']['annotation_source_position']})
 result=await selector.select_and_normalize_run_knowledge(harness,13,value['candidate'],text,language='ko',representation='korean-flat',context=value['context'],base_record=base_record)
 replayed,normalization_meta=selector.replay_normalization(root,result['evidence']['job'])
 from pipeline.agent_harness import CodexRunner
 CodexRunner._check_tool_profile(root/'agents'/result['evidence']['job'],'offline',normalization_meta)
 assert replayed==result['candidate'] and len(calls)==1
 selector.validate_normalization_context(root,result['context'],result['candidate'],text,'ko','korean-flat')
 if other_owner:
  independently_repaired=copy.deepcopy(result['candidate']);independently_repaired['segments'][2]['lexical_id']='child-corrected'
  selector.validate_normalization_context(root,result['context'],independently_repaired,text,'ko','korean-flat')
  bad=copy.deepcopy(result['candidate']);bad['segments'][0]['lexical_id']='selected-owner-homonym'
  with pytest.raises(RunLessonError):selector.validate_normalization_context(root,result['context'],bad,text,'ko','korean-flat')
 # Once run-canonical identity is present, no second selector job is needed.
 again=await selector.select_and_normalize_run_knowledge(harness,13,result['candidate'],text,language='ko',representation='korean-flat',context=result['context'],base_record=result['record'])
 assert len(calls)==1 and again['candidate']==result['candidate']


def test_same_reviewed_entry_at_two_current_positions_dedups_dictionary_origin(fixture):
 from pipeline.annotation_run_lessons import make_reusable_run_lesson_envelope
 from pipeline.annotation_dictionary_handoff import make_handoff
 value=inputs(fixture);parent='깊다며 깊다며';packets=[]
 for offset,index in [(0,0),(4,1)]:
  context={'chapter_text':parent,'source_start':offset,'annotation_source_position':{'chunk_index':index,'source_start':offset,'parent_text_digest':selector.digest(parent),'source_text_digest':selector.digest('깊다며')}}
  packets.append(make_reusable_run_lesson_envelope(fixture[1],source_descriptor=fixture[4]['lessons'][0],candidate=value['candidate'],source_text='깊다며',target_path=value['legal_targets'][0],language='ko',representation='korean-flat',context=context))
 handoff=make_handoff(fixture[1],packets,language='ko',chapter_text=parent,required_ids={'new-pattern'},published={})
 assert len(handoff['sources'])==1 and len(handoff['applications'])==2

def test_distinct_native_and_reused_entries_both_survive_handoff(fixture):
 from pipeline import annotation_run_lessons as lessons
 from pipeline.annotation_research import import_reviewed_lesson
 from pipeline.annotation_dictionary_handoff import make_handoff,replay_handoff
 value=inputs(fixture);parent=value['context']['chapter_text'];base=copy.deepcopy(fixture[2]);base['segments'][0]['text']='new';base['segments'][0]['form_steps'][0]['form']='new'
 expected={'language':'ko','chapter':{'text':parent,'segments':copy.deepcopy(base['segments'])},'target_occurrence':{'target_path':value['legal_targets'][0],'segment_index':0,'surface':'new','source_start':0,'source_text':'new'}}
 output={'words':[],'grammar':[{'id':'native-pattern','title_en':'Native fixture','pattern':'native fixture','explanation_en':'Setup only.'}]}
 _write_job(fixture[0],'native-writer','writer','{}',expected,output);_write_job(fixture[0],'native-critic','critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
 proof=import_reviewed_lesson(fixture[1],'native-writer','native-critic',lesson_id='native-pattern',expected_context=expected,issue_ids=['native'],source_run_dir=fixture[0])
 native=lessons.make_run_lesson_envelope(fixture[1],source_run_dir=fixture[0],import_receipt=proof['evidence'],expected_context=expected,language='ko',representation='korean-flat',context={'chapter_text':parent,'source_start':0})
 reused=lessons.make_reusable_run_lesson_envelope(fixture[1],source_descriptor=fixture[4]['lessons'][0],candidate=value['candidate'],source_text=value['source_text'],target_path=value['legal_targets'][0],language='ko',representation='korean-flat',context=value['context'])
 handoff=make_handoff(fixture[1],[native,reused],language='ko',chapter_text=parent,required_ids={'native-pattern','new-pattern'},published={})
 assert [entry['id'] for entry in replay_handoff(fixture[1],handoff,language='ko',chapter_text=parent,required_ids={'native-pattern','new-pattern'},published={})]==['native-pattern','new-pattern']


def test_actual_worker_submission_selection_gate_rejects_tampered_catalog(fixture,tmp_path):
 from pipeline.worker_workspace import build,check
 value=inputs(fixture);prompt,schema,workspace=selector.request(value)
 directory=tmp_path/'workspace';build(directory,prompt,schema,context=workspace)
 candidate=directory/'candidate.json';candidate.write_text(json.dumps(selection(value)))
 check(directory,candidate)
 bad=selection(value);bad['applications'][0]['source_key']='foreign';candidate.write_text(json.dumps(bad))
 with pytest.raises((ValueError,ValidationError)):check(directory,candidate)

@pytest.mark.asyncio
@pytest.mark.parametrize('language,module_name,class_name',[('zh','agent_harness','ChapterHarness'),('ja','japanese_agent_harness','JapaneseChapterHarness')])
async def test_actual_cj_lifecycle_reviews_selected_derived_candidate(tmp_path,monkeypatch,language,module_name,class_name):
 import importlib
 module=importlib.import_module('pipeline.'+module_name)
 candidate={'segments':[{'text':'x'}],'grammar_overlays':[{'grammar_candidate_key':'draft'}]};derived=copy.deepcopy(candidate);derived['grammar_overlays'][0]['grammar_candidate_key']='run-approved'
 calls=[]
 async def select(harness,index,base,text,**kwargs):
  calls.append(('select',kwargs['language']));return {'candidate':derived,'context':{},'evidence':{'setup_only':True},'record':None}
 async def generate(*args):return candidate
 async def review(*args):
  assert args[2]==derived;calls.append(('fresh_review',language));return {'verdict':'pass','issues':[]}
 monkeypatch.setattr(selector,'select_and_normalize_run_knowledge',select)
 harness=SimpleNamespace(run_dir=tmp_path,args=SimpleNamespace(annotation_mode='generative',max_annotation_repairs=0),annotation_candidate=generate,prepare_annotation_candidate=lambda _,value:value,prepare_planned_annotation=lambda _,value:value,annotation_reconstructs=lambda *_:True,annotation_contract_issues=lambda *_:[],review_annotation=review,annotation_chunk_cache_tag='fixture')
 result=await getattr(module,class_name).annotate_chunk(harness,0,'x')
 assert result['resolved'] and calls==[('select',language),('fresh_review',language)]


def test_nested_orphan_and_conflicting_normalization_markers_reject(fixture):
 from pipeline.annotation_run_lessons import validate_run_lesson_context_fields
 proof={'job':'setup-only'}
 with pytest.raises(RunLessonError):validate_run_lesson_context_fields({'chunk_review_context':{selector.FIELD:proof}})
 with pytest.raises(RunLessonError):validate_run_lesson_context_fields({selector.FIELD:proof,'reviewed_run_lessons':fixture[4],'chunk_review_context':{selector.FIELD:{'job':'different'},'reviewed_run_lessons':fixture[4]}})


def test_published_wrong_identity_registers_proposed_knowledge_without_silent_mapping(fixture):
 value=inputs(fixture);value['published_ids']=['old-pattern']
 chosen=selection(value)
 result,packets,edits=selector.normalized_candidate(value,chosen)
 assert result==value['candidate'] and edits==[] and packets
 assert packets[0]['lessons'][0]['import_receipt']['lesson_id']=='new-pattern'


def test_repeated_source_selection_rejects_before_registration(fixture):
 value=inputs(fixture);chosen=selection(value);chosen['applications'].append(copy.deepcopy(chosen['applications'][0]))
 with pytest.raises(RunLessonError):selector.normalized_candidate(value,chosen)
 assert not (fixture[1]/'annotation-run-applications').exists()
