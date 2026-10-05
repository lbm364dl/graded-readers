"""Scoped registry revisions consume authenticated research; never run-lesson IDs."""
import base64,copy,hashlib,importlib.util,json
from pathlib import Path
from unittest.mock import patch
from pipeline.annotation_adjudication import digest
from pipeline import annotation_research as research
ROOT=Path('/home/catalin/graded-readers')
FIELD='dictionary_research_handoff'
SOURCE=ROOT/'runs/maintenance/20261003/proposals/published-quotation-claim-revision-v2-20261005'
APPROVED_EVIDENCE_SHA='2b94a2d365fdae00a0f47cf1a42186d93ba86c2c24db3333f0f5910c256620cc'
SOURCE_DRIVER_SHA='9f164bab83ef29e584302904319efe71c21ca9d099d21af4aaed54b89502dc40'

def _read(p):return json.loads(Path(p).read_text())
def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _module(path,name):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

DATA_PATHS=frozenset({
 'runs/maintenance/20261003/checks/published-korean-reporting-formation-audit-20261005.json',
 'runs/maintenance/20261003/checks/published-korean-reporting-formation-inventory-20261005.json',
 'content/lexicon/korean/l1.grammar.json',
 *('content/korean/honggildong/'+level+'.annotations.json' for level in ('l1','l2','l5')),
})

def _data_snapshots(evidence,packet=None):
 expected=evidence['inputs']['context']['protected_artifact_sha256']
 if set(expected)!=DATA_PATHS:raise ValueError('Unknown protected data source policy')
 if packet is None:
  snapshots={}
  for name,sha in expected.items():
   path=ROOT/name
   if path.is_symlink() or not path.is_file():raise ValueError('Unsafe protected data source')
   raw=path.read_bytes()
   if _sha(path)!=sha or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Protected data source changed before archive')
   snapshots[name]={'sha256':sha,'bytes_base64':base64.b64encode(raw).decode('ascii')}
  return snapshots
 snapshots=packet.get('protected_data_snapshots')
 if not isinstance(snapshots,dict) or set(snapshots)!=DATA_PATHS:raise ValueError('Incomplete or foreign protected data archive')
 for name,row in snapshots.items():
  if not isinstance(row,dict) or set(row)!={'sha256','bytes_base64'} or row['sha256']!=expected[name]:raise ValueError('Protected data archive identity changed')
  try:raw=base64.b64decode(row['bytes_base64'],validate=True)
  except (ValueError,TypeError) as exc:raise ValueError('Invalid protected data archive bytes') from exc
  if hashlib.sha256(raw).hexdigest()!=expected[name]:raise ValueError('Protected data archive changed')
 return snapshots

def source_descriptor():
 path=SOURCE/'run-output/evidence.json'
 return {'provider':'published-dictionary-claim-revision-v2','evidence_relpath':str(path.relative_to(ROOT)),'evidence_sha256':_sha(path),'driver_sha256':SOURCE_DRIVER_SHA}

def _verify_source(descriptor,*,historical_packet=None):
 if descriptor.get('provider')!='published-dictionary-claim-revision-v2' or descriptor.get('driver_sha256')!=SOURCE_DRIVER_SHA:raise ValueError('Unknown research source protocol')
 path=ROOT/descriptor['evidence_relpath']
 if path!=SOURCE/'run-output/evidence.json' or path.is_symlink() or _sha(path)!=descriptor['evidence_sha256']:raise ValueError('Foreign or changed research source')
 if descriptor['evidence_sha256']!=APPROVED_EVIDENCE_SHA:raise ValueError('Research source was not the independently audited decision')
 if _sha(SOURCE/'run.py')!=SOURCE_DRIVER_SHA:raise ValueError('Audited research verifier changed')
 evidence=_read(path)
 if historical_packet is not None and historical_packet.get('version')==2:
  snapshots=_data_snapshots(evidence,historical_packet)
 else:
  snapshots=None
  if historical_packet is None:_data_snapshots(evidence)
 if evidence.get('approved') is not True or evidence.get('inputs',{}).get('claim_revision_policy_version')!=2:raise ValueError('Research successor is not independently approved')
 module=_module(SOURCE/'run.py','dictionary_claim_source_v2')
 if historical_packet is None:
  result=module.replay(evidence)
 else:
  # Freshness is checked separately against CURRENT registry/coverage. These
  # exact original source tasks and prompts must replay after registry revision.
  inputs=evidence['inputs'];original=_read(module.ORIGINAL/'run-output/research-evidence.json')
  registry=ROOT/'content/lexicon/korean/l1.grammar.json'
  archived=historical_packet['registry_before_text'].encode()
  if hashlib.sha256(archived).hexdigest()!=historical_packet['registry_sha256']:raise ValueError('Historical registry snapshot changed')
  def protected(hashes):
   for name,expected in hashes.items():
    actual=snapshots[name]['sha256'] if snapshots is not None and name in DATA_PATHS else (hashlib.sha256(archived).hexdigest() if ROOT/name==registry else _sha(ROOT/name))
    if actual!=expected:raise ValueError('Historical research protected source changed')
  if inputs['candidate']['approved_lesson']!=historical_packet['before_entry']:raise ValueError('Historical research entry differs')
  with patch.object(module.original,'required_inputs',lambda:(original['inputs'],original['inputs']['context']['protected_artifact_sha256'])),patch.object(module.original,'verify_protected',protected):
   module.original.verify_evidence(original)
  prior=_module(module.PRIOR/'run.py','dictionary_claim_prior_v1')
  prior_record=inputs['prior_diagnostic'];lane=module.PRIOR/'run-output/agents'/prior_record['job'];meta=_read(lane/'meta.json')
  ctx=research._workspace_inputs(lane,meta);r=ctx['annotation_uncertainty_research'];prior_inputs={k:v for k,v in r.items() if k not in ('stage','research_result','research_result_digest')}
  with patch.object(prior,'base',lambda:copy.deepcopy(prior_inputs)),patch.object(prior,'protected',lambda value:protected(value['context']['protected_artifact_sha256'])):
   contract=prior.contract('diagnostic',r);verified_meta,raw=research._verify_job(module.PRIOR/'run-output',job=contract['job'],expected_prompt=contract['prompt'],schema_text=contract['schema_text'],workspace_context=contract['workspace_context'])
   if digest(verified_meta)!=prior_record['meta_digest'] or digest(raw)!=prior_record['result_digest'] or raw!=prior_record['result']:raise ValueError('Prior diagnostic source changed')
  # Snapshot base is still bound to the exact authenticated original task.
  allowed={'request_kind','claim_revision_policy_version','known_reference_input','known_reference_digest'}
  for key,value in original['inputs'].items():
   if key not in allowed and inputs.get(key)!=value:raise ValueError('Successor foreign historical scope')
  if inputs['original_result']!=original['research_result'] or inputs['original_result_digest']!=digest(original['research_result']):raise ValueError('Successor original claims changed')
  captures=original['source_capture']['references'];refs=copy.deepcopy(original['inputs']['known_reference_input']);refs.update({r['reference_id']:{'kind':r['kind'],'content':r['content']} for r in captures})
  if inputs['known_reference_input']!=refs or inputs['known_reference_digest']!=digest(refs):raise ValueError('Captured source inventory changed')
  associations=inputs['new_capture_associations']
  if associations['original_capture_state_digest']!=digest(original['source_capture']) or associations['by_issue']!={i:[r['reference_id'] for r in captures] for i in inputs['issue_ids']}:raise ValueError('New source association changed')
  protected(inputs['context']['protected_artifact_sha256']);protected(inputs['protected_original_sha256'])
  with patch.object(module,'base',lambda:copy.deepcopy(inputs)),patch.object(module,'protected',lambda value:protected(value['context']['protected_artifact_sha256'])):
   result=module.replay(evidence)
 if result.get('approved') is not True or result.get('all_nine_reviewed') is not True:raise ValueError('Research remains unresolved')
 if len(evidence['derived_result']['findings'])!=9 or any(r['status']!='supported' for r in evidence['derived_result']['findings']):raise ValueError('Research scope is incomplete')
 return evidence

def make_handoff(descriptor,*,before_entry,registry_before_text,coverage):
 evidence=_verify_source(descriptor);inputs=evidence['inputs'];candidate=inputs['candidate']
 if candidate['approved_lesson']!=before_entry or inputs['context']['target_entry_id']!=before_entry['id']:raise ValueError('Research does not concern current published entry')
 inventory={'shortened_occurrences':candidate['shortened_occurrences'],'full_form_contrasts':candidate['full_form_contrasts'],'reporting_contrasts':candidate['reporting_contrasts']}
 if [len(inventory[k]) for k in inventory]!=[7,14,6]:raise ValueError('Complete research contrast inventory missing')
 registry_sha=hashlib.sha256(registry_before_text.encode()).hexdigest()
 if registry_sha!=inputs['context']['protected_artifact_sha256']['content/lexicon/korean/l1.grammar.json']:raise ValueError('Current published registry is stale')
 packet={'version':2,'protected_data_snapshots':_data_snapshots(evidence),'purpose':'published_registry_revision_only','language':'ko','entry_kind':'grammar','source':descriptor,'source_evidence_digest':evidence['digest'],'before_entry':copy.deepcopy(before_entry),'registry_sha256':registry_sha,'registry_before_text':registry_before_text,'coverage':copy.deepcopy(coverage),'research_inventory':inventory,'approved_claims':evidence['derived_result'],'known_references':inputs['known_reference_input'],'allowed_fields':['explanation_en']}
 packet['digest']=digest(packet);return packet

def verify_handoff(packet,*,before_entry=None,coverage=None,historical=False):
 if packet.get('version')==2 and (type(packet['version']) is not int or packet.get('language')!='ko' or packet.get('entry_kind')!='grammar'):
  raise ValueError('Policy2 research scope must be Korean grammar')
 if packet.get('version') not in (1,2) or packet.get('purpose')!='published_registry_revision_only' or packet.get('allowed_fields')!=['explanation_en']:raise ValueError('Unknown dictionary handoff contract')
 if packet.get('version')==1 and 'protected_data_snapshots' in packet:raise ValueError('Legacy source policy cannot archive data')
 if packet['digest']!=digest({k:v for k,v in packet.items() if k!='digest'}):raise ValueError('Dictionary research handoff changed')
 if not historical:
  registry=ROOT/'content/lexicon/korean/l1.grammar.json'
  if _sha(registry)!=packet['registry_sha256']:raise ValueError('Current published registry changed')
  from pipeline.korean_dictionary_revision import _verify_coverage_fresh
  _verify_coverage_fresh(packet['coverage'])
 raw=packet['registry_before_text'].encode()
 if hashlib.sha256(raw).hexdigest()!=packet['registry_sha256']:raise ValueError('Archived published registry changed')
 document=json.loads(packet['registry_before_text'])
 entries=document['entries']
 if [row for row in entries if row['id']==packet['before_entry']['id']]!=[packet['before_entry']]:raise ValueError('Archived published entry differs')
 evidence=_verify_source(packet['source'],historical_packet=packet if historical else None)
 if evidence['digest']!=packet['source_evidence_digest'] or evidence['derived_result']!=packet['approved_claims'] or evidence['inputs']['known_reference_input']!=packet['known_references']:raise ValueError('Dictionary handoff research claims changed')
 candidate=evidence['inputs']['candidate'];inventory={k:candidate[k] for k in ('shortened_occurrences','full_form_contrasts','reporting_contrasts')}
 if inventory!=packet['research_inventory'] or candidate['approved_lesson']!=packet['before_entry']:raise ValueError('Dictionary handoff source inventory changed')
 if before_entry is not None and before_entry!=packet['before_entry']:raise ValueError('Published entry changed before revision')
 if coverage is not None and coverage!=packet['coverage']:raise ValueError('Current occurrence coverage changed')
 return packet

def scoped_schema(packet):
 entry=packet['before_entry'];properties={k:({'type':'string','minLength':1,'not':{'const':v}} if k=='explanation_en' else {'const':v}) for k,v in entry.items()}
 urls=sorted({r['content']['url'] for r in packet['known_references'].values() if r['kind']=='primary_source' and 'captured_text' in r['content']})
 return {'type':'object','additionalProperties':False,'required':['entries','references'],'properties':{'entries':{'type':'array','minItems':1,'maxItems':1,'items':{'type':'object','additionalProperties':False,'required':list(entry),'properties':properties}},'references':{'type':'array','minItems':1,'items':{'type':'object','additionalProperties':False,'required':['entry_id','url','paraphrase_en'],'properties':{'entry_id':{'const':entry['id']},'url':{'enum':urls},'paraphrase_en':{'type':'string','minLength':1}}}}}}

def validate_scoped_revision(output,packet):
 from jsonschema import validate
 verify_handoff(packet,historical=True);validate(output,scoped_schema(packet))
 from pipeline.korean_dictionary_revision import check_revision
 check_revision(output,{packet['before_entry']['id']:packet['before_entry']},'grammar')
