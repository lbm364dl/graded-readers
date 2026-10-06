"""Exact record selection; no synthetic record is claimed independently authentic."""
import copy,sys
from pathlib import Path
import pytest
from pipeline import lexical_request_reconciliation as c

def data():
 entry={'id':'exact/noun','headword':'base','pos':'noun','meaning_en':'fixture','primary_url':'https://primary.invalid/exact'};record={'proposal':{'entries':[entry]},'review':{'approved':True,'issues':[]},'source_context':{'source_start':99,'text':'another occurrence'},'primary_evidence':{'fixture_only':True}};catalog=[{'id':entry['id'],'headword':entry['headword'],'kind':'word','reading':'','content':{'id':entry['id'],'headword':entry['headword'],'pos':entry['pos'],'meaning':entry['meaning_en'],'grade':None,'primary_url':entry['primary_url']}}];candidate={'segments':[{'text':'XX','lexical_id':entry['id']}]};return candidate,catalog,{'reviews':[record]}

def test_matching_identity_retains_research_critic_provenance_without_occurrence_approval():
 candidate,catalog,registry=data();refs=c.reviewed_primary_references(candidate,catalog,registry);ref=refs['lexical-primary:0'];assert ref['content']==registry['reviews'][0] and ref['applicable_catalog_ids']==['exact/noun'] and ref['occurrence_approval'] is False;assert ref['content']['source_context']['source_start']==99

def test_foreign_identity_not_selected_even_when_headword_matches():
 candidate,catalog,registry=data();candidate['segments'][0]['lexical_id']='foreign/noun';assert c.reviewed_primary_references(candidate,catalog,registry)=={}

@pytest.mark.parametrize('key,value',[('reading','different'),('kind','grammar')])
def test_foreign_reading_or_kind_rejected(key,value):
 candidate,catalog,registry=data();catalog[0][key]=value
 with pytest.raises(ValueError,match='identity/reading/kind'):c.reviewed_primary_references(candidate,catalog,registry)

def test_same_record_cannot_claim_approval_of_new_source_position():
 from tests.test_lexical_request_reconciliation import fixture
 inputs,result,_=fixture();inputs['references']['primary']={'kind':'reviewed_lexical_primary_record','content':{'fixture_only':True},'content_digest':c.digest({'fixture_only':True}),'applicable_catalog_ids':['word-a'],'occurrence_approval':True};result['decisions'][0]['citations'].append('primary');result['inputs_digest']=c.digest(inputs)
 with pytest.raises(ValueError,match='occurrence authority'):c.check_result(result,inputs)
 inputs['references']['primary'].update(occurrence_approval=False,applicable_catalog_ids=['foreign-id']);result['inputs_digest']=c.digest(inputs)
 with pytest.raises(ValueError,match='Foreign lexical'):c.check_result(result,inputs)
