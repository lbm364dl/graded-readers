import copy,hashlib,importlib.util,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from pipeline import annotation_admission_recovery as r
@pytest.fixture(autouse=True)
def isolated_budget_shapes(monkeypatch):
 from pipeline.agent_harness import CodexRunner
 monkeypatch.setattr(CodexRunner,'_check_tool_profile',lambda *a:None)
 from pipeline import annotation_research
 monkeypatch.setattr(annotation_research,'_request_fingerprint',lambda *a,**k:'fixture-fingerprint')
 from pipeline import lexical_request_reconciliation
 monkeypatch.setattr(lexical_request_reconciliation,'reconciliation_request',lambda *a,**k:copy.deepcopy(EXPECTED_REQUEST)) # Shape-only budget cases; real receipt test is separate.

EXPECTED_REQUEST={}
def fixture(tmp):
 global EXPECTED_REQUEST
 position={'chunk_index':0,'source_start':3,'source_text_digest':'a'*64,'parent_text_digest':'b'*64};prior={'version':2,'scope':{'phase':'reconciliation-repair','active_chunks':[1],'source_positions':{'0':position}},'contract':{'exact':'preserved'},'limit':2,'requests':[{'job':'original','request_digest':'old'}]};directory=tmp/'annotation-admission';directory.mkdir();ledger=directory/'ledger.json';raw=json.dumps(prior).encode();ledger.write_bytes(raw)
 files={}
 for job in ['original','original-submission-repair-a']:
  d=tmp/'agents'/job;d.mkdir(parents=True);meta={'job':job,'model':'gpt-6-luna','effort':'low','tool_profile':'workspace','fingerprint':'fixture-fingerprint','return_code':1,'attempt_count':1,'attempts':[{}]}
  if job=='original':meta['submission_repair']={'status':'rejected','job':'original-submission-repair-a'}
  rejection={'artifact_sha256':hashlib.sha256(b'{}').hexdigest(),'diagnostic':'Fixture rejection'}
  workspace=d/'workspace';workspace.mkdir();(workspace/'candidate.json').write_bytes(b'{}');(workspace/'submission-rejection.json').write_text(json.dumps(rejection))
  if job=='original':meta['submission_repair'].update(initial_rejection=rejection,repair_rejection=rejection);(d/'submission-recovery.json').write_text(json.dumps(meta['submission_repair']))
  f=d/'meta.json';f.write_text(json.dumps(meta));files[str(f.relative_to(tmp))]=hashlib.sha256(f.read_bytes()).hexdigest()
 files={str(f.relative_to(tmp)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (tmp/'agents').rglob('*') if f.is_file()}
 prior_ctx={'source_position':position,'reconciliation_repair':{'result_digest':'c'*64},'lexical_request_reconciliation_validation':{'mode':'repair','inputs':{},'origin_run_dir':'fixture'}};prior['requests'][0]['request']={'prompt':'Fixture old','schema':'{}','workspace_context':copy.deepcopy(prior_ctx)};raw=json.dumps(prior).encode();ledger.write_bytes(raw)
 request={'job':'explicit-new-contract','prompt':'Protocol fixture','schema':{'properties':{'base_digest':{'const':'c'*64}}},'workspace_context':{'reconciliation_patch_base_policy_version':1,'reconciliation_repair_expected_base_digest':'c'*64,'source_position':position,'reconciliation_repair':{'result_digest':'c'*64},'lexical_request_reconciliation_validation':{'mode':'repair','inputs':{},'origin_run_dir':'fixture'}}}
 EXPECTED_REQUEST=copy.deepcopy(request)
 kwargs={'ledger_name':'ledger.json','prior_ledger_sha256':hashlib.sha256(raw).hexdigest(),'prior_failure_files':files,'prior_executions':2,'protocol_request':request};return prior,raw,kwargs

def test_explicit_extension_preserves_original_and_is_one_time(tmp_path):
 prior,raw,args=fixture(tmp_path);receipt=r.register_protocol_recovery(tmp_path,**args);assert receipt['prior_executions']==2 and receipt['total_max_executions']==6
 assert (tmp_path/'annotation-admission/ledger.json.before-protocol-recovery.json').read_bytes()==raw
 ledger=json.loads((tmp_path/'annotation-admission/ledger.json').read_text());assert ledger['requests']==prior['requests'] and ledger['limit']==3
 assert r.verify_protocol_recovery(tmp_path,'ledger.json')==receipt
 with pytest.raises(ValueError):r.register_protocol_recovery(tmp_path,**args)
@pytest.mark.parametrize('bad',['ledger','failure','position','unchanged','no_revision','schema','bounds','foreign_pair','request_tamper'])
def test_recovery_authority_and_spending_rejected(tmp_path,bad):
 _,_,args=fixture(tmp_path)
 if bad=='ledger':args['prior_ledger_sha256']='0'*64
 elif bad=='failure':next(iter(args['prior_failure_files'].values()));args['prior_failure_files'][next(iter(args['prior_failure_files']))]='0'*64
 elif bad=='position':args['protocol_request']['workspace_context']['source_position']['source_start']=99
 elif bad=='unchanged':args['protocol_request']['job']='original'
 elif bad=='no_revision':args['protocol_request']['workspace_context'].pop('reconciliation_patch_base_policy_version')
 elif bad=='schema':args['protocol_request']['schema']['properties']['base_digest']['const']='d'*64
 elif bad=='foreign_pair':
  ledger=tmp_path/'annotation-admission/ledger.json';data=json.loads(ledger.read_text());data['requests'][0]['job']='different-prior';ledger.write_text(json.dumps(data));args['prior_ledger_sha256']=hashlib.sha256(ledger.read_bytes()).hexdigest()
 elif bad=='request_tamper':args['protocol_request']['prompt']='foreign prompt'
 else:args['prior_executions']=0
 with pytest.raises(ValueError):r.register_protocol_recovery(tmp_path,**args)
 assert json.loads((tmp_path/'annotation-admission/ledger.json').read_text())['limit']==2
@pytest.mark.parametrize('bad',['snapshot','old_reservation','bounds','failure','request_digest','request_file'])
def test_persisted_recovery_replay_tamper_rejected(tmp_path,bad):
 _,_,args=fixture(tmp_path);r.register_protocol_recovery(tmp_path,**args);path=tmp_path/'annotation-admission/ledger.json';ledger=json.loads(path.read_text())
 if bad=='snapshot':(tmp_path/'annotation-admission/ledger.json.before-protocol-recovery.json').write_text('{}')
 elif bad=='old_reservation':ledger['requests'][0]['job']='foreign'
 elif bad=='bounds':ledger['protocol_recovery']['additional_primary_roles']=3
 elif bad=='request_digest':ledger['protocol_recovery']['protocol_request_digest']='0'*64
 elif bad=='request_file':
  file=tmp_path/'annotation-admission'/ledger['protocol_recovery']['protocol_request_file'];value=json.loads(file.read_text());value['prompt']='foreign';file.write_text(json.dumps(value))
 else:next((tmp_path/'agents').glob('*/meta.json')).write_text('{}')
 path.write_text(json.dumps(ledger))
 with pytest.raises(ValueError):r.verify_protocol_recovery(tmp_path,'ledger.json')

def test_changed_recovery_caller_cannot_replay_receipt(tmp_path):
 _,_,args=fixture(tmp_path);r.register_protocol_recovery(tmp_path,**args);different=copy.deepcopy(args['protocol_request']);different['prompt']='changed caller'
 with pytest.raises(ValueError,match='caller'):r.verify_protocol_recovery(tmp_path,'ledger.json',protocol_request=different)

def test_replay_cannot_drop_required_failure_file(tmp_path):
 _,_,args=fixture(tmp_path);r.register_protocol_recovery(tmp_path,**args);path=tmp_path/'annotation-admission/ledger.json';ledger=json.loads(path.read_text());files=ledger['protocol_recovery']['failure_files'];files.pop(next(k for k in files if k.endswith('/workspace/candidate.json')));path.write_text(json.dumps(ledger))
 with pytest.raises(ValueError,match='Incomplete'):r.verify_protocol_recovery(tmp_path,'ledger.json')
