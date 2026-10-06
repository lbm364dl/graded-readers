"""One typed derived repair parent; historical original contracts remain separate."""
import copy,hashlib,json
from pathlib import Path
from pipeline.worker_paths import checked_directory,checked_regular_file

def _parent(descriptor):
 from pipeline.agent_harness import ROOT
 from pipeline.lexical_request_reconciliation import digest
 keys={'parent_kind','worker_run_relpath','inputs_sha256','evidence_sha256','result_digest','review_digest','source_position','target_bindings'}
 if not isinstance(descriptor,dict) or set(descriptor)!=keys or descriptor['parent_kind']!='repaired_reconciliation':raise ValueError('Invalid typed repair parent')
 rel=Path(descriptor['worker_run_relpath'])
 if rel.is_absolute() or '..' in rel.parts:raise ValueError('Foreign derived repair lane')
 lane=checked_directory(Path(ROOT)/rel)
 def read(name,sha):
  raw=checked_regular_file(lane/name).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Derived parent artifact changed')
  return json.loads(raw)
 old=read('immutable-inputs.json',descriptor['inputs_sha256']);evidence=read('evidence.json',descriptor['evidence_sha256'])
 if type(evidence.get('reconciliation_receipt_version')) is not int or evidence['reconciliation_receipt_version']!=2 or 'reconciliation_repair_round_version' in old:raise ValueError('Only one semantic continuation permitted')
 if evidence.get('inputs_digest')!=digest(old) or digest(evidence['result'])!=descriptor['result_digest'] or digest(evidence['review'])!=descriptor['review_digest']:raise ValueError('Derived parent binding changed')
 if descriptor['source_position']!=old['source_position'] or descriptor['target_bindings']!=_target_bindings(old,evidence):raise ValueError('Derived parent source interval binding changed')
 return lane,old,evidence

def _target_bindings(inputs,evidence):
 """Bind every actionable request to its exact occurrence intervals."""
 bindings=[];source_start=inputs['source_position']['source_start']
 for issue in evidence['review']['issues']:
  index=issue['request_index'];decision=evidence['result']['decisions'][index]
  occurrences=[]
  for row in decision['occurrences']:
   occurrences.append({'start':row['start'],'end':row['end'],'text':row['text'],
    'absolute_start':source_start+row['start'],'absolute_end':source_start+row['end']})
  bindings.append({'request_index':index,'occurrences':occurrences})
 return bindings

def round_marker(inputs):
 if 'reconciliation_repair_round_version' not in inputs:return False
 value=inputs['reconciliation_repair_round_version']
 if type(value) is not int or value!=1:raise ValueError('Unknown derived repair round')
 return True

def parent_task(inputs):
 from pipeline.lexical_request_reconciliation import digest
 round_marker(inputs);_,old,e=_parent(inputs['reconciliation_repair'])
 if e['review']['approved'] or not e['review']['issues']:raise ValueError('Derived parent lacks independent rejection')
 return {'reconciliation_repair_base':e['result'],'reconciliation_repair_review':e['review'],'reconciliation_repair_targets':sorted({i['request_index'] for i in e['review']['issues']}),'reconciliation_repair_target_bindings':_target_bindings(old,e),'reconciliation_repair_expected_base_digest':digest(e['result'])}

def authenticate_parent(origin_run_dir,inputs):
 from pipeline.lexical_request_reconciliation import verify_reconciliation_repair_jobs,digest,check_review
 if not round_marker(inputs):raise ValueError('Missing derived round marker')
 lane,old,e=_parent(inputs['reconciliation_repair'])
 if {k:v for k,v in inputs.items() if k not in ('reconciliation_repair','reconciliation_repair_round_version')}!={k:v for k,v in old.items() if k!='reconciliation_repair'}:raise ValueError('Derived repair source/context changed')
 bound=verify_reconciliation_repair_jobs(lane,old,origin_run_dir=origin_run_dir,repair_job=e['repair_job'],review_job=e['review_job'])
 if any(e[k]!=bound[k] for k in ('result','review','patch')):raise ValueError('Derived parent evidence does not replay')
 check_review(bound['review'],bound['result'],old)
 if bound['review']['approved'] or not bound['review']['issues']:raise ValueError('Derived parent lacks independent defect authority')
 return bound

def make_derived_repair_inputs(worker_run_dir,*,origin_run_dir):
 from pipeline.agent_harness import ROOT
 from pipeline.lexical_request_reconciliation import digest,authenticate_archived_sources
 lane=checked_directory(Path(worker_run_dir).absolute());raw=checked_regular_file(lane/'immutable-inputs.json').read_bytes();eraw=checked_regular_file(lane/'evidence.json').read_bytes();old=json.loads(raw);e=json.loads(eraw)
 try:rel=lane.relative_to(Path(ROOT))
 except ValueError as exc:raise ValueError('Derived lane outside repository') from exc
 result={**old,'reconciliation_repair_round_version':1,'reconciliation_repair':{'parent_kind':'repaired_reconciliation','worker_run_relpath':str(rel),'inputs_sha256':hashlib.sha256(raw).hexdigest(),'evidence_sha256':hashlib.sha256(eraw).hexdigest(),'result_digest':digest(e['result']),'review_digest':digest(e['review']),'source_position':copy.deepcopy(old['source_position']),'target_bindings':_target_bindings(old,e)}}
 authenticate_parent(origin_run_dir,result);authenticate_archived_sources(result,require_current=True)
 return result
