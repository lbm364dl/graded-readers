import copy, importlib.util
from pathlib import Path
import pytest
from pipeline import korean_dictionary as m
def base():
 return {'text':'가서 먹었다','title':'Fixture','segments':[{'text':'가서','type':'word','meaning_en':'fixture','lexical':{'id':'w','kind':'proper_name'},'form_steps':[{'form':'가서','reading':'','label':'fixture','meaning_en':'fixture complete','grammar_entry_ids':['g']}]},{'text':' ','type':'punctuation'},{'text':'먹었다','type':'word','meaning_en':'fixture','lexical':{'id':'w','kind':'proper_name'}}], 'grammar_links':[{'segment_index':0,'entry_id':'g','context_en':'fixture','display_form':'가서 먹었다','display_meaning_en':'fixture complete phrase','display_end_segment_index':2}], 'form_audit':{'reviewed':True,'inflected_segment_indices':[0]}}
def gate(value,tmp_path):return m.build_assets(value,tmp_path,word_registry={'w':{'id':'w','kind':'proper_name'}},grammar_registry={'g':{'id':'g'}},write=False)
def test_same_identity_form_and_complete_ranged_occurrence(tmp_path):
 value=base();_,asset=gate(value,tmp_path);assert asset['occurrences'][0]['display_form']=='가서 먹었다';assert value==base()
def test_direct_retained_stage_remains_valid(tmp_path):
 value=base();value['grammar_links'][0]={k:v for k,v in value['grammar_links'][0].items() if not k.startswith('display_')};gate(value,tmp_path)
@pytest.mark.parametrize('change',[{'display_end_segment_index':3},{'display_form':'먹었다'},{'display_meaning_en':''},{'display_end_segment_index':-1}])
def test_invalid_complete_range_rejected(tmp_path,change):
 value=base();value['grammar_links'][0].update(change)
 with pytest.raises(ValueError):gate(value,tmp_path)
@pytest.mark.parametrize('field',['display_form','display_meaning_en','display_end_segment_index'])
def test_partial_display_rejected(tmp_path,field):
 value=base();del value['grammar_links'][0][field]
 with pytest.raises(ValueError):gate(value,tmp_path)
def test_duplicate_same_anchor_lesson_still_rejected(tmp_path):
 value=base();value['grammar_links'].append({'segment_index':0,'entry_id':'g','context_en':'duplicate'})
 with pytest.raises(ValueError,match='duplicate anchor/entry: True'):gate(value,tmp_path)
def test_unlinked_stage_identity_still_rejected(tmp_path):
 value=base();value['grammar_links']=[]
 with pytest.raises(ValueError,match='Invalid Korean complete-form transformation'):gate(value,tmp_path)

def test_same_tap_complete_display_is_valid(tmp_path):
 value=base();value['grammar_links'][0].update(display_form='가서',display_end_segment_index=0);gate(value,tmp_path)
def test_nonstaged_complete_range_is_valid(tmp_path):
 value=base();value['segments'][0].pop('form_steps');value['form_audit']['inflected_segment_indices']=[];gate(value,tmp_path)
def test_nonstaged_form_analyzed_direct_link_needs_complete_display(tmp_path):
 value=base();value['segments'][0]['form_steps'][0]['grammar_entry_ids']=['other'];value['grammar_links'][0]={k:v for k,v in value['grammar_links'][0].items() if not k.startswith('display_')}
 with pytest.raises(ValueError,match='lacks complete form'):gate(value,tmp_path)
def test_legacy_and_current_representation_contracts_are_distinct():
 from pipeline import annotation_repairs as r
 for rep in ('korean-flat','korean-v4'):
  old=r._construction_occurrence_contract(rep,occurrence_policy_version=1)
  new=r._construction_occurrence_contract(rep,occurrence_policy_version=2)
  assert 'must be direct, never displayed' in old['staged_identity_occurrence']['rule']
  assert 'independent' in new['staged_identity_occurrence']['rule']
 for rep in ('chinese-annotation','japanese-annotation'):
  assert r._construction_occurrence_contract(rep,occurrence_policy_version=1)==r._construction_occurrence_contract(rep,occurrence_policy_version=2)
  assert r._representation_structure_contract(rep) is None
 assert r.REPAIR_EXPLANATION_GUIDANCE_VERSION==4
 assert r.REPAIR_DEPENDENCY_GUIDANCE_VERSION==3
