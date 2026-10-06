import copy,json
from pathlib import Path
import pytest
from pipeline import annotation_adjudication as a
from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
from tests.test_annotation_adjudication import fixture,output_for
from tests.test_annotation_research import _write_job
@pytest.fixture(autouse=True)
def no_workers(monkeypatch):
 from pipeline.agent_harness import CodexRunner
 from pipeline import annotation_research
 async def forbidden(*args,**kwargs):pytest.fail('Legacy audit attempted model call')
 monkeypatch.setattr(CodexRunner,'call',forbidden)
 monkeypatch.setattr(annotation_research,'_fetch_primary_page',lambda *args:pytest.fail('Legacy audit attempted capture'))
@pytest.fixture(params=['zh','ja','ko'])
def saved(tmp_path,request):
 _,_,_,_,_,current=fixture();kwargs={k:current[k] for k in ('language','representation','candidate','current_review','prior_history','context','known_reference_input','deterministic_gate_evidence','normal_review_receipt','source_text')};kwargs['language']=request.param
 inputs=a._build_inputs(**kwargs,host_binding_policy_version=1);base=a.INSTRUCTIONS.split('\n\n'+FORM_STAGE_EVIDENCE_GUIDANCE)[0];assert a.digest(base)==a.LEGACY_BASE_INSTRUCTION_DIGEST
 inputs['instructions_digest']=a.digest(base);inputs.pop('input_digest');inputs['input_digest']=a.digest(inputs)
 job='annotation-adjudication-'+inputs['input_digest'];prompt=base+a._worker_prompt(inputs)[len(a.INSTRUCTIONS):];schema=json.dumps(a._output_schema(1),ensure_ascii=False,indent=2)+'\n';context={'annotation_adjudication':inputs,'annotation_adjudication_validation':{'input_field':'annotation_adjudication'}}
 output=output_for(inputs,disposition='uncertain');lane=_write_job(tmp_path,job,prompt,schema,context,output)
 (lane/'adjudication-input.json').write_text(json.dumps(inputs));verified=a._validate_output(output,inputs)
 evidence={**verified,'job':job,'effort':'low','model':'gpt-6-luna','tool_profile':'research','worker_tool_profile':'workspace','normal_review_receipt':kwargs['normal_review_receipt'],'normal_review_receipt_digest':inputs['normal_review_receipt_digest'],'verified_result_digest':a.digest(verified)}
 (lane/'verified-adjudication.json').write_text(json.dumps(evidence));return tmp_path,kwargs,evidence,inputs,lane,prompt,schema,context,output

def test_exact_historical_replay_only(saved):
 run,kwargs,evidence,*_=saved;assert a.verify_adjudication_evidence(run,evidence,**kwargs)==evidence
 assert evidence['status']=='uncertain' and evidence['approved'] is False
@pytest.mark.parametrize('mutation',['input','candidate','prompt','explicit_marker','wrong_digest'])
def test_legacy_tampering_fails_closed(saved,mutation):
 run,kwargs,evidence,inputs,lane,prompt,schema,context,output=saved;kwargs=copy.deepcopy(kwargs)
 if mutation=='candidate':kwargs['candidate']['segments'][0]['meaning_en']='different'
 elif mutation=='prompt':_write_job(run,evidence['job'],prompt+'FOREIGN TASK',schema,context,output)
 else:
  altered=copy.deepcopy(inputs)
  if mutation=='input':altered['context']={'changed':True}
  if mutation=='explicit_marker':altered['host_binding_policy_version']=1
  if mutation=='wrong_digest':altered['instructions_digest']='f'*64
  (lane/'adjudication-input.json').write_text(json.dumps(altered))
 with pytest.raises(a.AdjudicationError):a.verify_adjudication_evidence(run,evidence,**kwargs)

def test_new_decisions_still_use_current_instruction_policy():
 _,_,_,_,_,inputs=fixture();assert inputs['instructions_digest']==a.digest(a._versioned_instructions(5));assert inputs['instructions_digest']!=a.LEGACY_BASE_INSTRUCTION_DIGEST
 assert FORM_STAGE_EVIDENCE_GUIDANCE in a._worker_prompt(inputs)
