"""Optional authentic private receipts: root must opt in and require no skips."""
import json,os
from pathlib import Path
import pytest
from pipeline import dictionary_editorial_criteria as c,dictionary_research_revision as r,dictionary_research_handoff as h,korean_dictionary_revision as e
ENV='GRADED_READERS_EDITORIAL_RETAINED_MANIFEST'
pytestmark=pytest.mark.skipif(not os.environ.get(ENV),reason='Optional retained research/editorial receipts require explicit env manifest')
@pytest.fixture(scope='module')
def retained():
 manifest=json.loads(Path(os.environ[ENV]).read_text())
 descriptor=manifest['source_descriptor']
 assert descriptor==h.source_descriptor() and descriptor['evidence_sha256']==manifest['approved_evidence_sha256']
 packet=h.make_handoff(descriptor,before_entry=e.dictionary._registry(e.dictionary.GRAMMAR)[manifest['before_entry_id']],registry_before_text=e.dictionary.GRAMMAR.read_text(),coverage=e.coverage({manifest['before_entry_id']},None,'grammar'))
 return manifest,packet

def test_authentic_nine_claims_and_full_37_coverage(retained):
 manifest,packet=retained
 assert h.verify_handoff(packet,historical=True)==packet
 assert len(packet['approved_claims']['findings'])==manifest['research_claim_count']==9
 assert len(packet['coverage']['occurrences'])==manifest['coverage_count']==37


def test_authentic_prior_critic0_critic1_receipts(retained):
 manifest,packet=retained;prior=r.authenticated_prior_reviews(manifest['prior_editorial_origin'],packet)
 assert [row['review']['approved'] for row in prior['reviews']]==manifest['prior_review_states']==[False,True]
 assert prior['reviews'][0]['writer_proposal']!=prior['reviews'][1]['writer_proposal']

@pytest.mark.asyncio
async def test_actual_new_writer_and_critic_both_receive_authenticated_prior_history(retained,tmp_path):
 manifest,packet=retained;expected=r.authenticated_prior_reviews(manifest['prior_editorial_origin'],packet);seen=[]
 class Stop(Exception):pass
 class Capture:
  async def call(self,job,prompt,schema,effort,**kw):
   assert effort=='low' and kw['tool_profile']=='workspace'
   ctx=kw['workspace_context']['dictionary_research_revision']
   assert ctx['authenticated_prior_editorial_reviews']==expected and ctx['dictionary_editorial_criteria']==c.marker()
   assert ctx[h.FIELD]==packet;seen.append(job);raise Stop()
 adapter=r.ResearchBoundRunner(Capture(),tmp_path,packet,c.marker(),manifest['prior_editorial_origin'])
 base={'entry_kind':'grammar','before_entries':[packet['before_entry']],'coverage':packet['coverage']}
 for job,fields in [('revision-0',{'previous':None,'issues':[]}),('revision-review-0',{'proposal':expected['reviews'][-1]['writer_proposal']})]:
  with pytest.raises(Stop):await adapter.call(job,'Prepaid actual boundary'+r.payload(**base,**fields),tmp_path/'unused.schema','low',tool_profile='research')
 assert seen==['revision-0','revision-review-0']
