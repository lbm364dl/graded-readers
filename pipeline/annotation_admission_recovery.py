"""Explicit one-time protocol recovery; preserves prior admission spending.

This grants worker-budget continuity only, never linguistic approval.
"""
import copy,hashlib,json
from pathlib import Path
from pipeline.worker_paths import checked_directory,checked_regular_file,atomic_write_managed

def register_protocol_recovery(run_dir, *, ledger_name, prior_ledger_sha256,
        prior_failure_files, prior_executions, protocol_request, additional_roles=2):
 directory=checked_directory(Path(run_dir).absolute()/'annotation-admission')
 if Path(ledger_name).name!=ledger_name or not ledger_name.endswith('.json'):raise ValueError('Unsafe admission ledger')
 ledger_path=checked_regular_file(directory/ledger_name)
 raw=ledger_path.read_bytes();ledger=json.loads(raw)
 if hashlib.sha256(raw).hexdigest()!=prior_ledger_sha256:raise ValueError('Prior admission ledger changed')
 if ledger.get('protocol_recovery') is not None:raise ValueError('Protocol recovery already registered')
 if type(ledger.get('version')) is not int or ledger.get('version')!=2 or ledger['scope']['phase']!='reconciliation-repair' or type(ledger['limit']) is not int or ledger['limit']!=2 or len(ledger['requests'])!=1:raise ValueError('Unexpected prior admission spending')
 if type(prior_executions) is not int or prior_executions!=2 or type(additional_roles) is not int or additional_roles!=2:raise ValueError('Unexpected recovery bound')
 # The supported protocol change is explicit, and its schema fails wrong bases
 # before expensive evidence authentication. Unchanged requests cannot extend.
 ctx=protocol_request['workspace_context'];marker=ctx.get('reconciliation_patch_base_policy_version');expected=ctx.get('reconciliation_repair_expected_base_digest')
 if type(marker) is not int or marker!=1 or not isinstance(expected,str) or len(expected)!=64:raise ValueError('No supported tested protocol revision')
 if protocol_request['schema']['properties']['base_digest'].get('const')!=expected:raise ValueError('Protocol schema lacks exact base')
 if protocol_request['job'] in {r['job'] for r in ledger['requests']}:raise ValueError('Unchanged protocol job')
 original_ctx=ledger['requests'][0].get('request',{}).get('workspace_context')
 if not isinstance(original_ctx,dict):raise ValueError('Missing exact prior request context')
 common=copy.deepcopy(ctx);common.pop('reconciliation_patch_base_policy_version',None);common.pop('reconciliation_repair_expected_base_digest',None)
 common.get('lexical_request_reconciliation_validation',{}).get('inputs',{}).pop('reconciliation_patch_base_policy_version',None)
 if common!=original_ctx:raise ValueError('Recovery context changed beyond tested base contract')
 if expected!=ctx['reconciliation_repair']['result_digest']:raise ValueError('Recovery base differs from original result provenance')
 position=ctx['source_position'];positions=ledger['scope']['source_positions']
 if position not in positions.values():raise ValueError('Recovery source position changed')
 if not prior_failure_files:raise ValueError('Missing prior failure evidence')
 failures={}
 for rel,sha in prior_failure_files.items():
  path=Path(rel)
  if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe failure evidence')
  file=checked_regular_file(Path(run_dir).absolute()/path)
  if hashlib.sha256(file.read_bytes()).hexdigest()!=sha:raise ValueError('Prior failure evidence changed')
  failures[rel]=sha
 from pipeline.agent_harness import CodexRunner
 metas=[]
 for rel in failures:
  if rel.endswith('/meta.json'):
   path=Path(run_dir)/rel;metadata=json.loads(path.read_text());CodexRunner._check_tool_profile(path.parent,'workspace',metadata);metas.append(metadata)
 if len(metas)!=2 or any(m.get('return_code')!=1 or type(m.get('attempt_count')) is not int or m['attempt_count']!=1 or len(m.get('attempts',[]))!=1 or m.get('model')!='gpt-6-luna' or m.get('effort')!='low' or m.get('tool_profile')!='workspace' for m in metas):raise ValueError('Prior executions not exact failed pair')
 parent=next((m for m in metas if m.get('submission_repair',{}).get('status')=='rejected'),None)
 if parent is None or parent.get('job')!=ledger['requests'][0]['job'] or parent['submission_repair']['job'] not in {m.get('job') for m in metas}:raise ValueError('Prior correction lineage missing')
 from pipeline.annotation_research import _request_fingerprint
 reserved=ledger['requests'][0]['request']
 if parent.get('fingerprint')!=_request_fingerprint(reserved['prompt'],reserved['schema'],reserved['workspace_context']):raise ValueError('Failed parent request differs from reserved request')
 child=parent['submission_repair']['job']
 if not child.startswith(parent['job']+'-submission-repair-') or '/' in child:raise ValueError('Foreign correction lineage')
 required={f'agents/{parent["job"]}/meta.json',f'agents/{parent["job"]}/workspace/candidate.json',f'agents/{parent["job"]}/workspace/submission-rejection.json',f'agents/{parent["job"]}/submission-recovery.json',f'agents/{child}/meta.json',f'agents/{child}/workspace/candidate.json',f'agents/{child}/workspace/submission-rejection.json'}
 if not required<=set(failures):raise ValueError('Incomplete original failure files')
 recovery=json.loads((Path(run_dir)/f'agents/{parent["job"]}/submission-recovery.json').read_text())
 if recovery!=parent['submission_repair']:raise ValueError('Failure recovery metadata changed')
 for job,key in [(parent['job'],'initial_rejection'),(child,'repair_rejection')]:
  rejection=json.loads((Path(run_dir)/f'agents/{job}/workspace/submission-rejection.json').read_text())
  # A correction workspace retains the INITIAL rejection as task input. Its
  # final rejection is authenticated separately in the parent's recovery record.
  if rejection!=recovery['initial_rejection'] or hashlib.sha256((Path(run_dir)/f'agents/{job}/workspace/candidate.json').read_bytes()).hexdigest()!=recovery[key]['artifact_sha256']:raise ValueError('Failure rejection/artifact changed')
 _verify_request(protocol_request)
 import fcntl
 lock=directory/(ledger_name+'.recovery.lock')
 if lock.exists() or lock.is_symlink():checked_regular_file(lock)
 with lock.open('a+') as handle:
  fcntl.flock(handle,fcntl.LOCK_EX)
  if ledger_path.read_bytes()!=raw:raise ValueError('Concurrent admission spending changed')
  snapshot=ledger_name+'.before-protocol-recovery.json'
  if (directory/snapshot).exists() or (directory/snapshot).is_symlink():raise ValueError('Recovery snapshot already exists')
  atomic_write_managed(directory,snapshot,raw)
  request_file=ledger_name+'.protocol-recovery-request.json'
  if (directory/request_file).exists() or (directory/request_file).is_symlink():raise ValueError('Protocol request snapshot already exists')
  atomic_write_managed(directory,request_file,(json.dumps(protocol_request,ensure_ascii=False,indent=2)+'\n').encode())
  receipt={'version':1,'protocol_request_file':request_file,'prior_ledger_sha256':prior_ledger_sha256,'prior_snapshot':snapshot,'prior_primary_roles':1,'prior_executions':2,'additional_primary_roles':2,'additional_max_executions':4,'total_primary_limit':3,'total_max_executions':6,'failure_files':failures,'protocol_request_digest':hashlib.sha256(json.dumps(protocol_request,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'source_position':position}
  updated=copy.deepcopy(ledger);updated['limit']=3;updated['protocol_recovery']=receipt
  atomic_write_managed(directory,ledger_name,(json.dumps(updated,ensure_ascii=False,indent=2)+'\n').encode())
 return receipt

def _verify_request(request):
 from pipeline.lexical_request_reconciliation import reconciliation_request
 validation=request['workspace_context']['lexical_request_reconciliation_validation']
 if validation['mode']!='repair' or reconciliation_request(validation['inputs'],'repair',origin_run_dir=validation['origin_run_dir'])!=request:raise ValueError('Protocol request is not canonical reconstruction')

def verify_protocol_recovery(run_dir, ledger_name, *, protocol_request=None):
 directory=checked_directory(Path(run_dir).absolute()/'annotation-admission');ledger=json.loads(checked_regular_file(directory/ledger_name).read_text());receipt=ledger.get('protocol_recovery')
 if not isinstance(receipt,dict) or type(receipt.get('version')) is not int or receipt['version']!=1:raise ValueError('Invalid protocol recovery receipt')
 snapshot=receipt.get('prior_snapshot','')
 if Path(snapshot).name!=snapshot:raise ValueError('Unsafe recovery snapshot')
 raw=checked_regular_file(directory/snapshot).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=receipt['prior_ledger_sha256']:raise ValueError('Prior recovery snapshot changed')
 prior=json.loads(raw)
 if prior.get('protocol_recovery') is not None or prior['limit']!=2 or len(prior['requests'])!=1:raise ValueError('Recovery history reset')
 if ledger['scope']!=prior['scope'] or ledger['contract']!=prior['contract'] or ledger['requests'][:1]!=prior['requests']:raise ValueError('Recovery original reservations changed')
 if ledger['limit']!=3 or len(ledger['requests'])>3 or any(type(receipt.get(k)) is not int or receipt[k]!=v for k,v in [('prior_primary_roles',1),('prior_executions',2),('additional_primary_roles',2),('additional_max_executions',4),('total_primary_limit',3),('total_max_executions',6)]):raise ValueError('Recovery bounds changed')
 if receipt['source_position'] not in prior['scope']['source_positions'].values():raise ValueError('Recovery position changed')
 request_file=receipt.get('protocol_request_file','')
 if Path(request_file).name!=request_file:raise ValueError('Unsafe protocol request snapshot')
 request=json.loads(checked_regular_file(directory/request_file).read_text())
 expected_request=hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 if expected_request!=receipt['protocol_request_digest']:raise ValueError('Protocol request digest changed')
 _verify_request(request)
 if protocol_request is not None and protocol_request!=request:raise ValueError('Recovery caller request changed')
 ctx=request['workspace_context'];common=copy.deepcopy(ctx);common.pop('reconciliation_patch_base_policy_version',None);common.pop('reconciliation_repair_expected_base_digest',None)
 common.get('lexical_request_reconciliation_validation',{}).get('inputs',{}).pop('reconciliation_patch_base_policy_version',None)
 if common!=prior['requests'][0]['request']['workspace_context']:raise ValueError('Recovery request context changed beyond tested protocol')
 if type(ctx.get('reconciliation_patch_base_policy_version')) is not int or ctx['reconciliation_patch_base_policy_version']!=1 or request['schema']['properties']['base_digest'].get('const')!=ctx.get('reconciliation_repair_expected_base_digest') or ctx.get('reconciliation_repair_expected_base_digest')!=ctx['reconciliation_repair']['result_digest']:raise ValueError('Recovery explicit base contract changed')
 parent_job=prior['requests'][0]['job'];parent_rel=f'agents/{parent_job}/meta.json'
 if parent_rel not in receipt['failure_files']:raise ValueError('Incomplete replay failure files')
 parent=json.loads(checked_regular_file(Path(run_dir).absolute()/parent_rel).read_text());child=parent.get('submission_repair',{}).get('job','')
 required={parent_rel,f'agents/{parent_job}/workspace/candidate.json',f'agents/{parent_job}/workspace/submission-rejection.json',f'agents/{parent_job}/submission-recovery.json',f'agents/{child}/meta.json',f'agents/{child}/workspace/candidate.json',f'agents/{child}/workspace/submission-rejection.json'}
 if not required<=set(receipt['failure_files']):raise ValueError('Incomplete replay failure files')
 for rel,expected in receipt['failure_files'].items():
  p=Path(rel)
  if p.is_absolute() or '..' in p.parts or hashlib.sha256(checked_regular_file(Path(run_dir).absolute()/p).read_bytes()).hexdigest()!=expected:raise ValueError('Recovery failure evidence changed')
 return receipt
