import copy,json
import pytest
from pipeline import lexical_request_reconciliation as c

def fixture(rep='korean-flat'):
 candidate={'segments':[{'surface' if rep=='japanese-annotation' else 'text':'XX','lemma':'base','lemma_kana':'reading','lexical_id':'word-a'}]}
 catalog=[{'id':'word-a','headword':'base','reading':'reading','kind':'word','content':{'id':'word-a','meaning':'fixture-only'}}]
 content=catalog[0]['content'];inputs={'policy_version':1,'representation':rep,'source_text':'XX','parent_text':'XXZZ','source_position':{'chunk_index':0,'source_start':0,'source_text_digest':c.digest('XX'),'parent_text_digest':c.digest('XXZZ')},'candidate':candidate,'candidate_digest':c.digest(candidate),'candidate_status':'unapproved_grounding','candidate_owners':c.candidate_owners(candidate,'XX',rep),'requests':['surface-request'],'catalog':catalog,'references':{'catalog-a':{'kind':'catalog_attestation','catalog_id':'word-a','content':content,'content_digest':c.digest(content)}}}
 decision={'request_index':0,'request':'surface-request','scope':'selected','occurrences':[{'start':0,'end':2,'text':'XX','candidate_paths':['/segments/0/lemma']}],'disposition':'existing_catalog_base','catalog_id':'word-a','headword':'base','reading':'reading','kind':'word','reason':'Structural fixture, not a linguistic claim.','citations':['catalog-a']}
 result={'policy_version':1,'inputs_digest':c.digest(inputs),'decisions':[decision]};review={'policy_version':1,'inputs_digest':c.digest(inputs),'result_digest':c.digest(result),'approved':True,'issues':[]}
 return inputs,result,review

def repair_case():
 inputs,base,_=fixture();inputs['reconciliation_instruction_policy_version']=2
 base['inputs_digest']=c.digest(inputs)
 review={'policy_version':1,'inputs_digest':c.digest(inputs),'result_digest':c.digest(base),'approved':False,'issues':[{'request_index':0,'explanation':'Protocol fixture correction.'}]}
 patch={'policy_version':2,'base_digest':c.digest(base),'edits':[{'request_index':0,'fields':{'reason':'Revised structural explanation.'}}]}
 return inputs,base,review,patch

def test_repair_exact_authority_preserves_occurrence_and_original():
 inputs,base,review,patch=repair_case();old=copy.deepcopy(base);new=c.apply_reconciliation_repair(patch,inputs,base,review)
 assert base==old and new['decisions'][0]['occurrences']==base['decisions'][0]['occurrences']
 assert new['decisions'][0]['reason']=='Revised structural explanation.'
 assert not c._consume_bound_lookup(new,{'policy_version':1,'inputs_digest':c.digest(inputs),'result_digest':c.digest(new),'approved':True,'issues':[]},inputs,source_text='XX',source_position=inputs['source_position'],current_catalog=inputs['catalog'])['annotation_approved']

@pytest.mark.parametrize('bad',['foreign','noop','source','base','missing','float','bool','review'])
def test_repair_scope_and_authority_fail_closed(bad):
 inputs,base,review,patch=repair_case()
 if bad=='foreign':patch['edits'][0]['request_index']=1
 elif bad=='noop':patch['edits'][0]['fields']['reason']=base['decisions'][0]['reason']
 elif bad=='source':patch['edits'][0]['fields']={'occurrences':[]}
 elif bad=='base':patch['base_digest']='0'*64
 elif bad=='missing':patch['edits']=[]
 elif bad=='float':patch['policy_version']=2.0
 elif bad=='bool':patch['policy_version']=True
 else:review['inputs_digest']='0'*64
 with pytest.raises(Exception):c.apply_reconciliation_repair(patch,inputs,base,review)

@pytest.mark.parametrize('stage',['resolver','review'])
def test_absent_and_explicit_old_instruction_exact(stage):
 old=c.GUIDANCE if stage=='resolver' else c.REVIEW_GUIDANCE
 assert c._worker_guidance({},stage)==c._worker_guidance({'reconciliation_instruction_policy_version':1},stage)==old
 assert c.EVIDENCE_SCOPE_GUIDANCE in c._worker_guidance({'reconciliation_instruction_policy_version':2},stage)

@pytest.mark.parametrize('marker',[True,2.0,'2',3])
def test_instruction_marker_type_rejected(marker):
 with pytest.raises(ValueError):c._worker_guidance({'reconciliation_instruction_policy_version':marker},'resolver')

@pytest.mark.parametrize('representation',['korean-flat','chinese-annotation','japanese-annotation'])
def test_representation_scoped_repair_owner_source_and_sibling_preserved(representation):
 inputs,base,_=fixture(representation)
 segment=inputs['candidate']['segments'][0]
 if representation=='japanese-annotation':segment.update(surface='XX',lemma='base',lemma_kana='reading',dictionary_key='word-a',pos='noun',meaning_en='fixture',form_steps=[])
 elif representation=='chinese-annotation':segment.update(text='XX',pinyin='fixture',meaning_en='fixture',form_steps=[])
 inputs['candidate_digest']=c.digest(inputs['candidate']);inputs['candidate_owners']=c.candidate_owners(inputs['candidate'],'XX',representation)
 inputs['requests'].append('context-request');sibling=copy.deepcopy(base['decisions'][0]);sibling.update(request_index=1,request='context-request');base['decisions'].append(sibling)
 inputs['reconciliation_instruction_policy_version']=2;base['inputs_digest']=c.digest(inputs)
 review={'policy_version':1,'inputs_digest':c.digest(inputs),'result_digest':c.digest(base),'approved':False,'issues':[{'request_index':0,'explanation':'Structural correction only.'}]}
 patch={'policy_version':2,'base_digest':c.digest(base),'edits':[{'request_index':0,'fields':{'reason':'Revised protocol fixture.'}}]}
 original=copy.deepcopy(inputs);new=c.apply_reconciliation_repair(patch,inputs,base,review)
 assert inputs==original and new['decisions'][1]==sibling
 assert new['decisions'][0]['occurrences']==base['decisions'][0]['occurrences']
 # This is the shared typed contract; C/J do not acquire fabricated Korean discovery history.
 assert c._worker_guidance({},'resolver')==c.GUIDANCE
