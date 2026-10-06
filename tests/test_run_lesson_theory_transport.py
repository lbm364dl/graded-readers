"""Authenticated setup lessons test scoped theory transport, not occurrence approval."""
from pathlib import Path
import importlib.util,sys,copy
import pytest
from tests.test_annotation_run_lessons import fixture,overlay_fixture
from tests.test_annotation_reusable_run_knowledge import fresh
from pipeline import annotation_run_lessons as lessons,annotation_research as research
from pipeline.annotation_adjudication import digest,normalize_review

def review(path):return {'approved':False,'issues':[{'explanation':'Check complete formation/meaning','candidate_paths':[path],'supporting_paths':[]}],'prose_revision_reason_en':''}
def args(fixture,path,version=2):
 if version==2:root,candidate,context,envelope=fresh(fixture)
 else:_,root,candidate,context,envelope=fixture
 context={**context,lessons.RUN_LESSON_FIELD:envelope}
 return root,dict(candidate=candidate,source_text='깊다며' if version==2 else '아이',language='ko',representation='korean-flat',context=context,current_review=review(path))

@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('path',['/segments/0/form_steps/0/meaning_en','/segments/0/form_steps/0/form'])
def test_marked_native_and_reused_exact_stage_theory_without_old_scope_change(fixture,version,path):
 root,options=args(fixture,path,version);old=lessons.validate_run_lessons(root,options['context'][lessons.RUN_LESSON_FIELD],**options)
 assert old['references']=={}
 new=lessons.validate_run_lessons(root,options['context'][lessons.RUN_LESSON_FIELD],**{**options,'context':lessons.theory_reference_context(options['context'])})
 ref=next(iter(new['references'].values()));assert ref['content']['id']=='new-pattern' and ref['content']['_annotation_run_lesson_scope']['version']==2
 assert path in next(iter(ref['content']['_annotation_run_lesson_scope']['issue_paths'].values()))
 assert options['context'].get(lessons.REFERENCE_POLICY_FIELD) is None

@pytest.mark.parametrize('path',['/segments/0/meaning_en','/segments/0/lexical_id'])
def test_unrelated_owner_field_never_gains_theory(fixture,path):
 root,options=args(fixture,path);options['context']=lessons.theory_reference_context(options['context'])
 assert lessons.validate_run_lessons(root,options['context'][lessons.RUN_LESSON_FIELD],**options)['references']=={}

@pytest.mark.parametrize('field',['meaning_en','pattern','explanation_en'])
def test_actual_cj_overlay_selected_analysis_fields(overlay_fixture,field):
 _,root,candidate,source,language,representation,context,envelope=overlay_fixture;path='/grammar_overlays/0/'+field
 if field not in candidate['grammar_overlays'][0]:return
 kwargs=dict(candidate=candidate,source_text=source,language=language,representation=representation,current_review={'verdict':'revise','issues':[{'explanation':'Check exact overlay','candidate_paths':[path],'supporting_paths':[]} ]} if language=='zh' else {'approved':False,'issues':[{'explanation':'Check exact overlay','candidate_paths':[path],'supporting_paths':[]} ]})
 ctx=lessons.theory_reference_context({**context,lessons.RUN_LESSON_FIELD:envelope})
 assert lessons.validate_run_lessons(root,envelope,context=ctx,**kwargs)['references']
 kwargs['current_review']['issues'][0]['candidate_paths']=['/segments/0/meaning_en']
 assert lessons.validate_run_lessons(root,envelope,context=ctx,**kwargs)['references']=={}

@pytest.mark.parametrize('change',['source','policy','critic'])
def test_transport_authentication_fails_closed(fixture,change):
 root,options=args(fixture,'/segments/0/form_steps/0/meaning_en');options['context']=lessons.theory_reference_context(options['context'])
 if change=='source':options['source_text']='foreign'
 if change=='policy':options['context'][lessons.REFERENCE_POLICY_FIELD]['policy_digest']='tampered'
 if change=='critic':(fixture[0]/'agents/critic/result.json').write_text('{"approved":false,"issues":["tampered"]}')
 with pytest.raises(ValueError):lessons.validate_run_lessons(root,options['context'][lessons.RUN_LESSON_FIELD],**options)


def test_policy7_transports_citable_theory_preserving_original_inputs(fixture):
 root,options=args(fixture,'/segments/0/form_steps/0/meaning_en');normalized=normalize_review('ko',options['current_review']);identity=normalized['issues'][0]['issue_id']
 initial={'status':'uncertain','classifications':[{'issue_id':identity,'disposition':'uncertain','candidate_paths':['/segments/0/form_steps/0/meaning_en'],'reason':'Theory needed','diagnosis':''}]}
 inputs={**options,'prior_history':[],'initial_adjudication':initial,'known_reference_input':{},'normal_review_receipt':{'source':'setup'},'deterministic_gate_evidence':{'passed':True,'issues':[],'candidate_digest':digest(options['candidate']),'source_text_digest':digest(options['source_text'])}}
 saved=copy.deepcopy(inputs)
 old=research._build_inputs(**inputs,policy_version=6);new=research._build_inputs(**inputs,policy_version=7,run_dir=root)
 assert old['known_reference_input']=={} and new['known_reference_input'] and inputs==saved
 assert new['context']==old['context'] and new['initial_adjudication']==old['initial_adjudication']
 assert new['run_lesson_theory_transport']['original_known_reference_digest']==digest({})
 assert new['research_policy_digest']!=old['research_policy_digest']

def test_bare_policy_marker_is_not_evidence(tmp_path):
 ctx=lessons.theory_reference_context({})
 with pytest.raises(ValueError,match='envelope'):lessons.validate_run_lessons(tmp_path,None,candidate={},source_text='',language='ko',representation='korean-flat',context=ctx)

def test_research_upgrade_preserves_original_identity_scope_reference(fixture):
 root,options=args(fixture,'/segments/0/form_steps/0/grammar_entry_ids/0');old=lessons.validate_run_lessons(root,options['context'][lessons.RUN_LESSON_FIELD],**options)['references'];identity=normalize_review('ko',options['current_review'])['issues'][0]['issue_id']
 initial={'status':'uncertain','classifications':[{'issue_id':identity,'disposition':'uncertain','candidate_paths':['/segments/0/form_steps/0/grammar_entry_ids/0'],'reason':'Theory needed','diagnosis':''}]}
 inputs=research._build_inputs(**options,prior_history=[],initial_adjudication=initial,known_reference_input=old,normal_review_receipt={'setup':'receipt'},deterministic_gate_evidence={'passed':True,'issues':[],'candidate_digest':digest(options['candidate']),'source_text_digest':digest(options['source_text'])},policy_version=7,run_dir=root)
 original_id=next(iter(old));assert inputs['known_reference_input'][original_id]=={key:old[original_id][key] for key in ('kind','content')}
 assert original_id+':theory-scope-v2' in inputs['known_reference_input']
 assert inputs['run_lesson_theory_transport']['original_known_reference_digest']==digest(old)

@pytest.mark.asyncio
async def test_actual_k_new_adju_transports_theory_without_changing_normal_review(fixture,monkeypatch):
 import ast
 import pipeline.korean_agent_harness as korean
 import pipeline.annotation_adjudication as adjudication
 from tests.test_annotation_semantic_derivation import chain,gate
 value,h,result,job=await chain(fixture,monkeypatch)
 h.level=4;h.words={};h.catalog={};h.policy='Setup';h.review_policy='Review'
 parent=value['context']['chapter_text'];offset=value['context']['source_start'];text=value['source_text'];texts=['']*13;texts[0]=parent[:offset];texts.extend([text,parent[offset+len(text):]])
 raw=review('/segments/0/form_steps/0/meaning_en')
 async def chunk(*args):return result['candidate'],result['record']
 class Captured(Exception):pass
 async def adju(*a,**kwargs):
  ctx=kwargs['context'];assert ctx[lessons.REFERENCE_POLICY_FIELD]==lessons.reference_policy_marker()
  assert lessons.REFERENCE_POLICY_FIELD not in ctx['chunk_review_context']
  bound=next(row for row in kwargs['known_reference_input'].values() if isinstance(row.get('content'),dict) and row['content'].get('id')=='new-pattern')
  assert bound['content']['_annotation_run_lesson_scope']['version']==2 and kwargs['current_review']==raw
  raise Captured()
 monkeypatch.setattr(adjudication,'adjudicate_annotation_review',adju)
 from tests.test_annotation_research import _write_job
 class Runner:
  async def call(self,job,prompt,schema,effort,**kwargs):
   normal=kwargs['workspace_context']['chunk_review_input']['context'];assert lessons.REFERENCE_POLICY_FIELD not in normal
   _write_job(h.run_dir,job,prompt,Path(schema).read_text(),kwargs['workspace_context'],raw);return raw
 h.runner=Runner()
 node=next(n for n in ast.walk(ast.parse(Path(korean.__file__).read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name=='reviewed_chunk')
 ns=dict(vars(korean));ns.update(self=h,chunk=chunk,texts=texts,prose={'text':parent},bound_plan={},focus={},reviewed_source_context={},checkpoint_approvals={},validate_chunk=lambda c,t:gate(c))
 tree=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(tree);exec(compile(tree,'actual_scoped_K','exec'),ns)
 with pytest.raises(Captured):await ns['reviewed_chunk'](13,text)

def test_policy7_theory_citation_cannot_move_to_other_issue_and_retains_owned_path():
 scope={'version':2,'policy_digest':digest(lessons.REFERENCE_POLICY_TEXT),'issue_paths':{'owned':['/segments/0/form_steps/0/meaning_en']}}
 refs={'theory':{'kind':'approved_lesson','content':{'id':'entry','explanation_en':'Setup standalone formation theory.','issue_ids':['owned'],'_annotation_run_lesson_scope':scope}}}
 output={'findings':[{'issue_id':'foreign','status':'supported','fact':'Setup fact, no linguistic claim','citations':[{'reference_id':'theory','path':'/explanation_en'}],'gap':''}]}
 # Historical policy deliberately stays byte-compatible; new transport closes this escape.
 assert research._validate_research_output(output,['foreign'],refs,policy_version=6)[0]
 with pytest.raises(ValueError,match='scope'):research._validate_research_output(output,['foreign'],refs,policy_version=7)
 output['findings'][0]['issue_id']='owned';facts,_=research._validate_research_output(output,['owned'],refs,policy_version=7)
 inputs={'research_policy_version':7,'initial_adjudication_digest':'setup','candidate_digest':'setup','review_digest':'setup','context_digest':'setup','source_text_digest':'setup'}
 derived=next(iter(research._reference_rows(inputs,facts).values()))['content']
 assert derived['_annotation_run_lesson_scope']==scope
 assert '/segments/1/meaning_en' not in derived['_annotation_run_lesson_scope']['issue_paths']['owned']

@pytest.mark.parametrize('field',['grammar_entry_ids','grammar_ids','grammar_entry_id','grammar_id'])
def test_native_legacy_admitted_identity_spelling_gets_exact_stage_theory(fixture,field):
 from tests.test_annotation_research import _write_job
 source,root,base,context,_=fixture;candidate=copy.deepcopy(base);step=candidate['segments'][0]['form_steps'][0];step.pop('grammar_entry_ids');step[field]=['old-pattern'] if field.endswith('ids') else 'old-pattern'
 path='/segments/0/form_steps/0/'+field+('/0' if field.endswith('ids') else '')
 expected={'language':'ko','chapter':{'text':context['chapter_text'],'segments':candidate['segments']},'target_occurrence':{'target_path':path,'segment_index':0,'surface':'아이','source_start':4,'source_text':'아이'}}
 output={'words':[],'grammar':[{'id':'new-pattern','title_en':'Setup pattern','pattern':'setup','explanation_en':'Standalone setup theory.'}]}
 _write_job(source,'alias-writer','setup writer','{}',expected,output);_write_job(source,'alias-critic','setup critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
 proof=research.import_reviewed_lesson(root,'alias-writer','alias-critic',lesson_id='new-pattern',expected_context=expected,issue_ids=['setup'],source_run_dir=source)
 envelope=lessons.make_run_lesson_envelope(root,source_run_dir=source,import_receipt=proof['evidence'],expected_context=expected,language='ko',representation='korean-flat',context=context)
 ctx={**context,lessons.RUN_LESSON_FIELD:envelope}
 kwargs=dict(candidate=candidate,source_text='아이',language='ko',representation='korean-flat',current_review=review('/segments/0/form_steps/0/meaning_en'))
 assert lessons.validate_run_lessons(root,envelope,context=ctx,**kwargs)['references']=={}
 assert lessons.validate_run_lessons(root,envelope,context=lessons.theory_reference_context(ctx),**kwargs)['references']

def test_grouped_issue_keeps_other_existing_stage_and_lexical_field_outside_theory(fixture):
 root,candidate,context,old=fresh(fixture);candidate['segments'][0]['form_steps'].append({'form':'깊다며','reading':'','label':'Setup separate stage','meaning_en':'Setup other complete meaning','grammar_entry_ids':['other-pattern']})
 envelope=lessons.make_reusable_run_lesson_envelope(root,source_descriptor=fixture[4]['lessons'][0],candidate=candidate,source_text='깊다며',target_path='/segments/0/form_steps/0/grammar_entry_ids/0',language='ko',representation='korean-flat',context=context)
 raw=review('/segments/0/form_steps/0/meaning_en');raw['issues'][0]['candidate_paths']+=['/segments/0/form_steps/1/meaning_en','/segments/0/lexical_id']
 raw['issues'][0]['supporting_paths']=['/segments/0/form_steps/1/form']
 ctx=lessons.theory_reference_context({**context,lessons.RUN_LESSON_FIELD:envelope})
 bound=lessons.validate_run_lessons(root,envelope,candidate=candidate,source_text='깊다며',language='ko',representation='korean-flat',context=ctx,current_review=raw)
 scope=next(iter(bound['references'].values()))['content']['_annotation_run_lesson_scope']
 assert list(scope['issue_paths'].values())==[['/segments/0/form_steps/0/meaning_en']]

def test_historical_absent_envelope_with_none_context_remains_empty(tmp_path):
 assert lessons.validate_run_lessons(tmp_path,None,candidate={},source_text='',language='ko',representation='korean-flat',context=None)=={'packet':{},'references':{},'lessons':[]}
