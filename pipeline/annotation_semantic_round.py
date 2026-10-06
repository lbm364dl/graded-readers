"""One explicit same-ledger semantic continuation; no namespace or spending reset."""
import copy,hashlib,json,fcntl
from pathlib import Path
from pipeline.worker_paths import checked_directory,checked_regular_file,atomic_write_managed

def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _directory(run_dir,name):
 if Path(name).name!=name or not name.endswith('.json'):raise ValueError('Unsafe semantic ledger')
 return checked_directory(Path(run_dir).absolute()/'annotation-admission')

def register_semantic_round(run_dir,*,ledger_name,prior_ledger_sha256,next_request,prior_executions=4):
 from pipeline.annotation_admission_recovery import verify_protocol_recovery,_verify_request
 from pipeline.lexical_reconciliation_round import authenticate_parent,round_marker,_parent
 from pipeline.lexical_request_reconciliation import digest
 from pipeline.agent_harness import CodexRunner
 directory=_directory(run_dir,ledger_name);path=checked_regular_file(directory/ledger_name);raw=path.read_bytes();prior=json.loads(raw)
 if _sha(raw)!=prior_ledger_sha256 or prior.get('semantic_round') is not None:raise ValueError('Semantic prior spending changed')
 if prior['scope']['phase']!='reconciliation-repair' or prior['limit']!=3 or len(prior['requests'])!=3 or type(prior_executions) is not int or prior_executions!=4:raise ValueError('Unexpected semantic prior bound')
 protocol=verify_protocol_recovery(run_dir,ledger_name);_verify_request(next_request)
 validation=next_request['workspace_context']['lexical_request_reconciliation_validation'];inputs=validation['inputs']
 if not round_marker(inputs):raise ValueError('Semantic continuation requires typed derived parent')
 bound=authenticate_parent(validation['origin_run_dir'],inputs);lane,old,e=_parent(inputs['reconciliation_repair'])
 if [r['job'] for r in prior['requests'][1:]]!=[e['repair_job'],e['review_job']]:raise ValueError('Foreign semantic parent jobs')
 if inputs['source_position'] not in prior['scope']['source_positions'].values():raise ValueError('Semantic source position changed')
 if next_request['job'] in {r['job'] for r in prior['requests']}:raise ValueError('Unchanged semantic request')
 count=protocol['prior_executions']
 parent_meta={}
 for job in (e['repair_job'],e['review_job']):
  root=lane/'agents'/job;meta=json.loads(checked_regular_file(root/'meta.json').read_text());CodexRunner._check_tool_profile(root,'workspace',meta)
  if meta.get('return_code')!=0 or type(meta.get('attempt_count')) is not int or meta['attempt_count']!=1 or len(meta.get('attempts',[]))!=1:raise ValueError('Unexpected semantic execution history')
  count+=1
  if meta.get('submission_repair') is not None:raise ValueError('Semantic prior execution count requires explicit additional history')
  parent_meta[f'{job}/meta.json']=_sha(checked_regular_file(root/'meta.json').read_bytes())
 if count!=prior_executions:raise ValueError('Semantic actual spending changed')
 targets=sorted({i['request_index'] for i in bound['review']['issues']})
 receipt={'version':1,'prior_snapshot':ledger_name+'.before-semantic-round.json','prior_ledger_sha256':_sha(raw),'next_request_file':ledger_name+'.semantic-round-request.json','next_request_digest':digest(next_request),'parent':copy.deepcopy(inputs['reconciliation_repair']),'targets':targets,'source_position':copy.deepcopy(inputs['source_position']),'parent_execution_metadata':parent_meta,'additional_runtime_policy':{'model':'gpt-6-luna','effort':'low','tool_profile':'workspace','retries':0,'submission_corrections':0},'prior_primary_roles':3,'prior_executions':4,'additional_primary_roles':2,'additional_max_executions':4,'total_primary_limit':5,'total_max_executions':8}
 lock=directory/(ledger_name+'.semantic.lock')
 if lock.exists() or lock.is_symlink():checked_regular_file(lock)
 with lock.open('a+') as handle:
  fcntl.flock(handle,fcntl.LOCK_EX)
  if path.read_bytes()!=raw:raise ValueError('Concurrent semantic spending changed')
  for name in (receipt['prior_snapshot'],receipt['next_request_file']):
   if (directory/name).exists() or (directory/name).is_symlink():raise ValueError('Semantic extension already exists')
  atomic_write_managed(directory,receipt['prior_snapshot'],raw)
  atomic_write_managed(directory,receipt['next_request_file'],(json.dumps(next_request,ensure_ascii=False,indent=2)+'\n').encode())
  new=copy.deepcopy(prior);new['limit']=5;new['semantic_round']=receipt
  atomic_write_managed(directory,ledger_name,(json.dumps(new,ensure_ascii=False,indent=2)+'\n').encode())
 return receipt

def verified_semantic_prior(run_dir,ledger_name,ledger,*,next_request=None):
 from pipeline.annotation_admission_recovery import _verify_request
 from pipeline.lexical_request_reconciliation import digest
 from pipeline.lexical_reconciliation_round import _parent,round_marker
 directory=_directory(run_dir,ledger_name);receipt=ledger.get('semantic_round')
 if not isinstance(receipt,dict) or type(receipt.get('version')) is not int or receipt['version']!=1:raise ValueError('Invalid semantic extension')
 for key in ('prior_snapshot','next_request_file'):
  if Path(receipt[key]).name!=receipt[key]:raise ValueError('Unsafe semantic snapshot')
 raw=checked_regular_file(directory/receipt['prior_snapshot']).read_bytes()
 if _sha(raw)!=receipt['prior_ledger_sha256']:raise ValueError('Semantic prior snapshot changed')
 prior=json.loads(raw)
 if prior.get('semantic_round') is not None or prior['limit']!=3 or len(prior['requests'])!=3 or ledger['limit']!=5 or len(ledger['requests'])>5 or ledger['requests'][:3]!=prior['requests'] or ledger['scope']!=prior['scope'] or ledger['contract']!=prior['contract'] or ledger['protocol_recovery']!=prior['protocol_recovery']:raise ValueError('Semantic original spending changed')
 for key,value in [('prior_primary_roles',3),('prior_executions',4),('additional_primary_roles',2),('additional_max_executions',4),('total_primary_limit',5),('total_max_executions',8)]:
  if type(receipt.get(key)) is not int or receipt[key]!=value:raise ValueError('Semantic bounds changed')
 request=json.loads(checked_regular_file(directory/receipt['next_request_file']).read_text());_verify_request(request)
 if digest(request)!=receipt['next_request_digest'] or (next_request is not None and next_request!=request):raise ValueError('Semantic caller request changed')
 inputs=request['workspace_context']['lexical_request_reconciliation_validation']['inputs']
 if not round_marker(inputs) or inputs['reconciliation_repair']!=receipt['parent'] or inputs['source_position']!=receipt['source_position'] or inputs['source_position'] not in prior['scope']['source_positions'].values():raise ValueError('Semantic parent/source changed')
 lane,old,e=_parent(receipt['parent'])
 from pipeline.lexical_reconciliation_round import authenticate_parent
 origin=request['workspace_context']['lexical_request_reconciliation_validation']['origin_run_dir']
 authenticate_parent(origin,inputs)
 metadata=receipt.get('parent_execution_metadata')
 if not isinstance(metadata,dict) or set(metadata)!={f'{job}/meta.json' for job in (e['repair_job'],e['review_job'])}:raise ValueError('Semantic parent execution inventory changed')
 from pipeline.agent_harness import CodexRunner
 for relative,expected_sha in metadata.items():
  path=checked_regular_file(lane/'agents'/relative);raw_meta=path.read_bytes();meta=json.loads(raw_meta)
  CodexRunner._check_tool_profile(path.parent, 'workspace', meta)
  if _sha(raw_meta)!=expected_sha or meta.get('return_code')!=0 or meta.get('model')!='gpt-6-luna' or meta.get('effort')!='low' or meta.get('tool_profile')!='workspace' or type(meta.get('attempt_count')) is not int or meta['attempt_count']!=1 or len(meta.get('attempts',[]))!=1 or meta.get('submission_repair') is not None:raise ValueError('Semantic parent execution history changed')
 if receipt.get('additional_runtime_policy')!={'model':'gpt-6-luna','effort':'low','tool_profile':'workspace','retries':0,'submission_corrections':0}:raise ValueError('Semantic runtime policy changed')
 if {k:v for k,v in inputs.items() if k not in ('reconciliation_repair','reconciliation_repair_round_version')}!={k:v for k,v in old.items() if k!='reconciliation_repair'}:raise ValueError('Semantic immutable context changed')
 if [r['job'] for r in prior['requests'][1:]]!=[e['repair_job'],e['review_job']] or e['review']['approved'] or sorted({i['request_index'] for i in e['review']['issues']})!=receipt['targets']:raise ValueError('Semantic target/parent authority changed')
 return prior

def verify_semantic_round(run_dir,ledger_name,*,next_request=None):
 directory=_directory(run_dir,ledger_name);ledger=json.loads(checked_regular_file(directory/ledger_name).read_text());prior=verified_semantic_prior(run_dir,ledger_name,ledger,next_request=next_request)
 from pipeline.annotation_admission_recovery import _verify_protocol_recovery_core
 _verify_protocol_recovery_core(run_dir,ledger_name,prior)
 return ledger['semantic_round']
