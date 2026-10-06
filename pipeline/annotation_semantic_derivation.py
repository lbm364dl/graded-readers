"""Authenticate semantic repair followed by identity-only normalization.

This proof qualifies evidence review, never annotation acceptance. Historical
plain semantic assemblies retain their exact existing replay path.
"""
from pathlib import Path
import json
from pipeline.annotation_adjudication import digest
from pipeline.annotation_repairs import _safe_job_path,replay_annotation_repair
from pipeline.worker_paths import checked_regular_file

class SemanticDerivationError(ValueError):pass

def verified_semantic_derivation(run_dir, *, candidate, source_text, language,
                                 representation, validate_candidate, record=None,
                                 semantic_job=None, normalization=None, context=None):
 """Authenticate CURRENT producer order; normalizing ordinary work is not repair."""
 root=Path(run_dir).absolute();normal_meta=None;normal_result=None;normal_producer=False
 if record:
  if language=='ko':
   from pipeline.korean_agent_harness import digest as korean_digest
   if record.get('text')!=source_text or record.get('digest')!=korean_digest(candidate):raise SemanticDerivationError('Current Korean producer record does not bind exact source and candidate')
  directory=_safe_job_path(root,record['job'])
  metadata=json.loads(checked_regular_file(directory/'meta.json').read_text())
  if metadata.get('kind')=='annotation_run_knowledge_normalization':
   normalization={'job':record['job'],'meta_digest':digest(metadata),'result_digest':metadata['result_digest']};normal_producer=True
  elif metadata.get('kind')=='annotation_patch_assembly':semantic_job=record['job']
  elif not normalization:return None
 # A current applied repair may follow an older normalization. Authenticate it
 # first rather than pretending that retained context is the current producer.
 plain=None;plain_meta=None
 if semantic_job is not None:
  directory=_safe_job_path(root,semantic_job);plain_meta=json.loads(checked_regular_file(directory/'meta.json').read_text())
  if plain_meta.get('kind')=='annotation_patch_assembly':
   if plain_meta.get('status')!='applied' or plain_meta.get('representation')!=representation:raise SemanticDerivationError('Semantic derivation parent is not applied in the matching representation')
   plain=replay_annotation_repair(root,semantic_job,validate_candidate=validate_candidate)
 if plain is not None and plain.get('status')=='applied' and plain.get('candidate')==candidate and not normal_producer:
  if normalization is not None:
   from pipeline.annotation_run_knowledge_selection import validate_normalization_context,FIELD
   if context is None:raise SemanticDerivationError('Retained normalization requires exact current context')
   nested=context.get('chunk_review_context',{})
   if any(FIELD in row and row[FIELD]!=normalization for row in (context,nested) if isinstance(row,dict)):raise SemanticDerivationError('Current context normalization differs from authenticated producer')
   validate_normalization_context(root,{**context,FIELD:normalization},candidate,source_text,language,representation)
  return {'semantic_job':semantic_job,'semantic_meta':plain_meta,'replayed':plain,'context_evidence':None}
 if normalization:
  from pipeline.annotation_run_knowledge_selection import replay_normalization,validate_normalization_context,FIELD
  normal_result,normal_meta=replay_normalization(root,normalization['job']);inputs=normal_meta['inputs']
  if (normalization.get('meta_digest')!=digest(normal_meta)
      or normalization.get('result_digest')!=digest(normal_result)
      or inputs['language']!=language or inputs['representation']!=representation
      or inputs['source_text']!=source_text or normal_result!=candidate):raise SemanticDerivationError('Normalized semantic derivation does not bind current immutable candidate')
  if context is not None:
   nested=context.get('chunk_review_context',{})
   if any(FIELD in row and row[FIELD]!=normalization for row in (context,nested) if isinstance(row,dict)):raise SemanticDerivationError('Current context normalization differs from authenticated producer')
   validate_normalization_context(root,{**context,FIELD:normalization},candidate,source_text,language,representation)
  parent=normal_meta.get('base_record')
  if parent:
   if semantic_job is not None and semantic_job!=parent['job']:raise SemanticDerivationError('Normalization and explicit semantic parent disagree')
   semantic_job=parent['job']
  if semantic_job is None:return None
 else:
  if semantic_job is None:return None
  raise SemanticDerivationError('Current semantic repair does not match the reviewed candidate')
 directory=_safe_job_path(root,semantic_job);meta=json.loads(checked_regular_file(directory/'meta.json').read_text())
 if meta.get('kind')!='annotation_patch_assembly':return None
 if meta.get('status')!='applied' or meta.get('representation')!=representation:raise SemanticDerivationError('Normalization origin is not an applied matching semantic assembly')
 replayed=plain if plain is not None else replay_annotation_repair(root,semantic_job,validate_candidate=validate_candidate)
 if replayed.get('status')!='applied' or replayed.get('candidate')!=normal_meta['inputs']['candidate']:raise SemanticDerivationError('Semantic repair output does not match exact normalization base')
 validate_candidate(candidate)
 body={'version':1,'language':language,'representation':representation,
       'source_text_digest':digest(source_text),'candidate_digest':digest(candidate),
       'semantic_assembly_job':semantic_job,'semantic_meta_digest':digest(meta),
       'normalization':normalization,'normalization_base_digest':digest(normal_meta['inputs']['candidate'])}
 return {'semantic_job':semantic_job,'semantic_meta':meta,'replayed':replayed,
         'context_evidence':{**body,'digest':digest(body)}}


def verify_semantic_derivation_context(run_dir, context, *, candidate, source_text,
                                        language, representation):
 """Fresh adjudication and publication replay authenticate additive provenance."""
 if not isinstance(context,dict):return
 nested=context.get('chunk_review_context',{})
 markers=[row['verified_semantic_derivation'] for row in (context,nested)
          if isinstance(row,dict) and 'verified_semantic_derivation' in row]
 if not markers:return
 if any(row!=markers[0] for row in markers):raise SemanticDerivationError('Conflicting semantic derivation markers')
 evidence=markers[0]
 if not isinstance(evidence,dict) or type(evidence.get('version')) is not int or evidence['version']!=1:raise SemanticDerivationError('Unknown semantic derivation version')
 from pipeline.annotation_run_lessons import _surfaces
 def source_gate(value):
  if not isinstance(source_text,str) or ''.join(_surfaces(value))!=source_text:raise SemanticDerivationError('Semantic derivation source reconstruction changed')
 actual=verified_semantic_derivation(run_dir,candidate=candidate,source_text=source_text,
  language=language,representation=representation,validate_candidate=source_gate,
  semantic_job=evidence.get('semantic_assembly_job'),normalization=evidence.get('normalization'),context=context)
 if actual is None or actual['context_evidence']!=evidence:raise SemanticDerivationError('Semantic derivation evidence differs from authenticated chain')
