"""Explicit version2 objection accounting; host validates provenance, not semantics."""
import copy
import json
import contextvars
AUTH_CHAIN=contextvars.ContextVar("dictionary_accountability_auth_chain",default=())
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from jsonschema import validate
from pipeline.annotation_adjudication import digest
from pipeline.annotation_review_ledger import resolve_pointer
FIELD='dictionary_prior_objection_accountability'
GUIDANCE='''Account independently for every retained prior objection in the exact roster. Do not omit a finding because another critic approved. A resolved_change needs an actual authorized changed semantic leaf and a concrete rationale connecting that change to the objection. A supported_retention needs concrete current proposal spans and cited approved linguistic evidence or an applicable explicit editorial policy explaining why retention is valid. An unresolved objection needs a corresponding current issue and forces rejection. A changed scalar alone does not establish semantic resolution; evidence coverage alone does not justify teaching another lesson. Diagnostic paths do not authorize edits. Keep the learner explanation separate from this assessment artifact.'''
def marker():return {'version':2,'policy_digest':digest(GUIDANCE)}
def roster(sources):
 """Sources are exact authenticated caller-provided review/proposal pairs."""
 rows=[]
 for source in sources:
  if set(source)!={'run_relpath','job','review','proposal','result_digest'}:raise ValueError('Unknown prior review source fields')
  if not source['run_relpath'] or source['run_relpath'].startswith('/') or '..' in source['run_relpath'].split('/'):raise ValueError('Invalid prior review origin')
  if source['result_digest']!=digest(source['review']):raise ValueError('Prior review result digest changed')
  issues=source['review'].get('issues')
  if not isinstance(issues,list) or any(not isinstance(s,str) or not s for s in issues):raise ValueError('Prior issue roster requires exact string findings')
  for index,text in enumerate(issues):
   origin={k:source[k] for k in ('run_relpath','job','result_digest')};origin.update(issue_index=index,issue_text=text)
   rows.append({'issue_id':'prior-issue-'+digest(origin)[:24],**origin,'proposal_digest':digest(source['proposal']),'original_proposal':copy.deepcopy(source['proposal'])})
 if len({r['issue_id'] for r in rows})!=len(rows):raise ValueError('Duplicate exact prior issue origin')
 return rows

def review_schema(rows):
 nonempty={'type':'string','minLength':1};pointer={'type':'string','pattern':'^/','minLength':2}
 span={'type':'object','additionalProperties':False,'required':['path','start','end','quote'],'properties':{'path':pointer,'start':{'type':'integer','minimum':0},'end':{'type':'integer','minimum':1},'quote':nonempty}}
 changed={'type':'object','additionalProperties':False,'required':['path','old_value_digest','current_value_digest'],'properties':{'path':pointer,'old_value_digest':nonempty,'current_value_digest':nonempty}}
 citation={'type':'object','additionalProperties':False,'required':['reference_id','path'],'properties':{'reference_id':nonempty,'path':pointer}}
 assessment={'type':'object','additionalProperties':False,'required':['issue_id','disposition','proposal_paths','proposal_spans','rationale'],'properties':{'issue_id':{'enum':[r['issue_id'] for r in rows]} if rows else {'not':{}},'disposition':{'enum':['resolved_change','supported_retention','unresolved']},'proposal_paths':{'type':'array','minItems':1,'uniqueItems':True,'items':pointer},'proposal_spans':{'type':'array','minItems':1,'items':span},'rationale':nonempty,'changed_fields':{'type':'array','minItems':1,'items':changed},'evidence_refs':{'type':'array','minItems':1,'items':citation},'current_issue_index':{'type':'integer','minimum':0}},'allOf':[{'if':{'properties':{'disposition':{'const':'resolved_change'}}},'then':{'required':['changed_fields']}},{'if':{'properties':{'disposition':{'const':'supported_retention'}}},'then':{'required':['evidence_refs']}},{'if':{'properties':{'disposition':{'const':'unresolved'}}},'then':{'required':['current_issue_index']}}]}
 return {'type':'object','additionalProperties':False,'required':['approved','issues','prior_issue_assessments'],'properties':{'approved':{'type':'boolean'},'issues':{'type':'array','items':nonempty},'prior_issue_assessments':{'type':'array','minItems':len(rows),'maxItems':len(rows),'items':assessment}}}

def validate_assessments(value,*,rows,proposal,semantic_paths,identity_paths,references,evidence_paths):
 validate(value,review_schema(rows));by_id={r['issue_id']:r for r in rows};seen=set();unresolved=False
 for assessment in value['prior_issue_assessments']:
  identity=assessment['issue_id']
  if identity in seen:raise ValueError('Duplicate prior issue assessment')
  seen.add(identity);paths=assessment['proposal_paths']
  if not set(paths)<=set(semantic_paths):raise ValueError('Assessment paths outside exact authorized entry semantic scope')
  for path in paths:
   if not isinstance(resolve_pointer(proposal,path),str):raise ValueError('Assessment must identify a semantic string leaf')
  if set(identity_paths)!=set(semantic_paths):raise ValueError('Exact semantic owner identity mapping required')
  for path in paths:
   owner_paths=identity_paths[path]
   if isinstance(owner_paths,str):owner_paths=[owner_paths]
   if not isinstance(owner_paths,list) or not owner_paths:raise ValueError('Missing exact identity pointers')
   for identity_path in owner_paths:
    if resolve_pointer(proposal,identity_path)!=resolve_pointer(by_id[identity]['original_proposal'],identity_path):raise ValueError('Foreign reviewed entry identity')
  span_paths=set()
  for span in assessment['proposal_spans']:
   if span['path'] not in paths:raise ValueError('Span outside diagnostic path')
   text=resolve_pointer(proposal,span['path'])
   if span['end']<=span['start'] or span['end']>len(text) or text[span['start']:span['end']]!=span['quote']:raise ValueError('Current proposal span does not match exact text')
   span_paths.add(span['path'])
  if span_paths!=set(paths):raise ValueError('Every diagnostic path needs an exact current span')
  disposition=assessment['disposition']
  if disposition=='resolved_change':
   for field in assessment['changed_fields']:
    path=field['path']
    if path not in paths:raise ValueError('Changed field not among diagnostic paths')
    old=resolve_pointer(by_id[identity]['original_proposal'],path);current=resolve_pointer(proposal,path)
    if old==current or field['old_value_digest']!=digest(old) or field['current_value_digest']!=digest(current):raise ValueError('No authenticated actual semantic change')
  elif disposition=='supported_retention':
   for citation in assessment['evidence_refs']:
    reference=references.get(citation['reference_id'])
    if not isinstance(reference,dict) or reference.get('kind') not in {'approved_lesson','primary_source','explicit_review_policy','independently_reviewed_claim'}:raise ValueError('Unsupported retention citation')
    if citation['path'] not in evidence_paths.get(citation['reference_id'],[]):raise ValueError('Citation outside authenticated substantive evidence scope')
    resolved=resolve_pointer(reference['content'],citation['path'])
    if not isinstance(resolved,str) or not resolved.strip():raise ValueError('Retention requires substantive cited text')
  else:
   unresolved=True
   if assessment['current_issue_index']>=len(value['issues']):raise ValueError('Unresolved objection must link a current issue')
 if seen!=set(by_id):raise ValueError('Missing retained prior objection')
 if value['approved'] is not (not value['issues'] and not unresolved):raise ValueError('Clean approval cannot waive unresolved findings')
 return value

def correction_schema(existing_schema,rows):
 """JA complete-data correction wrapper: real artifact plus explicit assessment."""
 return {'type':'object','additionalProperties':False,'required':['artifact','review'],'properties':{'artifact':copy.deepcopy(existing_schema),'review':review_schema(rows)}}


def validate_marker(value):
 if not isinstance(value,dict) or type(value.get('version')) is not int or value!=marker():raise ValueError('Unknown objection accountability policy')
 return value

def _contains_proposal(context,proposal):
 if not isinstance(context,dict):return False
 packet=context.get('dictionary_objection_review')
 if isinstance(packet,dict):
  nested=context.get('dictionary_research_revision')
  if isinstance(nested,dict) and (nested.get('proposal')!=proposal or nested.get('dictionary_objection_review')!=packet):return False
  return packet.get('proposal')==proposal
 # Only explicit retained dictionary critic request, never ancestor search.
 nested=context.get('dictionary_research_revision')
 if not isinstance(nested,dict):return False
 gate=nested.get('dictionary_research_revision_validation')
 return isinstance(gate,dict) and type(gate.get('version')) is int and gate.get('version')==1 and gate.get('stage')=='critic' and nested.get('proposal')==proposal and gate.get('handoff_digest')==nested.get('dictionary_research_handoff',{}).get('digest')

from pipeline.receipt_replay_session import invocation,memoized_provenance

@memoized_provenance
def authenticated_editorial_archive(origin):
 from pipeline import dictionary_research_revision as revision
 from pipeline.worker_paths import checked_regular_file
 if not isinstance(origin,dict) or set(origin)!={'run_relpath','evidence_sha256'}:raise ValueError('Unknown editorial archive origin')
 rel=origin['run_relpath']
 if not isinstance(rel,str) or not rel or Path(rel).is_absolute() or '..' in Path(rel).parts:raise ValueError('Foreign archive root')
 root=ROOT.resolve();run=(root/rel).resolve()
 if not run.is_relative_to(root):raise ValueError('Foreign archive path')
 path=checked_regular_file(run/'research-bound-revision-evidence.json')
 import hashlib
 raw=path.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=origin['evidence_sha256']:raise ValueError('Archived evidence bytes changed')
 evidence=json.loads(raw);revision.replay(run,evidence)
 # Only semantic artifacts and exact authenticated roles leave this provider;
 # full archived contexts remain at their original immutable origin.
 rows=evidence['contracts'];projection=[]
 for index,row in enumerate(rows):
  projection.append({'job':row['job'],'result':copy.deepcopy(row['result']),'is_critic':bool(index%2),'proposal':copy.deepcopy(rows[index-1]['result']) if index%2 else None})
 packet=evidence['handoff']
 return {'contracts':projection,'handoff_digest':packet['digest'],'known_references':copy.deepcopy(packet['known_references']),'approved_claims':copy.deepcopy(packet['approved_claims'])}

def _archive_contract(value):
 if not isinstance(value,dict) or set(value)!={'provider','origin','contract_index'} or value['provider']!='authenticated-dictionary-editorial-archive':raise ValueError('Unknown compact worker origin')
 index=value['contract_index']
 if type(index) is not int or index<0:raise ValueError('Invalid archived contract index')
 archive=authenticated_editorial_archive(value['origin'])
 if index>=len(archive['contracts']):raise ValueError('Archived contract absent')
 return archive['contracts'][index]

def _authenticate_source_contract(contract):
 from pipeline.annotation_research import _verify_job
 from pipeline.worker_paths import checked_directory
 if not isinstance(contract,dict) or set(contract)!={'source','writer','critic'}:raise ValueError('Unknown source contract')
 source=contract['source'];roster([source])
 root=ROOT.resolve();run=checked_directory(root / source['run_relpath'])
 if not run.is_relative_to(root):raise ValueError('Foreign source root')
 critic=contract['critic']
 critic_verified=isinstance(critic,dict) and critic.get('provider')==WORKER_PROVIDER
 if isinstance(critic,dict) and critic.get('provider')==WORKER_PROVIDER:
  if critic.get('run_relpath')!=source['run_relpath']:raise ValueError('Foreign critic origin')
  critic=resolve_verified_worker_contract(critic)
  version=critic['workspace_context'].get('dictionary_objection_source_contract_version')
  if type(version) is not int or version!=3:raise ValueError('Compact current source requires explicit version3')
 if isinstance(critic,dict) and critic.get('provider')=='authenticated-dictionary-editorial-archive':
  writer=contract['writer']
  if not isinstance(writer,dict) or writer.get('origin')!=critic.get('origin') or writer.get('contract_index')!=critic.get('contract_index',0)-1:raise ValueError('Archived paired producer differs')
  if source['run_relpath']!=critic['origin']['run_relpath']:raise ValueError('Foreign archived source run')
  c=_archive_contract(critic);w=_archive_contract(writer)
  if not c['is_critic'] or w['is_critic'] or c['job']!=source['job'] or c['result']!=source['review'] or c['proposal']!=source['proposal'] or w['result']!=source['proposal']:raise ValueError('Archived worker pairing/proposal differs')
  return copy.deepcopy(source)
 def verify(row):
  if not isinstance(row,dict) or set(row)!={'job','prompt','schema_text','workspace_context','result'}:raise ValueError('Invalid worker source contract')
  _verify_job(run,job=row['job'],expected_prompt=row['prompt'],schema_text=row['schema_text'],workspace_context=row['workspace_context'],expected_result=row['result'])
 if not critic_verified:verify(critic)
 if critic['job']!=source['job'] or critic['result']!=source['review'] or not _contains_proposal(critic['workspace_context'],source['proposal']):raise ValueError('Critic does not bind exact proposal')
 packet=critic['workspace_context'].get('dictionary_objection_review')
 if packet is not None:
  if json.loads(critic['schema_text'])!=review_schema(packet['rows']):raise ValueError('Foreign accountability critic schema')
  validate_submission(source['review'],packet)
 else:
  from pipeline.korean_contracts import REVIEW
  if json.loads(critic['schema_text'])!=REVIEW:raise ValueError('Foreign retained critic schema')
 writer=contract['writer']
 writer_binding=writer
 writer_verified=isinstance(writer,dict) and writer.get('provider')==WORKER_PROVIDER
 if isinstance(writer,dict) and writer.get('provider')==WORKER_PROVIDER:
  if writer.get('run_relpath')!=source['run_relpath']:raise ValueError('Foreign producer origin')
  writer=resolve_verified_worker_contract(writer)
 if packet is not None:
  bound=critic['workspace_context'].get('dictionary_complete_correction_producer')
  if writer is None and bound is not None:raise ValueError('Actual producer omitted from source contract')
  if writer is not None and bound!={'contract_digest':digest(writer_binding)}:raise ValueError('Actual producer differs from authenticated critic binding')
 elif writer is None:raise ValueError('Retained K critic requires actual paired writer')
 if writer is not None:
  if writer.get('job')==critic['job']:raise ValueError('Writer is not independent critic')
  if not writer_verified:verify(writer)
  if writer['result']!=source['proposal']:raise ValueError('Paired writer proposal differs')
 return copy.deepcopy(source)

def authenticate_source_contract(contract):
 identity=digest(contract);chain=AUTH_CHAIN.get()
 if identity in chain or len(chain)>=32:raise ValueError('Cyclic or excessive accountability receipt ancestry')
 token=AUTH_CHAIN.set(chain+(identity,))
 try:return _authenticate_source_contract(contract)
 finally:AUTH_CHAIN.reset(token)

def _references(descriptors):
 from pipeline.dictionary_editorial_criteria import CRITERIA
 refs={};paths={}
 for descriptor in descriptors:
  if descriptor=={'provider':'shared-dictionary-editorial-criteria','version':1} and type(descriptor.get('version')) is int:
   key='shared-dictionary-editorial-criteria-v1'
   refs[key]={'kind':'explicit_review_policy','content':{'criteria':CRITERIA}};paths[key]=['/criteria']
  elif isinstance(descriptor,dict) and descriptor.get('provider')=='reviewed-dictionary-research-handoff' and set(descriptor) in ({'provider','packet'},{'provider','origin','handoff_digest'}):
   from pipeline.dictionary_research_handoff import verify_handoff
   if 'packet' in descriptor:
    packet=descriptor['packet'];verify_handoff(packet,historical=True)
   else:
    archive=authenticated_editorial_archive(descriptor['origin'])
    if archive['handoff_digest']!=descriptor['handoff_digest']:raise ValueError('Foreign archived handoff reference')
    packet=archive
   for key,reference in packet['known_references'].items():
    content=reference.get('content');kind=reference.get('kind')
    if not isinstance(content,dict):continue
    allowed=('/pattern','/explanation_en','/definition_en') if kind=='approved_lesson' else ('/captured_text',) if kind=='primary_source' else ()
    admissible=[path for path in allowed if isinstance(content.get(path[1:]),str) and content[path[1:]].strip()]
    if admissible:
     if key in refs and refs[key]!=reference:raise ValueError('Conflicting authenticated reference')
     refs[key]=copy.deepcopy(reference);paths[key]=admissible
   for finding in packet['approved_claims']['findings']:
    if finding.get('status')!='supported':continue
    key='reviewed-claim-'+digest(finding)
    refs[key]={'kind':'independently_reviewed_claim','content':copy.deepcopy(finding)};paths[key]=['/fact']
  else:raise ValueError('Unknown authenticated reference provider')
 return refs,paths

@invocation
def prepare_packet(*,source_contracts,proposal,semantic_paths,identity_paths,reference_descriptors):
 sources=[authenticate_source_contract(c) for c in source_contracts]
 refs,paths=_references(reference_descriptors)
 if len(set(semantic_paths))!=len(semantic_paths) or set(identity_paths)!=set(semantic_paths):raise ValueError('Invalid semantic scope')
 for path in semantic_paths:
  if not isinstance(resolve_pointer(proposal,path),str):raise ValueError('Semantic scope must be string leaves')
 return {'marker':marker(),'source_contracts':copy.deepcopy(source_contracts),'rows':roster(sources),'proposal':copy.deepcopy(proposal),'semantic_paths':copy.deepcopy(semantic_paths),'identity_paths':copy.deepcopy(identity_paths),'reference_descriptors':copy.deepcopy(reference_descriptors),'references':refs,'evidence_paths':paths}

def validate_submission(value,packet):
 validate_marker(packet.get('marker'))
 expected=prepare_packet(**{k:packet[k] for k in ('source_contracts','proposal','semantic_paths','identity_paths','reference_descriptors')})
 if packet!=expected:raise ValueError('Accountability packet differs from authenticated reconstruction')
 return validate_assessments(value,**{k:packet[k] for k in ('rows','proposal','semantic_paths','identity_paths','references','evidence_paths')})

def validate_caller_evidence(contract,*,expected_proposal,expected_review):
 source=authenticate_source_contract(contract)
 if source['proposal']!=expected_proposal or source['review']!=expected_review:raise ValueError('Final caller evidence differs')
 critic=contract['critic']
 if isinstance(critic,dict) and critic.get('provider')==WORKER_PROVIDER:critic=resolve_verified_worker_contract(critic)
 context=critic['workspace_context']
 packet=context.get('dictionary_objection_review')
 if not isinstance(packet,dict):raise ValueError('Missing final accountability packet')
 return validate_submission(expected_review,packet)

WORKER_PROVIDER='verified-dictionary-worker-contract'
WORKER_KEYS={'job','prompt','schema_text','workspace_context','result'}

def persist_verified_worker_contract(run_dir,contract):
 """Persist exact replayed receipt inputs, never a verdict shortcut."""
 import hashlib
 from pipeline.annotation_research import _verify_job
 from pipeline.worker_paths import checked_directory,checked_regular_file,atomic_write_managed
 if not isinstance(contract,dict) or set(contract)!=WORKER_KEYS:raise ValueError('Invalid worker contract')
 root=ROOT.resolve();run=checked_directory(Path(run_dir).absolute())
 if not run.is_relative_to(root):raise ValueError('Foreign worker root')
 meta,result=_verify_job(run,job=contract['job'],expected_prompt=contract['prompt'],schema_text=contract['schema_text'],workspace_context=contract['workspace_context'],expected_result=contract['result'])
 blob={'version':1,'contract':copy.deepcopy(contract),'meta_digest':digest(meta),'result_digest':digest(result)}
 raw=json.dumps(blob,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
 rel='dictionary-accountability-worker-contracts/'+digest(blob)+'.json';target=run/rel
 if target.exists():
  if checked_regular_file(target).read_bytes()!=raw:raise ValueError('Immutable worker contract changed')
 else:atomic_write_managed(run,rel,raw)
 return {'provider':WORKER_PROVIDER,'version':1,'run_relpath':str(run.relative_to(root)),'contract_path':rel,'contract_sha256':hashlib.sha256(raw).hexdigest()}

@memoized_provenance
def resolve_verified_worker_contract(descriptor):
 import hashlib
 from pipeline.annotation_research import _verify_job
 from pipeline.worker_paths import checked_directory,checked_regular_file
 keys={'provider','version','run_relpath','contract_path','contract_sha256'}
 if not isinstance(descriptor,dict) or set(descriptor)!=keys or descriptor['provider']!=WORKER_PROVIDER or type(descriptor['version']) is not int or descriptor['version']!=1:raise ValueError('Invalid worker descriptor')
 root=ROOT.resolve()
 for key in ('run_relpath','contract_path'):
  value=descriptor[key]
  if not isinstance(value,str) or not value or Path(value).is_absolute() or '..' in Path(value).parts:raise ValueError('Foreign worker descriptor path')
 if not descriptor['contract_path'].startswith('dictionary-accountability-worker-contracts/'):raise ValueError('Unmanaged worker descriptor')
 run=checked_directory(root/descriptor['run_relpath']);path=checked_regular_file(run/descriptor['contract_path'])
 if not run.is_relative_to(root) or not path.is_relative_to(run):raise ValueError('Foreign worker descriptor root')
 raw=path.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=descriptor['contract_sha256']:raise ValueError('Worker descriptor bytes changed')
 blob=json.loads(raw)
 if set(blob)!={'version','contract','meta_digest','result_digest'} or type(blob['version']) is not int or blob['version']!=1:raise ValueError('Invalid persisted worker contract')
 contract=blob['contract']
 if not isinstance(contract,dict) or set(contract)!=WORKER_KEYS:raise ValueError('Invalid persisted worker fields')
 meta,result=_verify_job(run,job=contract['job'],expected_prompt=contract['prompt'],schema_text=contract['schema_text'],workspace_context=contract['workspace_context'],expected_result=contract['result'])
 if digest(meta)!=blob['meta_digest'] or digest(result)!=blob['result_digest']:raise ValueError('Worker receipt changed')
 return copy.deepcopy(contract)
