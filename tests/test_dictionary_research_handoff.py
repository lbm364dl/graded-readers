import copy,json,asyncio
from pathlib import Path
import pytest
from jsonschema import ValidationError
from pipeline import dictionary_research_handoff as h,dictionary_research_revision as r
from pipeline import korean_dictionary_revision as editorial
from pipeline.worker_workspace import build,submit,CandidateSubmissionError
from tests.test_annotation_research import _write_job

@pytest.fixture(scope='module')
def packet():
 raw=editorial.dictionary.GRAMMAR.read_text()
 return h.make_handoff(h.source_descriptor(),before_entry=editorial.dictionary._registry(editorial.dictionary.GRAMMAR)['quotation-rago'],registry_before_text=raw,coverage=editorial.coverage({'quotation-rago'},None,'grammar'))

def output(packet):
 entry=copy.deepcopy(packet['before_entry']);entry['explanation_en']='Setup-only revised explanation; this is not a linguistic verdict.'
 url=h.scoped_schema(packet)['properties']['references']['items']['properties']['url']['enum'][0]
 return {'entries':[entry],'references':[{'entry_id':entry['id'],'url':url,'paraphrase_en':'Setup citation; the critic must independently evaluate it.'}]}

def context(packet,stage='writer'):
 value={'entry_kind':'grammar','before_entries':[packet['before_entry']],'coverage':packet['coverage'],h.FIELD:packet,'dictionary_research_revision_validation':{'version':1,'stage':stage,'handoff_digest':packet['digest']}}
 if stage=='critic':value['proposal']=output(packet)
 return value

def test_actual_approved_source_and_complete_coverage(packet):
 assert h.verify_handoff(packet,historical=True)==packet
 assert [len(v) for v in packet['research_inventory'].values()]==[7,14,6]
 assert len(packet['approved_claims']['findings'])==9
 assert len(packet['coverage']['occurrences'])==37
 assert packet['source']['evidence_sha256']==h.APPROVED_EVIDENCE_SHA

@pytest.mark.parametrize('field',['id','title_en','pattern','noop','foreign_url'])
def test_scope_never_rewrites_identity_or_other_fields(packet,field):
 value=output(packet)
 if field=='noop':value['entries'][0]['explanation_en']=packet['before_entry']['explanation_en']
 elif field=='foreign_url':value['references'][0]['url']='https://example.org/foreign'
 else:value['entries'][0][field]='foreign'
 with pytest.raises(ValidationError):h.validate_scoped_revision(value,packet)

@pytest.mark.parametrize('change',['source','unapproved','claims','entry','inventory'])
def test_foreign_or_changed_handoff_rejected(packet,change):
 bad=copy.deepcopy(packet)
 if change=='source':bad['source']['evidence_relpath']='runs/foreign/evidence.json'
 elif change=='unapproved':bad['source']['evidence_sha256']='0'*64
 elif change=='claims':bad['approved_claims']['findings'][0]['fact']='foreign'
 elif change=='entry':bad['before_entry']['pattern']='foreign'
 else:bad['research_inventory']['shortened_occurrences'].pop()
 bad['digest']=h.digest({k:v for k,v in bad.items() if k!='digest'})
 with pytest.raises((ValueError,FileNotFoundError)):h.verify_handoff(bad,historical=True)

def test_stale_current_registry_vs_exact_historical_snapshot(packet,monkeypatch):
 original=h._sha;registry=h.ROOT/'content/lexicon/korean/l1.grammar.json'
 monkeypatch.setattr(h,'_sha',lambda path:'changed-current-registry' if Path(path)==registry else original(path))
 with pytest.raises(ValueError):h.verify_handoff(packet)
 assert h.verify_handoff(packet,historical=True)==packet

def test_current_entry_and_coverage_cannot_change(packet):
 with pytest.raises(ValueError):h.verify_handoff(packet,before_entry={**packet['before_entry'],'title_en':'changed'})
 with pytest.raises(ValueError):h.verify_handoff(packet,coverage={})

def test_workspace_checks_authentic_handoff_and_critic_consistency(packet,tmp_path):
 request=context(packet,'critic');workspace={'dictionary_research_revision':request,'dictionary_research_revision_validation':{'input_field':'dictionary_research_revision'}}
 task,_=build(tmp_path,r.REVIEW_GUIDANCE+r.payload(**workspace),r.contracts.REVIEW,context=workspace)
 assert len((tmp_path/'TASK.txt').read_bytes())<5000
 (tmp_path/'candidate.json').write_text(json.dumps({'approved':True,'issues':['Unresolved objection']}))
 with pytest.raises(CandidateSubmissionError):submit(tmp_path,{'candidate_path':'candidate.json'})
 (tmp_path/'candidate.json').write_text(json.dumps({'approved':True,'issues':[]}))
 assert submit(tmp_path,{'candidate_path':'candidate.json'})[0]=={'approved':True,'issues':[]}

@pytest.mark.asyncio
async def test_existing_bounded_editorial_route_exact_artifact_and_replay(packet,tmp_path):
 class Runner:
  async def call(self,job,prompt,schema,effort,**kwargs):
   assert effort=='low' and kwargs['tool_profile']=='workspace'
   value={'approved':True,'issues':[]} if job.startswith('revision-review') else output(packet)
   lane=_write_job(tmp_path,job,prompt,Path(schema).read_text(),kwargs['workspace_context'],value)
   submit(lane/'workspace',{'candidate_path':'candidate.json'})
   return value
 original=editorial.dictionary.GRAMMAR.read_bytes()
 result,evidence=await r.revise(packet,tmp_path,runner=Runner(),objection_accountability=None)
 assert result['status']=='reviewed' and r.replay(tmp_path,evidence)=={'reviewed':True,'promoted':False,'annotation_approval':False}
 assert editorial.dictionary.GRAMMAR.read_bytes()==original
 assert len(evidence['contracts'])==2
 assert r.promotion_preflight(tmp_path,evidence)['publication_and_curriculum_checks_required'] is True
 bad=copy.deepcopy(evidence);bad['contracts'][0]['workspace_context']['dictionary_research_revision']['coverage']={}
 bad['digest']=h.digest({k:v for k,v in bad.items() if k!='digest'})
 with pytest.raises(ValueError):r.replay(tmp_path,bad)


def test_absent_feature_preserves_historical_submission(tmp_path):
 schema={'type':'object','required':['approved','issues'],'properties':{'approved':{'type':'boolean'},'issues':{'type':'array'}}}
 build(tmp_path,'Historical task'+r.payload(old_context={'unchanged':True}),schema,context={'old_context':{'unchanged':True}})
 (tmp_path/'candidate.json').write_text(json.dumps({'approved':False,'issues':[]}))
 assert submit(tmp_path,{'candidate_path':'candidate.json'})[0]=={'approved':False,'issues':[]}

@pytest.mark.parametrize('change',['kind','extra_entry','coverage','handoff_digest'])
def test_worker_current_context_is_exact(packet,change):
 request=context(packet)
 if change=='kind':request['entry_kind']='word'
 elif change=='extra_entry':request['before_entries']=[packet['before_entry'],packet['before_entry']]
 elif change=='coverage':request['coverage']={}
 else:request['dictionary_research_revision_validation']['handoff_digest']='wrong'
 with pytest.raises(ValueError):r.validate_submission(output(packet),request)


def test_actual_source_archived_data_replay_and_current_freshness(packet,monkeypatch):
 assert packet['version']==2 and set(packet['protected_data_snapshots'])==h.DATA_PATHS
 original=Path.read_bytes
 changed=h.ROOT/'content/korean/honggildong/l5.annotations.json'
 # Simulate legitimate newly serialized JSON bytes without touching published files.
 monkeypatch.setattr(Path,'read_bytes',lambda path:original(path)+b'\n' if path==changed else original(path))
 assert h.verify_handoff(packet,historical=True)==packet
 # The same archive cannot supply authority for a new current decision.
 with pytest.raises((ValueError,AssertionError)):
  h.verify_handoff(packet)
 legacy=copy.deepcopy(packet);legacy['version']=1;legacy.pop('protected_data_snapshots')
 legacy['digest']=h.digest({k:v for k,v in legacy.items() if k!='digest'})
 with pytest.raises(ValueError):h.verify_handoff(legacy,historical=True)

@pytest.mark.parametrize('change',['bytes','sha','foreign','missing','policy'])
def test_archive_requires_exact_original_data_map(packet,change):
 bad=copy.deepcopy(packet);name=next(iter(h.DATA_PATHS))
 if change=='bytes':bad['protected_data_snapshots'][name]['bytes_base64']='e30='
 elif change=='sha':bad['protected_data_snapshots'][name]['sha256']='0'*64
 elif change=='foreign':bad['protected_data_snapshots']['runs/foreign/driver.py']=bad['protected_data_snapshots'][name]
 elif change=='missing':bad['protected_data_snapshots'].pop(name)
 else:bad['version']=1
 bad['digest']=h.digest({k:v for k,v in bad.items() if k!='digest'})
 with pytest.raises(ValueError):h.verify_handoff(bad,historical=True)


def test_policy1_exact_historical_contract_still_replays(packet):
 legacy=copy.deepcopy(packet);legacy['version']=1;legacy.pop('protected_data_snapshots')
 legacy['digest']=h.digest({k:v for k,v in legacy.items() if k!='digest'})
 assert h.verify_handoff(legacy,historical=True)==legacy


@pytest.mark.parametrize('field,value',[('language','ja'),('language','zh'),('entry_kind','word'),('version',2.0)])
def test_policy2_scope_metadata_cannot_be_rebound(packet,field,value):
 bad=copy.deepcopy(packet);bad[field]=value
 bad['digest']=h.digest({k:v for k,v in bad.items() if k!='digest'})
 with pytest.raises(ValueError):h.verify_handoff(bad,historical=True)
 with pytest.raises(ValueError):h.verify_handoff(bad)
