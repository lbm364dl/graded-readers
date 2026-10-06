"""Portable exact worker receipts with explicit source-auth isolation, not semantics."""
import copy,json
from pathlib import Path
import pytest
from pipeline import dictionary_research_revision as r,dictionary_research_handoff as h,dictionary_objection_accountability as a
from pipeline.annotation_adjudication import digest
from tests.test_annotation_research import _write_job
from tests.test_dictionary_editorial_criteria import packet

@pytest.mark.asyncio
async def test_korean_two_pairs_account_and_replay_exact_artifacts(packet,tmp_path,monkeypatch):
 monkeypatch.setattr(a,'ROOT',tmp_path)
 seen=[];base=packet['before_entry'];url=next(iter(packet['known_references'].values()))['content']['url']
 class Runner:
  async def call(self,job,prompt,schema,effort,**kw):
   assert effort=='low' and kw['tool_profile']=='workspace'
   ctx=kw['workspace_context'];seen.append(job)
   if not job.startswith('revision-review'):
    text='Setup first proposed explanation.' if job=='revision-0' else 'Setup revised explanation.'
    value={'entries':[{**base,'explanation_en':text}],'references':[{'entry_id':base['id'],'url':url,'paraphrase_en':'Setup source.'}]}
    if job=='revision-1':assert len(ctx['dictionary_research_revision']['dictionary_objection_accountability_history']['rows'])==1
   elif job=='revision-review-0':value={'approved':False,'issues':['Setup retained own explanation objection.'],'prior_issue_assessments':[]}
   else:
    p=ctx['dictionary_objection_review'];path='/entries/0/explanation_en';text=p['proposal']['entries'][0]['explanation_en']
    value={'approved':True,'issues':[],'prior_issue_assessments':[{'issue_id':p['rows'][0]['issue_id'],'disposition':'resolved_change','proposal_paths':[path],'proposal_spans':[{'path':path,'start':0,'end':len(text),'quote':text}],'rationale':'Setup actual changed authorized explanation; semantic truth is not inferred by this test.','changed_fields':[{'path':path,'old_value_digest':digest('Setup first proposed explanation.'),'current_value_digest':digest(text)}]}]}
   _write_job(tmp_path,job,prompt,Path(schema).read_text(),ctx,value);return value
 _,evidence=await r.revise(packet,tmp_path,runner=Runner())
 assert seen==['revision-0','revision-review-0','revision-1','revision-review-1']
 assert r.replay(tmp_path,evidence)=={'reviewed':True,'promoted':False,'annotation_approval':False}
 assert evidence['editorial_record']['review']['prior_issue_assessments']
 assert evidence['source_contract_version']==3
 for row in evidence['contracts']:
  assert row['worker_descriptor']['provider']==a.WORKER_PROVIDER
  assert a.resolve_verified_worker_contract(row['worker_descriptor'])['result']==row['result']
 # Proof artifact removal must fail closed; replay must not recreate it.
 target=tmp_path/evidence['contracts'][0]['worker_descriptor']['contract_path']
 raw=target.read_bytes();target.unlink()
 with pytest.raises((ValueError,FileNotFoundError)):r.replay(tmp_path,evidence)
 assert not target.exists()
 target.write_bytes(raw)
 bad=copy.deepcopy(evidence);bad['editorial_record']['review']['prior_issue_assessments']=[];bad['digest']=digest({k:v for k,v in bad.items() if k!='digest'})
 with pytest.raises(ValueError):r.replay(tmp_path,bad)

@pytest.mark.asyncio
async def test_retained_legacy_pairs_replay_as_exact_raw_roster_sources(packet,tmp_path,monkeypatch):
 monkeypatch.setattr(a,'ROOT',tmp_path)
 run=tmp_path/'legacy';base=packet['before_entry'];url=next(iter(packet['known_references'].values()))['content']['url']
 output={'entries':[{**base,'explanation_en':'Setup retained legacy proposal.'}],'references':[{'entry_id':base['id'],'url':url,'paraphrase_en':'Setup source.'}]}
 class Runner:
  async def call(self,job,prompt,schema,effort,**kw):
   value=({'approved':False,'issues':['Setup legacy retained objection.']} if job=='revision-review-0' else {'approved':True,'issues':[]}) if job.startswith('revision-review') else output
   _write_job(run,job,prompt,Path(schema).read_text(),kw['workspace_context'],value);return value
 await r.revise(packet,run,runner=Runner(),objection_accountability=None)
 descriptor={'run_relpath':'legacy','evidence_sha256':h._sha(run/'research-bound-revision-evidence.json')}
 sources=r.authenticated_accountability_sources(descriptor,packet)
 assert len(sources)==2
 rows=a.roster([c['source'] for c in sources]);assert len(rows)==1 and rows[0]['issue_text']=='Setup legacy retained objection.'
 assert all(a._archive_contract(c['writer'])['result']==output for c in sources)
 with pytest.raises(ValueError):r.authenticated_accountability_sources({**descriptor,'evidence_sha256':'0'*64},packet)
