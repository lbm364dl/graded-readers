"""Portable finite protocol contrasts; not a linguistic verdict."""
import importlib.util,json,os,sys
from pathlib import Path
import pytest
from pipeline import receipt_replay_session as m
@pytest.mark.parametrize('kind',['source','schema','policy','metadata'])
def test_same_mtime_mutation_reject(tmp_path,kind):
 p=tmp_path/kind;p.write_text('before');stamp=p.stat().st_mtime_ns;s=m.Session();s.read(p);p.write_text('after!');os.utime(p,ns=(stamp,stamp))
 with pytest.raises(m.ReplayChanged):s.finish()
def test_inventory_addition_reject(tmp_path):
 (tmp_path/'events.attempt-01.jsonl').write_text('original');s=m.Session();list(s.glob(tmp_path,'events.attempt-*.jsonl'));(tmp_path/'events.attempt-02.jsonl').write_text('added')
 with pytest.raises(m.ReplayChanged):s.finish()
def test_policy_scope_and_copy_no_poison(tmp_path):
 s=m.Session();token=m.ACTIVE.set(s);calls=[]
 @m.memoized_provenance
 def proof(scope):calls.append(scope);return {'scope':scope,'rows':[]}
 try:
  a=proof('first');a['rows'].append('changed');assert proof('first')['rows']==[];assert proof('second')['scope']=='second';assert calls==['first','second'];s.finish()
 finally:m.ACTIVE.reset(token)
def test_next_invocation_no_cache(tmp_path):
 p=tmp_path/'bytes';p.write_text('before');s=m.Session();assert s.read(p)==b'before';s.finish();p.write_text('after');assert m.Session().read(p)==b'after'
def test_current_registry_check_not_cached(monkeypatch,tmp_path):
 from pipeline import dictionary_research_handoff as h
 from pipeline.annotation_adjudication import digest
 registry=tmp_path/'content/lexicon/korean/l1.grammar.json';registry.parent.mkdir(parents=True);registry.write_text('current')
 packet={'version':1,'purpose':'published_registry_revision_only','allowed_fields':['explanation_en'],'registry_sha256':'not-the-current-hash'};packet['digest']=digest(packet)
 monkeypatch.setattr(h,'ROOT',tmp_path)
 with pytest.raises(ValueError,match='Current published registry changed'):h.verify_handoff(packet)
def test_source_loader_uses_exact_snapshot(tmp_path):
 p=tmp_path/'code.py';p.write_text('answer=1');s=m.Session();s.read(p);p.write_text('answer=2');token=m.ACTIVE.set(s)
 try:
  loader=m.SourceFileLoader('setup_code',str(p));code=m._get_code(loader,'setup_code');env={};exec(code,env);assert env['answer']==1
  with pytest.raises(m.ReplayChanged):s.finish()
 finally:m.ACTIVE.reset(token)

def test_concurrent_invocations_serialize_hooks(tmp_path,monkeypatch):
 import concurrent.futures,time
 monkeypatch.setattr(m,'ROOT',tmp_path)
 p=tmp_path/'data';p.write_text('stable');active=[];maxactive=[]
 @m.invocation
 def read():
  active.append(1);maxactive.append(len(active));time.sleep(.01);value=p.read_text();active.pop();return value
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:read(),range(4)))==['stable']*4
 assert max(maxactive)==1
@pytest.mark.parametrize('suffix',['.py','.json'])
def test_initial_code_schema_inventory_addition_reject(tmp_path,monkeypatch,suffix):
 monkeypatch.setattr(m,'ROOT',tmp_path);directory=tmp_path/'pipeline'/('schemas' if suffix=='.json' else '');directory.mkdir(parents=True);(directory/('old'+suffix)).write_text('{}' if suffix=='.json' else 'value=1')
 @m.invocation
 def gate():
  with m.ORIGINAL_IO_OPEN(directory/('new'+suffix),'w') as f:f.write('{}' if suffix=='.json' else 'value=2')
  return True
 with pytest.raises(m.ReplayChanged):gate()
def test_negative_optional_index_appearance_reject(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'ROOT',tmp_path);p=tmp_path/'INDEX.json'
 @m.invocation
 def gate():
  assert not p.exists()
  with m.ORIGINAL_IO_OPEN(p,'w') as f:f.write('{}')
  return True
 with pytest.raises(m.ReplayChanged):gate()
def test_builtin_read_snapshot_and_raw_descriptor_fail(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'ROOT',tmp_path);p=tmp_path/'source';p.write_text('exact')
 @m.invocation
 def gate():
  with open(p) as f:assert f.read()=='exact'
  with pytest.raises(m.ReplayChanged):os.open(p,os.O_RDONLY)
  return True
 assert gate() is True
def test_iterdir_addition_reject(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'ROOT',tmp_path);p=tmp_path/'directory';p.mkdir()
 @m.invocation
 def gate():
  assert list(p.iterdir())==[]
  with m.ORIGINAL_IO_OPEN(p/'new','w') as f:f.write('new')
  return True
 with pytest.raises(m.ReplayChanged):gate()

def test_unrelated_parent_directory_churn_is_not_receipt_change(tmp_path):
 p=tmp_path/'receipt.json';p.write_text('exact')
 s=m.Session();s.stat(tmp_path);s.read(p)
 (tmp_path/'unrelated.tmp').write_text('other lifecycle')
 s.finish()

def test_directory_identity_replacement_rejected(tmp_path):
 p=tmp_path/'owner';p.mkdir()
 s=m.Session();s.stat(p)
 p.rename(tmp_path/'old-owner');p.mkdir()
 with pytest.raises(m.ReplayChanged):s.finish()

def test_explicit_membership_churn_still_rejected(tmp_path):
 (tmp_path/'receipt.json').write_text('exact')
 s=m.Session();list(s.glob(tmp_path,'*.json'))
 (tmp_path/'new.json').write_text('new')
 with pytest.raises(m.ReplayChanged):s.finish()
