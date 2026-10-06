"""Hermetic shared local-gate contrasts; no linguistic conclusions."""
import copy,json
import pytest
from pipeline.annotation_adjudication import digest
from pipeline.worker_workspace import build,submit,CandidateSubmissionError
from pipeline.annotation_research_claim_revision import validate_claim_revision

@pytest.fixture(autouse=True)
def forbid_paid_and_capture(monkeypatch):
 from pipeline.agent_harness import CodexRunner
 from pipeline import annotation_research
 async def no_worker(*a,**k):pytest.fail('Shared-gate audit attempted paid worker')
 def no_capture(*a,**k):pytest.fail('Shared-gate audit attempted source capture')
 monkeypatch.setattr(CodexRunner,'call',no_worker);monkeypatch.setattr(annotation_research,'_fetch_primary_page',no_capture)

def bind(output,r):return {**output,'request_binding':{'request_digest':digest(r),'original_result_digest':r['original_result_digest'],'source_association_digest':digest(r['new_capture_associations'])}}
@pytest.fixture
def case():
 refs={'captured':{'kind':'primary_source','content':{'captured_text':'SETUP ONLY substantive captured page, not a linguistic conclusion.'}},'lesson':{'kind':'approved_lesson','content':{'explanation_en':'SETUP ONLY approved explanation, not a linguistic conclusion.'}},'origin':{'kind':'primary_source','content':{'url':'https://krdict.korean.go.kr'}}}
 findings=[{'issue_id':i,'status':'supported','fact':'SETUP ONLY initial claim, not linguistic evidence.','citations':[{'reference_id':'captured','path':'/captured_text'}],'gap':''} for i in ('one','two')]
 result={'findings':findings}
 return {'claim_revision_policy_version':2,'stage':'diagnostic','issue_ids':['one','two'],'research_result':result,'original_result':copy.deepcopy(result),'original_result_digest':digest(result),'new_capture_associations':{'version':1},'known_reference_input':refs}

def review(r,defective=False):
 return bind({'approved':not defective,'issues':[{'issue_id':'one','candidate_paths':['/findings/0/fact'],'reason':'SETUP ONLY'}] if defective else [],'assessments':[{'issue_id':i,'status':'defective' if defective and i=='one' else 'supported','reason':'SETUP ONLY substantive reason','citations':[{'reference_id':'captured','path':'/captured_text'}]} for i in r['issue_ids']]},r)
def workspace(tmp_path,r,out):
 context={'claim_request':r,'annotation_research_claim_revision_validation':{'input_field':'claim_request','request_digest':digest(r),'stage':r['stage']}}
 build(tmp_path,'SETUP ONLY task',{'type':'object'},context=context);(tmp_path/'candidate.json').write_text(json.dumps(out));return submit(tmp_path,{'candidate_path':'candidate.json'})

def test_supported_assessments_submit(case,tmp_path):workspace(tmp_path,case,review(case))
@pytest.mark.parametrize('mutation',['supported_issue','missing_issue','unknown_pointer','metadata','binding','coverage'])
def test_invalid_review_fails_local_gate(case,tmp_path,mutation):
 out=review(case,True)
 if mutation=='supported_issue':out['assessments'][0]['status']='supported'
 if mutation=='missing_issue':out['issues']=[];out['approved']=True
 if mutation=='unknown_pointer':out['assessments'][0]['citations'][0]['path']='/absent'
 if mutation=='metadata':out['assessments'][0]['citations'].append({'reference_id':'origin','path':'/url'})
 if mutation=='binding':out['request_binding']['request_digest']='f'*64
 if mutation=='coverage':out['assessments'].pop()
 with pytest.raises(CandidateSubmissionError):workspace(tmp_path,case,out)
def test_supplement_valid_with_primary(case,tmp_path):
 out=review(case);out['assessments'][0]['citations'].append({'reference_id':'lesson','path':'/explanation_en'});workspace(tmp_path,case,out)
def test_supplement_without_primary_rejected(case,tmp_path):
 out=review(case);out['assessments'][0]['citations']=[{'reference_id':'lesson','path':'/explanation_en'}]
 with pytest.raises(CandidateSubmissionError):workspace(tmp_path,case,out)
def test_repair_scope_and_uncertainty(case,tmp_path):
 diag=review(case,True);r={**case,'stage':'repair','diagnostic_request':case,'diagnostic_review':diag,'authorized_paths':['/findings/0/fact']}
 out=bind({'base_digest':digest(r['research_result']),'edits':[{'path':'/findings/0/fact','value':'SETUP ONLY changed substantive claim, no linguistic approval.'}]},r)
 workspace(tmp_path,r,out)
 for mutation in ('noop','missing','foreign','uncertain'):
  bad=copy.deepcopy(out);altered=copy.deepcopy(r)
  if mutation=='noop':bad['edits'][0]['value']=r['research_result']['findings'][0]['fact']
  if mutation=='missing':bad['edits']=[]
  if mutation=='foreign':bad['edits'][0]['path']='/findings/1/fact'
  if mutation=='uncertain':altered['diagnostic_review']['assessments'][0]['status']='uncertain'
  bad=bind(bad,altered)
  with pytest.raises(CandidateSubmissionError):workspace(tmp_path/mutation,altered,bad)
