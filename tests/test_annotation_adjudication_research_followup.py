"""Setup-only composition tests; actual prior chain replay is tested separately."""
from pathlib import Path
import copy,json,os
from unittest.mock import patch
import pytest
from pipeline import annotation_adjudication as a

def fixture():
 inputs={'candidate':{'text':'same'},'current_review':{'issues':[{'candidate_paths':['/x']}]},'prior_history':['immutable'],'source_text':'same','context':{},'known_reference_input':{}}
 terminal={'reference_research_version':2,'status':'uncertain','job':'prior','reference_research':{'references':{'old':{'kind':'primary_source','content':{'text':'old'}}}},'initial_adjudication':{},'reference_research_chain_digest':'setup'}
 return inputs,terminal
@pytest.mark.parametrize('version',[None,1,3,4])
def test_repeated_or_wrong_cycle_fails_before_any_worker(version):
 inputs,t=fixture();t['reference_research_version']=version
 with pytest.raises(a.AdjudicationError,match='exactly one'):a._followup_base(Path('/tmp'),t,inputs)

def test_new_receipt_retains_first_and_second_research_and_original_inputs():
 inputs,t=fixture();original=copy.deepcopy(inputs);new={'version':7,'references':{'new':{'kind':'primary_source','content':{'text':'new'}}}}
 effective=a._research_inputs(inputs,t['reference_research'],t['reference_research']['references'],policy_version=2)
 finalinputs=a._research_inputs(effective,new,new['references'],policy_version=2)
 assert set(finalinputs['known_reference_input'])=={'old','new'} and inputs==original
 wrapped=a._research_receipt({'status':'uncertain','job':'final'},t,new,version=3)
 assert wrapped['initial_adjudication']==t and wrapped['reference_research_version']==3

@pytest.mark.parametrize('field',['candidate','source_text','prior_history','current_review'])
def test_original_boundary_tamper_is_not_bypassed(field):
 inputs,t=fixture();inputs[field]='foreign'
 def verifier(run,receipt,**kw):
  assert kw[field]=='foreign'
  raise a.AdjudicationError('original boundary rejected')
 with patch.object(a,'verify_adjudication_evidence',side_effect=verifier),pytest.raises(a.AdjudicationError,match='boundary'):a._followup_base(Path('/tmp'),t,inputs)

def test_no_references_cannot_change_verdict_and_new_policy_must_be7():
 inputs,t=fixture();r={'version':7,'references':{}}
 stripped={k:v for k,v in t.items() if k not in {'reference_research_version','initial_adjudication','reference_research','reference_research_chain_digest'}}
 wrapped=a._research_receipt({**stripped,'status':'cleared'},t,r,version=3)
 with patch.object(a,'_followup_base',return_value=inputs),patch('pipeline.annotation_research.verify_research_evidence',return_value={'evidence':r,'references':{}}),pytest.raises(a.AdjudicationError,match='cannot change'):a._verify_followup_research_receipt(Path('/tmp'),wrapped,inputs)
 r['version']=6
 with patch.object(a,'_followup_base',return_value=inputs),patch('pipeline.annotation_research.verify_research_evidence',return_value={'evidence':r,'references':{}}),pytest.raises(a.AdjudicationError,match='policy7'):a._verify_followup_research_receipt(Path('/tmp'),wrapped,inputs)

def actual_case():
    # Explicit local retained-evidence audit only; never needed by portable CI.
    directory = os.environ.get('ANNOTATION_RETAINED_V3_AUDIT_INPUTS')
    if not directory:
        pytest.skip('Optional exact retained receipt audit not configured')
    root = Path(directory)
    manifest = json.loads((root/'audit-inputs.json').read_text())
    run = Path(manifest['run_dir'])
    inputs = json.loads(Path(manifest['original_inputs']).read_text())
    terminal = json.loads(Path(manifest['terminal_receipt']).read_text())
    return run, inputs, terminal

def test_actual_historical_v2_terminal_and_current_uncertain_scope_replay():
 run,inputs,t=actual_case();effective=a._followup_base(run,t,inputs)
 assert inputs['candidate']==effective['candidate'] and len(inputs['current_review']['issues'])==11
 from pipeline.annotation_research import _build_inputs
 packet=_build_inputs(run_dir=run,initial_adjudication=t,**inputs)
 assert {tuple(row['candidate_paths']) for row in packet['uncertainty_tasks']['tasks']}=={('/segments/68/form_steps/0/meaning_en',),('/segments/115/form_steps/0/meaning_en',)}
 assert '/segments/13/form_steps/0/form' not in str(packet['uncertainty_tasks']['tasks'])

@pytest.mark.parametrize('boundary',['candidate','source_text','prior_history','receipt'])
def test_actual_terminal_foreign_boundary_fails_closed(boundary):
 run,inputs,t=actual_case()
 if boundary=='receipt':t['classifications'][0]['reason']='tampered'
 elif boundary=='candidate':inputs['candidate']['segments'][13]['form_steps'][0]['form']='foreign'
 elif boundary=='source_text':inputs['source_text']+='foreign'
 else:inputs['prior_history']=[]
 with pytest.raises((a.AdjudicationError,ValueError)):a._followup_base(run,t,inputs)
