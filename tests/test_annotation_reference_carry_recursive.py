"""Setup-only v3 transport wiring; prior real receipts are separately replayed."""
import copy,json
from pathlib import Path
from unittest.mock import patch
import pytest
from pipeline import annotation_reference_carry as c

def fixture(tmp_path):
 from pipeline.annotation_adjudication import normalize_review
 review={'approved':False,'issues':[{'explanation':'meaning question','candidate_paths':['/segments/0/meaning_en'],'supporting_paths':[]}],'prose_revision_reason_en':''}
 issue=normalize_review('ko',review)['issues'][0]['issue_id']
 inputs={'language':'ko','representation':'korean-flat','candidate':{'segments':[{'text':'아이','meaning_en':'child','lexical_id':'child'}],'grammar_links':[]},'source_text':'아이','current_review':review,'prior_history':[],'context':{'chapter_text':'아이','source_start':0},'known_reference_input':{},'normal_review_receipt':{},'deterministic_gate_evidence':{}}
 ref=lambda fact:{'kind':'approved_lesson','content':{'issue_ids':[issue],'fact':fact},'issue_ids':[issue]}
 node1={'approved':True,'references':{'first':ref('first fact')},'inputs':{}}
 node2={'approved':True,'references':{'second':ref('second fact')},'inputs':{}}
 root={'job':'original'};prior={'job':'prior-enriched','reference_research_version':2,'initial_adjudication':root,'reference_research':node1}
 terminal={'job':'final','reference_research_version':3,'initial_adjudication':prior,'reference_research':node2}
 run=tmp_path/'run';(run/'agents/original').mkdir(parents=True);(run/'agents/final').mkdir(parents=True)
 (run/'agents/original/adjudication-input.json').write_text(json.dumps(inputs))
 (run/'agents/final/followup-reference-research-adjudication.json').write_text(json.dumps(terminal))
 descriptor={'run_relpath':'run','adjudication_job':'final','receipt_digest':c._digest(terminal),'receipt_version':3}
 return run,inputs,terminal,descriptor

def test_v3_reads_original_root_not_immediate_enriched_input_and_both_nodes(tmp_path):
 run,inputs,t,d=fixture(tmp_path)
 def verified(root,evidence,**kwargs):assert root==run and kwargs==inputs and evidence==t;return evidence
 with patch.object(c,'ROOT',tmp_path),patch('pipeline.annotation_adjudication.verify_adjudication_evidence',side_effect=verified):
  original,research=c._authenticate(d)
  assert original==inputs and set(research['references'])=={'first','second'}
  envelope=c.carry_from_adjudication(run,t);assert envelope['sources']==[d]
  bound=c.bind_carried_research(run,envelope,candidate=inputs['candidate'],source_text=inputs['source_text'],language='ko',representation='korean-flat',context=inputs['context'],current_review=inputs['current_review'])
  assert len(bound['packet']['facts'])==len(bound['references'])==2
  changed=copy.deepcopy(inputs['candidate']);changed['segments'][0]['meaning_en']='repaired'
  assert len(c.bind_carried_research(run,envelope,candidate=changed,source_text=inputs['source_text'],language='ko',representation='korean-flat',context=inputs['context'])['packet']['facts'])==2
  changed['segments'][0]['lexical_id']='foreign'
  assert not c.bind_carried_research(run,envelope,candidate=changed,source_text=inputs['source_text'],language='ko',representation='korean-flat',context=inputs['context'])['packet']

@pytest.mark.parametrize('tamper',['digest','filename','depth','root','nodecollision'])
def test_v3_carry_tamper_and_wrong_transport_fail_closed(tmp_path,tamper):
 run,inputs,t,d=fixture(tmp_path)
 if tamper=='digest':d['receipt_digest']='0'*64
 elif tamper=='filename':(run/'agents/final/followup-reference-research-adjudication.json').rename(run/'agents/final/reference-research-adjudication.json')
 elif tamper=='root':(run/'agents/original/adjudication-input.json').unlink()
 elif tamper=='depth':t['initial_adjudication']['reference_research_version']=3
 else:t['reference_research']['references']['first']={'kind':'approved_lesson','content':{'fact':'foreign'}}
 if tamper in ('depth','nodecollision'):
  (run/'agents/final/followup-reference-research-adjudication.json').write_text(json.dumps(t));d['receipt_digest']=c._digest(t)
 with patch.object(c,'ROOT',tmp_path),patch('pipeline.annotation_adjudication.verify_adjudication_evidence',return_value=t),pytest.raises(c.ResearchCarryError):c._authenticate(d)

def test_prior_approved_fact_still_carries_when_second_task_has_no_facts(tmp_path):
 run,inputs,t,d=fixture(tmp_path);t['reference_research']={'approved':False,'references':{},'inputs':{}}
 (run/'agents/final/followup-reference-research-adjudication.json').write_text(json.dumps(t));d['receipt_digest']=c._digest(t)
 with patch.object(c,'ROOT',tmp_path),patch('pipeline.annotation_adjudication.verify_adjudication_evidence',return_value=t):
  assert c.carry_from_adjudication(run,t)['sources']==[d]
  assert set(c._authenticate(d)[1]['references'])=={'first'}

def test_v3_theory_fact_scope_does_not_expand_to_grouped_other_target(tmp_path):
 run,inputs,t,d=fixture(tmp_path)
 from pipeline.annotation_run_lessons import reference_policy_marker
 issue=t['reference_research']['references']['second']['issue_ids'][0]
 inputs['candidate']['segments'][0]['context_en']='context'
 inputs['current_review']['issues'][0]['candidate_paths'].append('/segments/0/context_en')
 from pipeline.annotation_adjudication import normalize_review
 newissue=normalize_review('ko',inputs['current_review'])['issues'][0]['issue_id']
 for node in (t['reference_research'],t['initial_adjudication']['reference_research']):
  for ref in node['references'].values():
   ref['issue_ids']=[newissue];ref['content']['issue_ids']=[newissue]
   ref['content']['_annotation_run_lesson_scope']={**reference_policy_marker(),'issue_paths':{newissue:['/segments/0/meaning_en']}}
 (run/'agents/original/adjudication-input.json').write_text(json.dumps(inputs));(run/'agents/final/followup-reference-research-adjudication.json').write_text(json.dumps(t));d['receipt_digest']=c._digest(t)
 with patch.object(c,'ROOT',tmp_path),patch('pipeline.annotation_adjudication.verify_adjudication_evidence',return_value=t):
  bound=c.bind_carried_research(run,{'version':1,'sources':[d]},candidate=inputs['candidate'],source_text=inputs['source_text'],language='ko',representation='korean-flat',context=inputs['context'],current_review=inputs['current_review'])
  assert len(bound['packet']['facts'])==2 and not bound['references']
  assert all(x['candidate_paths']==['/segments/0/meaning_en'] for x in bound['packet']['facts'])

@pytest.mark.parametrize('legacy',[False,True])
def test_register_then_resume_preserves_descriptor_without_new_mutation(tmp_path,legacy):
 run,inputs,t,d=fixture(tmp_path)
 if legacy:d.pop('receipt_version')
 # Authentic-shaped setup: registration calls the actual shared persistence path.
 research={'references':t['reference_research']['references'],'inputs':{}}
 envelope={'version':1,'sources':[d]}
 with patch.object(c,'_authenticate',return_value=(inputs,research)):
  registered=c.register_carried_research(run,envelope,candidate=inputs['candidate'],source_text=inputs['source_text'],language='ko',representation='korean-flat',context=inputs['context'])
  assert registered==envelope
  assert c.load_carried_research(run,candidate=inputs['candidate'],source_text=inputs['source_text'],language='ko',representation='korean-flat',context=inputs['context'])==envelope

@pytest.mark.parametrize('field',['receipt','descriptor'])
def test_float_version_marker_rejected(tmp_path,field):
 run,inputs,t,d=fixture(tmp_path)
 with patch.object(c,'ROOT',tmp_path),pytest.raises(c.ResearchCarryError):
  if field=='receipt':
   t['reference_research_version']=3.0;c.carry_from_adjudication(run,t)
  else:d['receipt_version']=3.0;c._authenticate(d)
