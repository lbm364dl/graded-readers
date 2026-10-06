"""Setup-only writer/critic fixtures exercise provenance, not linguistic approval."""
import copy,json
from pathlib import Path
import pytest
from tests.test_annotation_research import _write_job
from pipeline.annotation_research import import_reviewed_lesson
from pipeline import annotation_run_lessons as module
@pytest.fixture
def fixture(tmp_path,monkeypatch):
 source=tmp_path/'source';source.mkdir();destination=tmp_path/'destination';destination.mkdir()
 monkeypatch.setattr(module,'_repo_relative_run_dir',lambda _: 'fixture-source')
 monkeypatch.setattr(module,'_resolve_repo_run_dir',lambda value: source)
 candidate={'segments':[{'text':'아이','lexical_id':'child','meaning_en':'child','form_steps':[{'form':'아이','reading':'','label':'reporting','meaning_en':'saying child','grammar_entry_ids':['old-pattern']}]}]}
 expected={'language':'ko','chapter':{'text':'pre 아이','segments':copy.deepcopy(candidate['segments'])},'target_occurrence':{'target_path':'/segments/0/form_steps/0/grammar_entry_ids/0','segment_index':0,'surface':'아이','source_start':4,'source_text':'아이'}}
 output={'words':[],'grammar':[{'id':'new-pattern','title_en':'Test pattern','pattern':'test','explanation_en':'Fixture lesson.'}]}
 _write_job(source,'writer','fixture writer','{}',expected,output)
 _write_job(source,'critic','fixture critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
 proof=import_reviewed_lesson(destination,'writer','critic',lesson_id='new-pattern',expected_context=expected,issue_ids=['issue1'],source_run_dir=source)
 context={'chapter_text':'pre 아이','source_start':4}
 envelope=module.make_run_lesson_envelope(destination,source_run_dir=source,import_receipt=proof['evidence'],expected_context=expected,language='ko',representation='korean-flat',context=context)
 return source,destination,candidate,context,envelope

def bind(fixture,**changes):
 source,destination,candidate,context,envelope=fixture
 return module.validate_run_lessons(destination,changes.get('envelope',envelope),candidate=changes.get('candidate',candidate),source_text=changes.get('source_text','아이'),language=changes.get('language','ko'),representation=changes.get('representation','korean-flat'),context=changes.get('context',context))

def test_exact_new_identity_allowed_without_weakening_fact_carry(fixture):
 assert bind(fixture)['lessons'][0]['id']=='new-pattern'
 candidate=copy.deepcopy(fixture[2]);candidate['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new-pattern'];candidate['segments'][0]['meaning_en']='new semantic meaning'
 assert bind(fixture,candidate=candidate)['lessons']
 from pipeline.annotation_reference_carry import _tap_geometry
 assert _tap_geometry(candidate)!=_tap_geometry(fixture[2])
@pytest.mark.parametrize('change',['position','parent','form','lexical','id','surfaces','language','representation'])
def test_wrong_geometry_identity_and_position_rejected(fixture,change):
 candidate=copy.deepcopy(fixture[2]);context=copy.deepcopy(fixture[3]);options={}
 if change=='position':context['source_start']=0
 if change=='parent':context['chapter_text']='bad アイ'
 if change=='form':candidate['segments'][0]['form_steps'][0]['form']='different'
 if change=='lexical':candidate['segments'][0]['lexical_id']='homonym'
 if change=='id':candidate['segments'][0]['form_steps'][0]['grammar_entry_ids']=['unrelated']
 if change=='surfaces':candidate['segments'].insert(0,{'text':'','meaning_en':''})
 if change=='language':options['language']='ja'
 if change=='representation':options['representation']='japanese-annotation'
 with pytest.raises(module.RunLessonError):bind(fixture,candidate=candidate,context=context,**options)
@pytest.mark.parametrize('job',['writer','critic'])
def test_tampered_real_workspace_source_receipt_rejected(fixture,job):
 path=fixture[0]/'agents'/job/'result.json';data=json.loads(path.read_text());data['tampered']=True;path.write_text(json.dumps(data))
 with pytest.raises(module.RunLessonError,match='authenticate|provenance'):bind(fixture)

def test_absent_historical_envelope_adds_nothing(fixture):assert bind(fixture,envelope=None)=={'packet':{},'references':{},'lessons':[]}
@pytest.mark.parametrize('language,representation',[('zh','chinese-annotation'),('ja','japanese-annotation')])
def test_relabelled_source_language_does_not_authenticate(fixture,language,representation):
 envelope=copy.deepcopy(fixture[4]);envelope['language']=language;envelope['representation']=representation
 with pytest.raises(module.RunLessonError,match='source language'):bind(fixture,envelope=envelope,language=language,representation=representation)

def test_exact_current_issue_reference_binding_and_unrelated_contrast(fixture):
 kwargs=dict(candidate=fixture[2],source_text='아이',language='ko',representation='korean-flat',context=fixture[3])
 review={'approved':False,'issues':[{'explanation':'Formation lesson missing','candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids/0'],'supporting_paths':[]}],'prose_revision_reason_en':''}
 result=module.validate_run_lessons(fixture[1],fixture[4],current_review=review,**kwargs)
 assert len(result['references'])==1
 reference=next(iter(result['references'].values()));assert reference['content']['issue_ids']==reference['issue_ids']
 review['issues'][0]['candidate_paths']=['/segments/0/meaning_en']
 assert module.validate_run_lessons(fixture[1],fixture[4],current_review=review,**kwargs)['references']=={}

def test_korean_fresh_review_and_replay_actual_envelope(fixture,monkeypatch):
 import asyncio
 from tests.test_korean_chunk_reviews import Runner
 from pipeline import korean_chunk_reviews as reviews
 root=fixture[1];candidate=copy.deepcopy(fixture[2]);candidate['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new-pattern']
 context={**fixture[3],module.RUN_LESSON_FIELD:fixture[4]};runner=Runner(root)
 review,evidence=asyncio.run(reviews.review_chunk(runner,root,annotation=candidate,text='아이',context=context,policy='Review'))
 assert module.RUN_LESSON_GUIDANCE in runner.prompts[0]
 assert reviews.verify_review(root,evidence,annotation=candidate,text='아이',chapter_text='pre 아이',source_start=4)['approved']
 (fixture[0]/'agents/critic/result.json').write_text('{"approved": false, "issues": ["tampered"]}')
 with pytest.raises(module.RunLessonError):reviews.verify_review(root,evidence,annotation=candidate,text='아이',chapter_text='pre 아이',source_start=4)

@pytest.fixture(params=['zh','ja'])
def overlay_fixture(tmp_path,monkeypatch,request):
 from jsonschema import validate
 language=request.param;source=tmp_path/'overlay-source';source.mkdir();destination=tmp_path/'overlay-destination';destination.mkdir()
 monkeypatch.setattr(module,'_repo_relative_run_dir',lambda _: 'fixture-overlay-source');monkeypatch.setattr(module,'_resolve_repo_run_dir',lambda _: source)
 if language=='zh':
  text='说吧';candidate={'segments':[{'text':'说','type':'word','pinyin':'shuō','meaning_en':'say'},{'text':'吧','type':'particle','pinyin':'ba','meaning_en':'suggestion'}],'grammar_overlays':[{'start':0,'end':2,'text':text,'grammar_candidate_key':'old-pattern','pattern':'old pattern','meaning_en':'fixture meaning'}]};representation='chinese-annotation';schema='annotation.schema.json'
 else:
  text='言って';candidate={'segments':[{'surface':text,'type':'word','lemma':'言う','surface_kana':'いって','lemma_kana':'いう','part_of_speech':'verb','conjugation_form':'te','meaning_en':'saying','grammar_candidate_key':'','dictionary_key':'say','dictionary_definition_en':'say','form_steps':[],'story_role':'none','story_importance_en':''}],'grammar_overlays':[{'start':0,'end':3,'surface':text,'grammar_candidate_key':'old-pattern','pattern':'old pattern','meaning_en':'fixture meaning','head_lemma':'言う','head_lemma_kana':'いう','form_label':'fixture form','explanation_en':'Fixture shape, no approval.','components':[{'start':0,'end':1,'surface':'言','lemma':'言う','lemma_kana':'いう','function_en':'base','lookup_kind':'lexical','dictionary_key':'say','dictionary_definition_en':'say'},{'start':1,'end':3,'surface':'って','lemma':'て','lemma_kana':'て','function_en':'ending','lookup_kind':'grammar','dictionary_key':'ending','dictionary_definition_en':'ending'}]}]};representation='japanese-annotation';schema='japanese-annotation.schema.json'
 validate(candidate,json.loads((Path.cwd()/'pipeline/schemas'/schema).read_text()))
 expected={'reviewed_run_lesson_scope_version':2,'language':language,'chapter':{'text':'pre '+text,**copy.deepcopy(candidate)},'target_occurrence':{'target_path':'/grammar_overlays/0/grammar_candidate_key','overlay_index':0,'start':0,'end':len(text),'surface':text,'source_start':4,'source_text':text}}
 output={'words':[],'grammar':[{'id':'new-pattern','title_en':'Fixture pattern','pattern':'reviewed pattern','explanation_en':'Fixture only.'}]}
 _write_job(source,'writer','writer','{}',expected,output);_write_job(source,'critic','critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
 proof=import_reviewed_lesson(destination,'writer','critic',lesson_id='new-pattern',expected_context=expected,issue_ids=['issue1'],source_run_dir=source)
 context={'chapter_text':'pre '+text,'source_start':4};envelope=module.make_run_lesson_envelope(destination,source_run_dir=source,import_receipt=proof['evidence'],expected_context=expected,language=language,representation=representation,context=context)
 return source,destination,candidate,text,language,representation,context,envelope

def test_real_cj_schema_overlay_identity_and_pattern(overlay_fixture):
 src,dst,candidate,text,language,representation,context,envelope=overlay_fixture
 changed=copy.deepcopy(candidate);changed['grammar_overlays'][0].update(grammar_candidate_key='new-pattern',pattern='reviewed pattern')
 result=module.validate_run_lessons(dst,envelope,candidate=changed,source_text=text,language=language,representation=representation,context=context)
 assert result['lessons'][0]['id']=='new-pattern'
@pytest.mark.parametrize('change',['range','surface','other-id','other-pattern','tap-geometry'])
def test_overlay_contrasts_fail_exact_scope(overlay_fixture,change):
 src,dst,candidate,text,language,representation,context,envelope=overlay_fixture;changed=copy.deepcopy(candidate);row=changed['grammar_overlays'][0]
 if change=='range':row['start']=1
 if change=='surface':row['surface' if language=='ja' else 'text']='unrelated'
 if change=='other-id':row['grammar_candidate_key']='unrelated'
 if change=='other-pattern':row['pattern']='unsupported pattern'
 if change=='tap-geometry':changed['segments'].reverse() if language=='zh' else changed['segments'].append(copy.deepcopy(changed['segments'][0]))
 with pytest.raises(module.RunLessonError):module.validate_run_lessons(dst,envelope,candidate=changed,source_text=text,language=language,representation=representation,context=context)

def test_grouped_related_link_and_unrelated_tap_scoped_independently(fixture):
 candidate=copy.deepcopy(fixture[2]);candidate['grammar_links']=[{'segment_index':0,'entry_id':'old-pattern','display_form':'','display_end_segment_index':-1,'context_en':'fixture'}]
 paths=['/segments/0/form_steps/0/grammar_entry_ids/0','/grammar_links/0/entry_id','/segments/0/meaning_en']
 review={'approved':False,'issues':[{'explanation':'Grouped targets','candidate_paths':paths,'supporting_paths':[]}],'prose_revision_reason_en':''}
 result=module.validate_run_lessons(fixture[1],fixture[4],candidate=candidate,source_text='아이',language='ko',representation='korean-flat',context=fixture[3],current_review=review)
 ref=next(iter(result['references'].values()));owned=next(iter(ref['content']['_annotation_run_lesson_scope']['issue_paths'].values()))
 assert set(owned)==set(paths[:2]) and paths[2] not in owned
 candidate['grammar_links'][0]['segment_index']=1
 result=module.validate_run_lessons(fixture[1],fixture[4],candidate=candidate,source_text='아이',language='ko',representation='korean-flat',context=fixture[3],current_review=review)
 owned=next(iter(next(iter(result['references'].values()))['content']['_annotation_run_lesson_scope']['issue_paths'].values()));assert owned==[paths[0]]


def test_substantive_snapshot_is_authenticated_and_tamper_rejected(fixture):
 source,destination,candidate,context,envelope=fixture
 rich,_=module.enrich_run_lesson_context(destination,candidate=candidate,source_text='아이',language='ko',representation='korean-flat',context={**context,module.RUN_LESSON_FIELD:envelope})
 assert rich['reviewed_run_grammar']==bind(fixture)['lessons']
 rich['reviewed_run_grammar'][0]['explanation_en']='Unsupported replacement.'
 with pytest.raises(module.RunLessonError):bind(fixture,context=rich)


@pytest.mark.parametrize('path',['/segments/99/form_steps/0/grammar_entry_ids/0','/segments/0/not_a_field','/grammar_overlays/0/grammar_candidate_key'])
def test_invalid_identity_pointer_fails_closed(fixture,path):
 envelope=copy.deepcopy(fixture[4]);envelope['lessons'][0]['expected_context']['target_occurrence']['target_path']=path
 with pytest.raises(module.RunLessonError):bind(fixture,envelope=envelope)

class CapturedBoundary(Exception):
 pass

class CapturingRunner:
 def __init__(self):self.calls=[]
 async def call(self,*args,**kwargs):
  self.calls.append((args,kwargs))
  raise CapturedBoundary()

def rich_context(fixture):
 return module.enrich_run_lesson_context(fixture[1],candidate=fixture[2],source_text='아이',language='ko',representation='korean-flat',context={**fixture[3],module.RUN_LESSON_FIELD:fixture[4]})[0]

def test_repair_plan_worker_receives_authenticated_lesson_and_gate(fixture):
 import asyncio
 from tests.test_annotation_repairs import Harness,_korean_candidate_gate
 from pipeline.annotation_repairs import repair_annotation
 runner=CapturingRunner();context={**rich_context(fixture),'chunk_text':'아이','candidate_gate':_korean_candidate_gate()}
 with pytest.raises(CapturedBoundary):
  asyncio.run(repair_annotation(Harness(fixture[1],runner),'fixture-repair',fixture[2],[{'explanation':'Use independently reviewed formation','candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids/0'],'supporting_paths':[]}],representation='korean-flat',language='ko',context=context,validate_candidate=lambda _:None))
 assert len(runner.calls)==1
 args,kwargs=runner.calls[0];workspace=kwargs['workspace_context'];gate=workspace['annotation_run_lesson_validation']
 assert args[0].endswith('_plan') and args[3]=='low'
 assert workspace['reviewed_run_grammar']==gate['lessons']==bind(fixture)['lessons']
 assert gate['candidate']==fixture[2] and gate['envelope']==fixture[4]
 assert gate['context']==context and gate['run_dir']==str(fixture[1].resolve())
 assert 'representation_structure_contract' in workspace

def test_adjudication_worker_receives_exact_scoped_lesson_and_gate(fixture):
 import asyncio
 from pipeline.annotation_adjudication import _adjudicate_once,digest
 runner=CapturingRunner();context=rich_context(fixture)
 review={'approved':False,'issues':[{'explanation':'Formation target','candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids/0'],'supporting_paths':[]}],'prose_revision_reason_en':''}
 refs=module.validate_run_lessons(fixture[1],fixture[4],candidate=fixture[2],source_text='아이',language='ko',representation='korean-flat',context=context,current_review=review)['references']
 with pytest.raises(CapturedBoundary):
  asyncio.run(_adjudicate_once(runner,fixture[1],language='ko',representation='korean-flat',candidate=fixture[2],source_text='아이',current_review=review,prior_history=[],context=context,known_reference_input=refs,deterministic_gate_evidence={'passed':True,'issues':[],'candidate_digest':digest(fixture[2]),'source_text_digest':digest('아이')},normal_review_receipt={'job':'fixture-review','review_digest':digest(review),'input_digest':'fixture-input'}))
 assert len(runner.calls)==1
 args,kwargs=runner.calls[0];workspace=kwargs['workspace_context'];gate=workspace['annotation_run_lesson_validation']
 assert args[3]=='low' and kwargs['tool_profile']=='research'
 assert gate['lessons']==context['reviewed_run_grammar'] and gate['envelope']==fixture[4]
 assert workspace['annotation_adjudication']['known_reference_input']==refs

def test_absent_lesson_keeps_existing_review_payload_identical(fixture):
 from pipeline.korean_chunk_reviews import review_request
 baseline=review_request(fixture[2],'아이',fixture[3],'Review')
 unchanged,refs=module.enrich_run_lesson_context(fixture[1],candidate=fixture[2],source_text='아이',language='ko',representation='korean-flat',context=fixture[3])
 assert unchanged is fixture[3] and refs=={}
 assert review_request(fixture[2],'아이',unchanged,'Review')==baseline

def test_korean_review_gate_contains_exact_lesson_snapshot(fixture):
 import asyncio
 from pipeline.korean_chunk_reviews import review_chunk
 runner=CapturingRunner();context=rich_context(fixture)
 with pytest.raises(CapturedBoundary):
  asyncio.run(review_chunk(runner,fixture[1],annotation=fixture[2],text='아이',context=context,policy='Review'))
 args,kwargs=runner.calls[0];workspace=kwargs['workspace_context'];gate=workspace['annotation_run_lesson_validation']
 assert args[3]=='low' and kwargs['tool_profile']=='offline'
 assert gate['lessons']==workspace['chunk_review_input']['context']['reviewed_run_grammar']
 assert gate['envelope']==fixture[4] and gate['candidate']==fixture[2]
 assert gate['source_text']=='아이' and gate['run_dir']==str(fixture[1].resolve())


def test_tampered_lesson_snapshot_stops_review_before_worker(fixture):
 import asyncio
 from pipeline.korean_chunk_reviews import review_chunk
 runner=CapturingRunner();context=rich_context(fixture)
 context['reviewed_run_grammar'][0]['pattern']='unsupported replacement'
 with pytest.raises(module.RunLessonError):
  asyncio.run(review_chunk(runner,fixture[1],annotation=fixture[2],text='아이',context=context,policy='Review'))
 assert runner.calls==[]

@pytest.mark.parametrize('new_applicable',[False,True])
def test_korean_complete_stage_checks_each_current_lesson_before_reuse(fixture,monkeypatch,new_applicable):
 from pipeline import korean_agent_harness as korean
 from pipeline import annotation_reference_carry_callers as carry_callers
 from pipeline import annotation_run_lesson_callers as lesson_callers
 seen=[];old=fixture[3]
 monkeypatch.setattr(korean,'read',lambda path:{'context':old})
 monkeypatch.setattr(korean,'read_annotation_chunk',lambda path,record:fixture[2])
 monkeypatch.setattr(carry_callers,'current_carry_eligibility',lambda *args,**kwargs:True)
 def eligible(run_dir,**kwargs):
  seen.append(kwargs)
  return not new_applicable or kwargs['context']['source_start']!=4
 monkeypatch.setattr(lesson_callers,'current_run_lesson_context_eligibility',eligible)
 meta={'chunks':[{'job':'sibling','text':'pre '},{'job':'target','text':'아이'}],
       'chunk_reviews':[{'job':'sibling-review'},{'job':'target-review'}]}
 assert korean.current_assembly_carry_eligibility(fixture[1],meta) is (not new_applicable)
 assert [row['context']['source_start'] for row in seen]==[0,4]
 assert all(row['context']['chapter_text']=='pre 아이' for row in seen)
 assert meta['chunk_reviews']==[{'job':'sibling-review'},{'job':'target-review'}]


def test_overlay_source_registration_and_uncertain_matcher_use_explicit_v2(overlay_fixture):
 from pipeline.annotation_research import _validate_registered_lesson_context,_validate_reviewed_context_scope,AnnotationResearchError
 src,dst,candidate,text,language,representation,context,envelope=overlay_fixture
 expected=envelope['lessons'][0]['expected_context'];path=expected['target_occurrence']['target_path']
 _validate_registered_lesson_context(expected)
 inputs={'candidate':candidate,'source_text':text,'context':context,'issue_ids':['finding-1'],
         'initial_adjudication':{'classifications':[{'issue_id':'finding-1','candidate_paths':[path]}]}}
 assert _validate_reviewed_context_scope(expected,inputs)==path
 moved=copy.deepcopy(inputs);moved['context']['source_start']=0
 with pytest.raises(AnnotationResearchError):_validate_reviewed_context_scope(expected,moved)
 invalid=copy.deepcopy(expected);invalid['target_occurrence']['end']+=1
 with pytest.raises(AnnotationResearchError):_validate_registered_lesson_context(invalid)


def test_historical_absent_review_workspace_has_no_new_gate_or_knowledge(fixture):
 import asyncio
 from pipeline.korean_chunk_reviews import review_chunk,review_request
 runner=CapturingRunner()
 with pytest.raises(CapturedBoundary):
  asyncio.run(review_chunk(runner,fixture[1],annotation=fixture[2],text='아이',context=fixture[3],policy='Review'))
 workspace=runner.calls[0][1]['workspace_context']
 expected_input=review_request(fixture[2],'아이',fixture[3],'Review',guidance_version=2)[0]
 assert workspace=={'chunk_review_input':expected_input,'annotation_issue_targets_validation':{
  'candidate':fixture[2],'source_text':'아이','representation':'korean-flat','require_typed':True}}
 assert 'reviewed_run_grammar' not in expected_input['context']


def test_current_manifest_independent_of_explicit_old_packet(fixture,monkeypatch):
 from pipeline import annotation_run_lesson_callers as callers
 source,destination,candidate,context,envelope=fixture
 rich=rich_context(fixture)
 expected=envelope['lessons'][0]['expected_context']
 output={'words':[],'grammar':[{'id':'new-pattern','title_en':'Test pattern','pattern':'test','explanation_en':'Fixture lesson.'}]}
 _write_job(source,'writer-new','new setup writer','{}',expected,output)
 _write_job(source,'critic-new','new setup critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
 proof=import_reviewed_lesson(destination,'writer-new','critic-new',lesson_id='new-pattern',expected_context=expected,issue_ids=['issue1'],source_run_dir=source)
 new=module.make_run_lesson_envelope(destination,source_run_dir=source,import_receipt=proof['evidence'],expected_context=expected,language='ko',representation='korean-flat',context=context)
 monkeypatch.setattr(callers,'load_run_lessons',lambda *args,**kwargs:new)
 kwargs=dict(candidate=candidate,source_text='아이',language='ko',representation='korean-flat')
 assert not callers.current_run_lesson_context_eligibility(destination,context=rich,old_context=rich,**kwargs)
 # Same claim from a different authenticated pair is not silently collapsed.
 # A duplicate identity's conflicting provenance fails closed during fresh union.
 with pytest.raises(module.RunLessonError,match='Duplicate'):
  module.enrich_run_lesson_context(destination,context=rich,**kwargs)
 monkeypatch.setattr(callers,'load_run_lessons',lambda *args,**kwargs:envelope)
 assert callers.current_run_lesson_context_eligibility(destination,context=rich,old_context=rich,**kwargs)
 resolved,_=module.enrich_run_lesson_context(destination,context=rich,**kwargs)
 assert resolved==rich

@pytest.mark.parametrize('case',['terminal','routing','foreign'])
def test_korean_checkpoint_additive_lesson_uses_authenticated_consumption(fixture,monkeypatch,case):
 import asyncio
 from pipeline import korean_agent_harness as korean
 from pipeline import annotation_reference_carry_callers as carry_callers
 from pipeline import annotation_run_lesson_callers as callers
 from pipeline.korean_chunk_reviews import review_chunk
 from tests.test_korean_chunk_reviews import Runner
 source,destination,candidate,base_context,envelope=fixture
 old=base_context if case=='terminal' else rich_context(fixture)
 _,normal=asyncio.run(review_chunk(Runner(destination),destination,annotation=candidate,text='아이',context=old,policy='Review'))
 expected=envelope['lessons'][0]['expected_context']
 writer='writer';critic='critic'
 if case=='foreign':
  writer='foreign-writer';critic='foreign-critic'
  output={'words':[],'grammar':[{'id':'new-pattern','title_en':'Test pattern','pattern':'test','explanation_en':'Fixture lesson.'}]}
  _write_job(source,writer,'foreign setup writer','{}',expected,output)
  _write_job(source,critic,'foreign setup critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
 proof=import_reviewed_lesson(destination,writer,critic,lesson_id='new-pattern',expected_context=expected,issue_ids=['new-route'],source_run_dir=source)
 current_envelope=module.make_run_lesson_envelope(destination,source_run_dir=source,import_receipt=proof['evidence'],expected_context=expected,language='ko',representation='korean-flat',context=base_context)
 current={**base_context,module.RUN_LESSON_FIELD:current_envelope,'reviewed_run_grammar':bind(fixture)['lessons']}
 # This unit isolates checkpoint orchestration after its existing full-chain
 # verifier. These setup markers are not forged publication receipts.
 checkpoint={'kind':'adjudicated','normal_review':normal,'replay_inputs':{'context':rich_context(fixture)}}
 verified=[]
 from pipeline import korean_chunk_reviews as reviews
 monkeypatch.setattr(reviews,'verify_chunk_review',lambda *args,**kwargs:verified.append(True))
 monkeypatch.setattr(carry_callers,'current_carry_eligibility',lambda *args,**kwargs:True)
 monkeypatch.setattr(callers,'load_run_lessons',lambda *args,**kwargs:current_envelope)
 if case=='foreign':
  with pytest.raises(ValueError,match='newly applicable'):
   korean.reusable_checkpoint_approval(destination,{'review':checkpoint,'review_context':old},annotation=candidate,text='아이',context=current,policy='Review')
 else:
  assert korean.reusable_checkpoint_approval(destination,{'review':checkpoint,'review_context':old},annotation=candidate,text='아이',context=current,policy='Review')==checkpoint
 assert verified==[True]

@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('other_container_envelope',[False,True])
def test_orphan_substantive_snapshot_rejected_by_direct_review_and_adju(fixture,nested,other_container_envelope):
 from pipeline.korean_chunk_reviews import review_request
 from pipeline.annotation_adjudication import _validate_run_lesson_context
 context=copy.deepcopy(fixture[3]);container=context
 if nested:
  context['chunk_review_context']=copy.deepcopy(fixture[3]);container=context['chunk_review_context']
 container['reviewed_run_grammar']=bind(fixture)['lessons']
 if other_container_envelope:
  other=context if nested else context.setdefault('chunk_review_context',copy.deepcopy(fixture[3]))
  other[module.RUN_LESSON_FIELD]=fixture[4]
 with pytest.raises(module.RunLessonError,match='own authenticated'):
  review_request(fixture[2],'아이',context,'Review')
 with pytest.raises(module.RunLessonError,match='own authenticated'):
  _validate_run_lesson_context(fixture[1],context,fixture[2],'아이','ko','korean-flat',{}, {})
 with pytest.raises(module.RunLessonError,match='own authenticated'):
  module.validate_run_lessons(fixture[1],None,candidate=fixture[2],source_text='아이',language='ko',representation='korean-flat',context=context)

@pytest.mark.parametrize('tamper',['envelope','root-snapshot','nested-snapshot'])
def test_dual_container_context_authenticates_both_or_rejects(fixture,tamper):
 context=rich_context(fixture);context['chunk_review_context']=copy.deepcopy(context)
 assert bind(fixture,context=context)['lessons']
 if tamper=='envelope':context['chunk_review_context'][module.RUN_LESSON_FIELD]['version']=99
 if tamper=='root-snapshot':context['reviewed_run_grammar'][0]['pattern']='unsupported root'
 if tamper=='nested-snapshot':context['chunk_review_context']['reviewed_run_grammar'][0]['pattern']='unsupported nested'
 with pytest.raises(module.RunLessonError):bind(fixture,context=context)
