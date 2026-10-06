import asyncio,copy,json
from argparse import Namespace
from types import SimpleNamespace
from pathlib import Path
import pytest
from pipeline.annotation_run_lessons import RUN_LESSON_FIELD, RUN_LESSON_GUIDANCE, RunLessonError
from pipeline.annotation_run_lesson_callers import bind_lifecycle_run_lessons,current_run_lesson_eligibility
from pipeline.annotation_reference_carry_callers import register_chunk_positions

@pytest.fixture(params=[('zh','chinese-annotation'),('ja','japanese-annotation')])
def fixture(request,tmp_path,monkeypatch):
    import pipeline.annotation_run_lessons as helper
    import pipeline.annotation_run_lesson_callers as callers
    language,representation=request.param
    harness=SimpleNamespace(run_dir=tmp_path)
    register_chunk_positions(harness,['猫'],parent_text='猫')
    envelope={'version':1,'language':language,'representation':representation,
              'position':harness._annotation_source_positions[0],'lessons':[{'source_run_relpath':'fixture-source','expected_context':{},
                          'import_receipt':{'proof':'exact-fixture'}}]}
    harness._annotation_run_lessons={0:envelope}
    candidate={'segments':[{'text':'猫','type':'word','pinyin':'māo','meaning_en':'cat'}] if language=='zh'
        else [{'surface':'猫','meaning_en':'cat','form_steps':[]}], 'grammar_overlays':[]}
    calls=[]
    def validate(run,packet,**kwargs):
        calls.append(kwargs)
        if packet!=envelope or kwargs['context'].get('annotation_source_position')!=envelope['position']:
            raise RunLessonError('Fixture envelope or position changed')
        return {'packet':copy.deepcopy(packet),'references':{},'lessons':[{'id':'fixture'}]}
    monkeypatch.setattr(helper,'validate_run_lessons',validate)
    monkeypatch.setattr(callers,'validate_run_lessons',validate)
    monkeypatch.setattr(callers,'load_run_lessons',lambda *a,**k:None)
    return harness,candidate,language,representation,envelope,calls


def test_absent_envelope_preserves_historical_context_exactly(tmp_path):
    harness=SimpleNamespace(run_dir=tmp_path)
    context={'original':'exact-context'}
    assert bind_lifecycle_run_lessons(harness,0,'猫',{},language='ja',
        representation='japanese-annotation',context=context)==(context,{})


def test_explicit_envelope_is_bound_and_new_evidence_invalidates_old_cache(fixture):
    harness,candidate,language,representation,envelope,calls=fixture
    context,_=bind_lifecycle_run_lessons(harness,0,'猫',candidate,language=language,representation=representation)
    assert context[RUN_LESSON_FIELD]==envelope
    assert context['reviewed_run_grammar']==[{'id':'fixture'}]
    assert context['annotation_run_lesson_validation']['candidate']==candidate
    kwargs=dict(language=language,representation=representation)
    assert not current_run_lesson_eligibility(harness,0,'猫',candidate,old_context={},**kwargs)
    assert current_run_lesson_eligibility(harness,0,'猫',candidate,old_context=context,**kwargs)
    bad=copy.deepcopy(context);bad[RUN_LESSON_FIELD]['lessons'][0]['import_receipt']['proof']='tampered'
    with pytest.raises(RunLessonError):current_run_lesson_eligibility(harness,0,'猫',candidate,old_context=bad,**kwargs)
    harness._annotation_source_positions[0]={**envelope['position'],'source_start':1}
    with pytest.raises(RunLessonError):bind_lifecycle_run_lessons(harness,0,'猫',candidate,**kwargs)

@pytest.mark.asyncio
async def test_actual_cj_review_producers_receive_explicit_lesson_and_semantic_gate(fixture):
    harness,candidate,language,representation,envelope,_=fixture
    calls=[]
    class Runner:
        async def call(self,job,prompt,schema,effort,**kwargs):
            calls.append((prompt,kwargs['workspace_context']))
            return {'verdict':'pass','issues':[]}
    if language=='zh':
        from tests.test_agent_harness import annotation_harness
        target=annotation_harness(Runner())
    else:
        from pipeline.japanese_agent_harness import JapaneseChapterHarness
        target=object.__new__(JapaneseChapterHarness);target.args=Namespace(annotation_review_effort='low',refresh=False);target.runner=Runner()
    target.run_dir=harness.run_dir;target._annotation_source_positions=harness._annotation_source_positions
    target._annotation_run_lessons=harness._annotation_run_lessons
    assert (await target.review_annotation(0,'猫',candidate,'derived'))['verdict']=='pass'
    assert len(calls)==(1 if language=='zh' else 2)
    for prompt,context in calls:
        assert RUN_LESSON_GUIDANCE in prompt
        assert context[RUN_LESSON_FIELD]==envelope
        assert context['reviewed_run_grammar']==[{'id':'fixture'}]
        assert context['annotation_run_lesson_validation']['source_text']=='猫'


def test_workspace_submission_and_publication_replay_validate_lesson_inputs(fixture):
    harness,candidate,language,representation,envelope,calls=fixture
    from pipeline.worker_workspace import build,submit,CandidateSubmissionError
    from pipeline.annotation_publication import normal_review_receipt,verify_normal_review_receipt,bind_review_job
    context,_=bind_lifecycle_run_lessons(harness,0,'猫',candidate,language=language,representation=representation)
    context['annotation_issue_targets_validation']={'candidate':candidate,'source_text':'猫',
        'representation':representation,'require_typed':True}
    job='review';root=harness.run_dir/'agents'/job
    schema={'type':'object','properties':{'verdict':{'const':'pass'},'issues':{'const':[]}},'required':['verdict','issues']}
    _,workspace_digest=build(root/'workspace','fixture independent review',schema,context=context)
    result={'verdict':'pass','issues':[]}
    (root/'workspace/candidate.json').write_text(json.dumps(result))
    _,artifact=submit(root/'workspace',{'candidate_path':'candidate.json'})
    (root/'result.json').write_text(json.dumps(result))
    (root/'meta.json').write_text(json.dumps({'return_code':0,'tool_profile':'workspace','workspace_digest':workspace_digest,'model':'gpt-6-luna','effort':'low','fingerprint':'input',**artifact}))
    bind_review_job(harness.run_dir,job,candidate=candidate,source_text='猫')
    receipt=normal_review_receipt(harness.run_dir,[job],result,candidate,'猫')
    assert verify_normal_review_receipt(harness.run_dir,receipt,review=result,candidate=candidate,source_text='猫',expected_children=1)==[result]
    assert len(calls)>=3  # Binder, actual submission gate, immutable receipt replay.
    # Bad explicit proof is rejected before the schema-valid review can submit.
    invalid=copy.deepcopy(context);invalid['annotation_run_lesson_validation']['envelope']['lessons'][0]['import_receipt']['proof']='forged'
    build(root/'workspace','fixture independent review',schema,context=invalid)
    (root/'workspace/candidate.json').write_text(json.dumps(result))
    with pytest.raises(CandidateSubmissionError,match='Fixture envelope'):
        submit(root/'workspace',{'candidate_path':'candidate.json'})


def test_unproved_run_grammar_content_does_not_become_reviewed_context(tmp_path):
    harness=SimpleNamespace(run_dir=tmp_path)
    with pytest.raises(RunLessonError,match='requires'):
        bind_lifecycle_run_lessons(harness,0,'猫',{},language='ja',
            representation='japanese-annotation',context={'reviewed_run_grammar':[{'id':'proposal'}]})

@pytest.fixture(params=[('zh','chinese-annotation'),('ja','japanese-annotation')])
def authenticated_overlay(request,tmp_path,monkeypatch):
    import pipeline.annotation_run_lessons as helper
    from pipeline.annotation_research import import_reviewed_lesson
    from tests.test_annotation_research import _write_job
    from jsonschema import validate
    language,representation=request.param
    source=tmp_path/'source';source.mkdir();destination=tmp_path/'destination';destination.mkdir()
    monkeypatch.setattr(helper,'_repo_relative_run_dir',lambda _: 'fixture-source')
    monkeypatch.setattr(helper,'_resolve_repo_run_dir',lambda _:source)
    if language=='zh':
        candidate={'segments':[{'text':'猫','type':'word','pinyin':'māo','meaning_en':'cat'} for _ in range(2)],
            'grammar_overlays':[{'start':0,'end':2,'text':'猫猫','grammar_candidate_key':'old-pattern',
                                 'pattern':'fixture pattern','meaning_en':'fixture meaning'}]}
    else:
        segment={'surface':'猫','type':'word','lemma':'猫','surface_kana':'ねこ','lemma_kana':'ねこ',
            'part_of_speech':'noun','conjugation_form':'','meaning_en':'cat','grammar_candidate_key':'',
            'dictionary_key':'','dictionary_definition_en':'','form_steps':[],
            'story_role':'none','story_importance_en':''}
        components=[{'start':i,'end':i+1,'surface':'猫','lemma':'猫','lemma_kana':'ねこ','function_en':'fixture',
            'lookup_kind':'none','dictionary_key':'','dictionary_definition_en':''} for i in range(2)]
        candidate={'segments':[copy.deepcopy(segment),copy.deepcopy(segment)],'grammar_overlays':[
            {'start':0,'end':2,'surface':'猫猫','grammar_candidate_key':'old-pattern','pattern':'fixture pattern',
             'meaning_en':'fixture meaning','head_lemma':'猫','head_lemma_kana':'ねこ','form_label':'fixture',
             'explanation_en':'Fixture explanation','components':components}]}
    schema=Path('pipeline/schemas')/('annotation.schema.json' if language=='zh' else 'japanese-annotation.schema.json')
    validate(candidate,json.loads(schema.read_text()))
    expected={'reviewed_run_lesson_scope_version':2,'language':language,
        'chapter':{'text':'pre 猫猫 tail',**copy.deepcopy(candidate)},
        'target_occurrence':{'target_path':'/grammar_overlays/0/grammar_candidate_key','overlay_index':0,
            'start':0,'end':2,'surface':'猫猫','source_start':4,'source_text':'猫猫'}}
    output={'words':[],'grammar':[{'id':'new-pattern','title_en':'Fixture lesson',
        'pattern':'fixture pattern','explanation_en':'Standalone independently reviewed fixture lesson.'}]}
    _write_job(source,'writer','fixture writer','{}',expected,output)
    _write_job(source,'critic','fixture critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
    proof=import_reviewed_lesson(destination,'writer','critic',lesson_id='new-pattern',
        expected_context=expected,issue_ids=['original-issue'],source_run_dir=source)
    harness=SimpleNamespace(run_dir=destination);register_chunk_positions(harness,['pre ','猫猫',' tail'],parent_text='pre 猫猫 tail')
    context={'annotation_source_position':harness._annotation_source_positions[1]}
    envelope=helper.make_run_lesson_envelope(destination,source_run_dir=source,import_receipt=proof['evidence'],
        expected_context=expected,language=language,representation=representation,context=context)
    harness._annotation_run_lessons={1:envelope}
    return harness,candidate,language,representation,envelope,source


def test_schema_valid_cj_overlays_use_authentic_distinct_temporary_lessons(authenticated_overlay):
    harness,candidate,language,representation,envelope,source=authenticated_overlay
    current=copy.deepcopy(candidate);current['grammar_overlays'][0]['grammar_candidate_key']='new-pattern'
    context,_=bind_lifecycle_run_lessons(harness,1,'猫猫',current,language=language,representation=representation)
    assert context['reviewed_run_grammar'][0]['id']=='new-pattern'
    assert context['reviewed_run_grammar'][0]['explanation_en'].startswith('Standalone')
    assert context[RUN_LESSON_FIELD]==envelope
    assert 'approved_grammar' not in context
    assert current_run_lesson_eligibility(harness,1,'猫猫',current,language=language,
        representation=representation,old_context=context)
    unrelated=copy.deepcopy(current);unrelated['grammar_overlays'][0]['grammar_candidate_key']='unrelated'
    with pytest.raises(RunLessonError):bind_lifecycle_run_lessons(harness,1,'猫猫',unrelated,language=language,representation=representation)
    wrong=copy.deepcopy(current);wrong['grammar_overlays'][0]['start']=1
    with pytest.raises(RunLessonError):bind_lifecycle_run_lessons(harness,1,'猫猫',wrong,language=language,representation=representation)
    # Source critic mutation is an authentication error, never an approval.
    path=source/'agents/critic/result.json';path.write_text(json.dumps({'approved':False,'issues':['changed']}))
    with pytest.raises(RunLessonError):bind_lifecycle_run_lessons(harness,1,'猫猫',current,language=language,representation=representation)


def test_authentic_cj_run_lesson_receipt_replays_and_content_transplant_fails(authenticated_overlay):
    from pipeline.worker_workspace import build,submit,CandidateSubmissionError
    from pipeline.annotation_publication import bind_review_job,normal_review_receipt,verify_normal_review_receipt,verify_item_research_positions
    harness,candidate,language,representation,envelope,_=authenticated_overlay
    candidate=copy.deepcopy(candidate);candidate['grammar_overlays'][0]['grammar_candidate_key']='new-pattern'
    context,_=bind_lifecycle_run_lessons(harness,1,'猫猫',candidate,language=language,representation=representation)
    context['annotation_issue_targets_validation']={'candidate':candidate,'source_text':'猫猫',
        'representation':representation,'require_typed':True}
    job='annotations/chunk_0001/review';root=harness.run_dir/'agents'/job
    _,workspace_digest=build(root/'workspace','fixture exact independent review',{},context=context)
    result={'verdict':'pass','issues':[]};(root/'workspace/candidate.json').write_text(json.dumps(result))
    _,artifact=submit(root/'workspace',{'candidate_path':'candidate.json'})
    (root/'result.json').write_text(json.dumps(result));(root/'meta.json').write_text(json.dumps({
        'return_code':0,'tool_profile':'workspace','workspace_digest':workspace_digest,
        'model':'gpt-6-luna','effort':'low','fingerprint':'fixture',**artifact}))
    bind_review_job(harness.run_dir,job,candidate=candidate,source_text='猫猫')
    receipt=normal_review_receipt(harness.run_dir,[job],result,candidate,'猫猫')
    assert verify_normal_review_receipt(harness.run_dir,receipt,review=result,candidate=candidate,source_text='猫猫',expected_children=1)==[result]
    item={'attempts':[{'normal_review_receipt':receipt}]}
    verify_item_research_positions(harness.run_dir,item,harness._annotation_source_positions[1])
    with pytest.raises(ValueError):verify_item_research_positions(harness.run_dir,item,{**harness._annotation_source_positions[1],'source_start':0})
    bad=copy.deepcopy(context);bad['reviewed_run_grammar'][0]['explanation_en']='Forged lesson'
    build(root/'workspace','fixture exact independent review',{},context=bad)
    (root/'workspace/candidate.json').write_text(json.dumps(result))
    with pytest.raises(CandidateSubmissionError,match='snapshot changed|content differs|grammar differs'):
        submit(root/'workspace',{'candidate_path':'candidate.json'})


def test_authentic_overlay_registration_loads_only_exact_position(authenticated_overlay,monkeypatch):
    import pipeline.annotation_research as research
    from pipeline.annotation_run_lessons import load_run_lessons
    harness,candidate,language,representation,envelope,source=authenticated_overlay
    monkeypatch.setattr(research,'_repo_relative_run_dir',lambda _: 'fixture-source')
    monkeypatch.setattr(research,'_resolve_repo_run_dir',lambda _:source)
    expected=envelope['lessons'][0]['expected_context']
    research.register_reviewed_lesson_source(harness.run_dir,source_run_dir=source,
        writer_job='writer',reviewer_job='critic',lesson_id='new-pattern',expected_context=expected)
    harness._annotation_run_lessons={}
    context,_=bind_lifecycle_run_lessons(harness,1,'猫猫',candidate,language=language,representation=representation)
    assert context['reviewed_run_grammar'][0]['id']=='new-pattern'
    assert current_run_lesson_eligibility(harness,1,'猫猫',candidate,language=language,
        representation=representation,old_context=context)
    wrong={'annotation_source_position':{**harness._annotation_source_positions[1],'source_start':0}}
    assert load_run_lessons(harness.run_dir,candidate=candidate,source_text='猫猫',
        language=language,representation=representation,context=wrong) is None

@pytest.mark.asyncio
async def test_actual_cj_normal_review_receives_authenticated_substantive_lesson(authenticated_overlay):
    harness,candidate,language,representation,_,_=authenticated_overlay
    candidate=copy.deepcopy(candidate);candidate['grammar_overlays'][0]['grammar_candidate_key']='new-pattern'
    calls=[]
    class Runner:
        async def call(self,job,prompt,schema,effort,**kwargs):
            calls.append(kwargs['workspace_context']);return {'verdict':'pass','issues':[]}
    if language=='zh':
        from tests.test_agent_harness import annotation_harness
        target=annotation_harness(Runner())
    else:
        from pipeline.japanese_agent_harness import JapaneseChapterHarness
        target=object.__new__(JapaneseChapterHarness);target.args=Namespace(annotation_review_effort='low',refresh=False);target.runner=Runner()
    target.run_dir=harness.run_dir;target._annotation_source_positions=harness._annotation_source_positions
    target._annotation_run_lessons=harness._annotation_run_lessons
    assert (await target.review_annotation(1,'猫猫',candidate,'derived'))['verdict']=='pass'
    assert len(calls)==(1 if language=='zh' else 2)
    for context in calls:
        lesson=context['reviewed_run_grammar'][0]
        assert lesson['id']=='new-pattern' and lesson['explanation_en'].startswith('Standalone')
        assert context['annotation_run_lesson_validation']['lessons']==context['reviewed_run_grammar']
        assert 'approved_grammar' not in context


def test_registered_source_does_not_hide_behind_old_explicit_envelope(authenticated_overlay,monkeypatch):
    from tests.test_annotation_research import _write_job
    from pipeline.annotation_research import import_reviewed_lesson
    import pipeline.annotation_research as research
    from pipeline.annotation_run_lesson_callers import current_run_lesson_context_eligibility,resolve_run_lesson_context
    harness,candidate,language,representation,envelope,source=authenticated_overlay
    monkeypatch.setattr(research,'_repo_relative_run_dir',lambda _: 'fixture-source')
    monkeypatch.setattr(research,'_resolve_repo_run_dir',lambda _:source)
    expected=envelope['lessons'][0]['expected_context']
    output=json.loads((source/'agents/writer/result.json').read_text())
    _write_job(source,'new-writer','new independent source writer','{}',expected,output)
    _write_job(source,'new-critic','new independent critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
    research.register_reviewed_lesson_source(harness.run_dir,source_run_dir=source,
        writer_job='new-writer',reviewer_job='new-critic',lesson_id='new-pattern',expected_context=expected)
    old={RUN_LESSON_FIELD:envelope,'annotation_source_position':harness._annotation_source_positions[1]}
    kwargs=dict(candidate=candidate,source_text='猫猫',language=language,representation=representation)
    assert not current_run_lesson_context_eligibility(harness.run_dir,context=old,old_context=old,**kwargs)
    # Conflicting duplicate identity provenance cannot become an implicit vote.
    with pytest.raises(RunLessonError,match='Duplicate'):
        resolve_run_lesson_context(harness.run_dir,context=old,**kwargs)
    new_proof=import_reviewed_lesson(harness.run_dir,'new-writer','new-critic',lesson_id='new-pattern',
        expected_context=expected,issue_ids=['new-issue'],source_run_dir=source)
    import pipeline.annotation_run_lessons as helper
    new_envelope=helper.make_run_lesson_envelope(harness.run_dir,source_run_dir=source,
        import_receipt=new_proof['evidence'],expected_context=expected,language=language,
        representation=representation,context=old)
    terminal={RUN_LESSON_FIELD:new_envelope,'annotation_source_position':harness._annotation_source_positions[1]}
    assert current_run_lesson_context_eligibility(harness.run_dir,context=old,old_context=old,
        terminal_context=terminal,**kwargs)


def test_same_authenticated_pair_registered_for_new_issue_deduplicates(authenticated_overlay,monkeypatch):
    import pipeline.annotation_research as research
    from pipeline.annotation_run_lesson_callers import current_run_lesson_context_eligibility,resolve_run_lesson_context
    harness,candidate,language,representation,envelope,source=authenticated_overlay
    monkeypatch.setattr(research,'_repo_relative_run_dir',lambda _: 'fixture-source')
    monkeypatch.setattr(research,'_resolve_repo_run_dir',lambda _:source)
    research.register_reviewed_lesson_source(harness.run_dir,source_run_dir=source,
        writer_job='writer',reviewer_job='critic',lesson_id='new-pattern',
        expected_context=envelope['lessons'][0]['expected_context'])
    old={RUN_LESSON_FIELD:envelope,'annotation_source_position':harness._annotation_source_positions[1]}
    kwargs=dict(candidate=candidate,source_text='猫猫',language=language,representation=representation)
    assert current_run_lesson_context_eligibility(harness.run_dir,context=old,old_context=old,**kwargs)
    assert resolve_run_lesson_context(harness.run_dir,context=old,**kwargs)['packet']==envelope


def test_cache_guard_rejects_orphan_snapshots_before_manifest_selection(monkeypatch, tmp_path):
    import pipeline.annotation_run_lesson_callers as callers
    monkeypatch.setattr(callers, 'load_run_lessons', lambda *a, **k: None)
    kwargs=dict(candidate={}, source_text='', language='ja', representation='japanese-annotation')
    assert callers.current_run_lesson_context_eligibility(tmp_path, context={}, old_context={}, **kwargs)
    for slot in ('context', 'old_context', 'terminal_context'):
        for orphan in ({'reviewed_run_grammar': []}, {'chunk_review_context': {'reviewed_run_grammar': []}}):
            arguments=dict(context={}, old_context={});arguments[slot]=orphan
            with pytest.raises(RunLessonError, match='authenticated lesson envelope'):
                callers.current_run_lesson_context_eligibility(tmp_path, **arguments, **kwargs)
    monkeypatch.setattr(callers, 'load_run_lessons', lambda *a, **k: pytest.fail('Must reject orphan before loading manifest'))
    with pytest.raises(RunLessonError, match='authenticated lesson envelope'):
        callers.resolve_run_lesson_context(tmp_path, context={'reviewed_run_grammar': []}, **kwargs)


def test_cache_guard_rejects_conflicting_dual_envelopes_before_selection(monkeypatch,tmp_path):
    import pipeline.annotation_run_lesson_callers as callers
    monkeypatch.setattr(callers,'load_run_lessons',lambda *a,**k:pytest.fail('Conflicting provenance must reject before load'))
    for root,nested in (({'lessons':[]},{'tampered':True}),({'tampered':True},{'lessons':[]})):
        context={RUN_LESSON_FIELD:root,'chunk_review_context':{RUN_LESSON_FIELD:nested,'reviewed_run_grammar':[]}}
        with pytest.raises(RunLessonError,match='Conflicting'):
            callers.current_run_lesson_context_eligibility(tmp_path,candidate={},source_text='',language='ja',representation='japanese-annotation',context=context,old_context={})
