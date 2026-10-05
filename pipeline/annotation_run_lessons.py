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
  if (not isinstance(envelope,dict) or set(envelope)!={'version','language','representation','position','lessons'} or type(envelope['version']) is not int or envelope['version']!=1 or envelope['language']!=language or envelope['representation']!=representation or not isinstance(envelope['lessons'],list) or not 1<=len(envelope['lessons'])<=3):raise RunLessonError('Unsupported run lesson envelope or language')
  position=_position(context,source_text)
  if position is None or envelope['position']!=position:raise RunLessonError('Run lesson source position changed')
  references={};lessons=[];seen=set()
  for row in envelope['lessons']:
   if not isinstance(row,dict) or set(row)!={'source_run_relpath','import_receipt','expected_context'}:raise RunLessonError('Malformed run lesson descriptor')
   expected,proof=_authenticate(row,run_dir)
   if expected['language']!=language:raise RunLessonError('Run lesson source language differs')
   base,target=_source_scope(expected);path=target['target_path']
   if target['source_text']!=source_text or expected['chapter']['text'][target['source_start']:target['source_start']+len(source_text)]!=source_text:raise RunLessonError('Run lesson original source differs')
   expected_position=_position({'chapter_text':expected['chapter']['text'],'source_start':target['source_start']},source_text)
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
