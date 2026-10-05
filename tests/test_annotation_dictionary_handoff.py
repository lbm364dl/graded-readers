import asyncio,copy,json
import pytest
from tests.test_annotation_run_lessons import fixture,overlay_fixture
from pipeline import annotation_dictionary_handoff as handoff
from pipeline import korean_dictionary_jobs as jobs
from pipeline.korean_agent_harness import read,save


def build(fixture):
    source,root,candidate,context,envelope=fixture
    return handoff.make_handoff(root,[envelope],language='ko',chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})


def test_authentic_exact_entry_and_unrelated_absence(fixture):
    descriptor=build(fixture)
    result=handoff.replay_handoff(fixture[1],descriptor,language='ko',chapter_text=fixture[3]['chapter_text'],required_ids={'new-pattern'},published={})
    original=json.loads((fixture[0]/'agents/writer/result.json').read_text())['grammar']
    assert result==original
    assert handoff.make_handoff(fixture[1],[fixture[4]],language='ko',chapter_text=fixture[3]['chapter_text'],required_ids={'unrelated'},published={}) is None
    assert handoff.replay_handoff(fixture[1],None,language='ko',chapter_text='',required_ids=set(),published={})==[]


@pytest.mark.parametrize('change',['writer','critic','chapter','language','requested','published','duplicate'])
def test_stale_conflicting_or_equivocal_source_rejected(fixture,change):
    descriptor=build(fixture);kwargs=dict(language='ko',chapter_text=fixture[3]['chapter_text'],required_ids={'new-pattern'},published={})
    if change in ('writer','critic'):
        path=fixture[0]/'agents'/change/'result.json';value=json.loads(path.read_text());value['tampered']=True;path.write_text(json.dumps(value))
    elif change=='chapter':kwargs['chapter_text']='other chapter'
    elif change=='language':kwargs['language']='ja'
    elif change=='requested':kwargs['required_ids']={'unrelated'}
    elif change=='published':kwargs['published']={'new-pattern':{}}
    else:
        descriptor['sources'].append(copy.deepcopy(descriptor['sources'][0]));descriptor['digest']=handoff.digest({k:v for k,v in descriptor.items() if k!='digest'})
    with pytest.raises(handoff.DictionaryHandoffError):handoff.replay_handoff(fixture[1],descriptor,**kwargs)


def test_shared_helper_accepts_authentic_cj_provenance_without_relabeling(overlay_fixture):
    source,root,candidate,text,language,representation,context,envelope=overlay_fixture
    descriptor=handoff.make_handoff(root,[envelope],language=language,chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})
    assert handoff.replay_handoff(root,descriptor,language=language,chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})[0]['id']=='new-pattern'
    with pytest.raises(handoff.DictionaryHandoffError):handoff.replay_handoff(root,descriptor,language='ko',chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})


def test_dictionary_producer_skips_imported_entry_and_preserves_whole_review(fixture):
    from types import SimpleNamespace
    root=fixture[1];descriptor=build(fixture);entry=handoff.replay_handoff(root,descriptor,language='ko',chapter_text=fixture[3]['chapter_text'],required_ids={'new-pattern'},published={})[0]
    calls=[]
    class Runner:
        async def call(self,job,prompt,*args,**kwargs):
            inputs=json.loads(prompt.rsplit('\nINPUT:\n',1)[1]);calls.append(inputs)
            assert 'new-pattern' not in inputs['grammar_requests']
            value={'words':[],'grammar':[{'id':i,'title_en':'Other','pattern':'other','explanation_en':'Other reviewed draft.'} for i in inputs['grammar_requests']]}
            save(root/'agents'/job/'result.json',value);save(root/'agents'/job/'meta.json',{'return_code':0});return value
    harness=SimpleNamespace(run_dir=root,runner=Runner(),policy='Policy',grammar={})
    context={'word_requests':{},'grammar_requests':['new-pattern','other'],'chapter':{'text':fixture[3]['chapter_text'],'segments':[]},handoff.FIELD:descriptor}
    produce=jobs.producer(harness,'Write entries.',context)
    value=asyncio.run(produce('dictionary-0',[]));assert value['grammar']==[{'id':'other','title_en':'Other','pattern':'other','explanation_en':'Other reviewed draft.'},entry]
    meta=read(root/'agents/dictionary-0/meta.json');assert jobs.replay(root,meta)==value
    handoff.verify_review_handoff(meta,context)
    with pytest.raises(handoff.DictionaryHandoffError):handoff.verify_review_handoff(meta,{**context,handoff.FIELD:{}})
    # Imported entry objection is an explicit source-revision route, never another drafting call.
    async def select(job,prompt,*args,**kwargs):return {'entries':[{'kind':'grammar','entry_id':'new-pattern'}]}
    harness.runner.call=select
    with pytest.raises(handoff.DictionaryHandoffError,match='explicit independently reviewed source revision'):
        asyncio.run(produce('dictionary-1',['Defect in retained standalone explanation.']))
    assert len(calls)==1


def test_import_only_assembly_needs_no_dictionary_writer(fixture):
    from types import SimpleNamespace
    descriptor=build(fixture)
    class Runner:
        async def call(self,*a,**k):pytest.fail('Retained entry must not be regenerated')
    context={'word_requests':{},'grammar_requests':['new-pattern'],'chapter':{'text':fixture[3]['chapter_text'],'segments':[]},handoff.FIELD:descriptor}
    harness=SimpleNamespace(run_dir=fixture[1],runner=Runner(),policy='Policy',grammar={})
    value=asyncio.run(jobs.producer(harness,'Write entries.',context)('dictionary-0',[]))
    meta=read(fixture[1]/'agents/dictionary-0/meta.json');assert meta['batches']==[]
    assert jobs.replay(fixture[1],meta)==value


def test_coordination_retains_approved_run_identity_but_other_drafts_remain_coordinatable():
    entry={'id':'new-pattern'}
    handoff.validate_retained_bindings({'bindings':[{'draft_id':'new-pattern','entry_id':'new-pattern'},{'draft_id':'other','entry_id':'published'}]},[entry])
    with pytest.raises(handoff.DictionaryHandoffError):handoff.validate_retained_bindings({'bindings':[{'draft_id':'new-pattern','entry_id':'other'}]},[entry])


def test_actual_dictionary_stage_still_runs_independent_whole_delta_review(fixture):
    from pipeline.korean_agent_harness import KoreanHarness,validate_delta
    source,root,_,context,_=fixture;descriptor=build(fixture);calls=[]
    class Runner:
        async def call(self,job,prompt,*args,**kwargs):
            calls.append(job)
            assert job=='dictionary-review-0'
            inputs=json.loads(prompt.rsplit('\nINPUT:\n',1)[1])
            assert inputs['context'][handoff.FIELD]==descriptor
            assert inputs['context']['reviewed_run_grammar']==inputs['output']['grammar']
            value={'approved':True,'issues':[]};save(root/'agents'/job/'result.json',value);save(root/'agents'/job/'meta.json',{'return_code':0});return value
    harness=KoreanHarness(root,1,runner=Runner())
    dictionary_context={'word_requests':{},'grammar_requests':['new-pattern'],
        'chapter':{'text':context['chapter_text'],'segments':[]},handoff.FIELD:descriptor,
        'reviewed_run_grammar':handoff.replay_handoff(root,descriptor,language='ko',chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})}
    value=asyncio.run(harness.stage('dictionary','Write standalone entries.','dictionary',
        lambda value:validate_delta(value,{}, {'new-pattern'}, {}, {}),dictionary_context))
    assert calls==['dictionary-review-0']
    assert harness.stages['dictionary']['approved']
    assert jobs.replay(root,read(root/'agents/dictionary-0/meta.json'))==value


def test_matching_published_entry_needs_no_import_but_different_content_is_explicit_conflict(fixture):
    descriptor=build(fixture)
    entry=handoff.replay_handoff(fixture[1],descriptor,language='ko',chapter_text=fixture[3]['chapter_text'],required_ids={'new-pattern'},published={})[0]
    kwargs=dict(language='ko',chapter_text=fixture[3]['chapter_text'],required_ids={'new-pattern'})
    assert handoff.make_handoff(fixture[1],[fixture[4]],published={'new-pattern':entry},**kwargs) is None
    changed={**entry,'explanation_en':'Different approved registry definition.'}
    with pytest.raises(handoff.DictionaryHandoffError,match='explicit registry revision'):
        handoff.make_handoff(fixture[1],[fixture[4]],published={'new-pattern':changed},**kwargs)


def test_collector_does_not_truncate_source_chunks(tmp_path):
    with pytest.raises(handoff.DictionaryHandoffError,match='chunk source'):
        handoff.collect_korean_handoff(tmp_path,[{},{}],['text'],'text',{})


def test_same_claim_from_different_authenticated_writer_pair_cannot_be_collapsed(fixture):
    from tests.test_annotation_research import _write_job
    from pipeline.annotation_research import import_reviewed_lesson
    source,root,_,_,envelope=fixture
    expected=envelope['lessons'][0]['expected_context'];output=read(source/'agents/writer/result.json')
    _write_job(source,'writer2','different writer','{}',expected,output)
    _write_job(source,'critic2','different critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
    proof=import_reviewed_lesson(root,'writer2','critic2',lesson_id='new-pattern',expected_context=expected,issue_ids=['issue1'],source_run_dir=source)
    second=copy.deepcopy(envelope);second['lessons'][0]['import_receipt']=proof['evidence']
    with pytest.raises(handoff.DictionaryHandoffError,match='conflicts, repeats'):
        handoff.make_handoff(root,[envelope,second],language='ko',chapter_text=expected['chapter']['text'],required_ids={'new-pattern'},published={})


def test_collector_loads_actual_registered_source_at_canonical_position(fixture,monkeypatch):
    import pipeline.annotation_research as research
    source,root,candidate,context,envelope=fixture
    monkeypatch.setattr(research,'_repo_relative_run_dir',lambda _: 'fixture-source')
    monkeypatch.setattr(research,'_resolve_repo_run_dir',lambda _:source)
    research.register_reviewed_lesson_source(root,source_run_dir=source,writer_job='writer',reviewer_job='critic',lesson_id='new-pattern',expected_context=envelope['lessons'][0]['expected_context'])
    candidate=copy.deepcopy(candidate);candidate['segments'][0]['form_steps'][0]['grammar_entry_ids']=['new-pattern'];candidate['grammar_links']=[{'entry_id':'new-pattern'}]
    descriptor=handoff.collect_korean_handoff(root,[{'segments':[{'text':'pre '}],'grammar_links':[]},candidate],['pre ','아이'],context['chapter_text'],{})
    assert descriptor is not None
    assert handoff.replay_handoff(root,descriptor,language='ko',chapter_text=context['chapter_text'],required_ids={'new-pattern'},published={})[0]['id']=='new-pattern'


@pytest.mark.parametrize('registered,promotion',[(False,None),(True,None),(True,'exact'),(True,'different')])
def test_actual_cached_annotation_stage_derives_new_handoff_without_rewriting_approval(tmp_path,monkeypatch,registered,promotion):
    from pipeline.korean_agent_harness import KoreanHarness,normalize_existing,digest,payload
    from pipeline import korean_contracts as contracts,korean_dictionary as dictionary
    from pipeline import annotation_run_lessons as lesson_module
    from tests.test_annotation_research import _write_job
    from pipeline.annotation_research import import_reviewed_lesson
    import pipeline.annotation_research as research
    chapter=json.loads(open('tests/fixtures/korean_manual_annotations.json').read())['chapters'][0]
    annotation=normalize_existing(chapter,dictionary._registry(dictionary.WORDS))
    texts=contracts.annotation_chunks(chapter['text']);values=contracts.slice_annotations(annotation,texts)
    target_index,step_index=next((i,j) for i,s in enumerate(values[0]['segments']) for j,t in enumerate(s['form_steps']) if t['grammar_entry_ids'])
    values[0]['segments'][target_index]['form_steps'][step_index]['grammar_entry_ids'][0]='new-pattern'
    values[0]['grammar_links'][0]['entry_id']='new-pattern'
    annotation=contracts.combine_annotations(values,texts)
    root=tmp_path/'destination';root.mkdir();source=tmp_path/'source';source.mkdir()
    expected={'language':'ko','chapter':{'text':chapter['text'],'segments':copy.deepcopy(values[0]['segments'])},'target_occurrence':{
        'target_path':f'/segments/{target_index}/form_steps/{step_index}/grammar_entry_ids/0','segment_index':target_index,
        'surface':values[0]['segments'][target_index]['text'],'source_start':0,'source_text':texts[0]}}
    output={'words':[],'grammar':[{'id':'new-pattern','title_en':'Test pattern','pattern':'test','explanation_en':'Fixture standalone lesson.'}]}
    _write_job(source,'writer','writer','{}',expected,output);_write_job(source,'critic','critic','{}',{'context':expected,'output':output},{'approved':True,'issues':[]})
    monkeypatch.setattr(lesson_module,'_resolve_repo_run_dir',lambda _:source)
    monkeypatch.setattr(research,'_resolve_repo_run_dir',lambda _:source)
    monkeypatch.setattr(research,'_repo_relative_run_dir',lambda _:'fixture-source')
    proof=import_reviewed_lesson(root,'writer','critic',lesson_id='new-pattern',expected_context=expected,issue_ids=['source-issue'],source_run_dir=source)
    records=[]
    for index,(value,text) in enumerate(zip(values,texts)):
        job=f'chunk-{index}';save(root/'agents'/job/'result.json',value);save(root/'agents'/job/'meta.json',{'return_code':0})
        records.append({'job':job,'text':text,'digest':digest(value)})
    save(root/'agents/annotation-0/result.json',annotation)
    saved_meta={'kind':'annotation_assembly','return_code':0,'chunks':records}
    if promotion is not None:
        saved_meta[handoff.FIELD]=handoff.make_handoff(root,[{'lessons':[{'source_run_relpath':'fixture-source','expected_context':expected,'import_receipt':proof['evidence']}]}],language='ko',chapter_text=chapter['text'],required_ids={'new-pattern'},published={})
    save(root/'agents/annotation-0/meta.json',saved_meta)
    calls=[]
    class Runner:
        async def call(self,job,prompt,*args,**kwargs):
            calls.append((job,kwargs));assert job=='annotation-review-0' and kwargs['cache_only'] is True
            return {'approved':True,'issues':[]}
    save(root/'agents/annotation-review-0/result.json',{'approved':True,'issues':[]})
    harness=KoreanHarness(root,1,runner=Runner())
    # Historical whole-review context already contains this exact source proof;
    # the new handoff feature was absent from the accepted assembly metadata.
    context={'prose':{'text':chapter['text']},'independently_reviewed_lesson':proof['evidence']}
    def producer(*a,**k):pytest.fail('Accepted stage must not invoke its producer')
    cached=asyncio.run(harness.stage('annotation','Review annotation.','annotation',lambda _:None,context,producer=producer))
    assert cached==annotation and len(calls)==1
    if registered:
        research.register_reviewed_lesson_source(root,source_run_dir=source,writer_job='writer',reviewer_job='critic',lesson_id='new-pattern',expected_context=expected)
    before={str(p.relative_to(root)):p.read_bytes() for p in (root/'agents').rglob('*') if p.is_file()}
    published={} if promotion is None else {'new-pattern':copy.deepcopy(output['grammar'][0])}
    if promotion=='different':
        published['new-pattern']['explanation_en']='A different published meaning.'
        with pytest.raises(handoff.DictionaryHandoffError,match='explicit registry revision'):
            handoff.handoff_after_annotation_stage(root,harness.stages['annotation'],cached,chapter_text=chapter['text'],published=published)
    else:
        result=handoff.handoff_after_annotation_stage(root,harness.stages['annotation'],cached,chapter_text=chapter['text'],published=published)
        assert (result is not None)==(registered and promotion is None)
        if registered and promotion is None:assert handoff.replay_handoff(root,result,language='ko',chapter_text=chapter['text'],required_ids={row['entry_id'] for row in annotation['grammar_links']},published={})==output['grammar']
    assert before=={str(p.relative_to(root)):p.read_bytes() for p in (root/'agents').rglob('*') if p.is_file()}
    assert read(root/'agents/annotation-0/meta.json')==saved_meta
    if registered:
        altered=copy.deepcopy(values[0]);altered['segments'][0]['meaning_en']='Changed after acceptance.'
        save(root/'agents/chunk-0/result.json',altered)
        with pytest.raises(handoff.DictionaryHandoffError,match='source chunk lineage changed'):
            handoff.handoff_after_annotation_stage(root,harness.stages['annotation'],cached,chapter_text=chapter['text'],published={})
