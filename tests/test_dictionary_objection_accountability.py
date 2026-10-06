"""Portable structural contrasts, not linguistic or primary-source approval."""
import copy
import pytest
from pipeline import dictionary_objection_accountability as a
from pipeline.annotation_adjudication import digest
P='/entries/0/explanation_en'
def packet(monkeypatch):
 old={'entries':[{'id':'setup','explanation_en':'Original explanation.'}]}
 source={'run_relpath':'runs/setup','job':'setup-critic','review':{'approved':False,'issues':['Setup retained objection.']},'proposal':old,'result_digest':digest({'approved':False,'issues':['Setup retained objection.']})}
 # Explicit auth seam; caller tests independently exercise genuine receipts.
 monkeypatch.setattr(a,'authenticate_source_contract',lambda c:copy.deepcopy(c['source']))
 current=copy.deepcopy(old);current['entries'][0]['explanation_en']='Changed explanation.'
 return a.prepare_packet(source_contracts=[{'source':source}],proposal=current,semantic_paths=[P],identity_paths={P:['/entries/0/id']},reference_descriptors=[{'provider':'shared-dictionary-editorial-criteria','version':1}])
def review(p,disposition='resolved_change'):
 text=p['proposal']['entries'][0]['explanation_en']
 row={'issue_id':p['rows'][0]['issue_id'],'disposition':disposition,'proposal_paths':[P],'proposal_spans':[{'path':P,'start':0,'end':len(text),'quote':text}],'rationale':'Setup independently assessed resolution, not proof from a changed string.'}
 if disposition=='resolved_change':row['changed_fields']=[{'path':P,'old_value_digest':digest('Original explanation.'),'current_value_digest':digest(text)}]
 elif disposition=='supported_retention':row['evidence_refs']=[{'reference_id':'shared-dictionary-editorial-criteria-v1','path':'/criteria'}]
 else:row['current_issue_index']=0
 return {'approved':disposition!='unresolved','issues':[] if disposition!='unresolved' else ['Setup unresolved.'],'prior_issue_assessments':[row]}
def test_changed_leaf_with_rationale_is_structurally_accepted(monkeypatch):
 p=packet(monkeypatch);a.validate_submission(review(p),p)
def test_unresolved_not_clean_vote(monkeypatch):
 p=packet(monkeypatch);r=review(p,'unresolved');a.validate_submission(r,p);r['approved']=True
 with pytest.raises(ValueError):a.validate_submission(r,p)
def test_supported_retention_requires_reconstructed_content(monkeypatch):
 p=packet(monkeypatch);r=review(p,'supported_retention');a.validate_submission(r,p)
 p['references']['shared-dictionary-editorial-criteria-v1']['content']['criteria']='Caller assertion'
 with pytest.raises(ValueError):a.validate_submission(r,p)
def test_missing_or_foreign_issue_not_waived(monkeypatch):
 p=packet(monkeypatch);r=review(p);r['prior_issue_assessments'][0]['issue_id']='foreign'
 with pytest.raises(Exception):a.validate_submission(r,p)
def test_exact_current_span_and_entry_identity(monkeypatch):
 p=packet(monkeypatch);r=review(p);r['prior_issue_assessments'][0]['proposal_spans'][0]['quote']='Wrong'
 with pytest.raises(ValueError):a.validate_submission(r,p)
 p['proposal']['entries'][0]['id']='foreign';r=review(p)
 with pytest.raises(ValueError):a.validate_submission(r,p)
def test_ancestor_proposal_not_current_authority():
 old={'entries':[{'id':'old'}]};current={'entries':[{'id':'current'}]}
 context={'dictionary_objection_review':{'proposal':current,'source_contracts':[{'critic':{'workspace_context':{'dictionary_objection_review':{'proposal':old}}}}]}}
 assert a._contains_proposal(context,current)
 assert not a._contains_proposal(context,old)

def test_compact_archive_projection_scope_and_role(monkeypatch,tmp_path):
 monkeypatch.setattr(a,'ROOT',tmp_path);(tmp_path/'run').mkdir()
 proposal={'entries':[{'id':'setup','explanation_en':'Setup exact artifact.'}]};result={'approved':False,'issues':['Setup retained finding.']}
 origin={'run_relpath':'run','evidence_sha256':'setup-auth-seam'}
 archive={'contracts':[{'job':'writer','result':proposal,'is_critic':False,'proposal':None},{'job':'critic','result':result,'is_critic':True,'proposal':proposal}]}
 monkeypatch.setattr(a,'authenticated_editorial_archive',lambda d:copy.deepcopy(archive))
 source={'run_relpath':'run','job':'critic','review':result,'proposal':proposal,'result_digest':digest(result)}
 contract={'source':source,'writer':{'provider':'authenticated-dictionary-editorial-archive','origin':origin,'contract_index':0},'critic':{'provider':'authenticated-dictionary-editorial-archive','origin':origin,'contract_index':1}}
 assert a.authenticate_source_contract(contract)==source
 bad=copy.deepcopy(contract);bad['source']['proposal']['entries'][0]['explanation_en']='Mutated projection'
 with pytest.raises(ValueError):a.authenticate_source_contract(bad)
 bad=copy.deepcopy(contract);bad['critic']['contract_index']=0
 with pytest.raises(ValueError):a.authenticate_source_contract(bad)
 bad=copy.deepcopy(contract);bad['critic']['contract_index']=True
 with pytest.raises(ValueError):a.authenticate_source_contract(bad)
