"""Derived repair contracts, immutable provenance, and shared submission gates."""
import copy,hashlib,importlib.util,json,sys
from pathlib import Path
import pytest
from pipeline import lexical_request_reconciliation as c,lexical_reconciliation_round as r,annotation_semantic_round as b,annotation_admission_recovery as recovery
from pipeline import agent_harness
from pipeline import worker_workspace as workspace
VERIFY_REPAIR=c.verify_reconciliation_repair_jobs
spec=importlib.util.spec_from_file_location('old_fixture',Path('tests/test_lexical_reconciliation_repair.py'));f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)

def case(tmp_path,monkeypatch,rep='korean-flat',repeated_parent=False):
 monkeypatch.setattr(agent_harness,'ROOT',tmp_path)
 inputs,base,_=f.fixture(rep)
 if repeated_parent:
  inputs['parent_text']='XXZZXX';inputs['source_position']['parent_text_digest']=c.digest(inputs['parent_text'])
 inputs['requests']=['first','second'];inputs.update(reconciliation_instruction_policy_version=2,reconciliation_patch_base_policy_version=1,reconciliation_repair={'worker_run_relpath':'original','inputs_sha256':'a'*64,'resolver_job':'old','review_job':'old-review','result_digest':'b'*64,'review_digest':'c'*64})
 base['decisions'][0]['request']='first';base['decisions'][0]['reason']='Already repaired first decision.';second=copy.deepcopy(base['decisions'][0]);second.update(request_index=1,request='second',reason='Needs next correction.');base['decisions'].append(second);base['inputs_digest']=c.digest(inputs)
 review={'policy_version':1,'inputs_digest':c.digest(inputs),'result_digest':c.digest(base),'approved':False,'issues':[{'request_index':1,'explanation':'Current independent protocol fixture.'}]}
 patch={'policy_version':2,'base_digest':'d'*64,'edits':[{'request_index':0,'fields':{'reason':'Already repaired first decision.'}}]}
 e={'reconciliation_receipt_version':2,'inputs_digest':c.digest(inputs),'repair_job':'parent-repair','review_job':'parent-review','result':base,'review':review,'patch':patch}
 lane=tmp_path/'parent';lane.mkdir();raw=(json.dumps(inputs)).encode();eraw=json.dumps(e).encode();(lane/'immutable-inputs.json').write_bytes(raw);(lane/'evidence.json').write_bytes(eraw)
 desc={'parent_kind':'repaired_reconciliation','worker_run_relpath':'parent','inputs_sha256':hashlib.sha256(raw).hexdigest(),'evidence_sha256':hashlib.sha256(eraw).hexdigest(),'result_digest':c.digest(base),'review_digest':c.digest(review),'source_position':copy.deepcopy(inputs['source_position']),'target_bindings':r._target_bindings(inputs,e)}
 child={**inputs,'reconciliation_repair_round_version':1,'reconciliation_repair':desc}
 monkeypatch.setattr(c,'verify_reconciliation_repair_jobs',lambda *a,**k:copy.deepcopy(e));monkeypatch.setattr(c,'authenticate_archived_sources',lambda *a,**k:None)
 return inputs,base,review,e,child

@pytest.mark.parametrize('rep',['korean-flat','chinese-annotation','japanese-annotation'])
def test_actual_representation_request_and_scoped_derived_repair(tmp_path,monkeypatch,rep):
 old,base,review,e,child=case(tmp_path,monkeypatch,rep)
 if rep=='chinese-annotation':assert 'text' in child['candidate']['segments'][0]
 if rep=='japanese-annotation':assert {'surface','lemma','lemma_kana'}<=child['candidate']['segments'][0].keys()
 assert r.authenticate_parent(tmp_path,child)['result']==base
 request=c.reconciliation_request(child,'repair',origin_run_dir=tmp_path)
 assert request['schema']['properties']['base_digest']['const']==c.digest(base)
 assert request['workspace_context']['reconciliation_repair_targets']==[1]
 patch={'policy_version':2,'base_digest':c.digest(base),'edits':[{'request_index':1,'fields':{'reason':'Corrected second decision only.'}}]}
 result=c.apply_reconciliation_repair(patch,child,base,review)
 assert result['decisions'][0]==base['decisions'][0] and result['decisions'][1]['occurrences']==base['decisions'][1]['occurrences'] and child['source_position']==old['source_position']

def _registered_receipt3(tmp_path,monkeypatch,*,repeated_parent=False):
 old,base,review,parent_e,child=case(tmp_path,monkeypatch,repeated_parent=repeated_parent)
 registered=tmp_path/'registered';registered.mkdir()
 def route(worker_run_dir,inputs,**kwargs):
  if r.round_marker(inputs):return VERIFY_REPAIR(worker_run_dir,inputs,**kwargs)
  return parent_e
 monkeypatch.setattr(c,'verify_reconciliation_repair_jobs',route)
 import pipeline.annotation_research as research
 def fake_worker(run_dir,*,job,expected_prompt,schema_text,workspace_context,expected_result=None):
  validation=workspace_context['lexical_request_reconciliation_validation'];mode=validation['mode']
  if mode=='repair':
   base_result=workspace_context['reconciliation_repair_base'];target=workspace_context['reconciliation_repair_targets'][0]
   value={'policy_version':2,'base_digest':c.digest(base_result),'edits':[{'request_index':target,'fields':{'reason':'Targeted derived repair fixture.'}}]}
  else:
   result=workspace_context['reconciliation_result']
   value={'policy_version':1,'inputs_digest':c.digest(validation['inputs']),'result_digest':c.digest(result),'approved':True,'issues':[]}
  return ({'fixture':True},value)
 monkeypatch.setattr(research,'_verify_job',fake_worker)
 request=c.reconciliation_request(child,'repair',origin_run_dir=tmp_path)
 target=parent_e['review']['issues'][0]['request_index'];patch={'policy_version':2,'base_digest':c.digest(parent_e['result']),'edits':[{'request_index':target,'fields':{'reason':'Targeted derived repair fixture.'}}]}
 projected=c.apply_reconciliation_repair(patch,child,parent_e['result'],parent_e['review'])
 review_request=c.reconciliation_request(child,'review',origin_run_dir=tmp_path,result=projected)
 result_bound=VERIFY_REPAIR(registered,child,origin_run_dir=tmp_path,repair_job=request['job'],review_job=review_request['job'])
 inputs_raw=(json.dumps(child,ensure_ascii=False,indent=2)+'\n').encode()
 evidence={'reconciliation_receipt_version':3,'inputs_digest':c.digest(child),'repair_job':request['job'],'review_job':review_request['job'],'result':result_bound['result'],'review':result_bound['review'],'patch':result_bound['patch'],'annotation_approved':False,'registry_promoted':False}
 evidence_raw=(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n').encode()
 (registered/'immutable-inputs.json').write_bytes(inputs_raw);(registered/'evidence.json').write_bytes(evidence_raw)
 descriptor={'worker_run_relpath':'registered','inputs_sha256':hashlib.sha256(inputs_raw).hexdigest(),'evidence_sha256':hashlib.sha256(evidence_raw).hexdigest()}
 return old,child,evidence,descriptor

def test_registered_receipt3_dispatches_and_replays_derived_parent(tmp_path,monkeypatch):
 old,child,evidence,descriptor=_registered_receipt3(tmp_path,monkeypatch)
 lookup=c.load_registered_reconciliation(tmp_path,descriptor,requests=child['requests'],source_text=child['source_text'],source_position=child['source_position'],current_catalog=child['catalog'])
 assert lookup['lookup_headwords']==['base'] and lookup['annotation_approved'] is False and lookup['registry_promoted'] is False

def test_receipt3_identical_text_transplant_to_other_source_position_rejects(tmp_path,monkeypatch):
 old,child,evidence,descriptor=_registered_receipt3(tmp_path,monkeypatch,repeated_parent=True)
 foreign={**child['source_position'],'source_start':4}
 assert child['parent_text'][foreign['source_start']:foreign['source_start']+len(child['source_text'])]==child['source_text']
 with pytest.raises(ValueError,match='Current source changed'):
  c.load_registered_reconciliation(tmp_path,descriptor,requests=child['requests'],source_text=child['source_text'],source_position=foreign,current_catalog=child['catalog'])

def _workspace_patch(tmp_path,monkeypatch,bad=None):
 old,base,review,parent_e,child=case(tmp_path,monkeypatch)
 def packet_origin(origin,inputs):
  assert Path(origin)==tmp_path
  authenticated=c.check_inputs(inputs)
  assert inputs==child
  return authenticated
 def source_provider(inputs):
  assert inputs==child
  return inputs
 monkeypatch.setattr(c,'authenticate_packet_origin',packet_origin)
 monkeypatch.setattr(c,'authenticate_archived_sources',source_provider)
 # Exact receipt2 result/review are the sole mocked parent-authentication output.
 monkeypatch.setattr(c,'authenticate_repair_base',lambda origin,inputs:copy.deepcopy(parent_e))
 request=c.reconciliation_request(child,'repair',origin_run_dir=tmp_path)
 if bad=='target':request=copy.deepcopy(request);request['workspace_context']['reconciliation_repair_target_bindings'][0]['occurrences'][0]['absolute_start']+=1
 if bad=='position':request=copy.deepcopy(request);request['workspace_context']['lexical_request_reconciliation_validation']['inputs']['source_position']['source_start']=2
 path=tmp_path/'workspace';_,manifest=workspace.build(path,request['prompt'],request['schema'],context=request['workspace_context'])
 patch={'policy_version':2,'base_digest':c.digest(base),'edits':[{'request_index':1,'fields':{'reason':'Validated derived workspace repair.'}}]}
 if bad=='base':patch['base_digest']='0'*64
 candidate=path/'candidate.json';candidate.write_text(json.dumps(patch,ensure_ascii=False))
 workspace.verify(path,manifest)
 return path,candidate,patch

def test_shared_workspace_build_check_submit_accepts_typed_receipt3_patch(tmp_path,monkeypatch):
 path,candidate,patch=_workspace_patch(tmp_path,monkeypatch)
 assert workspace.check(path,candidate)==patch
 value,receipt=workspace.submit(path,{'candidate_path':'candidate.json'})
 assert value==patch and receipt['artifact_path']=='candidate.json'

@pytest.mark.parametrize('bad',['base','target','position'])
def test_shared_workspace_submission_gate_rejects_typed_base_target_or_source_drift(tmp_path,monkeypatch,bad):
 path,candidate,_=_workspace_patch(tmp_path,monkeypatch,bad)
 with pytest.raises(workspace.CandidateSubmissionError):workspace.submit(path,{'candidate_path':'candidate.json'})

@pytest.mark.parametrize('bad',['position','context','base','target','patch_masquerade','initial_masquerade','marker','cycle','parent_hash'])
def test_derived_parent_and_scope_reject(tmp_path,monkeypatch,bad):
 old,base,review,e,child=case(tmp_path,monkeypatch)
 if bad=='position':child['source_position']={**child['source_position'],'source_start':1}
 elif bad=='context':child['parent_text']+='changed'
 elif bad=='base':child['reconciliation_repair']['result_digest']='0'*64
 elif bad=='target':review=copy.deepcopy(review);review['issues'][0]['request_index']=0;monkeypatch.setattr(c,'verify_reconciliation_repair_jobs',lambda *a,**k:{**e,'review':review})
 elif bad=='patch_masquerade':child['reconciliation_repair']['parent_kind']='patch'
 elif bad=='initial_masquerade':child['reconciliation_repair'].pop('parent_kind')
 elif bad=='marker':child['reconciliation_repair_round_version']=True
 elif bad=='cycle':
  lane=tmp_path/'parent';value=json.loads((lane/'immutable-inputs.json').read_text());value['reconciliation_repair_round_version']=1;raw=json.dumps(value).encode();(lane/'immutable-inputs.json').write_bytes(raw);child['reconciliation_repair']['inputs_sha256']=hashlib.sha256(raw).hexdigest()
 else:child['reconciliation_repair']['evidence_sha256']='0'*64
 with pytest.raises(ValueError):r.authenticate_parent(tmp_path,child)

@pytest.mark.parametrize('bad',['fixed_first','wrong_base','source','noop'])
def test_next_patch_cannot_undo_first_repair(tmp_path,monkeypatch,bad):
 _,base,review,_,child=case(tmp_path,monkeypatch);patch={'policy_version':2,'base_digest':c.digest(base),'edits':[{'request_index':1,'fields':{'reason':'Corrected.'}}]}
 if bad=='fixed_first':patch['edits'][0]['request_index']=0
 elif bad=='wrong_base':patch['base_digest']='0'*64
 elif bad=='source':patch['edits'][0]['fields']={'occurrences':[]}
 else:patch['edits'][0]['fields']['reason']=base['decisions'][1]['reason']
 with pytest.raises(Exception):c.apply_reconciliation_repair(patch,child,base,review)

def ledger_case(tmp_path,monkeypatch,rep='korean-flat'):
 old,base,review,e,child=case(tmp_path,monkeypatch,rep)
 request=c.reconciliation_request(child,'repair',origin_run_dir=tmp_path)
 for job in (e['repair_job'],e['review_job']):
  root=tmp_path/'parent/agents'/job;root.mkdir(parents=True);(root/'meta.json').write_text(json.dumps({'return_code':0,'attempt_count':1,'attempts':[{}],'model':'gpt-6-luna','effort':'low','tool_profile':'workspace'}))
 monkeypatch.setattr(agent_harness.CodexRunner,'_check_tool_profile',lambda *a:None)
 monkeypatch.setattr(recovery,'verify_protocol_recovery',lambda *a,**k:{'prior_executions':2})
 directory=tmp_path/'budget/annotation-admission';directory.mkdir(parents=True)
 ledger={'version':2,'scope':{'phase':'reconciliation-repair','source_positions':{'0':child['source_position']}},'contract':{'version':1},'limit':3,'requests':[{'job':'original'},{'job':e['repair_job']},{'job':e['review_job']}],'protocol_recovery':{'retained':'fixture'}}
 raw=json.dumps(ledger).encode();(directory/'ledger.json').write_bytes(raw)
 kwargs={'ledger_name':'ledger.json','prior_ledger_sha256':hashlib.sha256(raw).hexdigest(),'next_request':request,'prior_executions':4}
 return ledger,raw,kwargs

def test_same_ledger_semantic_extension_preserves_spending(tmp_path,monkeypatch):
 old,raw,kwargs=ledger_case(tmp_path,monkeypatch);lane=tmp_path/'budget';receipt=b.register_semantic_round(lane,**kwargs)
 new=json.loads((lane/'annotation-admission/ledger.json').read_text())
 assert new['limit']==5 and new['requests']==old['requests'] and new['protocol_recovery']==old['protocol_recovery']
 assert (lane/'annotation-admission'/receipt['prior_snapshot']).read_bytes()==raw
 assert receipt['prior_primary_roles']==3 and receipt['prior_executions']==4 and receipt['additional_max_executions']==4 and receipt['total_max_executions']==8
 assert b.verify_semantic_round(lane,'ledger.json',next_request=kwargs['next_request'])==receipt
 with pytest.raises(ValueError):b.register_semantic_round(lane,**kwargs)

@pytest.mark.parametrize('bad',['ledger','spent','old_parent','source','request','untyped'])
def test_semantic_extension_rejects_bad_authority(tmp_path,monkeypatch,bad):
 _,_,args=ledger_case(tmp_path,monkeypatch)
 if bad=='ledger':args['prior_ledger_sha256']='0'*64
 elif bad=='spent':args['prior_executions']=2
 elif bad=='old_parent':
  path=tmp_path/'budget/annotation-admission/ledger.json';value=json.loads(path.read_text());value['requests'][1]['job']='foreign';raw=json.dumps(value).encode();path.write_bytes(raw);args['prior_ledger_sha256']=hashlib.sha256(raw).hexdigest()
 elif bad=='source':args['next_request']['workspace_context']['source_position']['source_start']=2
 elif bad=='request':args['next_request']['prompt']='foreign'
 else:args['next_request']['workspace_context']['lexical_request_reconciliation_validation']['inputs'].pop('reconciliation_repair_round_version')
 with pytest.raises(Exception):b.register_semantic_round(tmp_path/'budget',**args)
 assert json.loads((tmp_path/'budget/annotation-admission/ledger.json').read_text())['limit']==3

@pytest.mark.parametrize('bad',['snapshot','originalrow','bounds','target','context','parent_file','parent_meta'])
def test_semantic_replay_mutations_reject(tmp_path,monkeypatch,bad):
 _,_,args=ledger_case(tmp_path,monkeypatch);lane=tmp_path/'budget';receipt=b.register_semantic_round(lane,**args);path=lane/'annotation-admission/ledger.json';ledger=json.loads(path.read_text())
 if bad=='snapshot':(path.parent/receipt['prior_snapshot']).write_text('{}')
 elif bad=='originalrow':ledger['requests'][0]['job']='changed'
 elif bad=='bounds':ledger['semantic_round']['total_max_executions']=9
 elif bad=='target':ledger['semantic_round']['targets']=[0]
 elif bad=='context':
  req=copy.deepcopy(args['next_request']);req['workspace_context']['foreign']=True
  with pytest.raises(ValueError):b.verify_semantic_round(lane,'ledger.json',next_request=req)
  return
 elif bad=='parent_file':(tmp_path/'parent/evidence.json').write_text('{}')
 elif bad=='parent_meta':
  path=tmp_path/'parent/agents/parent-repair/meta.json';meta=json.loads(path.read_text());meta['model']='other-model';path.write_text(json.dumps(meta))
 if bad!='parent_meta':path.write_text(json.dumps(ledger))
 with pytest.raises(Exception):b.verify_semantic_round(lane,'ledger.json')

@pytest.mark.parametrize('rep',['korean-flat','chinese-annotation','japanese-annotation'])
def test_old_receipt2_request_exact_against_canonical(tmp_path,monkeypatch,rep):
 old,base,review,e,child=case(tmp_path,monkeypatch,rep)
 original=tmp_path/'original/agents';(original/'old').mkdir(parents=True);(original/'old-review').mkdir();(original/'old/result.json').write_text(json.dumps(base));(original/'old-review/result.json').write_text(json.dumps(review));old['reconciliation_repair']['result_digest']=c.digest(base);old['reconciliation_repair']['review_digest']=c.digest(review)
 historical_hashes={'korean-flat:repair': '2540939d1881b103beb40ca382a9d532a5c2d4aa086fa4e46b2015205140442d', 'korean-flat:review': '9fd2931583940693c6f43e77010834eb2ba4c3a0b75f6bf4521cc76106b4994f', 'chinese-annotation:repair': 'aa91d600c51ae62d9564524fc10457ea93e8d45d8c4c31ee2c31c00fe06d4f23', 'chinese-annotation:review': '4e48bec71896ab7f5f617ebf80aaf88830c12db016f9fa6bb2a84e384d6bafe0', 'japanese-annotation:repair': 'a1e2daccbc9eb50efc9a801c1f94f8a6c0c110604e038de090463c48d105434e', 'japanese-annotation:review': '8db7f45071c4fff0027a30f9af7239bcf4268a3d1cc6b1fd51fc47739849d4ec'}
 for stage in ('repair','review'):
  options={'result':base} if stage=='review' else {}
  request=c.reconciliation_request(old,stage,origin_run_dir=tmp_path,**options)
  encoded=json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':')).replace(str(tmp_path),'<ROOT>')
  assert hashlib.sha256(encoded.encode()).hexdigest()==historical_hashes[rep+':'+stage]

@pytest.mark.asyncio
@pytest.mark.parametrize('rep',['korean-flat','chinese-annotation','japanese-annotation'])
async def test_shared_scheduler_same_phase_admits_only_two_new_roles(tmp_path,monkeypatch,rep):
 from types import SimpleNamespace
 from pipeline import chunk_scheduler as scheduler
 from pipeline.annotation_review_guidance import COMPLETE_STAGE_INSTRUCTION_POLICY_VERSION
 from pipeline.agent_harness import CachedCallUnavailable
 _,raw,args=ledger_case(tmp_path,monkeypatch,rep);budget=tmp_path/'budget';directory=budget/'annotation-admission';old=json.loads(raw);position=old['scope']['source_positions']['0']
 scope={'phase':'reconciliation-repair','active_chunks':[1],'source_positions':{'0':position}}
 contract={'version':1,'complete_stage_instruction_policy_version':COMPLETE_STAGE_INSTRUCTION_POLICY_VERSION,'phase':'reconciliation-repair','target_level':4,'model':'gpt-6-luna','effort':'low'}
 old.update(scope=scope,contract=contract);raw=json.dumps(old).encode();name=c.digest(scope)+'.json';(directory/name).write_bytes(raw);args.update(ledger_name=name,prior_ledger_sha256=hashlib.sha256(raw).hexdigest());b.register_semantic_round(budget,**args)
 observed=[]
 class Runner:
  model='gpt-6-luna';benchmark_effort=None
  async def call(self,job,prompt,schema,effort=None,**options):
   if options.get('cache_only'):raise CachedCallUnavailable('finite fixture')
   observed.append(job);return {'fixture':True}
 harness=SimpleNamespace(run_dir=budget,runner=Runner(),annotation_active_indices=[1],_annotation_source_positions={0:position},level=4)
 request=args['next_request'];schema=tmp_path/'schema.json';schema.write_text(json.dumps(request['schema']))
 with scheduler.bounded_chunk_jobs(harness,phase='reconciliation-repair',job_limit=5):
  await harness.runner.call(request['job'],request['prompt'],schema,'low',tool_profile='workspace',workspace_context=request['workspace_context'])
  await harness.runner.call('next-full-critic','fixture critic',schema,'low',tool_profile='workspace',workspace_context={'representation':rep})
  with pytest.raises(scheduler.ChunkJobLimitReached):await harness.runner.call('unexpected-third-role','fixture',schema,'low',tool_profile='workspace')
 ledger=json.loads((directory/name).read_text());assert ledger['requests'][:3]==old['requests'] and ledger['limit']==5 and len(ledger['requests'])==5 and len(observed)==2
