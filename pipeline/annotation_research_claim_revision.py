"""Versioned local validation for bounded independently planned research edits."""
import copy
from pipeline.annotation_adjudication import digest,LINGUISTIC_REFERENCE_KINDS,_substantive_reference
from pipeline.annotation_review_ledger import resolve_pointer
from pipeline.annotation_research import validate_research_submission

def validate_claim_revision(output,request,*,request_digest,stage):
 if request.get('claim_revision_policy_version')!=2 or stage not in ('diagnostic','repair','final'):raise ValueError('Unknown research claim revision policy/stage')
 if request_digest!=digest(request) or request.get('stage')!=stage:raise ValueError('Claim revision immutable request changed')
 expected={'request_digest':request_digest,'original_result_digest':request['original_result_digest'],'source_association_digest':digest(request['new_capture_associations'])}
 if output.get('request_binding')!=expected:raise ValueError('Claim revision submission binding changed')
 if digest(request['original_result'])!=request['original_result_digest']:raise ValueError('Original research base changed')
 rows=request['research_result']['findings'];ids=request['issue_ids']
 if len(rows)!=len(ids) or {r['issue_id'] for r in rows}!=set(ids):raise ValueError('Research hypothesis coverage changed')
 if stage=='repair':
  diagnostic=request['diagnostic_review'];validate_claim_revision(diagnostic,{**request['diagnostic_request']},request_digest=digest(request['diagnostic_request']),stage='diagnostic')
  if any(a['status']=='uncertain' for a in diagnostic['assessments']):raise ValueError('Uncertain findings do not authorize repair')
  authorized={p for i in diagnostic['issues'] for p in i['candidate_paths']}
  if set(request['authorized_paths'])!=authorized:raise ValueError('Repair authority differs from independent diagnostic')
  if output['base_digest']!=digest(request['research_result']):raise ValueError('Research edit base changed')
  seen=set();derived=copy.deepcopy(request['research_result'])
  for e in output['edits']:
   p=e['path']
   if p not in authorized or p in seen:raise ValueError('Duplicate or unauthorized research leaf')
   seen.add(p);_,_,n,field=p.split('/')
   if e['value']==rows[int(n)][field]:raise ValueError('Declared defect target unchanged')
   derived['findings'][int(n)][field]=copy.deepcopy(e['value'])
  if seen!=authorized:raise ValueError('Unaddressed research defect target')
  validate_research_submission(derived,request)
  for row in derived['findings']:
   if row['status']=='supported' and not any(c['path']=='/captured_text' and request['known_reference_input'][c['reference_id']]['kind']=='primary_source' and 'captured_text' in request['known_reference_input'][c['reference_id']]['content'] for c in row['citations']):raise ValueError('Derived claim lacks captured primary evidence')
  return
 assessments=output['assessments'];issues=output['issues']
 if len(assessments)!=len(ids) or len({a['issue_id'] for a in assessments})!=len(ids) or {a['issue_id'] for a in assessments}!=set(ids):raise ValueError('Assessment hypothesis coverage changed')
 issue_ids={i['issue_id'] for i in issues}
 if len(issue_ids)!=len(issues):raise ValueError('Duplicate research issue')
 for a in assessments:
  if (a['status']=='supported')!=(a['issue_id'] not in issue_ids):raise ValueError('Assessment/issue mismatch')
  primary=False
  for c in a['citations']:
   ref=request['known_reference_input'].get(c['reference_id'])
   if ref is None or ref['kind'] not in LINGUISTIC_REFERENCE_KINDS:raise ValueError('Unknown assessment reference')
   if ref['kind']=='primary_source' and 'captured_text' not in ref['content']:raise ValueError('Origin metadata is not assessment evidence')
   value=resolve_pointer(ref['content'],c['path'])
   if not _substantive_reference({'path':c['path'],'value':value,'reference_kind':ref['kind']},reference_content=ref['content']):raise ValueError('Empty assessment citation')
   primary |= ref['kind']=='primary_source' and 'captured_text' in ref['content'] and c['path']=='/captured_text'
  if not primary:raise ValueError('Assessment lacks captured primary evidence')
 for issue in issues:
  if not issue['candidate_paths']:raise ValueError('Missing research defect target')
  for p in issue['candidate_paths']:
   parts=p.split('/')
   if len(parts)!=4 or parts[1]!='findings' or parts[3] not in ('fact','citations') or not parts[2].isdigit():raise ValueError('Invalid research defect leaf')
   n=int(parts[2])
   if n>=len(rows) or rows[n]['issue_id']!=issue['issue_id']:raise ValueError('Foreign research finding target')
 if output['approved']!=(not issues):raise ValueError('Approval has outstanding research issues')
