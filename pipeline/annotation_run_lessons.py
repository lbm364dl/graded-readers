"""Additive exact-occurrence run lessons; independent evidence, never approval.
Proposal only. Existing fact-carry geometry and historical contexts stay intact.
"""
from copy import deepcopy
from pathlib import Path
import re
from pipeline.annotation_reference_carry import _position,_tap_geometry,ResearchCarryError
from pipeline.annotation_research import import_reviewed_lesson,_repo_relative_run_dir,_resolve_repo_run_dir,_validate_registered_lesson_context
from pipeline.annotation_review_ledger import resolve_pointer
from pipeline.annotation_adjudication import digest
RUN_LESSON_FIELD='reviewed_run_lessons'
RUN_LESSON_VERSION=1
RUN_LESSON_GUIDANCE='These independently reviewed run lessons are source-bound linguistic evidence, not annotation approval or registry promotion. Evaluate their substantive formation, pattern and function against the exact complete form and every current finding. A changed grammar identity must explicitly match the reviewed lesson; old fact carry is not permission to change identities. Preserve unresolved findings and source/tap boundaries.'
class RunLessonError(ValueError):pass

def _surfaces(candidate):
 rows=candidate.get('segments') if isinstance(candidate,dict) else None
 if not isinstance(rows,list):raise RunLessonError('Run lesson requires segment records')
 values=[row.get('surface',row.get('text')) for row in rows]
 if any(not isinstance(value,str) for value in values):raise RunLessonError('Run lesson lacks exact tap surfaces')
 return values

def _masked_owner(candidate,path):
 parts=path.strip('/').split('/')
 if len(parts)<4 or parts[0]!='segments' or not parts[1].isdigit():raise RunLessonError('Run lesson target must be an exact segment identity field')
 if not re.fullmatch(r'/segments/\d+/form_steps/\d+/(?:grammar_entry_ids|grammar_ids)/\d+|/segments/\d+/form_steps/\d+/(?:grammar_entry_id|grammar_id)',path):raise RunLessonError('Run lesson target is not a grammar formation identity')
 owner=deepcopy(candidate['segments'][int(parts[1])]);cursor=owner
 for part in parts[2:-1]:cursor=cursor[int(part)] if isinstance(cursor,list) else cursor[part]
 last=parts[-1]
 if isinstance(cursor,list):cursor[int(last)]={'_reviewed_identity_target':True}
 else:cursor[last]={'_reviewed_identity_target':True}
 return _tap_geometry(owner)

def _source_scope(expected):
 """New C/J overlay scope is explicit; historical Korean source is unchanged."""
 target=expected.get('target_occurrence',{});chapter=expected.get('chapter',{})
 if expected.get('reviewed_run_lesson_scope_version')==2:
  if type(expected['reviewed_run_lesson_scope_version']) is not int or expected.get('language') not in ('zh','ja'):raise RunLessonError('Invalid overlay source scope version/language')
  match=re.fullmatch(r'/grammar_overlays/(\d+)/grammar_candidate_key',target.get('target_path',''))
  index=target.get('overlay_index');start=target.get('start');end=target.get('end');offset=target.get('source_start');source=target.get('source_text')
  if not match or type(index) is not int or index!=int(match[1]) or any(type(value) is not int for value in (start,end,offset)) or not 0<=start<end or offset<0 or not isinstance(source,str):raise RunLessonError('Malformed exact overlay target')
  base={'segments':chapter.get('segments'),'grammar_overlays':chapter.get('grammar_overlays')}
  overlay=base['grammar_overlays'][index];surface=overlay.get('surface',overlay.get('text'))
  if ''.join(_surfaces(base))!=source or (start,end)!=(overlay.get('start'),overlay.get('end')) or source[start:end]!=surface or surface!=target.get('surface') or chapter['text'][offset:offset+len(source)]!=source:raise RunLessonError('Overlay source interval or tap geometry changed')
  return base,target
 if 'reviewed_run_lesson_scope_version' in expected:raise RunLessonError('Unsupported source scope version')
 geometry_context=deepcopy(expected)
 if expected.get('language')=='ja':
  for segment in geometry_context.get('chapter',{}).get('segments',[]):
   if isinstance(segment,dict) and 'surface' in segment:segment.pop('text',None)
 _validate_registered_lesson_context(geometry_context)
 return {'segments':chapter['segments']},target

def _masked_overlay(candidate,path):
 index=int(path.split('/')[2]);owner=deepcopy(candidate['grammar_overlays'][index])
 owner['grammar_candidate_key']={'_reviewed_identity_target':True}
 owner['pattern']={'_reviewed_pattern_target':True}
 return _tap_geometry(owner)

def _authenticate(row,run_dir):
 try:
  source=_resolve_repo_run_dir(row['source_run_relpath']);expected=row['expected_context'];receipt=row['import_receipt']
  _source_scope(expected)
  if expected.get('language') not in {'zh','ja','ko'}:raise RunLessonError('Run lesson source requires explicit language')
  proof=import_reviewed_lesson(Path(run_dir).absolute(),receipt['writer_job'],receipt['reviewer_job'],lesson_id=receipt['lesson_id'],expected_context=expected,issue_ids=receipt['issue_ids'],source_run_dir=source,_persist=False,_receipt_version=receipt['version'])
  if proof['evidence']!=receipt:raise RunLessonError('Run lesson writer/critic provenance changed')
  ref=next(iter(proof['references'].values()));lesson=ref['content']
  if not isinstance(receipt['lesson_id'],str) or not receipt['lesson_id'] or lesson.get('id')!=receipt['lesson_id']:raise RunLessonError('Run lesson identity does not match exact imported entry')
  return expected,proof
 except (OSError,KeyError,TypeError,ValueError) as exc:
  if isinstance(exc,RunLessonError):raise
  raise RunLessonError('Run lesson source receipt does not authenticate') from exc

def make_run_lesson_envelope(run_dir,*,source_run_dir,import_receipt,expected_context,language,representation,context):
 row={'source_run_relpath':_repo_relative_run_dir(source_run_dir),'import_receipt':deepcopy(import_receipt),'expected_context':deepcopy(expected_context)}
 _authenticate(row,run_dir)
 if expected_context['language']!=language:raise RunLessonError('Run lesson source language differs')
 source=expected_context['target_occurrence']['source_text'];position=_position(context,source)
 if position is None:raise RunLessonError('Run lesson requires complete source position')
 return {'version':1,'language':language,'representation':representation,'position':position,'lessons':[row]}

def _owned_identity_paths(candidate,target,old_id,lesson_id,language):
 paths={target['target_path']}
 if language=='ko' and target['target_path'].startswith('/segments/'):
  owner=target['segment_index'];surface=target['surface']
  for index,row in enumerate(candidate.get('grammar_links',[])):
   if not isinstance(row,dict) or row.get('segment_index')!=owner or row.get('entry_id') not in (old_id,lesson_id):continue
   direct=row.get('display_form')=='' and row.get('display_end_segment_index')==-1
   complete=row.get('display_form')==surface and row.get('display_end_segment_index')==owner
   if direct or complete:paths.add('/grammar_links/'+str(index)+'/entry_id')
 return paths

def validate_run_lesson_context_fields(context):
 """Substantive lesson snapshots require provenance in their own container."""
 if not isinstance(context,dict):return
 nested_normalization=context.get('chunk_review_context',{})
 if isinstance(nested_normalization,dict) and context.get('annotation_run_knowledge_normalization') is not None and nested_normalization.get('annotation_run_knowledge_normalization') is not None and context['annotation_run_knowledge_normalization']!=nested_normalization['annotation_run_knowledge_normalization']:raise RunLessonError('Root and nested identity normalization proofs disagree')
 for container in (context,nested_normalization):
  if isinstance(container,dict) and container.get('annotation_run_knowledge_normalization') is not None and container.get(RUN_LESSON_FIELD) is None:raise RunLessonError('Identity normalization requires its own authenticated run knowledge envelope')
 nested=context.get('chunk_review_context')
 if isinstance(nested,dict) and context.get(RUN_LESSON_FIELD) is not None and nested.get(RUN_LESSON_FIELD) is not None and context[RUN_LESSON_FIELD]!=nested[RUN_LESSON_FIELD]:
  raise RunLessonError('Root and nested run lesson envelopes disagree')
 for container in (context,context.get('chunk_review_context')):
  if isinstance(container,dict) and 'reviewed_run_grammar' in container and container.get(RUN_LESSON_FIELD) is None:
   raise RunLessonError('Run grammar content requires its own authenticated lesson envelope')

def validate_run_lessons(run_dir,envelope,*,candidate,source_text,language,representation,context,current_review=None):
 validate_run_lesson_context_fields(context)
 if envelope is None:return {'packet':{},'references':{},'lessons':[]}
 try:
  if (not isinstance(envelope,dict) or set(envelope)!={'version','language','representation','position','lessons'} or type(envelope['version']) is not int or envelope['version'] not in (1,2) or envelope['language']!=language or envelope['representation']!=representation or not isinstance(envelope['lessons'],list) or not 1<=len(envelope['lessons'])<=3):raise RunLessonError('Unsupported run lesson envelope or language')
  position=_position(context,source_text)
  if position is None or envelope['position']!=position:raise RunLessonError('Run lesson source position changed')
  references={};lessons=[];seen=set()
  for row in envelope['lessons']:
   if not isinstance(row,dict) or set(row) not in ({'source_run_relpath','import_receipt','expected_context'}, {'source_run_relpath','import_receipt','expected_context','application'}) or ('application' in row and envelope['version']!=2):raise RunLessonError('Malformed run lesson descriptor')
   expected,proof=_authenticate(row,run_dir)
   if expected['language']!=language:raise RunLessonError('Run lesson source language differs')
   if 'application' in row:
    base,target=_application_scope(row['application'],source_text,language,representation)
   else:
    base,target=_source_scope(expected)
   path=target['target_path']
   if 'application' not in row and (target['source_text']!=source_text or expected['chapter']['text'][target['source_start']:target['source_start']+len(source_text)]!=source_text):raise RunLessonError('Run lesson original source differs')
   expected_position=(_position({'chapter_text':expected['chapter']['text'],'source_start':target['source_start']},source_text) if 'application' not in row else position)
   if any(position.get(key)!=value for key,value in expected_position.items()):raise RunLessonError('Run lesson original parent or offset differs')
   if _surfaces(base)!=_surfaces(candidate) or ''.join(_surfaces(candidate))!=source_text:raise RunLessonError('Run lesson source/tap geometry changed')
   old_id=resolve_pointer(base,path);new_id=resolve_pointer(candidate,path);lesson_id=row['import_receipt']['lesson_id']
   if not isinstance(old_id,str) or new_id not in (old_id,lesson_id):raise RunLessonError('Run lesson target identity changed to unrelated lesson')
   if path.startswith('/grammar_overlays/'):
    index=target['overlay_index'];old_overlay=base['grammar_overlays'][index];new_overlay=candidate['grammar_overlays'][index]
    lesson=next(iter(proof['references'].values()))['content']
    if new_overlay.get('pattern') not in (old_overlay.get('pattern'),lesson.get('pattern')):raise RunLessonError('Overlay pattern is not the exact reviewed pattern')
    if _masked_overlay(base,path)!=_masked_overlay(candidate,path):raise RunLessonError('Run lesson overlay owner/source/components changed')
   elif _masked_owner(base,path)!=_masked_owner(candidate,path):raise RunLessonError('Run lesson target form, lexical identity or stages changed')
   for identity,ref in proof['references'].items():
    if identity in seen or ref['content']['id'] in seen:raise RunLessonError('Duplicate run lesson proof or identity')
    seen.update((identity,ref['content']['id']))
    lesson={key:deepcopy(value) for key,value in ref['content'].items() if key not in ('_annotation_research_fact','issue_ids')}
    lessons.append(lesson)
    if current_review is not None:
     from pipeline.annotation_adjudication import normalize_review
     from pipeline.annotation_issue_targets import issue_target_paths
     issue_paths={}
     owned_paths=_owned_identity_paths(candidate,target,old_id,lesson_id,language)
     for issue in normalize_review(language,current_review)['issues']:
      paths=issue_target_paths(issue['issue'],candidate,source_text=source_text,representation=representation)
      applicable=sorted(set(paths or []) & owned_paths)
      if applicable:issue_paths[issue['issue_id']]=applicable
     if issue_paths:
      issue_ids=list(issue_paths)
      references[identity]={'kind':'approved_lesson','content':{**lesson,'_annotation_research_fact':True,'issue_ids':issue_ids,'_annotation_run_lesson_scope':{'version':1,'issue_paths':issue_paths}},'issue_ids':issue_ids}

  for substantive in (context,context.get('chunk_review_context')):
   if isinstance(substantive,dict) and 'reviewed_run_grammar' in substantive and substantive['reviewed_run_grammar'] != lessons:raise RunLessonError('Run lesson substantive snapshot changed')
  for normalization_context in (context,context.get('chunk_review_context')):
   if isinstance(normalization_context,dict) and normalization_context.get('annotation_run_knowledge_normalization') is not None:
    from pipeline.annotation_run_knowledge_selection import validate_normalization_context
    validate_normalization_context(run_dir,normalization_context,candidate,source_text,language,representation)
  return {'packet':deepcopy(envelope),'references':references,'lessons':lessons}
 except (OSError,KeyError,TypeError,IndexError,ValueError) as exc:
  if isinstance(exc,RunLessonError):raise
  raise RunLessonError('Run lesson envelope does not bind current source or identity') from exc

def load_run_lessons(run_dir,*,candidate,source_text,language,representation,context):
 """Select only exact-position descriptors from existing explicit source index.
 No job scan, inference, registry promotion, or inferred historical envelope.
 """
 from pipeline.annotation_research import _reviewed_lesson_source_manifest_path,MAX_REVIEWED_LESSON_SOURCES
 from pipeline.worker_paths import checked_regular_file
 import json
 root=Path(run_dir).absolute();path=_reviewed_lesson_source_manifest_path(root,create=False)
 if not path.exists():return None
 try:
  manifest=json.loads(checked_regular_file(path).read_text())
  if manifest.get('version')!=1 or not isinstance(manifest.get('sources'),list) or len(manifest['sources'])>MAX_REVIEWED_LESSON_SOURCES or manifest.get('manifest_digest')!=digest({'version':manifest['version'],'sources':manifest['sources']}):raise RunLessonError('Run lesson source index integrity changed')
  position=_position(context,source_text)
  if position is None:return None
  rows=[]
  for descriptor in manifest['sources']:
   if not isinstance(descriptor,dict):raise RunLessonError('Malformed registered run lesson source')
   body={key:value for key,value in descriptor.items() if key!='descriptor_digest'}
   if descriptor.get('descriptor_digest')!=digest(body) or descriptor.get('expected_context_digest')!=digest(descriptor.get('expected_context')):raise RunLessonError('Run lesson source descriptor changed')
   expected=descriptor['expected_context'];target=expected.get('target_occurrence',{});chapter=expected.get('chapter',{})
   if expected.get('language')!=language or target.get('source_text')!=source_text or target.get('source_start')!=position['source_start'] or digest(chapter.get('text'))!=position['parent_text_digest']:continue
   source=_resolve_repo_run_dir(descriptor['source_run_relpath'])
   imported=import_reviewed_lesson(root,descriptor['writer_job'],descriptor['reviewer_job'],lesson_id=descriptor['lesson_id'],expected_context=expected,issue_ids=['_source_descriptor_registration'],source_run_dir=source,_persist=False)
   if imported['evidence']['evidence_digest']!=descriptor['source_pair_evidence_digest']:raise RunLessonError('Registered run lesson source proof changed')
   rows.append({'source_run_relpath':descriptor['source_run_relpath'],'import_receipt':imported['evidence'],'expected_context':expected})
  if not rows:return None
  envelope={'version':1,'language':language,'representation':representation,'position':position,'lessons':rows}
  validate_run_lessons(root,envelope,candidate=candidate,source_text=source_text,language=language,representation=representation,context=context)
  return envelope
 except (OSError,KeyError,TypeError,ValueError) as exc:
  if isinstance(exc,RunLessonError):raise
  raise RunLessonError('Run lesson source index does not authenticate') from exc


def enrich_run_lesson_context(run_dir,*,candidate,source_text,language,representation,context,current_review=None):
 """Add only authenticated applicable knowledge; absent context stays identical."""
 from pipeline.annotation_run_lesson_callers import resolve_run_lesson_context
 bound=resolve_run_lesson_context(run_dir,candidate=candidate,source_text=source_text,language=language,representation=representation,context=context,current_review=current_review)
 if not bound['packet']:return context,{}
 return {**context,RUN_LESSON_FIELD:bound['packet'],'reviewed_run_grammar':bound['lessons']},bound['references']


RUN_KNOWLEDGE_GUIDANCE='These independently reviewed standalone run lessons retain their original writer/critic source provenance. Their explicit current application scope is a proposed use at this source position, not a prior occurrence approval or published registry entry. Independently assess the lesson pattern, formation and function against this complete form, current targets and every finding. Reusing dictionary knowledge does not rebind original occurrence facts. Preserve uncertainty, source and tap geometry.'

def run_lesson_guidance(envelope):
 return RUN_KNOWLEDGE_GUIDANCE if isinstance(envelope,dict) and envelope.get('version')==2 else RUN_LESSON_GUIDANCE

def _application_scope(application,source_text,language,representation):
 if not isinstance(application,dict) or set(application)!={'candidate','source_text','target_path'} or application['source_text']!=source_text:raise RunLessonError('Malformed or stale current lesson application')
 base=application['candidate'];path=application['target_path']
 if ''.join(_surfaces(base))!=source_text:raise RunLessonError('Application source/tap geometry differs')
 if language=='ko' and representation=='korean-flat':
  if not re.fullmatch(r'/segments/\d+/form_steps/\d+/grammar_entry_ids/\d+',path):raise RunLessonError('Application requires exact Korean stage identity')
  _masked_owner(base,path)
  index=int(path.split('/')[2]);target={'target_path':path,'segment_index':index,'surface':_surfaces(base)[index],'source_text':source_text}
 elif (language,representation) in (('zh','chinese-annotation'),('zh','chinese-fixed'),('ja','japanese-annotation')):
  if not re.fullmatch(r'/grammar_overlays/\d+/grammar_candidate_key',path):raise RunLessonError('Application requires exact C/J overlay identity')
  index=int(path.split('/')[2]);overlay=base['grammar_overlays'][index];start=overlay['start'];end=overlay['end'];surface=overlay.get('surface',overlay.get('text'))
  if type(start) is not int or type(end) is not int or not 0<=start<end<=len(source_text) or source_text[start:end]!=surface:raise RunLessonError('Application overlay source interval differs')
  target={'target_path':path,'overlay_index':index,'start':start,'end':end,'surface':surface,'source_text':source_text}
 else:raise RunLessonError('Unsupported current lesson application representation')
 if not isinstance(resolve_pointer(base,path),str) or not resolve_pointer(base,path):raise RunLessonError('Application identity must be explicit')
 return base,target

def make_reusable_run_lesson_envelope(run_dir,*,source_descriptor,candidate,source_text,target_path,language,representation,context):
 """Explicit coordinator selection; no source scan or inferred applicability."""
 row=deepcopy(source_descriptor)
 if not isinstance(row,dict) or set(row)!={'source_run_relpath','import_receipt','expected_context'}:raise RunLessonError('Reusable knowledge requires unchanged original descriptor')
 expected,_proof=_authenticate(row,run_dir)
 if expected['language']!=language:raise RunLessonError('Reusable lesson language differs')
 application={'candidate':deepcopy(candidate),'source_text':source_text,'target_path':target_path}
 _application_scope(application,source_text,language,representation)
 position=_position(context,source_text)
 if position is None or set(position)!={'chunk_index','source_start','source_text_digest','parent_text_digest'}:raise RunLessonError('Reusable lesson requires complete current source position')
 row['application']=application
 envelope={'version':2,'language':language,'representation':representation,'position':position,'lessons':[row]}
 validate_run_lessons(run_dir,envelope,candidate=candidate,source_text=source_text,language=language,representation=representation,context=context)
 return envelope


def _application_index_path(run_dir,language,representation,context,source_text,create=False):
 from pipeline.worker_paths import checked_directory
 root=checked_directory(Path(run_dir).absolute(),create=create)
 position=_position(context,source_text)
 if position is None:raise RunLessonError('Application index requires complete source position')
 directory=root/'annotation-run-applications'
 if create:checked_directory(directory,create=True)
 key=digest({'language':language,'representation':representation,'position':position})
 return directory/(key+'.json')

def load_run_lesson_applications(run_dir,*,candidate,source_text,language,representation,context):
 import json
 from pipeline.worker_paths import checked_regular_file
 if not (Path(run_dir).absolute()/'annotation-run-applications').exists():return None
 path=_application_index_path(run_dir,language,representation,context,source_text)
 if not path.exists():return None
 document=json.loads(checked_regular_file(path).read_text())
 if set(document)!={'version','packet','digest'} or document['version']!=1 or document['digest']!=digest(document['packet']):raise RunLessonError('Run application index changed')
 return validate_run_lessons(run_dir,document['packet'],candidate=candidate,source_text=source_text,language=language,representation=representation,context=context)['packet']

def register_run_lesson_application(run_dir,envelope,*,candidate,source_text,language,representation,context):
 """Persist explicit authenticated selection, never infer a linguistic match.
 Immutable worker inputs retain selected envelopes; publication does not consult
 this mutable index. Different source positions have independent locked files.
 """
 import os,stat,fcntl,json
 from pipeline.worker_paths import atomic_write_managed
 if not isinstance(envelope,dict) or envelope.get('version')!=2:raise RunLessonError('Application registration requires v2 envelope')
 bound=validate_run_lessons(run_dir,envelope,candidate=candidate,source_text=source_text,language=language,representation=representation,context=context)
 path=_application_index_path(run_dir,language,representation,context,source_text,create=True)
 fd=os.open(str(path.with_suffix('.lock')),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:
  info=os.fstat(fd)
  if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise RunLessonError('Application lock must be single-link regular file')
  fcntl.flock(fd,fcntl.LOCK_EX)
  previous=load_run_lesson_applications(run_dir,candidate=candidate,source_text=source_text,language=language,representation=representation,context=context)
  packet=deepcopy(bound['packet'])
  if previous:
   from pipeline.annotation_run_lesson_callers import _source_key
   keys={_source_key(row) for row in packet['lessons']}
   packet['lessons'] += [row for row in previous['lessons'] if _source_key(row) not in keys]
   validate_run_lessons(run_dir,packet,candidate=candidate,source_text=source_text,language=language,representation=representation,context=context)
  document={'version':1,'packet':packet,'digest':digest(packet)}
  atomic_write_managed(path.parent,path.name,(json.dumps(document,ensure_ascii=False,indent=2)+'\n').encode())
  return packet
 finally:os.close(fd)


def authenticated_run_lesson_catalog(run_dir,source_descriptors,*,language):
 """Explicit reusable dictionary selection input, without occurrence applicability.
 A coordinator may expose this catalog to a selector; selected uses still require
 make/register application plus fresh annotation review. Not registry promotion.
 """
 if language not in ('ko','zh','ja') or not isinstance(source_descriptors,list) or not 1<=len(source_descriptors)<=3:raise RunLessonError('Invalid standalone run catalog')
 rows=[];lessons=[];seen={}
 from pipeline.annotation_run_lesson_callers import _source_key
 for row in source_descriptors:
  if not isinstance(row,dict) or set(row)!={'source_run_relpath','import_receipt','expected_context'}:raise RunLessonError('Catalog requires original authenticated dictionary descriptors')
  expected,proof=_authenticate(row,run_dir)
  if expected['language']!=language:raise RunLessonError('Catalog source language differs')
  ref=next(iter(proof['references'].values()));lesson={key:deepcopy(value) for key,value in ref['content'].items() if key not in ('_annotation_research_fact','issue_ids')};identity=lesson['id'];key=_source_key(row)
  if identity in seen:
   if seen[identity]!=key:raise RunLessonError('Catalog has conflicting independent source pairs for same identity')
   continue
  seen[identity]=key;rows.append(deepcopy(row));lessons.append(lesson)
 return {'version':1,'language':language,'sources':rows,'lessons':lessons}
