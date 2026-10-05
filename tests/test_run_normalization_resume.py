"""Authenticated setup receipts test cache context restoration, not linguistic approval."""
import asyncio,copy,ast,inspect
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.test_annotation_run_lessons import fixture,overlay_fixture
from tests.test_annotation_run_knowledge_selection import inputs,selection
from tests.test_annotation_research import _write_job
from pipeline import annotation_run_knowledge_selection as selector
from pipeline.annotation_run_lessons import RunLessonError

async def normalized(fixture,monkeypatch):
 value=inputs(fixture);root=fixture[1];text=value['source_text'];calls=[]
 monkeypatch.setattr(selector,'registered_catalog',lambda *_:value['catalog'])
 class Runner:
  model='gpt-6-luna';benchmark_effort='low';legacy_tool_restrictions=False
  async def call(self,job,prompt,schema,effort,**kwargs):
   chosen=selection(kwargs['workspace_context']['annotation_run_knowledge_selection'])
   _write_job(root,job,prompt,schema.read_text(),kwargs['workspace_context'],chosen);calls.append(job);return chosen
 _write_job(root,'setup-base','setup producer','{}',{},value['candidate'])
 from pipeline.korean_agent_harness import digest
 record={'job':'setup-base','text':text,'digest':digest(value['candidate'])}
 h=SimpleNamespace(run_dir=root,runner=Runner(),grammar={},_annotation_source_positions={13:value['context']['annotation_source_position']})
 result=await selector.select_and_normalize_run_knowledge(h,13,value['candidate'],text,language='ko',representation='korean-flat',context=value['context'],base_record=record)
 from pipeline.annotation_run_lessons import enrich_run_lesson_context
 pending=result['context'].pop(selector.FIELD)
 result['context'],_=enrich_run_lesson_context(root,candidate=result['candidate'],source_text=text,language='ko',representation='korean-flat',context=result['context']);result['context'][selector.FIELD]=pending
 return value,result,h,calls

@pytest.mark.asyncio
async def test_authenticated_restore_recovers_position_packet_content_and_marker(fixture,monkeypatch):
 value,result,h,calls=await normalized(fixture,monkeypatch)
 context={k:value['context'][k] for k in ('chapter_text','source_start')}
 restored=selector.restore_current_normalization_context(h.run_dir,candidate=result['candidate'],source_text=value['source_text'],language='ko',representation='korean-flat',context=context,index=13,record=result['record'],old_context=result['context'])
 for key in ('annotation_source_position','reviewed_run_lessons','reviewed_run_grammar',selector.FIELD):assert restored[key]==result['context'][key]
 assert context=={k:value['context'][k] for k in ('chapter_text','source_start')} and len(calls)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['identity','form','source','position','proof','content'])
async def test_restore_rejects_stale_scope_or_tampered_provenance(fixture,monkeypatch,change):
 value,result,h,_=await normalized(fixture,monkeypatch);candidate=copy.deepcopy(result['candidate']);context={k:value['context'][k] for k in ('chapter_text','source_start')};old=copy.deepcopy(result['context'])
 if change=='identity':candidate['segments'][0]['form_steps'][0]['grammar_entry_ids']=['foreign']
 if change=='form':candidate['segments'][0]['form_steps'][0]['form']='different'
 if change=='source':context['chapter_text']='wrong parent'
 if change=='position':context['source_start']=0
 if change=='proof':old[selector.FIELD]['meta_digest']='bad'
 if change=='content':old['reviewed_run_grammar'][0]['explanation_en']='tampered'
 with pytest.raises((RunLessonError,ValueError)):
  selector.restore_current_normalization_context(h.run_dir,candidate=candidate,source_text=value['source_text'],language='ko',representation='korean-flat',context=context,index=13,record=result['record'],old_context=old)

def test_absent_historical_context_byteidentical(tmp_path):
 context={'chapter_text':'source','source_start':0,'historical':'untouched'}
 result=selector.restore_current_normalization_context(tmp_path,candidate={'segments':[]},source_text='source',language='ko',representation='korean-flat',context=context,index=0)
 assert result==context

@pytest.mark.asyncio
async def test_actual_k_callback_restores_before_checkpoint_without_second_selector(fixture,monkeypatch):
 import pipeline.korean_agent_harness as korean
 value,result,h,calls=await normalized(fixture,monkeypatch);parent=value['context']['chapter_text'];text=value['source_text'];offset=value['context']['source_start'];texts=['']*13;texts[0]=parent[:offset];texts+=[text,parent[offset+len(text):]]
 h.level=4;h.words={};h.catalog={};h.policy='setup';h.review_policy='setup'
 captures=[];proof={'setup_only_cache_boundary':True};real_checkpoint=korean.reusable_checkpoint_approval
 def checkpoint(run_dir,row,**kwargs):
  ctx=kwargs['context']
  for key in ('annotation_source_position','reviewed_run_lessons','reviewed_run_grammar',selector.FIELD):assert ctx[key]==result['context'][key]
  captures.append(ctx);return proof
 monkeypatch.setattr(korean,'reusable_checkpoint_approval',checkpoint)
 async def chunk(number,source,*args):assert number==14 and source==text;return result['candidate'],result['record']
 source=Path(inspect.getsourcefile(korean.KoreanHarness)).read_text()
 node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.AsyncFunctionDef) and n.name=='reviewed_chunk')
 ns=dict(vars(korean));ns.update(self=h,chunk=chunk,texts=texts,prose={'text':parent},bound_plan={},focus={},reviewed_source_context={},checkpoint_approvals={13:{'review_context':result['context'],'review':proof}})
 tree=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(tree);exec(compile(tree,'actual_callback','exec'),ns)
 current,record,actualproof=await ns['reviewed_chunk'](13,text)
 assert current==result['candidate'] and record==result['record'] and actualproof==proof and len(captures)==1 and len(calls)==1
 # Now create a schema-valid authenticated setup normal receipt for the exact
 # captured actual callback context, then exercise the REAL checkpoint verifier.
 from pipeline.korean_chunk_reviews import review_chunk
 class SetupReviewRunner:
  async def call(self,job,prompt,schema,effort,**options):
   output={'approved':True,'issues':[],'prose_revision_reason_en':''}
   _write_job(h.run_dir,job,prompt,Path(schema).read_text(),options['workspace_context'],output)
   return output
 reviewed,evidence=await review_chunk(SetupReviewRunner(),h.run_dir,annotation=current,text=text,context=captures[0],policy=h.policy+'\n'+h.review_policy)
 honest_proof={'kind':'ordinary',**evidence}
 monkeypatch.setattr(korean,'reusable_checkpoint_approval',real_checkpoint)
 ns['reusable_checkpoint_approval']=real_checkpoint
 ns['checkpoint_approvals']={13:{'review_context':captures[0],'review':honest_proof}}
 async def forbidden(*a,**k):raise AssertionError('Authenticated cache resume attempted worker')
 h.runner.call=forbidden
 resumed=await ns['reviewed_chunk'](13,text)
 assert resumed==(current,record,honest_proof) and len(calls)==1
 for change in ('owner','source','marker'):
  bad=copy.deepcopy(current);old_context=copy.deepcopy(captures[0]);saved_prose=ns['prose']
  if change=='owner':bad['segments'][0]['form_steps'][0]['grammar_entry_ids']=['foreign']
  if change=='source':ns['prose']={'text':'wrong parent'}
  if change=='marker':old_context[selector.FIELD]['meta_digest']='tampered'
  async def bad_chunk(number,source,*args):return bad,record
  ns['chunk']=bad_chunk;ns['checkpoint_approvals']={13:{'review_context':old_context,'review':honest_proof}}
  with pytest.raises((RunLessonError,ValueError)):await ns['reviewed_chunk'](13,text)
  ns['prose']=saved_prose



@pytest.mark.asyncio
async def test_actual_cj_overlay_resume_authenticates_selected_scope(overlay_fixture,monkeypatch):
 from pipeline.annotation_run_lessons import authenticated_run_lesson_catalog,enrich_run_lesson_context
 from pipeline.annotation_run_lesson_callers import _source_key
 src,root,candidate,text,language,representation,ctx,envelope=overlay_fixture
 catalog=authenticated_run_lesson_catalog(root,envelope['lessons'],language=language)
 monkeypatch.setattr(selector,'registered_catalog',lambda *_:catalog)
 position={'chunk_index':0,'source_start':4,'source_text_digest':selector.digest(text),'parent_text_digest':selector.digest(ctx['chapter_text'])}
 class Runner:
  model='gpt-6-luna';benchmark_effort='low';legacy_tool_restrictions=False
  async def call(self,job,prompt,schema,effort,**kwargs):
   v=kwargs['workspace_context']['annotation_run_knowledge_selection'];chosen={'base_digest':selector.digest(candidate),'applications':[{'source_key':_source_key(catalog['sources'][0]),'lesson_id':'new-pattern','target_path':'/grammar_overlays/0/grammar_candidate_key','reason':'Setup only.'}],'no_selection_reason':''}
   _write_job(root,job,prompt,schema.read_text(),kwargs['workspace_context'],chosen);return chosen
 h=SimpleNamespace(run_dir=root,runner=Runner(),_annotation_source_positions={0:position})
 result=await selector.select_and_normalize_run_knowledge(h,0,candidate,text,language=language,representation=representation,context=ctx)
 context={**ctx,'annotation_source_position':position}
 pending=result['context'].pop(selector.FIELD)
 old,_=enrich_run_lesson_context(root,candidate=result['candidate'],source_text=text,language=language,representation=representation,context=result['context']);old[selector.FIELD]=pending
 current=selector.restore_current_normalization_context(root,candidate=result['candidate'],source_text=text,language=language,representation=representation,context=context,index=0,old_context=old,position=position)
 assert current[selector.FIELD]==result['evidence'] and current['reviewed_run_grammar'][0]['id']=='new-pattern'
 bad=copy.deepcopy(result['candidate']);bad['grammar_overlays'][0]['end']-=1
 with pytest.raises(RunLessonError):selector.restore_current_normalization_context(root,candidate=bad,source_text=text,language=language,representation=representation,context=current,index=0,position=position)
