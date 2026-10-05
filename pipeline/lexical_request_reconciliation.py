"""Opt-in typed reconciliation of unverified search requests, never annotations.

Uses the shared worker runner/artifact protocol and independent review. It does
not deinflect text, generate dictionary identities, mutate registries, or approve
an annotation. Historical validation consumes its immutable input snapshots;
current use additionally compares source/selected catalog records.
"""
from __future__ import annotations
import hashlib,json,copy
from jsonschema import validate
VERSION=1
GUIDANCE='''Reconcile every original unverified search request against the exact selected source. Full parent text is context; other positions must not enlarge the search scope. Provide exact Unicode source intervals and copied source text, never a suffix-derived host alias. Candidate lexical bindings are explicitly unapproved prior grounding, not proof. Preserve homonyms, readings and kinds. Reuse an exact supplied catalog identity only when the cited independently approved/authoritative records establish its lexical base for this source form. All supplied approved formation lessons are available, including prerequisites not linked by the candidate. Candidate links do not delimit available knowledge; choose applicable lessons through exact formation and cited evidence, never treat availability as applicability or occurrence approval. Existing reviewed unresolved investigations remain unresolved in their original scope. A missing headword for an inflected substring does not prove unsupported analysis. Needs_research and unresolved are valid outcomes; do not invent a new identity or gloss. Only supplied authenticated reference IDs may support a reusable decision; uncaptured web observations cannot. All decisions concern lookup requests, not candidate correctness or registry publication.'''
REVIEW_GUIDANCE='''Independently review each reconciliation claim, exact selected occurrence, identity/reading/kind and cited records. Do not review whether the immutable annotation is already repaired or approved. Approving honest needs_research/unresolved records does not approve an annotation. Reject unsupported base mappings, foreign source scope, false catalog conflicts, and omitted requests. Inspect applicable approved formation prerequisites even when the candidate omitted their links; verify every claim against its own exact selected occurrence and cited source. Preserve the original unverified requests and prior unresolved findings.'''
EVIDENCE_SCOPE_GUIDANCE='Assess each current request against every applicable supplied authoritative catalog record and retained primary record before choosing its disposition. A historical unresolved investigation describes its original request, source and retrieval gap; preserve that immutable result, but do not use it as a blanket veto on a newly supplied catalog identity or attested formation. Distinguish an unsuccessful exact-inflected-substring search from authoritative evidence for its dictionary-form base. Explicitly address applicable curriculum guide examples and approved formation prerequisites when they attest that base relation. Reuse the exact attested headword, reading and kind only when the current selected occurrence and cited evidence support it. Matching spelling or an old candidate link alone cannot choose a different homonym, reading or functional kind. If current evidence does not establish the base relation, retain needs_research or unresolved and describe the precise remaining gap. Availability is not occurrence approval; do not infer an arbitrary deinflection or invent an identity. The independent critic checks this evidence comparison, not whether the historical investigation already proposed an entry.'

def _worker_guidance(inputs,stage):
 version=inputs.get("reconciliation_instruction_policy_version",1)
 if type(version) is not int or version not in (1,2):raise ValueError("Unknown lexical evidence scope instruction policy")
 base=GUIDANCE if stage=="resolver" else REVIEW_GUIDANCE
 return base if version==1 else base+"\n\n"+EVIDENCE_SCOPE_GUIDANCE

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def obj(properties):return {'type':'object','additionalProperties':False,'properties':properties,'required':list(properties)}
TEXT={'type':'string'};NONEMPTY={'type':'string','minLength':1}
OCCURRENCE=obj({'start':{'type':'integer','minimum':0},'end':{'type':'integer','minimum':1},'text':NONEMPTY,'candidate_paths':{'type':'array','items':NONEMPTY,'uniqueItems':True}})
DECISION=obj({'request_index':{'type':'integer','minimum':0},'request':NONEMPTY,'scope':{'enum':['selected','outside_selected_scope','uncertain']},'occurrences':{'type':'array','items':OCCURRENCE},'disposition':{'enum':['existing_catalog_base','needs_research','unresolved']},'catalog_id':TEXT,'headword':TEXT,'reading':TEXT,'kind':TEXT,'reason':NONEMPTY,'citations':{'type':'array','items':NONEMPTY,'uniqueItems':True}})
RESULT_SCHEMA=obj({'policy_version':{'const':VERSION},'inputs_digest':NONEMPTY,'decisions':{'type':'array','items':DECISION}})
REVIEW_SCHEMA=obj({'policy_version':{'const':VERSION},'inputs_digest':NONEMPTY,'result_digest':NONEMPTY,'approved':{'type':'boolean'},'issues':{'type':'array','items':obj({'request_index':{'type':'integer','minimum':0},'explanation':NONEMPTY})}})
def candidate_owners(candidate,source_text,representation):
 rows=[];cursor=0;reconstructed=[]
 for index,record in enumerate(candidate['segments']):
  text=record.get('surface') if representation=='japanese-annotation' else record.get('text')
  if not isinstance(text,str):raise ValueError('Candidate source representation missing')
  if text:
   paths=[f'/segments/{index}/{key}' for key in ('text','surface','lemma','lemma_kana','reading','lexical_id','lexical_kind','entry_id','word_id') if isinstance(record.get(key),str)]
   rows.append({'start':cursor,'end':cursor+len(text),'text':text,'paths':paths,'record_digest':digest(record)})
  reconstructed.append(text);cursor+=len(text)
 if ''.join(reconstructed)!=source_text:raise ValueError('Candidate source reconstruction changed')
 return rows

def approved_formation_inventory(references,grammar):
 """All approved lessons available; applicability remains an independently reviewed claim."""
 inventory=[]
 for identity,entry in sorted(grammar.items()):
  key='grammar:'+identity
  expected={'kind':'published_grammar','content':entry,'content_digest':digest(entry)}
  if key in references and references[key]!=expected:raise ValueError('Approved formation reference equivocation')
  references[key]=expected
  inventory.append({'id':identity,'reference_id':key,'pattern':entry.get('pattern',''),'title':entry.get('title','')})
 return inventory


def reviewed_primary_references(candidate,catalog,registry):
 """Selection only: source provider separately authenticates entire registry."""
 from pipeline.korean_lexical_research import candidate_lexical_identities
 selected=candidate_lexical_identities(candidate);byid={row['id']:row for row in catalog};references={}
 for index,record in enumerate(registry['reviews']):
  matched=[]
  for entry in record['proposal']['entries']:
   identity=entry['id'];row=byid.get(identity)
   if identity not in selected or row is None:continue
   projected={'id':identity,'headword':entry['headword'],'pos':entry['pos'],'meaning':entry['meaning_en'],'grade':None,'primary_url':entry['primary_url']}
   if row['kind']!='word' or row['reading'] or row['content']!=projected or row['headword']!=entry['headword']:raise ValueError('Retained primary lexical identity/reading/kind changed')
   matched.append(identity)
  if matched:
   references['lexical-primary:'+str(index)]={'kind':'reviewed_lexical_primary_record','content':record,'content_digest':digest(record),'applicable_catalog_ids':sorted(matched),'occurrence_approval':False,'scope_note':'Reusable exact lexical identity knowledge only. Original source context is descriptive provenance; its occurrence findings do not transfer to this position.'}
 return references


def check_inputs(inputs):
 if type(inputs.get('policy_version')) is not int or inputs['policy_version']!=VERSION:raise ValueError('Unsupported reconciliation input policy')
 if 'language' in inputs and (inputs['language'],inputs['representation']) not in {('ko','korean-flat'),('zh','chinese-annotation'),('ja','japanese-annotation')}:raise ValueError('Unsupported language/representation pair')
 source=inputs['source_text'];parent=inputs['parent_text'];pos=inputs['source_position']
 if set(pos)!=set(('chunk_index','source_start','source_text_digest','parent_text_digest')):raise ValueError('Incomplete source position')
 if any(type(pos[k]) is not int or pos[k]<0 for k in ('chunk_index','source_start')):raise ValueError('Invalid source indices')
 if pos['source_text_digest']!=digest(source) or pos['parent_text_digest']!=digest(parent) or parent[pos['source_start']:pos['source_start']+len(source)]!=source:raise ValueError('Source position changed')
 if inputs['candidate_status']!='unapproved_grounding' or digest(inputs['candidate'])!=inputs['candidate_digest']:raise ValueError('Candidate grounding changed')
 if inputs['candidate_owners']!=candidate_owners(inputs['candidate'],source,inputs['representation']):raise ValueError('Candidate owner bindings changed')
 requests=inputs['requests']
 if not requests or any(type(v) is not str or not v for v in requests):raise ValueError('Invalid request inventory')
 ids=[v['id'] for v in inputs['catalog']]
 if len(ids)!=len(set(ids)):raise ValueError('Equivocal catalog identity')
 for record in inputs['references'].values():
  if record['content_digest']!=digest(record['content']):raise ValueError('Reference snapshot changed')
 for row in inputs['candidate_owners']:
  if type(row['start']) is not int or type(row['end']) is not int or row['start']<0 or row['end']<=row['start'] or row['end']>len(source) or source[row['start']:row['end']]!=row['text']:raise ValueError('Candidate source owner changed')
 return inputs

def check_result(result,inputs):
 check_inputs(inputs);validate(result,RESULT_SCHEMA)
 if type(result['policy_version']) is not int or result['inputs_digest']!=digest(inputs):raise ValueError('Result input binding changed')
 rows=result['decisions']
 if [r['request_index'] for r in rows]!=list(range(len(inputs['requests']))):raise ValueError('Request coverage changed')
 catalog={r['id']:r for r in inputs['catalog']};refs=inputs['references'];owners=inputs['candidate_owners'];source=inputs['source_text']
 for row,request in zip(rows,inputs['requests']):
  if row['request']!=request:raise ValueError('Original request spelling changed')
  if row['scope']!='selected' and (row['occurrences'] or row['disposition']!='unresolved'):raise ValueError('Unselected request cannot authorize lookup/research')
  if row['scope']=='selected' and not row['occurrences']:raise ValueError('Selected request lacks exact source occurrence')
  for occurrence in row['occurrences']:
   start,end=occurrence['start'],occurrence['end']
   if type(start) is not int or type(end) is not int or not 0<=start<end<=len(source) or source[start:end]!=occurrence['text']:raise ValueError('Foreign occurrence scope')
   for path in occurrence['candidate_paths']:
    matches=[o for o in owners if path in o['paths'] and o['start']>=start and o['end']<=end]
    if len(matches)!=1:raise ValueError('Foreign candidate owner')
  if any(key not in refs for key in row['citations']):raise ValueError('Unknown citation')
  if row['disposition']=='existing_catalog_base':
   record=catalog.get(row['catalog_id'])
   if row['scope']!='selected' or record is None or not row['citations']:raise ValueError('Reusable base lacks attested scope')
   if any(row[key]!=record[key] for key in ('headword','reading','kind')):raise ValueError('Identity/reading/kind changed')
   if any(refs[key]['kind']=='reviewed_lexical_primary_record' and (row['catalog_id'] not in refs[key]['applicable_catalog_ids'] or refs[key]['occurrence_approval'] is not False) for key in row['citations']):raise ValueError('Foreign lexical primary identity or occurrence authority')
   if not any(refs[key].get('catalog_id')==row['catalog_id'] and refs[key]['kind']=='catalog_attestation' for key in row['citations']):raise ValueError('Catalog attestation missing')
  elif any(row[key] for key in ('catalog_id','headword','reading','kind')):raise ValueError('Uncertain/research request invents a reusable identity')
 return result

def check_review(review,result,inputs):
 check_result(result,inputs);validate(review,REVIEW_SCHEMA)
 if type(review['policy_version']) is not int or review['inputs_digest']!=digest(inputs) or review['result_digest']!=digest(result):raise ValueError('Independent review binding changed')
 if review['approved'] and review['issues'] or not review['approved'] and not review['issues']:raise ValueError('Review approval/issues mismatch')
 if any(i['request_index']>=len(inputs['requests']) for i in review['issues']):raise ValueError('Foreign review request')
 return review

def _consume_bound_lookup(result,review,inputs,*,source_text,source_position,current_catalog):
 """Internal shape/freshness check; public callers must authenticate receipts."""
 check_review(review,result,inputs)
 if not review['approved']:raise ValueError('Reconciliation independently rejected')
 if source_text!=inputs['source_text'] or source_position!=inputs['source_position']:raise ValueError('Current source changed')
 ids=[v['id'] for v in current_catalog]
 if len(ids)!=len(set(ids)):raise ValueError('Equivocal current catalog identity')
 current={v['id']:v for v in current_catalog}
 for row in result['decisions']:
  if row['disposition']=='existing_catalog_base':
   saved=next(v for v in inputs['catalog'] if v['id']==row['catalog_id'])
   if current.get(saved['id'])!=saved:raise ValueError('Current catalog identity changed')
 return {'lookup_headwords':sorted({r['headword'] for r in result['decisions'] if r['disposition']=='existing_catalog_base'}),'research_requests':[r for r in result['decisions'] if r['disposition']=='needs_research'],'unresolved':[r for r in result['decisions'] if r['disposition']=='unresolved' and r['scope']!='outside_selected_scope'],'annotation_approved':False,'registry_promoted':False}

def authenticate_original_requests(run_dir,inputs):
 """Exact generic workspace receipt authentication; no model/network calls.

Policy1 transports two existing Korean headword-request jobs. This is not a
universal legacy receipt adapter or a fabricated C/J discovery stage.
"""
 from pathlib import Path
 from pipeline.agent_harness import CodexRunner,digest as worker_digest
 from pipeline.worker_paths import checked_regular_file
 check_inputs(inputs)
 original=inputs['original_requests']
 if len(original)!=2:raise ValueError('Original discovery/triage coverage changed')
 results=[]
 for row in original:
  job=row['job']
  if Path(job).name!=job or not job.startswith('annotation-lexical-candidates-'):raise ValueError('Invalid original request job')
  root=Path(run_dir)/'agents'/job
  meta=json.loads(checked_regular_file(root/'meta.json').read_text());result=json.loads(checked_regular_file(root/'result.json').read_text())
  if digest(meta)!=row['meta_digest'] or digest(result)!=row['result_digest'] or result!=row['result']:raise ValueError('Original receipt changed')
  if (meta['model'],meta['effort'],meta['tool_profile'])!=('gpt-6-luna','low','workspace') or meta['return_code']!=0:raise ValueError('Original worker not authenticated workspace result')
  fp=worker_digest(row['prompt'],row['schema_text'],meta['model'],meta['effort']);fp=worker_digest(fp,'workspace','tool-profile-v1');fp=worker_digest(fp,row['workspace_version'])
  if row['workspace_context'] is not None:fp=worker_digest(fp,json.dumps(row['workspace_context'],sort_keys=True))
  if fp!=meta['fingerprint']:raise ValueError('Original exact request fingerprint changed')
  CodexRunner._check_tool_profile(root,'workspace',meta)
  data=json.loads(row['prompt'].split('\nINPUT:\n',1)[1]);scope=data['annotation_admission_discovery']
  if data['prose']['text']!=inputs['parent_text'] or scope['selected_occurrences']!=[{'chunk_index':inputs['source_position']['chunk_index']+1,'text':inputs['source_text'],'source_position':inputs['source_position']}]:raise ValueError('Original request current source scope changed')
  if scope['active_chunks']!=[inputs['source_position']['chunk_index']+1]:raise ValueError('Original selected admission changed')
  results.append(result)
 if inputs['requests']!=results[-1]['headwords']:raise ValueError('Current inventory differs from authentic triage')
 return results


def authenticate_current_korean_sources(inputs):
 """Policy1 current source provider, not a generic arbitrary snapshot authority.

 Replayable resolver receipts are separate from current catalog eligibility.
 This provider reconstructs every supplied record from reviewed repository data.
 """
 from pipeline import korean_contracts as contracts
 from pipeline.korean_lexical_research import REGISTRY,candidates
 from pipeline.korean_dictionary import GRAMMAR,_registry
 from pipeline.korean_curriculum import entries
 from pathlib import Path
 check_inputs(inputs)
 if (inputs.get('language'),inputs['representation'])!=('ko','korean-flat'):raise ValueError('Unsupported policy1 source provider')
 catalog=contracts.lexical_catalog();known={}
 for records in catalog.values():
  for entry in records:
   row={'id':entry['id'],'headword':entry['headword'],'reading':'','kind':'word','content':entry}
   old=known.get(row['id'])
   if old is not None and old!=row:raise ValueError('Authoritative catalog identity equivocation')
   known[row['id']]=row
 for row in inputs['catalog']:
  if known.get(row['id'])!=row:raise ValueError('Catalog record lacks current source authority')
 candidates();registry=json.loads(Path(REGISTRY).read_text());grammar=_registry(GRAMMAR)
 curriculum={r['id']:r for r in entries('vocabulary')}
 expected_primary=reviewed_primary_references(inputs['candidate'],inputs['catalog'],registry)
 if any(inputs['references'].get(key)!=value for key,value in expected_primary.items()):raise ValueError('Retained primary record coverage changed')
 expected_references={}
 expected_inventory=approved_formation_inventory(expected_references,grammar)
 if inputs.get('approved_formation_inventory')!=expected_inventory or any(inputs['references'].get(key)!=value for key,value in expected_references.items()):raise ValueError('Approved formation inventory source coverage changed')
 for key,reference in inputs['references'].items():
  content=reference['content'];kind=reference['kind']
  if key.startswith('catalog:'):
   identity=key.removeprefix('catalog:')
   if kind!='catalog_attestation' or reference.get('catalog_id')!=identity or known.get(identity,{}).get('content')!=content:raise ValueError('Catalog reference source authority changed')
  elif key.startswith('prior-investigation:'):
   try:index=int(key.split(':',1)[1]);expected=registry['reviews'][index]
   except (ValueError,IndexError):raise ValueError('Unknown reviewed research source')
   if kind!='reviewed_lexical_investigation' or content!=expected:raise ValueError('Reviewed lexical research source changed')
  elif key.startswith('lexical-primary:'):
   if expected_primary.get(key)!=reference:raise ValueError('Foreign retained lexical primary source')
  elif key.startswith('grammar:'):
   if kind!='published_grammar' or grammar.get(key.split(':',1)[1])!=content:raise ValueError('Published grammar source changed')
  elif key.startswith('curriculum:'):
   identity=key.split(':',1)[1]
   if kind!='catalog_attestation' or curriculum.get(identity)!=content:raise ValueError('Pinned curriculum source changed')
   record=known.get(reference.get('catalog_id'),{})
   if record.get('content',{}).get('curriculum_source_id')!=identity:raise ValueError('Curriculum identity association changed')
  else:raise ValueError('Unknown authoritative source provider')
 return inputs


def verify_reconciliation_jobs(worker_run_dir,inputs,*,origin_run_dir,resolver_job,review_job):
 """Authenticate two exact generic workspace receipts before consuming verdicts.

 This fresh policy1 route requires current source authority. Historical receipt
 replay will need an explicit archived-provider policy; no live freshness waiver.
 """
 from pathlib import Path
 from pipeline.annotation_research import _verify_job
 authenticate_packet_origin(origin_run_dir,inputs);authenticate_archived_sources(inputs)
 resolver_context={**inputs,'lexical_request_reconciliation_validation':{'mode':'resolver','inputs':inputs,'origin_run_dir':str(Path(origin_run_dir))}}
 resolver_request=reconciliation_request(inputs,'resolver',origin_run_dir=origin_run_dir)
 resolver_prompt=resolver_request['prompt']
 _,result=_verify_job(Path(worker_run_dir),job=resolver_job,expected_prompt=resolver_prompt,schema_text=json.dumps(RESULT_SCHEMA),workspace_context=resolver_context)
 check_result(result,inputs)
 review_payload={**inputs,'reconciliation_result':result}
 review_context={**review_payload,'lexical_request_reconciliation_validation':{'mode':'review','inputs':inputs,'result':result,'origin_run_dir':str(Path(origin_run_dir))}}
 review_request=reconciliation_request(inputs,'review',origin_run_dir=origin_run_dir,result=result)
 review_prompt=review_request['prompt']
 _,review=_verify_job(Path(worker_run_dir),job=review_job,expected_prompt=review_prompt,schema_text=json.dumps(REVIEW_SCHEMA),workspace_context=review_context)
 check_review(review,result,inputs)
 return {'result':result,'review':review,'annotation_approved':False,'registry_promoted':False}


def authenticate_packet_origin(run_dir,inputs):
 from pathlib import Path
 from pipeline.worker_paths import checked_directory,checked_regular_file
 from pipeline.agent_harness import CodexRunner
 root=Path(run_dir);agents=checked_directory(root/'agents')
 authenticate_original_requests(root,inputs)
 origin=inputs['candidate_origin'];job=origin['job']
 if Path(job).name!=job:raise ValueError('Invalid candidate origin job')
 lane=checked_directory(agents/job)
 meta=json.loads(checked_regular_file(lane/'meta.json').read_text());candidate=json.loads(checked_regular_file(lane/'result.json').read_text())
 if digest(meta)!=origin['meta_digest'] or digest(candidate)!=origin['result_digest'] or candidate!=inputs['candidate']:raise ValueError('Candidate origin changed')
 CodexRunner._check_tool_profile(lane,'workspace',meta)
 budget=inputs['original_discovery_budget'];relative=Path(budget['ledger_path'])
 repository=Path(__file__).resolve()
 # Absolute authority is the existing run's repository root, not proposal location.
 from pipeline.agent_harness import ROOT
 if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid discovery ledger path')
 path=checked_regular_file(Path(ROOT)/relative)
 import hashlib
 if hashlib.sha256(path.read_bytes()).hexdigest()!=budget['ledger_sha256']:raise ValueError('Original discovery ledger changed')
 ledger=json.loads(path.read_text())
 if path.parent!=root/'annotation-admission' or ledger['limit']!=2 or (budget['spent'],budget['limit'])!=(2,2) or len(ledger['requests'])!=2:raise ValueError('Discovery budget origin changed')
 for saved,request in zip(inputs['original_requests'],ledger['requests']):
  raw=request['request']
  if digest(raw)!=request['request_digest'] or any(saved[key]!=raw[other] for key,other in [('job','job'),('prompt','prompt'),('schema_text','schema'),('workspace_context','workspace_context')]):raise ValueError('Discovery ledger exact request changed')
 return inputs


def archive_current_sources():
 """Fixed policy1 repository DATA closure, captured before any worker call."""
 from pipeline import korean_contracts as k,korean_curriculum as curriculum,korean_lexical_research as lexical,korean_dictionary as dictionary
 from pipeline.agent_harness import ROOT
 import base64
 paths=[k.VOCAB_SOURCE,curriculum.SOURCE,lexical.REGISTRY,dictionary.WORDS,dictionary.GRAMMAR]
 return {str(path.relative_to(ROOT)):{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes_base64':base64.b64encode(path.read_bytes()).decode()} for path in paths}


def authenticate_archived_sources(inputs,*,require_current=False):
 """Reconstruct historical sources from exact request-bound DATA bytes.

 Fresh admission MUST also compare these bytes with current authoritative data;
 historical receipts authenticate this immutable packet, not current freshness.
 """
 import base64,tempfile
 from pathlib import Path
 from unittest.mock import patch
 from pipeline import korean_contracts as k,korean_curriculum as curriculum,korean_lexical_research as lexical,korean_dictionary as dictionary
 from pipeline.agent_harness import ROOT
 archive=inputs['authoritative_source_archive'];expected=[k.VOCAB_SOURCE,curriculum.SOURCE,lexical.REGISTRY,dictionary.WORDS,dictionary.GRAMMAR]
 names=[str(path.relative_to(ROOT)) for path in expected]
 if set(archive)!=set(names):raise ValueError('Authoritative source archive coverage changed')
 for reference in inputs['references'].values():
  if 'source_path' in reference and (reference['source_path'] not in archive or reference.get('source_sha256')!=archive[reference['source_path']]['sha256']):raise ValueError('Reference archived source association changed')
 raw={}
 for name,row in archive.items():
  if set(row)!=set(('sha256','bytes_base64')):raise ValueError('Malformed authoritative archive')
  raw[name]=base64.b64decode(row['bytes_base64'],validate=True)
  if hashlib.sha256(raw[name]).hexdigest()!=row['sha256']:raise ValueError('Authoritative archived bytes changed')
 if require_current and archive!=archive_current_sources():raise ValueError('Current authoritative sources changed')
 if hashlib.sha256(raw[names[1]]).hexdigest()!=curriculum.SOURCE_SHA256:raise ValueError('Unpinned curriculum archive')
 original_candidates=lexical.candidates
 original_registry=dictionary._registry
 registry_sources={dictionary.WORDS.resolve():3,dictionary.GRAMMAR.resolve():4}
 with tempfile.TemporaryDirectory(prefix='lexical-reconciliation-source-') as temporary:
  paths=[]
  for index,name in enumerate(names):
   path=Path(temporary)/str(index);path.write_bytes(raw[name]);paths.append(path)
  def archived_registry(path):
   index=registry_sources.get(Path(path).resolve())
   return original_registry(paths[index] if index is not None else path)
  with patch.object(k,'VOCAB_SOURCE',paths[0]),patch.object(curriculum,'catalog',lambda:json.loads(raw[names[1]])),patch.object(lexical,'REGISTRY',paths[2]),patch.object(lexical,'candidates',lambda:original_candidates(paths[2])),patch.object(dictionary,'_registry',archived_registry):
   authenticate_current_korean_sources(inputs)
 return inputs


def consume_verified_reconciliation(worker_run_dir,inputs,*,origin_run_dir,resolver_job,review_job,source_text,source_position,current_catalog):
 """Production-facing lookup handoff: bare model approval never grants reuse."""
 bound=verify_reconciliation_jobs(worker_run_dir,inputs,origin_run_dir=origin_run_dir,resolver_job=resolver_job,review_job=review_job)
 return _consume_bound_lookup(bound['result'],bound['review'],inputs,source_text=source_text,source_position=source_position,current_catalog=current_catalog)


def load_registered_reconciliation(origin_run_dir,descriptor,*,requests,source_text,source_position,current_catalog):
 """Explicit persisted evidence only; never scan other workers or approvals."""
 from pathlib import Path
 from pipeline.agent_harness import ROOT
 from pipeline.worker_paths import checked_directory,checked_regular_file
 if not isinstance(descriptor,dict) or set(descriptor)!=set(('worker_run_relpath','inputs_sha256','evidence_sha256')):raise ValueError('Invalid reconciliation descriptor')
 relative=Path(descriptor['worker_run_relpath'])
 if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid reconciliation worker lane')
 lane=checked_directory(Path(ROOT)/relative)
 read=lambda name:checked_regular_file(lane/name).read_bytes()
 input_bytes=read('immutable-inputs.json');evidence_bytes=read('evidence.json')
 if hashlib.sha256(input_bytes).hexdigest()!=descriptor['inputs_sha256'] or hashlib.sha256(evidence_bytes).hexdigest()!=descriptor['evidence_sha256']:raise ValueError('Registered reconciliation evidence changed')
 inputs=json.loads(input_bytes);evidence=json.loads(evidence_bytes)
 if requests!=inputs['requests'] or evidence['inputs_digest']!=digest(inputs):raise ValueError('Current request inventory changed')
 if 'reconciliation_receipt_version' in evidence:
  if type(evidence['reconciliation_receipt_version']) is not int or evidence['reconciliation_receipt_version']!=2:raise ValueError('Unknown reconciliation receipt version')
  if source_text!=inputs['source_text'] or source_position!=inputs['source_position']:raise ValueError('Current source changed')
  ids=[record['id'] for record in current_catalog]
  if len(ids)!=len(set(ids)):raise ValueError('Equivocal current catalog identity')
  current={record['id']:record for record in current_catalog};saved={record['id']:record for record in inputs['catalog']}
  for decision in evidence['result']['decisions']:
   if decision['disposition']=='existing_catalog_base' and current.get(decision['catalog_id'])!=saved.get(decision['catalog_id']):raise ValueError('Current catalog identity changed')
  bound=verify_reconciliation_repair_jobs(lane,inputs,origin_run_dir=origin_run_dir,repair_job=evidence['repair_job'],review_job=evidence['review_job'])
 else:
  bound=verify_reconciliation_jobs(lane,inputs,origin_run_dir=origin_run_dir,resolver_job=evidence['resolver_job'],review_job=evidence['review_job'])
 if evidence['result']!=bound['result'] or evidence['review']!=bound['review']:raise ValueError('Registered reconciliation verdict changed')
 return _consume_bound_lookup(bound['result'],bound['review'],inputs,source_text=source_text,source_position=source_position,current_catalog=current_catalog)

REPAIR_FIELDS=('disposition','catalog_id','headword','reading','kind','reason','citations')
REPAIR_PATCH_SCHEMA={'type':'object','required':['policy_version','base_digest','edits'],'additionalProperties':False,'properties':{'policy_version':{'const':2},'base_digest':{'type':'string','pattern':'^[a-f0-9]{64}$'},'edits':{'type':'array','minItems':1,'items':{'type':'object','required':['request_index','fields'],'additionalProperties':False,'properties':{'request_index':{'type':'integer','minimum':0},'fields':{'type':'object','minProperties':1,'additionalProperties':False,'properties':{key:({'type':'array','items':{'type':'string'}} if key=='citations' else {'type':'string'}) for key in REPAIR_FIELDS}}}}}}}

def _repair_task_inputs(inputs):
 from pipeline.agent_harness import ROOT
 from pipeline.worker_paths import checked_directory,checked_regular_file
 from pathlib import Path
 descriptor=inputs['reconciliation_repair'];relative=Path(descriptor['worker_run_relpath'])
 if relative.is_absolute() or '..' in relative.parts:raise ValueError('Foreign repair worker lane')
 lane=checked_directory(Path(ROOT)/relative);values=[]
 for key,digest_key in [('resolver_job','result_digest'),('review_job','review_digest')]:
  job=descriptor[key]
  if Path(job).name!=job:raise ValueError('Unsafe prior reconciliation job')
  value=json.loads(checked_regular_file(lane/'agents'/job/'result.json').read_text())
  if digest(value)!=descriptor[digest_key]:raise ValueError('Repair task source changed')
  values.append(value)
 return {'reconciliation_repair_base':values[0],'reconciliation_repair_review':values[1],'reconciliation_repair_targets':sorted({issue['request_index'] for issue in values[1]['issues']})}

def reconciliation_request(inputs,stage,*,origin_run_dir,result=None):
 """Canonical fresh request builder shared by producers and receipt replay."""
 if stage not in ('resolver','review','repair'):raise ValueError('Unknown reconciliation worker stage')
 if stage=='repair' and inputs.get('reconciliation_instruction_policy_version')!=2:raise ValueError('Repair requires explicit instruction policy2')
 payload=inputs if result is None else {**inputs,'reconciliation_result':result}
 if stage=='repair':payload={**inputs,**_repair_task_inputs(inputs)}
 schema=REPAIR_PATCH_SCHEMA if stage=='repair' else RESULT_SCHEMA if stage=='resolver' else REVIEW_SCHEMA
 guidance=_worker_guidance(inputs,'review' if stage=='review' else 'resolver')
 if stage=='repair':guidance+='\nRead the organized reconciliation_repair_base, reconciliation_repair_review and reconciliation_repair_targets fields before editing. Submit only the exact base-digest scoped patch. Change lookup fields only for independently rejected request indices. Preserve all other decisions and all source intervals. Unresolved context is not additional repair authority. Do not regenerate the full reconciliation result.'
 validation={'mode':stage,'inputs':inputs,'origin_run_dir':str(origin_run_dir)}
 if result is not None:validation['result']=result
 return {'job':'lexical-request-'+stage+'-'+digest(payload),'prompt':guidance+'\nINPUT:\n'+json.dumps(payload,ensure_ascii=False),'schema':schema,'workspace_context':{**payload,'lexical_request_reconciliation_validation':validation}}

def authenticate_repair_base(origin_run_dir,inputs):
 """Prior rejected receipts, never a caller-supplied approval or changed source."""
 from pipeline.agent_harness import ROOT
 from pipeline.worker_paths import checked_directory,checked_regular_file
 from pathlib import Path
 descriptor=inputs.get('reconciliation_repair')
 expected={'worker_run_relpath','inputs_sha256','resolver_job','review_job','result_digest','review_digest'}
 if not isinstance(descriptor,dict) or set(descriptor)!=expected:raise ValueError('Invalid reconciliation repair provenance')
 relative=Path(descriptor['worker_run_relpath'])
 if relative.is_absolute() or '..' in relative.parts:raise ValueError('Foreign repair worker lane')
 lane=checked_directory(Path(ROOT)/relative);raw=checked_regular_file(lane/'immutable-inputs.json').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=descriptor['inputs_sha256']:raise ValueError('Repair original inputs changed')
 old=json.loads(raw)
 old_version=old.get('reconciliation_instruction_policy_version',1)
 if type(old_version) is not int or old_version not in (1,2):raise ValueError('Unknown prior instruction policy')
 if type(inputs.get('reconciliation_instruction_policy_version')) is not int or inputs['reconciliation_instruction_policy_version']!=2:raise ValueError('Repair requires explicit instruction policy2')
 old_common={key:value for key,value in old.items() if key!='reconciliation_instruction_policy_version'}
 current_common={key:value for key,value in inputs.items() if key not in ('reconciliation_instruction_policy_version','reconciliation_repair')}
 if current_common!=old_common:raise ValueError('Repair source or current contract changed')
 bound=verify_reconciliation_jobs(lane,old,origin_run_dir=origin_run_dir,resolver_job=descriptor['resolver_job'],review_job=descriptor['review_job'])
 if digest(bound['result'])!=descriptor['result_digest'] or digest(bound['review'])!=descriptor['review_digest']:raise ValueError('Repair prior verdict changed')
 if bound['review']['approved'] or not bound['review']['issues']:raise ValueError('Repair lacks independently rejected authority')
 return bound

def apply_reconciliation_repair(patch,inputs,base,review):
 if type(inputs.get('reconciliation_instruction_policy_version')) is not int or inputs['reconciliation_instruction_policy_version']!=2:raise ValueError('Repair requires explicit instruction policy2')
 validate(patch,REPAIR_PATCH_SCHEMA)
 if type(patch['policy_version']) is not int or patch['policy_version']!=2:raise ValueError('Repair patch policy must be integer2')
 validate(review,REVIEW_SCHEMA)
 if review['inputs_digest']!=base['inputs_digest'] or review['result_digest']!=digest(base):raise ValueError('Repair diagnosis changed base')
 if patch['base_digest']!=digest(base):raise ValueError('Repair base digest changed')
 authorized={issue['request_index'] for issue in review['issues']}
 if review['approved'] or not authorized:raise ValueError('Repair lacks independent defect authority')
 indices=[row['request_index'] for row in patch['edits']]
 if len(indices)!=len(set(indices)) or set(indices)!=authorized:raise ValueError('Repair must cover exact independently rejected requests')
 derived=copy.deepcopy(base);derived['inputs_digest']=digest(inputs)
 for edit in patch['edits']:
  row=derived['decisions'][edit['request_index']]
  if all(row.get(key)==value for key,value in edit['fields'].items()):raise ValueError('Reconciliation repair is unchanged')
  row.update(edit['fields'])
 check_result(derived,inputs)
 return derived

def verify_reconciliation_repair_jobs(worker_run_dir,inputs,*,origin_run_dir,repair_job,review_job):
 from pipeline.annotation_research import _verify_job
 from pathlib import Path
 prior=authenticate_repair_base(origin_run_dir,inputs)
 request=reconciliation_request(inputs,'repair',origin_run_dir=origin_run_dir)
 if repair_job!=request['job']:raise ValueError('Foreign repair request job')
 _,patch=_verify_job(Path(worker_run_dir),job=repair_job,expected_prompt=request['prompt'],schema_text=json.dumps(request['schema']),workspace_context=request['workspace_context'])
 result=apply_reconciliation_repair(patch,inputs,prior['result'],prior['review'])
 request=reconciliation_request(inputs,'review',origin_run_dir=origin_run_dir,result=result)
 if review_job!=request['job']:raise ValueError('Foreign repaired review job')
 _,review=_verify_job(Path(worker_run_dir),job=review_job,expected_prompt=request['prompt'],schema_text=json.dumps(request['schema']),workspace_context=request['workspace_context'])
 check_review(review,result,inputs)
 return {'result':result,'review':review,'patch':patch,'prior_result':prior['result'],'prior_review':prior['review'],'annotation_approved':False,'registry_promoted':False}

def make_reconciliation_repair_inputs(worker_run_dir,*,origin_run_dir,resolver_job,review_job):
 from pathlib import Path
 from pipeline.agent_harness import ROOT
 from pipeline.worker_paths import checked_directory,checked_regular_file
 lane=checked_directory(Path(worker_run_dir).absolute());raw=checked_regular_file(lane/'immutable-inputs.json').read_bytes();old=json.loads(raw)
 bound=verify_reconciliation_jobs(lane,old,origin_run_dir=origin_run_dir,resolver_job=resolver_job,review_job=review_job)
 authenticate_archived_sources(old,require_current=True)
 if bound['review']['approved'] or not bound['review']['issues']:raise ValueError('Repair lacks independently rejected authority')
 try:relative=lane.relative_to(Path(ROOT))
 except ValueError as error:raise ValueError('Repair source outside repository run evidence') from error
 return {**old,'reconciliation_instruction_policy_version':2,'reconciliation_repair':{'worker_run_relpath':str(relative),'inputs_sha256':hashlib.sha256(raw).hexdigest(),'resolver_job':resolver_job,'review_job':review_job,'result_digest':digest(bound['result']),'review_digest':digest(bound['review'])}}
