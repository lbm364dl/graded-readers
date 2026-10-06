import copy,json
from pathlib import Path
import pytest
from pipeline import dictionary_editorial_criteria as c,dictionary_research_revision as r,dictionary_research_handoff as h,korean_dictionary_revision as e
from tests.test_annotation_research import _write_job

@pytest.fixture
def packet(tmp_path,monkeypatch):
 # Portable editorial isolation: source research authentication is tested by the
 # opt-in retained suite. These setup claims are not scientific evidence.
 before={'id':'setup-pattern','title_en':'Setup formation','pattern':'SETUP','explanation_en':'Setup original explanation.'}
 registry=tmp_path/'content/lexicon/korean/l1.grammar.json';registry.parent.mkdir(parents=True)
 registry.write_text(json.dumps({'reviewed':True,'entries':[before]}))
 coverage={'occurrences':[{'source':'setup-source','chapter':1,'segment_index':i,'grammar_entry_id':before['id']} for i in (0,2)]}
 refs={'setup-primary':{'kind':'primary_source','content':{'url':'https://krdict.korean.go.kr/eng/dicSearch/SearchView?ParaWordNo=1','captured_text':'Setup only, not a linguistic fact.'}}}
 inventory={'shortened_occurrences':[], 'full_form_contrasts':[], 'reporting_contrasts':[]}
 research={'inputs':{'candidate':{'approved_lesson':before,**inventory},'known_reference_input':refs},'derived_result':{'findings':[]},'digest':'setup-source-authentication-isolation'}
 descriptor={'provider':'setup-only-authentication-seam'}
 def verify_source(value,**kwargs):
  if value!=descriptor:raise ValueError('Foreign setup source')
  return research
 original_root=Path(e.__file__).resolve().parents[1]
 schema=tmp_path/'pipeline/schemas/grammar-dictionary.schema.json';schema.parent.mkdir(parents=True)
 schema.write_bytes((original_root/'pipeline/schemas/grammar-dictionary.schema.json').read_bytes())
 monkeypatch.setattr(h,'ROOT',tmp_path);monkeypatch.setattr(h,'_verify_source',verify_source)
 monkeypatch.setattr(e.dictionary,'GRAMMAR',registry)
 monkeypatch.setattr(e,'coverage',lambda *a,**k:copy.deepcopy(coverage))
 monkeypatch.setattr(e,'_verify_coverage_fresh',lambda value: value==coverage or (_ for _ in ()).throw(ValueError('Changed setup coverage')))
 value={'version':2,'purpose':'published_registry_revision_only','language':'ko','entry_kind':'grammar','source':descriptor,'source_evidence_digest':research['digest'],'before_entry':before,'registry_sha256':h._sha(registry),'registry_before_text':registry.read_text(),'coverage':coverage,'research_inventory':inventory,'approved_claims':research['derived_result'],'known_references':refs,'allowed_fields':['explanation_en']}
 value['digest']=h.digest(value);return value

def test_absent_feature_preserves_exact_old_writer_and_critic(packet):
 for review in (False,True):
  old=(r.REVIEW_GUIDANCE if review else r.GUIDANCE)+' '+r.ARCHIVE_GUIDANCE
  assert r.instructions(packet,review)==old
  assert r.instructions(packet,review,c.marker())==old+' '+c.CRITERIA

@pytest.mark.parametrize('change',['version','digest'])
def test_unknown_policy_cannot_appear_in_current_request(change):
 bad=c.marker();bad['version' if change=='version' else 'policy_digest']='foreign'
 with pytest.raises(ValueError):c.guidance(bad)

@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[False,True])
async def test_exact_writer_critic_artifacts_and_historical_replay(packet,tmp_path,enabled):
 entry={**packet['before_entry'],'explanation_en':'Setup own-pattern explanation, not linguistic approval.'}
 url=h.scoped_schema(packet)['properties']['references']['items']['properties']['url']['enum'][0]
 output={'entries':[entry],'references':[{'entry_id':entry['id'],'url':url,'paraphrase_en':'Setup citation.'}]}
 class Runner:
  async def call(self,job,prompt,schema,effort,**kw):
   assert effort=='low' and kw['tool_profile']=='workspace'
   ctx=kw['workspace_context']['dictionary_research_revision']
   assert ('dictionary_editorial_criteria' in ctx)==enabled
   if job.startswith('revision-review'):assert ctx['proposal']==output
   value={'approved':True,'issues':[]} if job.startswith('revision-review') else output
   _write_job(tmp_path,job,prompt,Path(schema).read_text(),kw['workspace_context'],value)
   return value
 kwargs={} if enabled else {'editorial_criteria':None}
 _,ev=await r.revise(packet,tmp_path,runner=Runner(),objection_accountability=None,**kwargs)
 assert r.replay(tmp_path,ev)['reviewed']
 if enabled:
  bad=copy.deepcopy(ev);bad['dictionary_editorial_criteria']['version']=2;bad['digest']=h.digest({k:v for k,v in bad.items() if k!='digest'})
  with pytest.raises(ValueError):r.replay(tmp_path,bad)


def test_schema_contrasts_preserve_formation_and_dedicated_occurrence_channels(packet):
 # This proposal adds reviewer guidance, not a prose keyword validator.
 schema=h.scoped_schema(packet)
 assert 'examples' not in schema['properties']['entries']['items']['properties']
 ja=json.loads((h.ROOT/'pipeline/schemas/grammar-dictionary.schema.json').read_text())
 assert 'formation' in ja['properties']['entries']['items']['properties']
 assert 'assignments' in ja['properties']
 # Minimal own-formation/prerequisite guidance stays allowed; catalog prevention
 # is adjudicated by the independent critic rather than English substring checks.
 assert not hasattr(c,'validate_explanation')


@pytest.mark.asyncio
async def test_portable_prior_receipt_lineage_bound_and_not_lost(packet,tmp_path):
 run=tmp_path/'prior-run';entry={**packet['before_entry'],'explanation_en':'Setup changed own formation.'}
 output={'entries':[entry],'references':[{'entry_id':entry['id'],'url':next(iter(packet['known_references'].values()))['content']['url'],'paraphrase_en':'Setup-only citation.'}]}
 class Runner:
  async def call(self,job,prompt,schema,effort,**kw):
   value=({'approved':False,'issues':['Setup scope defect']} if job=='revision-review-0' else {'approved':True,'issues':[]}) if job.startswith('revision-review') else output
   _write_job(run,job,prompt,Path(schema).read_text(),kw['workspace_context'],value);return value
 await r.revise(packet,run,runner=Runner(),objection_accountability=None,editorial_criteria=None)
 descriptor={'run_relpath':str(run.relative_to(h.ROOT)),'evidence_sha256':h._sha(run/'research-bound-revision-evidence.json')}
 prior=r.authenticated_prior_reviews(descriptor,packet)
 assert [row['review']['approved'] for row in prior['reviews']]==[False,True]
 assert all(row['writer_proposal']==output for row in prior['reviews'])
 with pytest.raises(ValueError):r.authenticated_prior_reviews({**descriptor,'evidence_sha256':'0'*64},packet)


def test_boolean_policy_version_is_not_integer_version():
 bad=c.marker();bad['version']=True
 with pytest.raises(ValueError):c.guidance(bad)

@pytest.mark.asyncio
async def test_rejected_prior_attempt_reaches_next_writer_and_fresh_critic(packet,tmp_path):
 entry={**packet['before_entry'],'explanation_en':'Setup own-pattern explanation; no linguistic verdict.'}
 url=h.scoped_schema(packet)['properties']['references']['items']['properties']['url']['enum'][0]
 output={'entries':[entry],'references':[{'entry_id':entry['id'],'url':url,'paraphrase_en':'Setup citation.'}]};seen=[]
 class Runner:
  async def call(self,job,prompt,schema,effort,**kw):
   ctx=kw['workspace_context']['dictionary_research_revision'];history=ctx['prior_attempt_editorial_reviews']
   if job.endswith('-1'):
    assert len(history)==1 and history[0]['review']['issues']==['Setup scope defect']
    assert history[0]['writer_proposal']==output
   else:assert history==[]
   seen.append(job)
   value={'approved':False,'issues':['Setup scope defect']} if job=='revision-review-0' else ({'approved':True,'issues':[]} if job.startswith('revision-review') else output)
   _write_job(tmp_path,job,prompt,Path(schema).read_text(),kw['workspace_context'],value);return value
 _,evidence=await r.revise(packet,tmp_path,runner=Runner(),objection_accountability=None)
 assert seen==['revision-0','revision-review-0','revision-1','revision-review-1']
 assert r.replay(tmp_path,evidence)['reviewed']
