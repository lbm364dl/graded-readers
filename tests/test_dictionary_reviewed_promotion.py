"""Portable setup fixtures: structural/provenance tests, not linguistic evidence."""
import copy,hashlib,json,importlib.util
from pathlib import Path
import pytest
from pipeline import dictionary_reviewed_promotion as m

def fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'ROOT',tmp_path)
    before={'reviewed':True,'entries':[{'id':'a','pattern':'x','explanation_en':'old'},{'id':'b','pattern':'y','explanation_en':'sibling'}]}
    record={'before_entries':[before['entries'][0]],'proposal':{'entries':[dict(before['entries'][0],explanation_en='new')]},'review':{'approved':True,'issues':[],'prior_issue_assessments':[{'setup_only':True}]},'coverage':{'occurrences':[{'source_start':i,'entry_id':'a'} for i in range(37)]}}
    evidence={'digest':'setup-evidence','editorial_record':record,'handoff':{'registry_before_text':json.dumps(before),'registry_sha256':hashlib.sha256(json.dumps(before).encode()).hexdigest()}};run=tmp_path/'run';run.mkdir();path=run/'evidence.json';path.write_text(json.dumps(evidence));reg=tmp_path/'registry.json';reg.write_text(json.dumps(before));monkeypatch.setattr(m.revision.editorial.dictionary,'GRAMMAR',reg)
    monkeypatch.setattr(m.revision,'promotion_preflight',lambda r,e: {'record':copy.deepcopy(e['editorial_record'])})
    calls=[];monkeypatch.setattr(m.revision,'replay',lambda r,e:calls.append((r,e)))
    after,receipt=m.build_promotion(run,path,reg);proof=run/'promotion.json';proof.write_text(json.dumps(receipt));desc={'version':3,'receipt_relpath':'run/promotion.json','receipt_sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}
    rec=copy.deepcopy(record);rec[m.FIELD]=desc
    return reg,path,proof,after,receipt,rec,calls

def test_exact_and_historical_registry_change(tmp_path,monkeypatch):
    reg,path,proof,after,receipt,rec,calls=fixture(tmp_path,monkeypatch)
    assert m.verify_history(rec,rec[m.FIELD]);assert len(calls)==1
    reg.write_text(json.dumps(after));assert m.verify_history(rec,rec[m.FIELD])
    assert len(after['grammar_revision_reviews'][0]['coverage']['occurrences'])==37
    assert after['entries'][1]['explanation_en']=='sibling'

@pytest.mark.parametrize('target',['receipt','evidence','record','sibling','marker'])
def test_tamper(tmp_path,monkeypatch,target):
    reg,path,proof,after,receipt,rec,calls=fixture(tmp_path,monkeypatch)
    if target=='receipt':proof.write_text(proof.read_text()+' ')
    if target=='evidence':path.write_text(path.read_text()+' ')
    if target=='record':rec['proposal']['entries'][0]['explanation_en']='foreign'
    if target=='marker':rec[m.FIELD]['version']=1.0
    if target=='sibling':
        receipt['after_document']['entries'][1]['explanation_en']='foreign';receipt['digest']=m.digest({k:v for k,v in receipt.items() if k!='digest'});proof.write_text(json.dumps(receipt));rec[m.FIELD]['receipt_sha256']=hashlib.sha256(proof.read_bytes()).hexdigest()
    with pytest.raises(ValueError):m.verify_history(rec,rec[m.FIELD])

def test_partial_before_and_fresh_gate(tmp_path,monkeypatch):
    reg,path,proof,after,receipt,rec,calls=fixture(tmp_path,monkeypatch)
    doc=json.loads(reg.read_text());doc['entries'][0]['pattern']='foreign';reg.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='authenticated accepted snapshot'):m.build_promotion(path.parent,path,reg)
    monkeypatch.setattr(m.revision,'promotion_preflight',lambda *a:(_ for _ in ()).throw(ValueError('Stale registry/coverage')))
    with pytest.raises(ValueError,match='Stale'):m.build_promotion(path.parent,path,reg)

def test_all_37_layers_actual_gate_dispatch(tmp_path,monkeypatch):
    from pipeline import korean_dictionary,korean_dictionary_revision
    p=tmp_path/'chapters.json';p.write_text(json.dumps({'chapters':[{'number':1,'text':'setup','segments':[]}]}))
    record={'coverage':{'files':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()},'chapter_digests':[{'file':str(p),'chapter':1}], 'occurrences':[{'source_start':i} for i in range(37)]}}
    calls=[];monkeypatch.setattr(korean_dictionary_revision,'_verify_coverage_fresh',lambda c:None)
    monkeypatch.setattr(korean_dictionary,'build_assets',lambda *a,**k:calls.append(k))
    result=m.verify_affected_coverage(record,word_registry={},grammar_registry={})
    assert result['occurrence_layers']==37 and len(calls)==1 and calls[0]['write'] is False

def test_versioned_history_adapter_preserves_assessments_and_legacy(tmp_path,monkeypatch):
    import sys
    from pipeline import korean_dictionary as d,korean_dictionary_revision as e
    from tests import test_korean_dictionary_revision as setup
    old,source=setup.setup_grammar_revision(tmp_path,monkeypatch)
    proposal=setup.grammar_proposal(old);coverage=e.coverage({old['id']},None,'grammar')
    review={'approved':True,'issues':[],'prior_issue_assessments':[{'setup_only':True}]}
    context={'entry_kind':'grammar','before_entries':[old],'coverage':coverage}
    record=dict(context,proposal=proposal,review=review,proposal_digest=e.digest(proposal),review_digest=e.digest(review),context_digest=e.digest(context))
    doc=json.loads(d.GRAMMAR.read_text());doc['entries'][0]=proposal['entries'][0];doc['grammar_revision_reviews']=[record]
    d.GRAMMAR.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='independent review'):d._registry(d.GRAMMAR)
    # Only the explicit new history branch delegates proof validation; setup seam.
    proof=sys.modules['pipeline.dictionary_reviewed_promotion'];seen=[]
    monkeypatch.setattr(proof,'verify_history',lambda row,desc:seen.append((row,desc)))
    record[m.FIELD]={'version':1,'setup_only':True};d.GRAMMAR.write_text(json.dumps(doc));assert old['id'] in d._registry(d.GRAMMAR);assert len(seen)==1
    assert d._registry(d.GRAMMAR)[old['id']]==proposal['entries'][0]

def test_readonly_auth_then_persist_then_fresh_auth_sequence(tmp_path):
    from pipeline.receipt_replay_session import invocation,ReplayChanged
    path=tmp_path/'proof.json'
    @invocation
    def construct():return {'setup_only':True}
    @invocation
    def authenticate():return json.loads(path.read_text())
    packet=construct();path.write_text(json.dumps(packet));assert authenticate()==packet
    @invocation
    def forbidden_invoke_write():path.write_text('bad')
    with pytest.raises(ReplayChanged,match='may not mutate'):forbidden_invoke_write()
    assert authenticate()==packet

@pytest.mark.parametrize('kind',['different','symlink'])
def test_managed_artifact_collision_preserves_foreign(tmp_path,kind):
    target=tmp_path/'artifact';foreign=tmp_path/'foreign';foreign.write_bytes(b'unchanged')
    if kind=='symlink':target.symlink_to(foreign)
    else:target.write_bytes(b'unchanged')
    with pytest.raises(ValueError):m._immutable_artifact(target,b'new')
    assert foreign.read_bytes()==b'unchanged'
    assert target.read_bytes()==b'unchanged'

def test_preexisting_destination_not_unlinked(tmp_path,monkeypatch):
    from pipeline import korean_dictionary
    reg,path,proof,after,receipt,rec,calls=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'build_promotion',lambda *a:(copy.deepcopy(after),copy.deepcopy(receipt)))
    monkeypatch.setattr(m,'verify_affected_coverage',lambda *a,**k:{'setup_only':True})
    monkeypatch.setattr(korean_dictionary,'_registry',lambda *a:{})
    destination=reg.with_name('.'+reg.name+'.promotion-'+receipt['digest']);destination.write_bytes(b'foreign staging')
    original=reg.read_bytes()
    with pytest.raises(FileExistsError):m.promote_exact_reviewed(path.parent,path,reg,word_registry={},grammar_registry={})
    assert destination.read_bytes()==b'foreign staging' and reg.read_bytes()==original

def test_stripped_accountability_record_cannot_be_legacy(tmp_path,monkeypatch):
    from pipeline import korean_dictionary as d,korean_dictionary_revision as e
    from tests import test_korean_dictionary_revision as setup
    old,source=setup.setup_grammar_revision(tmp_path,monkeypatch);proposal=setup.grammar_proposal(old);coverage=e.coverage({old['id']},None,'grammar')
    context={'entry_kind':'grammar','before_entries':[old],'coverage':coverage};review={'approved':True,'issues':[]}
    record=dict(context,proposal=proposal,review=review,proposal_digest=e.digest(proposal),review_digest=e.digest(review),context_digest=e.digest(context),dictionary_objection_accountability={'version':2})
    doc=json.loads(d.GRAMMAR.read_text());doc['entries'][0]=proposal['entries'][0];doc['grammar_revision_reviews']=[record];d.GRAMMAR.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='requires authenticated promotion'):d._registry(d.GRAMMAR)


def test_rehashed_before_and_after_sibling_forgery(tmp_path,monkeypatch):
    import base64
    reg,path,proof,after,receipt,rec,calls=fixture(tmp_path,monkeypatch)
    receipt['before_document']['entries'][1]['explanation_en']='foreign sibling'
    receipt['after_document']['entries'][1]['explanation_en']='foreign sibling'
    raw=json.dumps(receipt['before_document']).encode()
    receipt['before_registry_bytes']=base64.b64encode(raw).decode()
    receipt['before_registry_sha256']=hashlib.sha256(raw).hexdigest()
    receipt['digest']=m.digest({k:v for k,v in receipt.items() if k!='digest'})
    proof.write_text(json.dumps(receipt));rec[m.FIELD]['receipt_sha256']=hashlib.sha256(proof.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='authenticated accepted snapshot'):m.verify_history(rec,rec[m.FIELD])

def test_build_rejects_unrelated_current_sibling_delta(tmp_path,monkeypatch):
    reg,path,proof,after,receipt,rec,calls=fixture(tmp_path,monkeypatch)
    doc=json.loads(reg.read_text());doc['entries'][1]['explanation_en']='changed sibling';reg.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='authenticated accepted snapshot'):m.build_promotion(path.parent,path,reg)


def test_legitimate_old_staged_receipt_is_historical_only(tmp_path,monkeypatch):
    reg,path,proof,after,receipt,rec,calls=fixture(tmp_path,monkeypatch)
    receipt['version']=1;receipt.pop('binding_policy');receipt['digest']=m.digest({k:v for k,v in receipt.items() if k!='digest'})
    proof.write_text(json.dumps(receipt));rec[m.FIELD]['version']=1;rec[m.FIELD]['receipt_sha256']=hashlib.sha256(proof.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='Unknown promotion marker'):m.verify_history(rec,rec[m.FIELD])
    assert m.verify_history(rec,rec[m.FIELD],historical_staged=True)
