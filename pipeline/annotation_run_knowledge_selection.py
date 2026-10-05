"""Explicit authenticated standalone catalog selection; never annotation approval."""
from copy import deepcopy
from pathlib import Path
import json,re
from jsonschema import validate
from pipeline.annotation_adjudication import digest
from pipeline.annotation_reference_carry import _position,_tap_geometry
from pipeline.annotation_run_lessons import (RunLessonError,_authenticate,_application_scope,
 authenticated_run_lesson_catalog,make_reusable_run_lesson_envelope,register_run_lesson_application)
from pipeline.annotation_run_lesson_callers import _source_key
from pipeline.annotation_review_ledger import resolve_pointer
FIELD='annotation_run_knowledge_normalization'
POLICY='Select independently reviewed standalone grammar knowledge only when its exact pattern, formation and function fit this immutable current occurrence. Inspect complete forms and context, not spelling alone. Published approval and run approval are distinct. Select exact catalog provenance and legal current identity pointers, or none. This bounded pass permits at most one target per catalog source and one target per structural owner; leave additional occurrences for independently scoped followup. Do not edit meanings, stages, source, taps, ranges or occurrence context. A selection proposes an identity normalization, not linguistic annotation approval: a fresh independent annotation review follows and every existing finding remains open. Do not borrow an old occurrence fact or invent a current objection. Use tools and validate the exact artifact.'

def registered_catalog(run_dir,language):
 """Only the explicit existing reviewed source registrar, never a job scan."""
 from pipeline.annotation_research import (_reviewed_lesson_source_manifest_path,
  _resolve_repo_run_dir,import_reviewed_lesson,MAX_REVIEWED_LESSON_SOURCES)
 from pipeline.worker_paths import checked_regular_file
 root=Path(run_dir).absolute();path=_reviewed_lesson_source_manifest_path(root,create=False)
 if not path.exists():return None
 document=json.loads(checked_regular_file(path).read_text())
 if document.get('version')!=1 or not isinstance(document.get('sources'),list) or len(document['sources'])>MAX_REVIEWED_LESSON_SOURCES or document.get('manifest_digest')!=digest({'version':1,'sources':document['sources']}):raise RunLessonError('Registered catalog integrity changed')
 rows=[]
 for descriptor in document['sources']:
  if descriptor.get('descriptor_digest')!=digest({key:value for key,value in descriptor.items() if key!='descriptor_digest'}) or descriptor.get('expected_context_digest')!=digest(descriptor.get('expected_context')):raise RunLessonError('Registered catalog descriptor changed')
  expected=descriptor['expected_context']
  if expected.get('language')!=language:continue
  source=_resolve_repo_run_dir(descriptor['source_run_relpath'])
  proof=import_reviewed_lesson(root,descriptor['writer_job'],descriptor['reviewer_job'],lesson_id=descriptor['lesson_id'],expected_context=expected,issue_ids=['_source_descriptor_registration'],source_run_dir=source,_persist=False)
  if proof['evidence']['evidence_digest']!=descriptor['source_pair_evidence_digest']:raise RunLessonError('Registered catalog source pair changed')
  rows.append({'source_run_relpath':descriptor['source_run_relpath'],'expected_context':expected,'import_receipt':proof['evidence']})
 return authenticated_run_lesson_catalog(root,rows,language=language) if rows else None

def legal_targets(candidate,language,representation,published_ids):
 paths=[]
 if (language,representation)==('ko','korean-flat'):
  for owner,row in enumerate(candidate.get('segments',[])):
   for step,item in enumerate(row.get('form_steps',[])):
    for index,identity in enumerate(item.get('grammar_entry_ids',[])):
     if identity:paths.append(f'/segments/{owner}/form_steps/{step}/grammar_entry_ids/{index}')
 elif (language,representation) in (('zh','chinese-annotation'),('ja','japanese-annotation')):
  for index,row in enumerate(candidate.get('grammar_overlays',[])):
   if row.get('grammar_candidate_key'):paths.append(f'/grammar_overlays/{index}/grammar_candidate_key')
 else:raise RunLessonError('Unsupported run knowledge selector representation')
 return paths

def request(inputs):
 keys=[_source_key(row) for row in inputs['catalog']['sources']]
 identities=[entry['id'] for entry in inputs['catalog']['lessons']]
 schema={'type':'object','additionalProperties':False,'required':['base_digest','applications','no_selection_reason'],'properties':{'base_digest':{'const':digest(inputs['candidate'])},'applications':{'type':'array','maxItems':3,'items':{'type':'object','additionalProperties':False,'required':['source_key','lesson_id','target_path','reason'],'properties':{'source_key':{'enum':keys},'lesson_id':{'enum':identities},'target_path':{'enum':inputs['legal_targets']},'reason':{'type':'string','minLength':1}}}},'no_selection_reason':{'type':'string'}}}
 prompt=POLICY+'\nInspect the organized annotation_run_knowledge_selection input.'
 workspace={'annotation_run_knowledge_selection':inputs,'annotation_run_knowledge_selection_validation':{'run_dir':inputs['run_dir'],'inputs':inputs}}
 return prompt,schema,workspace

def validate_selection(inputs,selection):
 _,schema,_=request(inputs);validate(selection,schema)
 catalog=authenticated_run_lesson_catalog(inputs['run_dir'],inputs['catalog']['sources'],language=inputs['language'])
 if catalog!=inputs['catalog']:raise RunLessonError('Selector catalog substantive/provenance snapshot changed')
 position=_position(inputs['context'],inputs['source_text'])
 if position is None or set(position)!={'chunk_index','source_start','source_text_digest','parent_text_digest'}:raise RunLessonError('Selector requires complete current position')
 if inputs['legal_targets']!=_eligible_targets(inputs):raise RunLessonError('Selector legal identity scope changed')
 sources={_source_key(row):row for row in catalog['sources']};seen=set();seen_sources=set();seen_owners=set();packets=[]
 for application in selection['applications']:
  path=application['target_path'];row=sources[application['source_key']]
  if application['lesson_id']!=row['import_receipt']['lesson_id'] or path in seen:raise RunLessonError('Selector identity/provenance mismatch or repeated target')
  owner=tuple(path.strip('/').split('/')[:2])
  if application['source_key'] in seen_sources or owner in seen_owners:raise RunLessonError('Bounded selection permits one application per source and structural owner')
  seen.add(path);seen_sources.add(application['source_key']);seen_owners.add(owner)
  packets.append(make_reusable_run_lesson_envelope(inputs['run_dir'],source_descriptor=row,candidate=inputs['candidate'],source_text=inputs['source_text'],target_path=path,language=inputs['language'],representation=inputs['representation'],context=inputs['context']))
 return packets

def normalized_candidate(inputs,selection):
 packets=validate_selection(inputs,selection);result=deepcopy(inputs['candidate']);edits=[]
 for selected in selection['applications']:
  path=selected['target_path'];old=resolve_pointer(result,path);new=selected['lesson_id'];paths=[path]
  if old in inputs['published_ids'] or old==new:continue
  if inputs['language']=='ko':
   owner=int(path.split('/')[2]);target_parts=path.split('/');target_step=int(target_parts[4]);target_id=int(target_parts[6])
   remaining_old=any(identity==old for step_index,step in enumerate(result['segments'][owner].get('form_steps',[])) for identity_index,identity in enumerate(step.get('grammar_entry_ids',[])) if (step_index,identity_index)!=(target_step,target_id))
   links=result.get('grammar_links',[])
   direct=lambda row:row.get('display_form','')=='' and row.get('display_end_segment_index',-1)==-1
   new_direct=any(row.get('segment_index')==owner and row.get('entry_id')==new and direct(row) for row in links)
   if any(row.get('segment_index')==owner and row.get('entry_id')==new and not direct(row) for row in links):raise RunLessonError('Selected identity needs supported occurrence-shape repair, not leaf normalization')
   old_direct=[index for index,row in enumerate(links) if row.get('segment_index')==owner and row.get('entry_id')==old and direct(row)]
   if remaining_old:
    if not new_direct:raise RunLessonError('Retained old identity needs its link; new identity requires an independently planned row operation')
   elif new_direct:
    if old_direct:raise RunLessonError('Normalization would need redundant-row removal; use independently reviewed semantic repair')
   else:
    if len(old_direct)!=1:raise RunLessonError('Normalization requires one exact existing dependent direct link')
    paths.append(f'/grammar_links/{old_direct[0]}/entry_id')
  for target in paths:
   parts=target.strip('/').split('/');cursor=result
   for part in parts[:-1]:cursor=cursor[int(part)] if isinstance(cursor,list) else cursor[part]
   key=parts[-1]
   if isinstance(cursor,list):cursor[int(key)]=new
   else:cursor[key]=new
   if old!=new:edits.append({'path':target,'old':old,'new':new})
 return result,packets,edits

async def select_and_normalize_run_knowledge(harness,index,candidate,source_text,*,language,representation,context,base_record=None):
 from pipeline.annotation_research import _check_runner_policy,_schema_path,_verify_job,_write_json
 root=Path(harness.run_dir).absolute();catalog=registered_catalog(root,language)
 if language=='ko':
  published_entries=list(getattr(harness,'grammar',{}).values());grammar_knowledge={'catalog_status':'published','approved_entries':published_entries}
 elif language=='ja':
  from pipeline.japanese_agent_harness import japanese_semantic_repair_grammar_knowledge
  grammar_knowledge=japanese_semantic_repair_grammar_knowledge();published_entries=grammar_knowledge['approved_entries']
 else:
  from pipeline.agent_harness import chinese_semantic_repair_grammar_knowledge
  grammar_knowledge=chinese_semantic_repair_grammar_knowledge(candidate);published_entries=[]
 published={entry['id'] for entry in published_entries}
 targets=legal_targets(candidate,language,representation,published) if catalog else []
 if not catalog or not targets:return {'candidate':candidate,'context':context,'record':base_record,'evidence':None}
 if language=='ko' and base_record is None:raise RunLessonError('Korean normalization requires authenticated original producer record')
 position=getattr(harness,'_annotation_source_positions',{}).get(index)
 if position is None:
  fallback=_position(context,source_text)
  if fallback is not None:position={'chunk_index':index,**fallback}
 context={**context,**({'annotation_source_position':position} if position else {})}
 actual_position=_position(context,source_text)
 if actual_position is None or set(actual_position)!={'chunk_index','source_start','source_text_digest','parent_text_digest'}:raise RunLessonError('Selector missing complete source position')
 inputs={'version':1,'run_dir':str(root),'candidate':candidate,'source_text':source_text,'language':language,'representation':representation,'context':context,'catalog':catalog,'published_ids':sorted(published),'legal_targets':targets,'base_record':base_record,'language_grammar_knowledge':grammar_knowledge}
 from pipeline.annotation_run_lessons import load_run_lesson_applications
 clean=deepcopy(context);clean.pop(FIELD,None)
 inputs['already_bound_packet']=load_run_lesson_applications(root,candidate=candidate,source_text=source_text,language=language,representation=representation,context=clean)
 inputs['legal_targets']=_eligible_targets(inputs)
 if not inputs['legal_targets']:return {'candidate':candidate,'context':context,'record':base_record,'evidence':None}
 prompt,schema,workspace=request(inputs);identity=digest(inputs);job='run-knowledge-selection-'+identity
 schema_path=_schema_path(root,job,schema);_check_runner_policy(harness.runner)
 selection=await harness.runner.call(job,prompt,schema_path,'low',tool_profile='workspace',workspace_context=workspace)
 _verify_job(root,job=job,expected_prompt=prompt,schema_text=schema_path.read_text(),workspace_context=workspace,expected_result=selection)
 updated,packets,edits=normalized_candidate(inputs,selection)
 for packet in packets:register_run_lesson_application(root,packet,candidate=candidate,source_text=source_text,language=language,representation=representation,context=context)
 if not edits:return {'candidate':candidate,'context':context,'record':base_record,'evidence':None}
 assembly='run-knowledge-normalization-'+identity
 metadata={'kind':'annotation_run_knowledge_normalization','version':1,'return_code':0,'inputs':inputs,'selection':selection,'selection_job':job,'base_record':base_record,'edits':edits,'result_digest':digest(updated)}
 from pipeline.annotation_repairs import _safe_job_path
 directory=_safe_job_path(root,assembly);_write_json(directory/'meta.json',metadata);_write_json(directory/'result.json',updated)
 evidence={'job':assembly,'meta_digest':digest(metadata),'result_digest':digest(updated)}
 context={**context,FIELD:evidence};getattr(harness,'_annotation_run_normalizations',{}).update({index:evidence})
 if not hasattr(harness,'_annotation_run_normalizations'):harness._annotation_run_normalizations={index:evidence}
 if base_record:
  from pipeline.korean_agent_harness import digest as korean_digest
  record={**base_record,'job':assembly,'digest':korean_digest(updated)}
 else:record=None
 if record:record.pop('raw_digest',None)
 return {'candidate':updated,'context':context,'record':record,'evidence':evidence}

def replay_normalization(run_dir,job):
 from pipeline.annotation_repairs import _safe_job_path
 from pipeline.annotation_research import _verify_job
 from pipeline.worker_paths import checked_regular_file
 directory=_safe_job_path(Path(run_dir).absolute(),job)
 meta=json.loads(checked_regular_file(directory/'meta.json').read_text());result=json.loads(checked_regular_file(directory/'result.json').read_text())
 if meta.get('kind')!='annotation_run_knowledge_normalization' or meta.get('version')!=1 or meta.get('return_code')!=0:raise RunLessonError('Invalid normalization assembly')
 inputs=meta['inputs']
 if meta.get('base_record')!=inputs.get('base_record'):raise RunLessonError('Normalization base lineage changed')
 prompt,schema,workspace=request(inputs)
 # json representation must match _schema_path's canonical writer exactly
 from pipeline.annotation_research import _json_bytes
 _verify_job(Path(run_dir).absolute(),job=meta['selection_job'],expected_prompt=prompt,schema_text=_json_bytes(schema).decode(),workspace_context=workspace,expected_result=meta['selection'])
 updated,_,edits=normalized_candidate(inputs,meta['selection'])
 if updated!=result or meta['edits']!=edits or meta['result_digest']!=digest(result):raise RunLessonError('Normalization identity leaves or result changed')
 if meta.get('base_record'):
  from pipeline.korean_agent_harness import read_annotation_chunk
  from pipeline.agent_harness import CodexRunner
  base=_safe_job_path(Path(run_dir).absolute(),meta['base_record']['job']);base_meta=json.loads(checked_regular_file(base/'meta.json').read_text());CodexRunner._check_tool_profile(base,'offline',base_meta)
  if read_annotation_chunk(base/'result.json',meta['base_record'])!=inputs['candidate']:raise RunLessonError('Normalization original producer lineage changed')
 return result,meta

def validate_normalization_context(run_dir,context,candidate,source_text,language,representation):
 evidence=context.get(FIELD)
 if evidence is None:return
 result,meta=replay_normalization(run_dir,evidence['job'])
 inputs=meta['inputs']
 if evidence.get('meta_digest')!=digest(meta) or evidence.get('result_digest')!=digest(result) or inputs['language']!=language or inputs['representation']!=representation or inputs['source_text']!=source_text or _position(context,source_text)!=_position(inputs['context'],source_text) or not _current_normalization_scope(meta,candidate,result):raise RunLessonError('Normalization proof differs from current source/geometry/identity')


def _current_normalization_scope(meta,candidate,derived):
 from pipeline.annotation_run_lessons import _surfaces
 if _surfaces(candidate)!=_surfaces(derived):return False
 # Bind each selected structural owner, not unrelated independently repaired identities.
 for edit in meta['edits']:
  path=edit['path']
  try:
   if resolve_pointer(candidate,path)!=edit['new']:return False
   parts=path.strip('/').split('/')
   owner='/'+ '/'.join(parts[:2])
   if _tap_geometry(resolve_pointer(candidate,owner))!=_tap_geometry(resolve_pointer(derived,owner)):return False
  except (KeyError,IndexError,TypeError):return False
 return True


def _eligible_targets(inputs):
 paths=legal_targets(inputs['candidate'],inputs['language'],inputs['representation'],set(inputs['published_ids']))
 packet=inputs.get('already_bound_packet')
 if packet:
  from pipeline.annotation_run_lessons import validate_run_lessons
  context=deepcopy(inputs['context']);context.pop(FIELD,None)
  validate_run_lessons(inputs['run_dir'],packet,candidate=inputs['candidate'],source_text=inputs['source_text'],language=inputs['language'],representation=inputs['representation'],context=context)
  bound={row.get('application',{}).get('target_path',row['expected_context']['target_occurrence']['target_path']) for row in packet['lessons']}
  paths=[path for path in paths if path not in bound]
 return paths
