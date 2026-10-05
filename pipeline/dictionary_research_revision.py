"""Evidence-bound adapter over the existing bounded dictionary editorial route.

This proposal never promotes a registry or grants annotation approval.
"""
import copy,json
from pathlib import Path
from pipeline import korean_contracts as contracts,korean_dictionary_revision as editorial
from pipeline.annotation_adjudication import digest
from pipeline.korean_agent_harness import payload
from pipeline import annotation_research as research
from pipeline.dictionary_research_handoff import FIELD,verify_handoff,scoped_schema,validate_scoped_revision

GUIDANCE='''Use the supplied independently reviewed research claims and exact captured primary pages. Preserve entry ID, title and pattern; revise ONLY explanation_en. Explain the reusable pattern and its relevant formation, distinguishing shortened quotation from full quotation and the supplied copular/reporting contrasts. Do not copy occurrence translations, add unrelated transformation catalogs, or treat research approval as annotation/publication approval. Do not regenerate the facts or substitute uncaptured evidence. A genuine scope/source defect blocks this route and must be reported to the coordinator. Preserve the edited explanation when packaging the validated artifact. Read every supplied occurrence and contrast. Submit the exact validated artifact.'''
REVIEW_GUIDANCE='''Independently review the proposed explanation against ALL nine independently reviewed claims, their exact captured primary citations, seven shortened occurrences, fourteen full-form contrasts, six reporting contrasts and the complete published/draft occurrence coverage. Research approval does not compel approval of the explanation. Check standalone clarity, correct formation and meaning scope, preserved valid functions and unchanged ID/title/pattern. Report every unresolved finding. Approve only with no issues. Do not evaluate whether immutable annotations have already been repaired.'''

ARCHIVE_GUIDANCE='Read the organized dictionary_research_handoff approved_claims, known_references, research_inventory and coverage fields for semantic work. protected_data_snapshots base64 holds historical replay bytes, not additional semantic claims; avoid dumping the archive when reading the claims. Tools and all inputs remain available when needed.'

def instructions(packet,review=False,editorial_criteria=None):
 value=REVIEW_GUIDANCE if review else GUIDANCE
 value=value+' '+ARCHIVE_GUIDANCE if packet.get('version')==2 else value
 if editorial_criteria is not None:
  from pipeline.dictionary_editorial_criteria import guidance
  value+=' '+guidance(editorial_criteria)
 return value

def authenticated_prior_reviews(descriptor,packet):
 if descriptor is None:return None
 from pipeline.dictionary_research_handoff import ROOT,_sha
 if set(descriptor)!={'run_relpath','evidence_sha256'}:raise ValueError('Unknown prior editorial origin')
 run=(ROOT/descriptor['run_relpath']).resolve()
 if not run.is_relative_to(ROOT):raise ValueError('Foreign prior editorial root')
 path=run/'research-bound-revision-evidence.json'
 if path.is_symlink() or _sha(path)!=descriptor['evidence_sha256']:raise ValueError('Prior editorial evidence changed')
 prior=json.loads(path.read_text())
 if 'prior_editorial_origin' in prior:raise ValueError('Prior editorial chain exceeds one bounded authenticated handoff')
 replay(run,prior)
 if prior['handoff']['before_entry']!=packet['before_entry'] or prior['handoff']['source_evidence_digest']!=packet['source_evidence_digest'] or prior['handoff']['coverage']!=packet['coverage']:raise ValueError('Prior editorial scope differs')
 return {'origin':descriptor,'evidence_digest':prior['digest'],'reviews':[{'job':row['job'],'meta_digest':row['meta_digest'],'result_digest':row['result_digest'],'review':row['result'],'writer_proposal':prior['contracts'][i-1]['result']} for i,row in enumerate(prior['contracts']) if i%2]}

class ResearchBoundRunner:
 def __init__(self,runner,run_dir,packet,editorial_criteria=None,prior_editorial_origin=None):
  self.runner=runner;self.run_dir=Path(run_dir);self.packet=copy.deepcopy(packet);self.contracts=[];self.editorial_criteria=editorial_criteria;self.prior_editorial_origin=prior_editorial_origin
 async def call(self,name,prompt,schema,effort,**kwargs):
  if effort!='low' or kwargs.get('tool_profile') not in ('research','offline'):raise ValueError('Unexpected editorial worker request')
  marker='\nINPUT:\n'
  if marker not in prompt:raise ValueError('Editorial request lacks organized input marker')
  inputs=json.loads(prompt.split(marker,1)[1]);review=name.startswith('revision-review-')
  verify_handoff(self.packet,before_entry=inputs['before_entries'][0],coverage=inputs['coverage'])
  if inputs['entry_kind']!='grammar' or len(inputs['before_entries'])!=1:raise ValueError('Research adapter scope changed')
  if review:validate_scoped_revision(inputs['proposal'],self.packet)
  context={**inputs,FIELD:self.packet,'dictionary_research_revision_validation':{'version':1,'stage':'critic' if review else 'writer','handoff_digest':self.packet['digest']}}
  if self.editorial_criteria is not None:context['dictionary_editorial_criteria']=self.editorial_criteria
  if self.editorial_criteria is not None:context['prior_attempt_editorial_reviews']=[{'job':row['job'],'review':row['result'],'writer_proposal':self.contracts[i-1]['result']} for i,row in enumerate(self.contracts) if i%2]
  prior=authenticated_prior_reviews(self.prior_editorial_origin,self.packet)
  if prior is not None:context['authenticated_prior_editorial_reviews']=prior
  instruction_text=instructions(self.packet,review,self.editorial_criteria)
  workspace_context={'dictionary_research_revision':context,'dictionary_research_revision_validation':{'input_field':'dictionary_research_revision'}}
  new_prompt=instruction_text+payload(**workspace_context)
  actual_schema=contracts.REVIEW if review else scoped_schema(self.packet)
  path=research._schema_path(self.run_dir,'dictionary-research-'+('critic' if review else 'writer'),actual_schema)
  contract={'job':name,'prompt':new_prompt,'schema_text':path.read_text(),'workspace_context':workspace_context}
  value=await self.runner.call(name,new_prompt,path,'low',tool_profile='workspace',workspace_context=workspace_context)
  validate_submission(value,context)
  meta,raw=research._verify_job(self.run_dir,job=name,expected_prompt=new_prompt,schema_text=path.read_text(),workspace_context=workspace_context,expected_result=value)
  self.contracts.append({**contract,'meta_digest':digest(meta),'result_digest':digest(raw),'result':raw})
  return value

from pipeline.receipt_replay_session import invocation

@invocation
def validate_submission(output,context):
 if 'dictionary_editorial_criteria' in context:
  from pipeline.dictionary_editorial_criteria import validate
  validate(context['dictionary_editorial_criteria'])
 gate=context['dictionary_research_revision_validation'];packet=context[FIELD]
 if 'authenticated_prior_editorial_reviews' in context:
  supplied=context['authenticated_prior_editorial_reviews']
  if supplied!=authenticated_prior_reviews(supplied['origin'],packet):raise ValueError('Prior editorial review context changed')
 if context.get('entry_kind')!='grammar' or context.get('before_entries')!=[packet['before_entry']]:raise ValueError('Dictionary revision current scope changed')
 if gate.get('version')!=1 or gate.get('handoff_digest')!=packet['digest']:raise ValueError('Dictionary revision gate binding changed')
 verify_handoff(packet,before_entry=context['before_entries'][0],coverage=context['coverage'],historical=True)
 if gate['stage']=='writer':validate_scoped_revision(output,packet)
 elif gate['stage']=='critic':
  from jsonschema import validate
  validate_scoped_revision(context['proposal'],packet);validate(output,contracts.REVIEW)
  if output['approved']!=(not output['issues']):raise ValueError('Critic approval and outstanding issues disagree')
 else:raise ValueError('Unknown dictionary revision stage')

async def revise(packet,run_dir,*,runner,draft=None,editorial_criteria="current",prior_editorial_origin=None):
 if editorial_criteria=="current":
  from pipeline.dictionary_editorial_criteria import marker
  editorial_criteria=marker()
 verify_handoff(packet)
 adapter=ResearchBoundRunner(runner,run_dir,packet,editorial_criteria,prior_editorial_origin)
 result=await editorial.revise([packet['before_entry']['id']],Path(run_dir),draft,promote=False,runner=adapter,entry_kind='grammar')
 accepted=json.loads((Path(run_dir)/'accepted-revision.json').read_text())
 evidence={'version':1,'purpose':'reviewed_dictionary_revision_not_promotion','handoff':packet,'editorial_record':accepted,'contracts':adapter.contracts}
 if prior_editorial_origin is not None:evidence['prior_editorial_origin']=prior_editorial_origin
 if editorial_criteria is not None:evidence['dictionary_editorial_criteria']=editorial_criteria
 evidence['digest']=digest(evidence)
 replay(Path(run_dir),evidence)
 editorial.save(Path(run_dir)/'research-bound-revision-evidence.json',evidence)
 return result,evidence

@invocation
def replay(run_dir,evidence):
 if evidence.get('version')!=1 or evidence.get('purpose')!='reviewed_dictionary_revision_not_promotion' or evidence['digest']!=digest({k:v for k,v in evidence.items() if k!='digest'}):raise ValueError('Dictionary revision evidence changed')
 verify_handoff(evidence['handoff'],historical=True)
 rows=evidence['contracts'];record=evidence['editorial_record']
 if not rows or len(rows)>8 or len(rows)%2:raise ValueError('Missing bounded editorial chain')
 previous=None;issues=[]
 for index,row in enumerate(rows):
  attempt=index//2;review=index%2==1;name=('revision-review-' if review else 'revision-')+str(attempt)
  context={'entry_kind':'grammar','before_entries':[evidence['handoff']['before_entry']],'coverage':evidence['handoff']['coverage']}
  context.update({'proposal':rows[index-1]['result']} if review else {'previous':previous,'issues':issues})
  context[FIELD]=evidence['handoff'];context['dictionary_research_revision_validation']={'version':1,'stage':'critic' if review else 'writer','handoff_digest':evidence['handoff']['digest']}
  if 'dictionary_editorial_criteria' in evidence:context['dictionary_editorial_criteria']=evidence['dictionary_editorial_criteria']
  if 'dictionary_editorial_criteria' in evidence:context['prior_attempt_editorial_reviews']=[{'job':row['job'],'review':row['result'],'writer_proposal':rows[i-1]['result']} for i,row in enumerate(rows[:index]) if i%2]
  prior=authenticated_prior_reviews(evidence.get('prior_editorial_origin'),evidence['handoff'])
  if prior is not None:context['authenticated_prior_editorial_reviews']=prior
  schema=contracts.REVIEW if review else scoped_schema(evidence['handoff'])
  workspace_context={'dictionary_research_revision':context,'dictionary_research_revision_validation':{'input_field':'dictionary_research_revision'}}
  expected_prompt=instructions(evidence['handoff'],review,evidence.get('dictionary_editorial_criteria'))+payload(**workspace_context)
  if row['job']!=name or row['prompt']!=expected_prompt or row['workspace_context']!=workspace_context or json.loads(row['schema_text'])!=schema:raise ValueError('Foreign editorial request')
  meta,value=research._verify_job(Path(run_dir),job=name,expected_prompt=expected_prompt,schema_text=row['schema_text'],workspace_context=workspace_context,expected_result=row['result'])
  if digest(meta)!=row['meta_digest'] or digest(value)!=row['result_digest']:raise ValueError('Editorial receipt changed')
  validate_submission(value,context)
  if review:
   if editorial.approved(value) and index!=len(rows)-1:raise ValueError('Work continued after approval')
   previous=rows[index-1]['result'];issues=value['issues']
 if not editorial.approved(rows[-1]['result']) or record['proposal']!=rows[-2]['result'] or record['review']!=rows[-1]['result']:raise ValueError('Editorial revision not independently approved')
 expected_context={'entry_kind':'grammar','before_entries':[evidence['handoff']['before_entry']],'coverage':evidence['handoff']['coverage']}
 if record.get('entry_kind')!='grammar' or record.get('before_entries')!=expected_context['before_entries'] or record.get('coverage')!=expected_context['coverage']:raise ValueError('Editorial record scope changed')
 if record['context_digest']!=editorial.digest(expected_context) or record['proposal_digest']!=editorial.digest(record['proposal']) or record['review_digest']!=editorial.digest(record['review']):raise ValueError('Editorial record binding changed')
 return {'reviewed':True,'promoted':False,'annotation_approval':False}


def promotion_preflight(run_dir,evidence):
 """Return the exact reviewed delta after current freshness checks; never write."""
 replay(run_dir,evidence)
 packet=evidence['handoff']
 verify_handoff(packet)
 current=editorial.dictionary._registry(editorial.dictionary.GRAMMAR)[packet['before_entry']['id']]
 if current!=packet['before_entry']:raise ValueError('Current published entry changed')
 editorial._verify_coverage_fresh(packet['coverage'])
 return {'promoted':False,'entry_kind':'grammar','record':copy.deepcopy(evidence['editorial_record']),
         'authenticated_research_revision_digest':evidence['digest'],
         'annotation_approval':False,'publication_and_curriculum_checks_required':True}
