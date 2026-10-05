"""Setup-only authenticated worker artifacts prove producer ordering, not linguistics."""
import copy,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.test_annotation_run_lessons import fixture
from tests.test_annotation_run_knowledge_selection import inputs,selection
from tests.test_annotation_research import _write_job
from tests.test_annotation_repairs import _plan,_target,_korean_candidate_gate
from pipeline import annotation_run_knowledge_selection as selector
from pipeline.annotation_repairs import repair_annotation,candidate_digest
from pipeline import annotation_semantic_derivation as derivation

def candidate_gate():
 value=_korean_candidate_gate();value['focus']['entries']=[];value['plan']['beats']=[];return value

def gate(candidate):
 assert ''.join(row['text'] for row in candidate['segments'])=='깊다며'

async def chain(fixture,monkeypatch,ordinary=False):
 value=inputs(fixture);root=value['run_dir']=fixture[1];base=copy.deepcopy(value['candidate'])
 from pipeline import korean_contracts
 monkeypatch.setattr(korean_contracts,'lexical_catalog',lambda:{'fixture':[{'id':'child','headword':'fixture','pos':'명','meaning':'setup'}]})
 class Runner:
  model='gpt-6-luna';benchmark_effort='low';legacy_tool_restrictions=False
  async def call(self,job,prompt,schema,effort,**kwargs):
   context=kwargs['workspace_context']
   if 'annotation_run_knowledge_selection' in context:result=selection(context['annotation_run_knowledge_selection'])
   elif job.endswith('_plan'):result=_plan((_target('set_field','/segments/0/meaning_en'),))
   else:result={'base_digest':candidate_digest(self.base),'edits':[{'op':'set_field','path':'/segments/0/meaning_en','value':self.meaning}]}
   _write_job(root,job,prompt,Path(schema).read_text(),context,result);return result
 runner=Runner();runner.base=base;runner.meaning='setup corrected complete meaning'
 h=SimpleNamespace(run_dir=root,runner=runner,grammar={},args=SimpleNamespace(annotation_repair_effort='low',refresh=False),_annotation_source_positions={13:value['context']['annotation_source_position']})
 if ordinary:
  _write_job(root,'setup-generation','setup producer','{}',{},base);candidate=base;job='setup-generation'
 else:
  changed=await repair_annotation(h,'setup-semantic',base,[{'explanation':'Setup correction','candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}],representation='korean-flat',language='ko',context={'chunk_text':'깊다며','candidate_gate':candidate_gate()},validate_candidate=gate)
  candidate=changed['candidate'];job=changed['evidence']['assembly_job']
 from pipeline.korean_agent_harness import digest
 record={'job':job,'text':'깊다며','digest':digest(candidate)}
 monkeypatch.setattr(selector,'registered_catalog',lambda *_:value['catalog'])
 normalized=await selector.select_and_normalize_run_knowledge(h,13,candidate,'깊다며',language='ko',representation='korean-flat',context=value['context'],base_record=record)
 return value,h,normalized,job

def verify(value,h,result,**extra):
 return derivation.verified_semantic_derivation(h.run_dir,candidate=result['candidate'],source_text='깊다며',language='ko',representation='korean-flat',validate_candidate=gate,record=result['record'],context=result['context'],**extra)

@pytest.mark.asyncio
async def test_authenticated_semantic_then_normalization_composes_exact_current(fixture,monkeypatch):
 value,h,result,job=await chain(fixture,monkeypatch);proof=verify(value,h,result)
 assert proof['semantic_job']==job and proof['context_evidence']['candidate_digest']==derivation.digest(result['candidate'])
 assert proof['replayed']['candidate']!=result['candidate']
 context={**result['context'],'verified_semantic_derivation':proof['context_evidence']}
 derivation.verify_semantic_derivation_context(h.run_dir,context,candidate=result['candidate'],source_text='깊다며',language='ko',representation='korean-flat')
 context['verified_semantic_derivation']['semantic_meta_digest']='tampered'
 with pytest.raises(ValueError):derivation.verify_semantic_derivation_context(h.run_dir,context,candidate=result['candidate'],source_text='깊다며',language='ko',representation='korean-flat')

@pytest.mark.asyncio
async def test_ordinary_generation_normalization_never_qualifies(fixture,monkeypatch):
 value,h,result,job=await chain(fixture,monkeypatch,ordinary=True)
 assert verify(value,h,result) is None

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['identity','source','normalization','semantic'])
async def test_composed_derivation_rejects_tampering(fixture,monkeypatch,change):
 value,h,result,job=await chain(fixture,monkeypatch)
 if change=='identity':result['candidate']['segments'][0]['form_steps'][0]['grammar_entry_ids']=['foreign']
 if change=='source':result['candidate']['segments'][0]['text']='wrong'
 if change=='normalization':result['context'][selector.FIELD]['meta_digest']='bad'
 if change=='semantic':
  path=h.run_dir/'agents'/job/'meta.json';meta=json.loads(path.read_text());meta['base_digest']='bad';path.write_text(json.dumps(meta))
 with pytest.raises((ValueError,AssertionError)):verify(value,h,result)

@pytest.mark.asyncio
async def test_later_semantic_repair_is_current_producer_with_retained_normalization(fixture,monkeypatch):
 value,h,result,job=await chain(fixture,monkeypatch);h.runner.base=result['candidate'];h.runner.meaning='setup later semantic correction'
 later=await repair_annotation(h,'setup-later',result['candidate'],[{'explanation':'Later correction','candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}],representation='korean-flat',language='ko',context={'chunk_text':'깊다며','candidate_gate':candidate_gate()},validate_candidate=gate)
 proof=derivation.verified_semantic_derivation(h.run_dir,candidate=later['candidate'],source_text='깊다며',language='ko',representation='korean-flat',validate_candidate=gate,semantic_job=later['evidence']['assembly_job'],normalization=result['context'][selector.FIELD],context=result['context'])
 assert proof['semantic_job']==later['evidence']['assembly_job'] and proof['context_evidence'] is None

def test_absent_historical_context_unchanged(tmp_path):
 context={'historical':'unchanged'}
 assert derivation.verify_semantic_derivation_context(tmp_path,context,candidate={},source_text='',language='ko',representation='korean-flat') is None
 assert context=={'historical':'unchanged'}

@pytest.mark.asyncio
async def test_actual_k_callback_routes_current_normalized_rejection_to_adjudication(fixture,monkeypatch):
 import ast
 import pipeline.korean_agent_harness as korean
 import pipeline.annotation_adjudication as adjudication
 value,h,result,job=await chain(fixture,monkeypatch)
 h.level=4;h.words={};h.catalog={};h.policy='Setup policy';h.review_policy='Setup review'
 parent=value['context']['chapter_text'];offset=value['context']['source_start'];text=value['source_text']
 texts=['']*13;texts[0]=parent[:offset];texts.extend([text,parent[offset+len(text):]])
 raw={'approved':False,'issues':[{'explanation':'Setup scoped new objection','candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}],'prose_revision_reason_en':''}
 async def chunk(number,source,*args):
  assert number==14 and source==text and args==(None,None)
  return copy.deepcopy(result['candidate']),result['record']
 class Captured(Exception):pass
 async def adju(harness,*args,**kwargs):
  assert kwargs['candidate']==result['candidate'] and kwargs['current_review']==raw
  assert kwargs['prior_history'][0]['semantic_repair']['job']==job
  assert kwargs['prior_history'][0]['annotation']!=result['candidate']
  assert kwargs['context']['verified_semantic_derivation']['candidate_digest']==derivation.digest(result['candidate'])
  assert kwargs['deterministic_gate_evidence']['passed'] is True
  raise Captured()
 monkeypatch.setattr(adjudication,'adjudicate_annotation_review',adju)
 class ReviewRunner:
  async def call(self,job,prompt,schema,effort,**kwargs):
   assert job.startswith('annotation-local-review-')
   _write_job(h.run_dir,job,prompt,Path(schema).read_text(),kwargs['workspace_context'],raw);return raw
 h.runner=ReviewRunner()
 node=next(n for n in ast.walk(ast.parse(Path(korean.__file__).read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name=='reviewed_chunk')
 ns=dict(vars(korean));ns.update(self=h,chunk=chunk,texts=texts,prose={'text':parent},bound_plan={},focus={},reviewed_source_context={},checkpoint_approvals={},validate_chunk=lambda c,t:gate(c))
 tree=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(tree);exec(compile(tree,'actual_proposed_k_callback','exec'),ns)
 with pytest.raises(Captured):await ns['reviewed_chunk'](13,text)

@pytest.mark.asyncio
async def test_current_k_record_digest_and_source_must_match(fixture,monkeypatch):
 value,h,result,job=await chain(fixture,monkeypatch)
 for key in ('digest','text'):
  bad=copy.deepcopy(result);bad['record'][key]='stale'
  with pytest.raises(derivation.SemanticDerivationError,match='producer record'):verify(value,h,bad)

@pytest.mark.asyncio
async def test_nested_conflicting_normalization_rejected(fixture,monkeypatch):
 value,h,result,job=await chain(fixture,monkeypatch);proof=verify(value,h,result)
 nested=copy.deepcopy(result['context']);nested[selector.FIELD]['meta_digest']='tampered'
 context={'chunk_review_context':nested,'verified_semantic_derivation':proof['context_evidence']}
 with pytest.raises(derivation.SemanticDerivationError,match='differs'):
  derivation.verify_semantic_derivation_context(h.run_dir,context,candidate=result['candidate'],source_text='깊다며',language='ko',representation='korean-flat')
