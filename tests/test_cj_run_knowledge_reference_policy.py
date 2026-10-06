"""Actual C/J callbacks with setup-only authenticated writer/critic artifacts.

No linguistic verdict is created: stop at the adjudication request boundary.
Semantic producer replay is controlled here; shared genuine lineage tests cover it.
"""
import copy,json
from argparse import Namespace
from pathlib import Path
import pytest
from tests.test_annotation_run_lesson_callers import authenticated_overlay
from tests.test_annotation_research import _write_job

@pytest.mark.parametrize('field',['meaning_en','pattern','explanation_en'])
@pytest.mark.parametrize('packet_version',[1,2])
def test_authentic_overlay_analysis_reference_scope_is_explicit(authenticated_overlay,field,packet_version):
 from pipeline.annotation_run_lessons import validate_run_lessons,theory_reference_context,REFERENCE_POLICY_FIELD,make_reusable_run_lesson_envelope,RunLessonError
 from pipeline.annotation_run_lesson_callers import bind_lifecycle_run_lessons
 h,candidate,language,representation,envelope,_=authenticated_overlay
 if field not in candidate['grammar_overlays'][0]:pytest.skip('Chinese overlay has no explanation field')
 current=copy.deepcopy(candidate);current['grammar_overlays'][0]['grammar_candidate_key']='new-pattern'
 ctx,_=bind_lifecycle_run_lessons(h,1,'猫猫',current,language=language,representation=representation)
 if packet_version==2:
  envelope=make_reusable_run_lesson_envelope(h.run_dir,source_descriptor=envelope['lessons'][0],
   candidate=current,source_text='猫猫',target_path='/grammar_overlays/0/grammar_candidate_key',
   language=language,representation=representation,context=ctx)
  ctx['reviewed_run_lessons']=envelope
 review={'verdict':'revise','issues':[{'problem':'grammar','candidate_paths':['/grammar_overlays/0/'+field],
  'supporting_paths':[],'explanation':'SETUP ONLY analysis scope question.'}]}
 def refs(context):return validate_run_lessons(h.run_dir,envelope,candidate=current,source_text='猫猫',
  language=language,representation=representation,context=context,current_review=review)['references']
 assert refs(ctx)=={} and REFERENCE_POLICY_FIELD not in ctx
 scoped=theory_reference_context(ctx);result=refs(scoped);assert len(result)==1
 ref=next(iter(result.values()));scope=ref['content']['_annotation_run_lesson_scope']
 assert scope['version']==2 and list(scope['issue_paths'].values())==[['/grammar_overlays/0/'+field]]
 assert ref['content']['explanation_en']=='Standalone independently reviewed fixture lesson.'
 # Context-only neighboring lexical fields and another overlay are not owned.
 for path in ('/segments/0/meaning_en','/segments/1/grammar_candidate_key'):
  if path.endswith('grammar_candidate_key') and language=='zh':continue
  review['issues'][0]['candidate_paths']=[path];assert refs(scoped)=={}
 # A different overlay at the same repeated surface is not this selected owner.
 current['grammar_overlays'].append(copy.deepcopy(current['grammar_overlays'][0]))
 current['grammar_overlays'][1]['grammar_candidate_key']='foreign-pattern'
 review['issues'][0]['candidate_paths']=['/grammar_overlays/1/meaning_en']
 review['issues'][0]['supporting_paths']=['/grammar_overlays/0/grammar_candidate_key']
 assert refs(scoped)=={}
 # Policy2 does not waive immutable source-writer/critic authentication.
 source=authenticated_overlay[-1];critic=source/'agents/critic/result.json'
 critic.write_text(json.dumps({'approved':False,'issues':['SETUP ONLY changed critic']}))
 with pytest.raises(RunLessonError,match='authenticate|provenance|review'):
  refs(scoped)
 assert REFERENCE_POLICY_FIELD not in ctx

@pytest.mark.parametrize('route',['main','tail'])
@pytest.mark.asyncio
async def test_actual_cj_adjudication_binds_only_new_theory_context(authenticated_overlay,monkeypatch,route):
 from pipeline import annotation_repairs,annotation_semantic_derivation,annotation_adjudication,annotation_run_knowledge_selection
 from pipeline.annotation_run_lessons import REFERENCE_POLICY_FIELD
 from pipeline.annotation_run_lesson_callers import bind_lifecycle_run_lessons
 from pipeline.annotation_publication import bind_review_job
 from pipeline.agent_harness import ChapterHarness
 from pipeline.japanese_agent_harness import JapaneseChapterHarness
 seed,candidate,language,representation,envelope,_=authenticated_overlay
 if language=='zh' and route=='tail':pytest.skip('Chinese has one lifecycle adjudication site')
 candidate=copy.deepcopy(candidate);candidate['grammar_overlays'][0]['grammar_candidate_key']='new-pattern'
 issue={'problem':'grammar','start':0,'end':2,'segment_text':'猫猫',
  'candidate_paths':['/grammar_overlays/0/meaning_en'],'supporting_paths':[],
  'explanation':'SETUP ONLY current contextual meaning question.'}
 if language=='ja':issue.pop('start');issue.pop('end')
 repaired={};ordinary_contexts=[];repair_contexts=[];adjudications=[]
 class Boundary(Exception):pass
 Parent=ChapterHarness if language=='zh' else JapaneseChapterHarness
 class Harness(Parent):
  async def annotation_candidate(self,index,chunk,**kwargs):
   return copy.deepcopy(next(reversed(repaired.values()))) if repaired else copy.deepcopy(candidate)
  async def review_annotation(self,index,chunk,annotation,stage):
   context,_=bind_lifecycle_run_lessons(self,index,chunk,annotation,language=language,representation=representation)
   assert REFERENCE_POLICY_FIELD not in context
   ordinary_contexts.append(copy.deepcopy(context));result={'verdict':'revise','issues':[issue]}
   jobs=([f'annotations/chunk_{index:04d}/{stage}_review'] if language=='zh'
    else [f'fixture/{stage}/general',f'fixture/{stage}/boundary'])
   for job in jobs:
    _write_job(self.run_dir,job,'SETUP ONLY ordinary review','{}',context,result)
    bind_review_job(self.run_dir,job,candidate=annotation,source_text=chunk)
   if language=='ja':
    receipts=getattr(self,'_annotation_review_receipts',{});receipts[(index,stage)]=[
     {'job':job,'input_digest':'fixture-input','result_digest':'fixture-result'} for job in jobs]
    self._annotation_review_receipts=receipts
   return result
  def annotation_reconstructs(self,chunk,value):return ''.join(x.get('text',x.get('surface')) for x in value['segments'])==chunk
  annotation_surfaces_reconstruct=annotation_reconstructs
  def annotation_contract_issues(self,chunk,value):return []
  def prepare_annotation_candidate(self,chunk,value):return value
  prepare_planned_annotation=prepare_annotation_candidate
 async def repair(harness,job,base,issues,**kwargs):
  context=kwargs['context'];repair_contexts.append(copy.deepcopy(context))
  derived=copy.deepcopy(base);derived['grammar_overlays'][0]['meaning_en']+=' changed'
  directory=harness.run_dir/'agents'/job;directory.mkdir(parents=True,exist_ok=True)
  (directory/'meta.json').write_text(json.dumps({'kind':'annotation_patch_assembly','status':'applied','representation':representation}))
  repaired[job]=derived
  return {'status':'applied','candidate':derived,'evidence':{'assembly_job':job}}
 def replay(run_dir,job,*,validate_candidate):
  value=repaired[job];validate_candidate(value);return {'status':'applied','candidate':value}
 async def select(harness,index,value,chunk,**kwargs):return {'candidate':value}
 async def adjudicate(*args,**kwargs):
  assert kwargs['candidate']==next(reversed(repaired.values()))
  assert kwargs['source_text']=='猫猫' and kwargs['current_review']['issues']==[issue]
  assert kwargs['prior_history'] and kwargs['deterministic_gate_evidence']['passed']
  context=kwargs['context'];assert context[REFERENCE_POLICY_FIELD]['version']==2
  assert context['reviewed_run_lessons']==envelope
  references=[v for v in kwargs['known_reference_input'].values() if isinstance(v.get('content'),dict)
   and v['content'].get('id')=='new-pattern'];assert len(references)==1
  scope=references[0]['content']['_annotation_run_lesson_scope'];assert scope['version']==2
  assert list(scope['issue_paths'].values())==[['/grammar_overlays/0/meaning_en']]
  assert references[0]['content']['explanation_en']=='Standalone independently reviewed fixture lesson.'
  adjudications.append(copy.deepcopy(kwargs))
  if route=='tail' and len(adjudications)==1:
   return {'status':'actionable','approved':False,'repair_diagnoses':[{'issue_id':'fixture-current',
    'paths':['/grammar_overlays/0/meaning_en'],'diagnosis':'SETUP ONLY downstream lifecycle route.'}]}
  raise Boundary()
 monkeypatch.setattr(annotation_repairs,'repair_annotation',repair)
 monkeypatch.setattr(annotation_semantic_derivation,'replay_annotation_repair',replay)
 monkeypatch.setattr(annotation_run_knowledge_selection,'select_and_normalize_run_knowledge',select)
 monkeypatch.setattr(annotation_adjudication,'adjudicate_annotation_review',adjudicate)
 h=object.__new__(Harness);h.run_dir=seed.run_dir;h.runner=object()
 h._annotation_run_lessons=seed._annotation_run_lessons;h._annotation_source_positions=seed._annotation_source_positions
 h.args=Namespace(level='n5',max_annotation_repairs=1,max_annotation_fresh_repairs=1,
  max_annotation_adjudications=0,annotation_chunk=350,annotation_chunk_maximum=400,
  annotation_repair_effort='low',annotation_final_effort='low',annotation_effort='low',
  annotation_review_effort='low',refresh=False,annotation_chunk_indices=None,no_grammar_overlays=False)
 h.story_vocabulary_plan={'terms':[]}
 (h.run_dir/'manifest.json').write_text(json.dumps({'annotation_chunk_target':350,'annotation_chunk_maximum':400}))
 (h.run_dir/'focus-vocabulary-plan.json').write_text(json.dumps({'names':[],'story_terms':[]}))
 with pytest.raises(Boundary):await h.annotate_chunk(1,'猫猫')
 assert len(adjudications)==(2 if route=='tail' else 1)
 assert ordinary_contexts and all(REFERENCE_POLICY_FIELD not in v for v in ordinary_contexts)
 # Ordinary repair inputs remain unmarked; a later repair can retain an actual
 # prior adjudication's nested evidence, without changing the review producer.
 assert repair_contexts and REFERENCE_POLICY_FIELD not in repair_contexts[0]
