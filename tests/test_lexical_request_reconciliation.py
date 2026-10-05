import copy,sys
from pathlib import Path
import pytest
from pipeline import lexical_request_reconciliation as c

def fixture(rep='korean-flat'):
 candidate={'segments':[{'surface' if rep=='japanese-annotation' else 'text':'XX','lemma':'base','lemma_kana':'reading','lexical_id':'word-a'}]}
 catalog=[{'id':'word-a','headword':'base','reading':'reading','kind':'word','content':{'id':'word-a','meaning':'fixture-only'}}]
 content=catalog[0]['content'];inputs={'policy_version':1,'representation':rep,'source_text':'XX','parent_text':'XXZZ','source_position':{'chunk_index':0,'source_start':0,'source_text_digest':c.digest('XX'),'parent_text_digest':c.digest('XXZZ')},'candidate':candidate,'candidate_digest':c.digest(candidate),'candidate_status':'unapproved_grounding','candidate_owners':c.candidate_owners(candidate,'XX',rep),'requests':['surface-request'],'catalog':catalog,'references':{'catalog-a':{'kind':'catalog_attestation','catalog_id':'word-a','content':content,'content_digest':c.digest(content)}}}
 decision={'request_index':0,'request':'surface-request','scope':'selected','occurrences':[{'start':0,'end':2,'text':'XX','candidate_paths':['/segments/0/lemma']}],'disposition':'existing_catalog_base','catalog_id':'word-a','headword':'base','reading':'reading','kind':'word','reason':'Structural fixture, not a linguistic claim.','citations':['catalog-a']}
 result={'policy_version':1,'inputs_digest':c.digest(inputs),'decisions':[decision]};review={'policy_version':1,'inputs_digest':c.digest(inputs),'result_digest':c.digest(result),'approved':True,'issues':[]}
 return inputs,result,review
@pytest.mark.parametrize('rep',['korean-flat','chinese-annotation','japanese-annotation'])
def test_primary_source_representations(rep):
 inputs,result,review=fixture(rep);assert c.check_review(review,result,inputs);lookup=c._consume_bound_lookup(result,review,inputs,source_text='XX',source_position=inputs['source_position'],current_catalog=inputs['catalog']);assert lookup['lookup_headwords']==['base'] and not lookup['annotation_approved']
@pytest.mark.parametrize('key,value',[('reading','foreign-reading'),('kind','grammar'),('catalog_id','foreign-id')])
def test_identity_reading_kind_not_collapsed(key,value):
 inputs,result,_=fixture();result['decisions'][0][key]=value
 with pytest.raises(ValueError):c.check_result(result,inputs)
def test_foreign_following_occurrence_rejected():
 inputs,result,_=fixture();result['decisions'][0]['occurrences'][0].update(start=2,end=4,text='ZZ')
 with pytest.raises(ValueError):c.check_result(result,inputs)
def test_candidate_owner_swapping_rejected():
 inputs,result,_=fixture();inputs['candidate_owners'][0]['paths']=['/segments/99/lemma'];result['inputs_digest']=c.digest(inputs)
 with pytest.raises(ValueError):c.check_result(result,inputs)
@pytest.mark.parametrize('disposition',['needs_research','unresolved'])
def test_uncertain_or_genuine_unknown_compound_has_no_reuse_authority(disposition):
 inputs,result,_=fixture();row=result['decisions'][0];row['disposition']=disposition
 for key in ('catalog_id','headword','reading','kind'):row[key]=''
 c.check_result(result,inputs);row['catalog_id']='word-a'
 with pytest.raises(ValueError):c.check_result(result,inputs)
def test_outside_selected_does_not_expand_research():
 inputs,result,_=fixture();row=result['decisions'][0];row.update(scope='outside_selected_scope',occurrences=[],disposition='unresolved')
 for key in ('catalog_id','headword','reading','kind'):row[key]=''
 c.check_result(result,inputs);row['disposition']='needs_research'
 with pytest.raises(ValueError):c.check_result(result,inputs)
def test_changed_catalog_and_source_fail_current_use_but_old_snapshot_replays():
 inputs,result,review=fixture();changed=copy.deepcopy(inputs['catalog']);changed[0]['content']['meaning']='changed'
 assert c.check_review(review,result,inputs)
 with pytest.raises(ValueError):c._consume_bound_lookup(result,review,inputs,source_text='XX',source_position=inputs['source_position'],current_catalog=changed)
 with pytest.raises(ValueError):c._consume_bound_lookup(result,review,inputs,source_text='ZZ',source_position=inputs['source_position'],current_catalog=inputs['catalog'])
def test_full_request_coverage_and_review_binding():
 inputs,result,review=fixture();result['decisions']=[]
 with pytest.raises(ValueError):c.check_result(result,inputs)
 inputs,result,review=fixture();review['result_digest']='foreign'
 with pytest.raises(ValueError):c.check_review(review,result,inputs)
 inputs,result,review=fixture();review['issues']=[{'request_index':0,'explanation':'blocked'}]
 with pytest.raises(ValueError):c.check_review(review,result,inputs)
@pytest.mark.parametrize('version',[True,1.0,2])
def test_explicit_policy_type_failclosed(version):
 inputs,result,_=fixture();result['policy_version']=version
 with pytest.raises((ValueError,Exception)):c.check_result(result,inputs)

def test_oversized_source_interval_rejected():
 inputs,result,_=fixture();result['decisions'][0]['occurrences'][0]['end']=999999
 with pytest.raises(ValueError,match='Foreign occurrence'):c.check_result(result,inputs)
def test_duplicate_current_catalog_identity_rejected():
 inputs,result,review=fixture();duplicate=copy.deepcopy(inputs['catalog'][0]);duplicate['content']['meaning']='equivocal'
 with pytest.raises(ValueError,match='Equivocal current'):c._consume_bound_lookup(result,review,inputs,source_text='XX',source_position=inputs['source_position'],current_catalog=inputs['catalog']+[duplicate])

@pytest.mark.parametrize('language,representation',[('ja','korean-flat'),('xx','japanese-annotation'),('ko','chinese-annotation')])
def test_foreign_language_representation_rejected(language,representation):
 inputs,result,_=fixture();inputs.update(language=language,representation=representation);result['inputs_digest']=c.digest(inputs)
 with pytest.raises(ValueError,match='language/representation'):c.check_result(result,inputs)

def test_real_schema_fields_japanese_reading_and_kind_remain_distinct():
 inputs,result,review=fixture('japanese-annotation');segment=inputs['candidate']['segments'][0];segment.update(surface='はし',lemma='橋',lemma_kana='はし',dictionary_key='word-hashi',pos='noun',meaning_en='bridge',form_steps=[]);inputs['source_text']='はし';inputs['parent_text']='はしZZ';inputs['source_position'].update(source_text_digest=c.digest('はし'),parent_text_digest=c.digest('はしZZ'));inputs['candidate_digest']=c.digest(inputs['candidate']);inputs['candidate_owners']=c.candidate_owners(inputs['candidate'],'はし','japanese-annotation');result['decisions'][0]['occurrences'][0]['text']='はし';result['inputs_digest']=c.digest(inputs);c.check_result(result,inputs)
 result['decisions'][0]['kind']='grammar'
 with pytest.raises(ValueError,match='Identity/reading/kind'):c.check_result(result,inputs)

def test_real_schema_fields_chinese_source_stays_exact():
 inputs,result,_=fixture('chinese-annotation');inputs['candidate']['segments'][0].update(text='两人',pinyin='liǎng rén',meaning_en='two people',form_steps=[]);inputs.update(source_text='两人',parent_text='两人ZZ');inputs['source_position'].update(source_text_digest=c.digest('两人'),parent_text_digest=c.digest('两人ZZ'));inputs['candidate_digest']=c.digest(inputs['candidate']);inputs['candidate_owners']=c.candidate_owners(inputs['candidate'],'两人','chinese-annotation');result['decisions'][0]['occurrences'][0]['text']='两人';result['inputs_digest']=c.digest(inputs);c.check_result(result,inputs)
 result['decisions'][0]['occurrences'][0]['text']='二人'
 with pytest.raises(ValueError,match='Foreign occurrence'):c.check_result(result,inputs)
