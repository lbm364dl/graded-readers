import asyncio,importlib.util,sys
from pathlib import Path
import pytest
p=Path(__file__).with_name('chunk_scheduler.py')
if p.exists():
 s=importlib.util.spec_from_file_location('isolated_scheduler',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
else:
 from pipeline import chunk_scheduler as m
def test_default_unchanged():
 async def go():
  seen=[]
  async def worker(i,text):seen.append(i);return (i,text)
  assert await m.map_chunks(['a','a','c'],worker,2)==[(0,'a'),(1,'a'),(2,'c')]
  assert sorted(seen)==[0,1,2]
 asyncio.run(go())
def test_selection_preserves_full_indices_and_successes():
 async def go():
  seen=[]
  async def worker(i,text):seen.append(i);return ('new',i,text)
  async def retained(i,text):
   if i==0:return ('auth-proof',i,text)
   raise m.ChunkDeferred('held')
  with pytest.raises(m.ChunkAdmissionIncomplete) as e:await m.map_chunks(['same','same','last'],worker,2,active_indices=[1],retained_worker=retained)
  assert seen==[1]
  assert [x.index for x in e.value.chunk_batch_successes]==[0,1]
  assert [x.index for x in e.value.chunk_batch_failures]==[2]
 asyncio.run(go())
@pytest.mark.parametrize('indices',[[True],[1,1],[-1],[3]])
def test_invalid(indices):
 async def worker(i,x):raise AssertionError('Worker must never start')
 with pytest.raises(ValueError):asyncio.run(m.map_chunks(['a','b','c'],worker,1,active_indices=indices))
def test_all_held_no_workers():
 async def worker(i,x):raise AssertionError('No admitted worker')
 with pytest.raises(m.ChunkAdmissionIncomplete):asyncio.run(m.map_chunks(['a'],worker,1,active_indices=[]))
def test_selected_failure_keeps_valid_sibling():
 async def go():
  async def worker(i,x):raise ValueError('actual selected failure')
  async def retained(i,x):return 'auth'
  with pytest.raises(m.ChunkAdmissionIncomplete) as e:await m.map_chunks(['a','b'],worker,1,active_indices=[1],retained_worker=retained)
  assert e.value.chunk_batch_successes[0].index==0
 asyncio.run(go())
import ast
from types import SimpleNamespace
def method(file,name,namespace):
 source=Path(__file__).with_name(file)
 if not source.exists():source=Path(__file__).resolve().parents[1]/'pipeline'/file
 tree=ast.parse(source.read_text())
 node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
 exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(Path(__file__).with_name(file)),'exec'),namespace)
 return namespace[name]
def test_actual_k_retained_callback_blocks_before_chunk():
 ns={'checkpoint_approvals':{},'self':SimpleNamespace()}
 fn=method('korean_agent_harness.py','reviewed_chunk',ns)
 # The actual callback imports the public scheduler; isolate only that dependency.
 from unittest.mock import patch
 with patch.dict(sys.modules,{'pipeline.chunk_scheduler':m}):
  with pytest.raises(m.ChunkDeferred):asyncio.run(fn(12,'immutable',retained_only=True))
def test_actual_j_retained_callback_never_generates():
 import pipeline.japanese_agent_harness as ja
 ns=dict(vars(ja));fn=method('japanese_agent_harness.py','annotate_chunk',ns)
 self=SimpleNamespace(annotation_chunk_cache_tag='test',args=SimpleNamespace(refresh=False))
 from unittest.mock import patch
 with patch.dict(sys.modules,{'pipeline.chunk_scheduler':m}):
  with pytest.raises(m.ChunkDeferred):asyncio.run(fn(self,0,'同じ',retained_only=True))
def test_actual_c_annotation_only_admits_only_selected(tmp_path):
 import pipeline.agent_harness as cn
 ns=dict(vars(cn));ns.update(map_chunks=m.map_chunks,admission_indices=m.admission_indices,ChunkAdmissionIncomplete=m.ChunkAdmissionIncomplete,bounded_chunk_jobs=m.bounded_chunk_jobs,persist_admission_outcomes=m.persist_admission_outcomes)
 fn=method('agent_harness.py','run_annotation_only',ns);seen=[];statuses=[]
 async def annotate(i,x):seen.append((i,x));raise ValueError('selected defect, not approval')
 self=SimpleNamespace(run_dir=tmp_path,runner=SimpleNamespace(model='gpt-6-luna'),args=SimpleNamespace(annotation_mode='generative',concurrency=1,annotation_active_indices=[2]),chapter='甲乙',chinese_annotation_chunks=lambda text:['甲','乙'],write_annotation_manifest=statuses.append,annotate_chunk=annotate)
 with pytest.raises(m.ChunkAdmissionIncomplete):asyncio.run(fn(self))
 assert seen==[(1,'乙')] and statuses==['running','incomplete']

def test_empty_selection_validation():
 async def worker(i,x):raise AssertionError('unreachable')
 with pytest.raises(ValueError):asyncio.run(m.map_chunks([],worker,1,active_indices=[0]))
 assert asyncio.run(m.map_chunks([],worker,1,active_indices=[]))==[]
def test_selected_and_deferred_errors_remain_distinct():
 async def worker(i,x):raise ValueError('genuine prose/boundary diagnosis')
 with pytest.raises(m.ChunkAdmissionIncomplete) as e:asyncio.run(m.map_chunks(['a','b'],worker,1,active_indices=[0]))
 assert e.value.attempted_failures[0].index==0 and isinstance(e.value.attempted_failures[0].error,ValueError)
 assert e.value.deferred_chunks[0].index==1
@pytest.mark.parametrize('number',[1,2])
def test_actual_historical_proof_replay_and_changed_context_reject(number):
 import json,socket,subprocess
 from unittest.mock import patch
 import pipeline.korean_agent_harness as k
 from pipeline.korean_chunk_reviews import verify_chunk_review
 run=k.ROOT/'runs/korean-topik4/chapter-001';path=run/'agents/annotation-partial-checkpoint-2ffff41d657da0ca/meta.json'
 if not path.exists():pytest.skip('Optional retained production evidence')
 meta=json.loads(path.read_text());j=meta['chunk_source_positions'].index(number-1);record=meta['chunks'][j];proof=meta['chunk_reviews'][j];text=meta['chunk_texts'][number-1];parent=meta['chapter_text'];start=sum(map(len,meta['chunk_texts'][:number-1]));value=k.read_annotation_chunk(run/'agents'/record['job']/'result.json',record);normal=proof.get('normal_review',proof);old=json.loads((run/'agents'/normal['job']/'review-input.json').read_text())['context'];h=k.KoreanHarness(run,1,level=4,workers=1);row={'record':record,'annotation':value,'review':proof,'review_context':old}
 def forbid(*a,**kw):raise AssertionError('Proof replay attempted external access')
 with patch.object(k.CodexRunner,'call',side_effect=forbid),patch.object(socket,'create_connection',side_effect=forbid),patch.object(subprocess,'Popen',side_effect=forbid):
  assert k.reusable_checkpoint_approval(run,row,annotation=value,text=text,context=old,policy=h.policy+'\n'+h.review_policy)==proof
  changed=dict(old);changed['target_level']=5
  with pytest.raises(ValueError):k.reusable_checkpoint_approval(run,row,annotation=value,text=text,context=changed,policy=h.policy+'\n'+h.review_policy)
  with pytest.raises(ValueError):verify_chunk_review(run,proof,annotation=value,text=text,chapter_text=parent,source_start=start+1)
def test_explicit_hold_source_and_evidence_binding(tmp_path,monkeypatch):
 import hashlib,json
 file=tmp_path/'pipeline/chunk_scheduler.py';file.parent.mkdir();file.write_text('setup')
 evidence=tmp_path/'evidence.json';evidence.write_text('authentic exact investigation note')
 monkeypatch.setattr(m,'__file__',str(file));text='same';pos={'chunk_index':0,'source_start':0,'parent_text_digest':__import__('pipeline.annotation_adjudication',fromlist=['digest']).digest(text),'source_text_digest':__import__('pipeline.annotation_adjudication',fromlist=['digest']).digest(text)};row={'chunk_index':1,**{k:v for k,v in pos.items() if k!='chunk_index'},'reason':'unresolved','evidence':[{'path':'evidence.json','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]};h=SimpleNamespace(annotation_holds={'version':1,'holds':[row]},_annotation_source_positions={0:pos})
 assert m.investigation_hold(h,0,text)==row
 wrong=SimpleNamespace(annotation_holds=h.annotation_holds,_annotation_source_positions={0:{**pos,'source_start':1}})
 with pytest.raises(ValueError):m.investigation_hold(wrong,0,text)
 evidence.write_text('tampered')
 with pytest.raises(ValueError):m.investigation_hold(h,0,text)
def test_persistent_job_cap_and_authenticated_cache_first(tmp_path):
 from pipeline.agent_harness import CachedCallUnavailable
 class FakeRunner:
  model='gpt-6-luna'
  def __init__(self):self.cache={};self.paid=[]
  async def call(self,job,prompt,schema,effort=None,cache_only=False,**kw):
   key=(job,prompt)
   if cache_only:
    if key not in self.cache:raise CachedCallUnavailable('miss')
    return self.cache[key]
   self.paid.append(job);self.cache[key]={'setup-only':True};return self.cache[key]
 runner=FakeRunner();schema=tmp_path/'schema.json';schema.write_text('{}');h=SimpleNamespace(run_dir=tmp_path,runner=runner,annotation_active_indices=[1],annotation_admission_job_limit=1,_annotation_source_positions={0:{'parent_text_digest':'parent','source_text_digest':'chunk','source_start':0,'chunk_index':0}})
 async def go():
  with m.bounded_chunk_jobs(h):
   await h.runner.call('first','exact',schema,'low',workspace_context={'base':'immutable'})
  with m.bounded_chunk_jobs(h):
   await h.runner.call('first','exact',schema,'low',workspace_context={'base':'immutable'})
   with pytest.raises(m.ChunkJobLimitReached):await h.runner.call('second','exact',schema,'low')
   with pytest.raises(ValueError):await h.runner.call('first','foreign',schema,'low')
 asyncio.run(go());assert runner.paid==['first']
def test_actual_ja_progressive_map_admits_before_generation(tmp_path):
 import pipeline.japanese_agent_harness as ja
 from unittest.mock import patch
 ns=dict(vars(ja));ns.update(map_chunks=m.map_chunks,bounded_chunk_jobs=m.bounded_chunk_jobs,persist_admission_outcomes=m.persist_admission_outcomes)
 progressive=method('japanese_agent_harness.py','annotate_chunks_progressively',ns)
 annotate=method('japanese_agent_harness.py','annotate_chunk',ns)
 seen=[]
 async def candidate(i,x):seen.append((i,x));raise ValueError('selected genuine failure')
 h=SimpleNamespace(run_dir=tmp_path,runner=SimpleNamespace(model='gpt-6-luna'),args=SimpleNamespace(annotation_active_indices=[2],concurrency=1,refresh=False),annotation_chunk_cache_tag='setup',prepare_planned_annotation=lambda a,b:b,annotation_candidate=candidate)
 h.annotate_chunk=lambda i,x,**kw:annotate(h,i,x,**kw)
 with patch.dict(sys.modules,{'pipeline.chunk_scheduler':m}):
  with pytest.raises(m.ChunkAdmissionIncomplete):asyncio.run(progressive(h,['同','同']))
 assert seen==[(1,'同')]
@pytest.mark.parametrize('route',[0,1])
def test_both_actual_chinese_map_statements_bound(route,tmp_path):
 source=Path(__file__).with_name('agent_harness.py')
 if not source.exists():source=Path(__file__).resolve().parents[1]/'pipeline/agent_harness.py'
 tree=ast.parse(source.read_text())
 blocks=[n for n in ast.walk(tree) if isinstance(n,ast.With) and any(isinstance(x.context_expr,ast.Call) and isinstance(x.context_expr.func,ast.Name) and x.context_expr.func.id=='bounded_chunk_jobs' for x in n.items)]
 assert len(blocks)==2
 body=ast.AsyncFunctionDef(name='actual_route',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[blocks[route]],decorator_list=[])
 from unittest.mock import patch
 seen=[]
 async def worker(i,x):seen.append(i);raise ValueError('selected finding')
 h=SimpleNamespace(run_dir=tmp_path,runner=SimpleNamespace(model='gpt-6-luna'),args=SimpleNamespace(annotation_active_indices=[2],concurrency=1),annotate_chunk=worker,_annotation_source_positions={0:{'source':'first'},1:{'source':'second'}})
 ns={'self':h,'chunks':['同','同'],'map_chunks':m.map_chunks,'admission_indices':m.admission_indices,'bounded_chunk_jobs':m.bounded_chunk_jobs}
 exec(compile(ast.fix_missing_locations(ast.Module(body=[body],type_ignores=[])),'actual_cn_statement','exec'),ns)
 with pytest.raises(m.ChunkAdmissionIncomplete):asyncio.run(ns['actual_route']())
 assert seen==[1]
def test_actual_checkpoint_union_keeps_exact_indexed_proofs(tmp_path):
 import json
 import pipeline.korean_agent_harness as k
 run=k.ROOT/'runs/korean-topik4/chapter-001';source=run/'agents/annotation-partial-checkpoint-2ffff41d657da0ca/meta.json'
 if not source.exists():pytest.skip('Optional actual evidence')
 old=json.loads(source.read_text());tuples={}
 for index,record,proof in zip(old['chunk_source_positions'],old['chunks'],old['chunk_reviews']):
  if index==10:continue
  value=k.read_annotation_chunk(run/'agents'/record['job']/'result.json',record)
  tuples[index]=(value,record,proof)
 async def go():
  async def worker(i,x):return tuples[i] # selected already-reviewed occurrence; no fake new approval
  async def retained(i,x):
   if i in tuples:return tuples[i]
   raise m.ChunkDeferred('held or missing')
  with pytest.raises(m.ChunkAdmissionIncomplete) as e:await m.map_chunks(old['chunk_texts'],worker,1,active_indices=[26],retained_worker=retained)
  from unittest.mock import patch
  with patch.dict(sys.modules,{'pipeline.chunk_scheduler':m}):
   job=k.save_partial_annotation_checkpoint(tmp_path,source_job='exact-evidence-union',chapter_text=old['chapter_text'],chunk_texts=old['chunk_texts'],error=e.value)
  current=json.loads((tmp_path/'agents'/job/'meta.json').read_text())
  assert current['chunk_source_positions']==sorted(tuples)
  assert len(current['chunks'])==16 and current['complete'] is False
  for index,proof in zip(current['chunk_source_positions'],current['chunk_reviews']):assert proof==tuples[index][2]
 asyncio.run(go())
def test_admission_effective_shared_low_policy(tmp_path):
 from pipeline.agent_harness import CachedCallUnavailable
 class SharedPolicyRunner:
  model='gpt-6-luna';benchmark_effort=None
  async def call(self,job,prompt,schema,effort=None,cache_only=False,**kw):
   if cache_only:raise CachedCallUnavailable('miss')
   self.received=effort;return {'effective_effort':self.benchmark_effort or 'low'}
 runner=SharedPolicyRunner();schema=tmp_path/'schema.json';schema.write_text('{}')
 h=SimpleNamespace(run_dir=tmp_path,runner=runner,annotation_active_indices=[1],annotation_admission_job_limit=6,_annotation_source_positions={0:{'source':'exact'}})
 async def go():
  with m.bounded_chunk_jobs(h):assert (await h.runner.call('requested-high','p',schema,'high'))['effective_effort']=='low'
  runner.benchmark_effort='high'
  with m.bounded_chunk_jobs(h):
   with pytest.raises(ValueError):await h.runner.call('benchmark','p',schema,'low')
 asyncio.run(go());assert runner.received=='high'
def test_admission_rejects_symlink_ancestor(tmp_path):
 directory=tmp_path/'real';directory.mkdir();link=tmp_path/'alias';link.symlink_to(directory,target_is_directory=True)
 h=SimpleNamespace(run_dir=link/'child',runner=SimpleNamespace(model='gpt-6-luna'),annotation_active_indices=[1],_annotation_source_positions={0:{'source':'exact'}})
 with pytest.raises(ValueError):
  with m.bounded_chunk_jobs(h):pass
@pytest.mark.parametrize('missing',[False,True])
def test_actual_k_selected_discovery_and_triage_scope(tmp_path,missing):
 import ast,json
 import pipeline.korean_agent_harness as k
 from unittest.mock import patch
 source=Path(__file__).with_name('korean_agent_harness.py')
 if not source.exists():source=Path(k.__file__)
 tree=ast.parse(source.read_text());run=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_run')
 loop=next(n for n in ast.walk(run) if isinstance(n,ast.For) and any(isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='discovery_scope' for t in x.targets) for x in n.body))
 start=next(i for i,n in enumerate(loop.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='discovery_scope' for t in n.targets))
 end=next(i for i,n in enumerate(loop.body[start:],start) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='annotation_prompt' for t in n.targets))
 fn=ast.AsyncFunctionDef(name='actual_discovery',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=loop.body[start:end],decorator_list=[])
 class Fake:
  model='gpt-6-luna';benchmark_effort=None
  async def call(self,job,prompt,schema,effort=None,**kwargs):calls.append((job,prompt));return {'headwords':['새단어'] if missing else []}
 calls=[];text='첫 문장이다. 둘째 문장이다.'
 h=SimpleNamespace(runner=Fake(),run_dir=tmp_path,policy='immutable-policy',level=4,annotation_active_indices=[2],annotation_holds=None,annotation_admission_job_limit=6,annotation_batch_characters=0,catalog={})
 ns=dict(vars(k));ns.update(self=h,prose={'title':'제목','text':text},focus={'entries':[]},prose_attempt=0,candidate_entries=[],proposed_headwords=set())
 exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'<actual-discovery>','exec'),ns)
 with patch.dict(sys.modules,{'pipeline.chunk_scheduler':m}):
  if missing:
   with pytest.raises(m.ChunkAdmissionIncomplete) as held:asyncio.run(ns['actual_discovery']())
   assert [row.index for row in held.value.deferred_chunks]==[1]
   assert not held.value.attempted_failures
  else:asyncio.run(ns['actual_discovery']())
 assert len(calls)==2
 for job,prompt in calls:
  assert '-admission-' in job
  data=json.loads(prompt.split('\nINPUT:\n',1)[1]);scope=data['annotation_admission_discovery']
  assert data['prose']['text']==text and scope['active_chunks']==[2]
  assert scope['selected_occurrences'][0]['text']=='둘째 문장이다.'
  assert len(scope['source_positions'])==2
@pytest.mark.parametrize('language',['ko','zh','ja'])
def test_real_registered_positions_hold_digest_and_repeated_offset(language,tmp_path,monkeypatch):
 import hashlib,copy
 from pipeline.annotation_reference_carry_callers import register_chunk_positions
 file=tmp_path/'pipeline/chunk_scheduler.py';file.parent.mkdir();file.write_text('setup');monkeypatch.setattr(m,'__file__',str(file))
 evidence=tmp_path/'evidence.json';evidence.write_text('immutable investigation')
 h=SimpleNamespace(language=language);chunks=['同文。','同文。'];register_chunk_positions(h,chunks,parent_text=''.join(chunks))
 position=h._annotation_source_positions[1]
 row={'chunk_index':2,**{k:v for k,v in position.items() if k!='chunk_index'},'reason':'known unresolved','evidence':[{'path':'evidence.json','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
 h.annotation_holds={'version':1,'holds':[row]};assert m.investigation_hold(h,1,chunks[1])==row;assert m.investigation_hold(h,0,chunks[0]) is None
 foreign=copy.deepcopy(row);foreign['source_start']=0;h.annotation_holds['holds']=[foreign]
 with pytest.raises(ValueError):m.investigation_hold(h,1,chunks[1])
 foreign=copy.deepcopy(row);foreign['source_text_digest']=hashlib.sha256(chunks[1].encode()).hexdigest();h.annotation_holds['holds']=[foreign]
 with pytest.raises(ValueError):m.investigation_hold(h,1,chunks[1])
