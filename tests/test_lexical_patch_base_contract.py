import copy
import pytest
from jsonschema import validate,ValidationError
from pipeline import lexical_request_reconciliation as c

@pytest.mark.parametrize('marker',[None,True,1.0,2,'1'])
def test_explicit_patch_base_marker_type(marker):
 with pytest.raises(ValueError):c.repair_base_contract({'reconciliation_patch_base_policy_version':marker})

def test_expected_entire_result_not_input_or_row_digest(monkeypatch):
 base={'inputs_digest':'a'*64,'decisions':[{'request_index':0,'reason':'Fixture only.'}]}
 task={'reconciliation_repair_base':base,'reconciliation_repair_review':{'issues':[{'request_index':0}]},'reconciliation_repair_targets':[0]}
 inputs={'reconciliation_instruction_policy_version':2,'reconciliation_patch_base_policy_version':1}
 task['reconciliation_repair_expected_base_digest']=c.digest(base)
 monkeypatch.setattr(c,'_repair_task_inputs',lambda packet:copy.deepcopy(task)) # Request projection shape only, not authority.
 request=c.reconciliation_request(inputs,'repair',origin_run_dir='fixture')
 expected=c.digest(base);assert request['schema']['properties']['base_digest']['const']==expected
 assert request['workspace_context']['reconciliation_repair_expected_base_digest']==expected
 patch={'policy_version':2,'base_digest':expected,'edits':[{'request_index':0,'fields':{'reason':'Changed structural fixture.'}}]}
 validate(patch,request['schema'])
 for wrong in [base['inputs_digest'],c.digest(inputs),c.digest(base['decisions'][0])]:
  with pytest.raises(ValidationError):validate({**patch,'base_digest':wrong},request['schema'])

def test_unmarked_request_contract_remains_exact(monkeypatch):
 task={'reconciliation_repair_base':{'inputs_digest':'a'*64},'reconciliation_repair_review':{'issues':[]},'reconciliation_repair_targets':[]}
 monkeypatch.setattr(c,'_repair_task_inputs',lambda packet:copy.deepcopy(task))
 request=c.reconciliation_request({'reconciliation_instruction_policy_version':2},'repair',origin_run_dir='fixture')
 assert request['schema']==c.REPAIR_PATCH_SCHEMA
 assert 'reconciliation_repair_expected_base_digest' not in request['workspace_context']
 assert 'Copy reconciliation_repair_expected_base_digest' not in request['prompt']
