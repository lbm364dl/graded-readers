"""Portable proof-storage tests; receipt seam is setup, not linguistic approval."""
import copy
import json
import pytest
from pipeline import dictionary_objection_accountability as a
from pipeline import annotation_research as r
@pytest.fixture
def setup(tmp_path,monkeypatch):
 monkeypatch.setattr(a,'ROOT',tmp_path)
 calls=[]
 def verify(run,**kw):
  calls.append(kw)
  return {'setup_meta':kw['job']},copy.deepcopy(kw['expected_result'])
 monkeypatch.setattr(r,'_verify_job',verify)
 run=tmp_path/'run';run.mkdir()
 contract={'job':'writer','prompt':'exact','schema_text':'{}','workspace_context':{'proposal':{'explanation':'original'}},'result':{'explanation':'original'}}
 return run,contract,calls

def test_exact_roundtrip_and_deepcopy(setup):
 run,c,calls=setup;d=a.persist_verified_worker_contract(run,c)
 assert len(calls)==1
 resolved=a.resolve_verified_worker_contract(d);assert resolved==c
 resolved['result']['explanation']='tampered'
 assert a.resolve_verified_worker_contract(d)==c
 assert len(json.dumps(d))<700
@pytest.mark.parametrize('change',['hash','path','version','bytes','receipt'])
def test_descriptor_tamper_rejected(setup,monkeypatch,change):
 run,c,calls=setup;d=a.persist_verified_worker_contract(run,c)
 if change=='hash':d['contract_sha256']='0'*64
 elif change=='path':d['contract_path']='../outside'
 elif change=='version':d['version']=1.0
 elif change=='bytes':(run/d['contract_path']).write_text('{}')
 else:monkeypatch.setattr(r,'_verify_job',lambda *x,**kw:({'foreign_meta':True},kw['expected_result']))
 with pytest.raises(ValueError):a.resolve_verified_worker_contract(d)
def test_persistence_requires_actual_verification(setup,monkeypatch):
 run,c,_=setup
 def reject(*args,**kw):raise ValueError('receipt unavailable')
 monkeypatch.setattr(r,'_verify_job',reject)
 with pytest.raises(ValueError):a.persist_verified_worker_contract(run,c)
 assert not (run/'dictionary-accountability-worker-contracts').exists()

def paired(setup):
 run,w,_=setup;w['result']['id']='entry';wd=a.persist_verified_worker_contract(run,w)
 packet=a.prepare_packet(source_contracts=[],proposal=w['result'],semantic_paths=['/explanation'],identity_paths={'/explanation':'/id'},reference_descriptors=[])
 review={'approved':True,'issues':[],'prior_issue_assessments':[]}
 critic={'job':'critic','prompt':'exact critic','schema_text':json.dumps(a.review_schema([])),'workspace_context':{'dictionary_objection_source_contract_version':3,'dictionary_objection_review':packet,'dictionary_complete_correction_producer':{'contract_digest':a.digest(wd)}},'result':review}
 cd=a.persist_verified_worker_contract(run,critic)
 source={'run_relpath':'run','job':'critic','proposal':w['result'],'review':review,'result_digest':a.digest(review)}
 return {'source':source,'writer':wd,'critic':cd}

def test_pair_authenticates_original_producer_descriptor(setup):
 contract=paired(setup)
 assert a.authenticate_source_contract(contract)==contract['source']
@pytest.mark.parametrize('change',['omit','substitute','artifact','run'])
def test_pair_substitution_fails(setup,change):
 contract=paired(setup)
 if change=='omit':contract['writer']=None
 elif change=='substitute':contract['writer']=contract['critic']
 elif change=='artifact':contract['source']['proposal']={'explanation':'foreign'}
 else:contract['source']['run_relpath']='foreign'
 with pytest.raises((ValueError,FileNotFoundError)):a.authenticate_source_contract(contract)

def test_expanded_legacy_rows_cannot_skip_receipt_verify(setup):
 run,_,calls=setup;contract=paired(setup)
 w=a.resolve_verified_worker_contract(contract['writer']);c=a.resolve_verified_worker_contract(contract['critic'])
 c['workspace_context']['dictionary_complete_correction_producer']={'contract_digest':a.digest(w)}
 contract.update(writer=w,critic=c);calls.clear()
 assert a.authenticate_source_contract(contract)==contract['source']
 assert [row['job'] for row in calls]==['critic','writer']

def test_mutation_after_cached_resolution_fails_invocation_freshness(setup):
 from pipeline.receipt_replay_session import invocation,ReplayChanged
 run,c,_=setup;d=a.persist_verified_worker_contract(run,c)
 @invocation
 def replay_then_change():
  a.resolve_verified_worker_contract(d)
  (run/d['contract_path']).write_text('{}')
  a.resolve_verified_worker_contract(d)
 with pytest.raises((ReplayChanged,ValueError)):replay_then_change()
