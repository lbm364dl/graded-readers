"""Setup-only dictionary receipts; no fixture establishes linguistic correctness."""
import copy,json
import pytest
from tests.test_annotation_run_lessons import fixture,overlay_fixture
from pipeline import annotation_run_lessons as module

PATH='/segments/0/form_steps/0/grammar_entry_ids/0'
def fresh(fixture):
 _,dst,base,_,old=fixture
 candidate=copy.deepcopy(base);candidate['segments'][0]['text']='깊다며';candidate['segments'][0]['form_steps'][0]['form']='깊다며'
 context={'chapter_text':'new parent 깊다며 tail','source_start':11}
 from pipeline.annotation_adjudication import digest
 context['annotation_source_position']={'chunk_index':13,'source_start':11,'source_text_digest':digest('깊다며'),'parent_text_digest':digest(context['chapter_text'])}
 envelope=module.make_reusable_run_lesson_envelope(dst,source_descriptor=old['lessons'][0],candidate=candidate,source_text='깊다며',target_path=PATH,language='ko',representation='korean-flat',context=context)
 return dst,candidate,context,envelope

def bind(values,**kwargs):
 dst,candidate,context,envelope=values
 return module.validate_run_lessons(dst,kwargs.pop('envelope',envelope),candidate=kwargs.pop('candidate',candidate),source_text=kwargs.pop('source_text','깊다며'),language=kwargs.pop('language','ko'),representation=kwargs.pop('representation','korean-flat'),context=kwargs.pop('context',context),**kwargs)

def test_original_provenance_retained_new_position_independently_scoped(fixture):
 values=fresh(fixture);row=values[3]['lessons'][0]
 assert {key:row[key] for key in fixture[4]['lessons'][0]}==fixture[4]['lessons'][0]
 assert row['expected_context']['target_occurrence']['surface']=='아이'
 assert bind(values)['lessons'][0]['id']=='new-pattern'
 candidate=copy.deepcopy(values[1]);candidate['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new-pattern']
 candidate['segments'][0]['meaning_en']='changed scoped meaning'
 assert bind(values,candidate=candidate)['lessons']
 assert module.run_lesson_guidance(values[3])==module.RUN_KNOWLEDGE_GUIDANCE
 assert module.run_lesson_guidance(fixture[4])==module.RUN_LESSON_GUIDANCE

@pytest.mark.parametrize('change',['position','parent','form','identity','lexical','language','representation','target','source'])
def test_current_application_contrasts_reject(fixture,change):
 values=fresh(fixture);candidate=copy.deepcopy(values[1]);context=copy.deepcopy(values[2]);envelope=copy.deepcopy(values[3]);kwargs={}
 if change=='position':context['source_start']=0;context['annotation_source_position']['source_start']=0
 if change=='parent':context['chapter_text']='otherparent 깊다며 tail'
 if change=='form':candidate['segments'][0]['form_steps'][0]['form']='different'
 if change=='identity':candidate['segments'][0]['form_steps'][0]['grammar_entry_ids']=['foreign']
 if change=='lexical':candidate['segments'][0]['lexical_id']='homonym'
 if change=='language':kwargs['language']='ja'
 if change=='representation':kwargs['representation']='japanese-annotation'
 if change=='target':envelope['lessons'][0]['application']['target_path']='/segments/0/meaning_en'
 if change=='source':kwargs['source_text']='other'
 with pytest.raises(module.RunLessonError):bind(values,candidate=candidate,context=context,envelope=envelope,**kwargs)

@pytest.mark.parametrize('job',['writer','critic'])
def test_original_source_tamper_rejected_at_new_position(fixture,job):
 values=fresh(fixture);path=fixture[0]/'agents'/job/'result.json';data=json.loads(path.read_text());data['tampered']=True;path.write_text(json.dumps(data))
 with pytest.raises(module.RunLessonError):bind(values)

def test_v1_crossposition_still_rejected_and_absent_still_empty(fixture):
 values=fresh(fixture)
 with pytest.raises(module.RunLessonError):bind(values,envelope=fixture[4])
 assert bind(values,envelope=None)=={'packet':{},'references':{},'lessons':[]}
 original=module.validate_run_lessons(fixture[1],fixture[4],candidate=fixture[2],source_text='아이',language='ko',representation='korean-flat',context=fixture[3])
 assert original['packet']==fixture[4]

def test_new_issue_targets_only_current_application_identity(fixture):
 values=fresh(fixture)
 review={'approved':False,'issues':[{'explanation':'Check current formation','candidate_paths':[PATH],'supporting_paths':[]}],'prose_revision_reason_en':''}
 assert len(bind(values,current_review=review)['references'])==1
 review['issues'][0]['candidate_paths']=['/segments/0/meaning_en']
 assert bind(values,current_review=review)['references']=={}

def test_actual_cj_overlay_application_has_new_position_and_original_receipts(overlay_fixture):
 _,dst,candidate,text,lang,rep,_,old=overlay_fixture
 context={'chapter_text':'new '+text+' tail','source_start':4}
 from pipeline.annotation_adjudication import digest
 context['annotation_source_position']={'chunk_index':3,'source_start':4,'source_text_digest':digest(text),'parent_text_digest':digest(context['chapter_text'])}
 packet=module.make_reusable_run_lesson_envelope(dst,source_descriptor=old['lessons'][0],candidate=candidate,source_text=text,target_path='/grammar_overlays/0/grammar_candidate_key',language=lang,representation=rep,context=context)
 assert packet['version']==2
 bound=module.validate_run_lessons(dst,packet,candidate=candidate,source_text=text,language=lang,representation=rep,context=context)
 assert bound['lessons'][0]['id']=='new-pattern'
 changed=copy.deepcopy(candidate);changed['grammar_overlays'][0]['start']=1
 with pytest.raises(module.RunLessonError):module.validate_run_lessons(dst,packet,candidate=changed,source_text=text,language=lang,representation=rep,context=context)


def test_registered_application_actual_shared_caller_load_and_cache_scope(fixture):
 from pipeline.annotation_run_lesson_callers import resolve_run_lesson_context,current_run_lesson_context_eligibility
 values=fresh(fixture);dst,candidate,context,packet=values
 module.register_run_lesson_application(dst,packet,candidate=candidate,source_text='깊다며',language='ko',representation='korean-flat',context=context)
 bound=resolve_run_lesson_context(dst,candidate=candidate,source_text='깊다며',language='ko',representation='korean-flat',context=context)
 assert bound['packet']==packet
 assert not current_run_lesson_context_eligibility(dst,candidate=candidate,source_text='깊다며',language='ko',representation='korean-flat',context=context,old_context=context)
 consumed={**context,module.RUN_LESSON_FIELD:packet,'reviewed_run_grammar':bound['lessons']}
 assert current_run_lesson_context_eligibility(dst,candidate=candidate,source_text='깊다며',language='ko',representation='korean-flat',context=context,old_context=consumed)
 path=next((dst/'annotation-run-applications').glob('*.json'));data=json.loads(path.read_text());data['digest']='bad';path.write_text(json.dumps(data))
 with pytest.raises(module.RunLessonError):resolve_run_lesson_context(dst,candidate=candidate,source_text='깊다며',language='ko',representation='korean-flat',context=context)

def test_reusable_dictionary_handoff_authenticates_application_before_dedup(fixture):
 from pipeline.annotation_dictionary_handoff import make_handoff,replay_handoff,DictionaryHandoffError
 dst,candidate,context,packet=fresh(fixture)
 handoff=make_handoff(dst,[packet,copy.deepcopy(packet)],language='ko',chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})
 assert handoff['version']==2 and len(handoff['sources'])==1
 assert 'application' not in handoff['sources'][0]
 assert replay_handoff(dst,handoff,language='ko',chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})[0]['id']=='new-pattern'
 bad=copy.deepcopy(packet);bad['position']['source_start']=0
 with pytest.raises((module.RunLessonError,DictionaryHandoffError)):make_handoff(dst,[bad],language='ko',chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})
