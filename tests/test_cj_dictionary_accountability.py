"""Real caller boundary/receipt fixtures; no linguistic approval evidence."""
import copy,json
from pathlib import Path
import pytest
from pipeline import usage_dictionary as cn,japanese_grammar_dictionary as ja
from pipeline import dictionary_objection_accountability as a
from pipeline.annotation_adjudication import digest
from tests.test_annotation_research import _write_job


def artifact(language):
    if language=='zh':
        return {'entries':[{'id':'setup-entry','headword':'字','reading':'zì','kind':'word','senses':[{'id':'setup-sense','definition':'Setup original explanation.','occurrences':['setup-position']}]}]}
    return {'entries':[{'id':'ja-grammar-setup','title':'SETUP','reading':'','kind':'construction','summary_en':'Setup summary.','explanation_en':'Setup original explanation.','formation':[{'form':'SETUP','explanation_en':'Setup own formation.'}],'notes_en':[]}],'assignments':[]}


def leaf(language):
    return '/entries/0/senses/0/definition' if language=='zh' else '/entries/0/explanation_en'


def replace(proposal,language,text):
    if language=='zh':proposal['entries'][0]['senses'][0]['definition']=text
    else:proposal['entries'][0]['explanation_en']=text


@pytest.mark.parametrize('language',['zh','ja'])
def test_schema_exact_semantic_scope_and_owner_identities(language):
    module=cn if language=='zh' else ja
    paths,owners=module._accountability_scope(artifact(language),language)
    assert leaf(language) in paths
    assert '/entries/0/id' not in paths
    assert owners[leaf(language)]==(['/entries/0/id','/entries/0/senses/0/id'] if language=='zh' else ['/entries/0/id'])
    if language=='ja':assert '/entries/0/formation/0/explanation_en' in paths and 'assignments' not in paths


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['zh','ja'])
async def test_actual_critic_contract_carries_authenticated_prior_issue_and_changed_artifact(tmp_path,monkeypatch,language):
    # Both jobs write real workspace/receipt fixtures. Their content is SETUP,
    # not a research claim or a linguistic verdict.
    module=cn if language=='zh' else ja
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(a,'ROOT',tmp_path)
    run=tmp_path/'runs/caller';base=artifact(language);requests=[]
    class Runner:
        async def call(self,job,prompt,schema,effort,**options):
            assert effort=='low' and options['tool_profile']=='workspace'
            packet=options['workspace_context']['dictionary_objection_review'];requests.append(packet)
            assert packet['proposal']==(base if job=='first' else changed)
            if job=='first':value={'approved':False,'issues':['Setup own explanation requires correction.'],'prior_issue_assessments':[]}
            else:
                assert len(packet['rows'])==1
                path=leaf(language);text='Setup corrected own explanation.'
                value={'approved':True,'issues':[],'prior_issue_assessments':[{'issue_id':packet['rows'][0]['issue_id'],'disposition':'resolved_change','proposal_paths':[path],'proposal_spans':[{'path':path,'start':0,'end':len(text),'quote':text}],'rationale':'Setup exact authorized explanation changed; not a linguistic judgment.','changed_fields':[{'path':path,'old_value_digest':digest('Setup original explanation.'),'current_value_digest':digest(text)}]}]}
            _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value)
            return value
    runner=Runner()
    first,origin=await module._accountability_critic(runner,run,'first','Setup independent critic',base,[],a.marker(),language)
    changed=copy.deepcopy(base);replace(changed,language,'Setup corrected own explanation.')
    second,second_origin=await module._accountability_critic(runner,run,'second','Setup fresh independent critic',changed,[origin],a.marker(),language)
    assert first['approved'] is False and second['approved'] is True
    assert second_origin['source']['proposal']==changed
    assert requests[1]['source_contracts']==[origin]
    assert requests[1]['rows'][0]['original_proposal']==base


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['zh','ja'])
async def test_current_clean_vote_cannot_drop_exact_prior_issue(tmp_path,monkeypatch,language):
    module=cn if language=='zh' else ja;monkeypatch.setattr(module,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    run=tmp_path/'runs/caller';proposal=artifact(language)
    class Runner:
        async def call(self,job,prompt,schema,effort,**options):
            value={'approved':job!='first','issues':['Setup retained objection.'] if job=='first' else [],'prior_issue_assessments':[]}
            _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value)
            return value
    first,origin=await module._accountability_critic(Runner(),run,'first','Setup critic',proposal,[],a.marker(),language)
    with pytest.raises(Exception):await module._accountability_critic(Runner(),run,'second','Setup critic',proposal,[origin],a.marker(),language)

@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[False,True])
async def test_real_ja_entry_correction_then_separate_critic_preserves_sibling(tmp_path,monkeypatch,enabled):
    from pipeline.agent_harness import CodexRunner
    monkeypatch.setattr(ja,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    monkeypatch.setattr(ja,'REGISTRY',tmp_path/'registry.json');monkeypatch.setattr(ja,'REQUESTS',tmp_path/'requests.json');monkeypatch.setattr(ja,'OUTPUT',tmp_path/'published.json')
    monkeypatch.setattr(ja,'candidates',lambda:({},[]))
    # Source-independent setup entry fixtures; build publication is a separate test.
    monkeypatch.setattr(ja,'build',lambda:None)
    base=artifact('ja');sibling=copy.deepcopy(base['entries'][0]);sibling['id']='ja-grammar-sibling';base['entries'].append(sibling)
    ja.atomic_json(ja.REGISTRY,{'reviewed':True,'source_fingerprint':ja.decision_digest([]),'review_digest':ja.decision_digest(base),'data':base})
    ja.atomic_json(ja.REQUESTS,[{'id':'setup-edit','entry_id':'ja-grammar-setup','reason':'Setup own explanation correction.'}])
    run=tmp_path/'runs/caller';calls=[]
    async def call(self,job,prompt,schema,effort,**options):
        calls.append((job,prompt,effort,options))
        if job.endswith('/accountability-review'):
            packet=options['workspace_context']['dictionary_objection_review']
            assert packet['proposal']['entries'][0]['explanation_en']=='Setup corrected own explanation.'
            value={'approved':True,'issues':[],'prior_issue_assessments':[]}
            _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value)
            return value
        result=artifact('ja');result['entries'][0]['explanation_en']='Setup corrected own explanation.'
        if enabled:_write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],result)
        return result
    monkeypatch.setattr(CodexRunner,'call',call)
    await ja.update(run_dir=run,**({} if enabled else {'objection_accountability':None}))
    after=ja.words.read(ja.REGISTRY)
    assert after['data']['entries'][1]==sibling
    assert [c[0].split('/')[-1] for c in calls]==(['propose','review','accountability-review'] if enabled else ['propose','review'])
    assert [c[2] for c in calls]==(['low']*3 if enabled else ['low','high'])
    assert ('Use no tools.' in calls[0][1]) is (not enabled)
    assert bool(after.get('dictionary_accountability_records')) is enabled
    previous_calls=len(calls)
    await ja.update(run_dir=run,**({} if enabled else {'objection_accountability':None}))
    assert len(calls)==previous_calls

@pytest.mark.asyncio
@pytest.mark.parametrize('critic_rejects',[False,True])
async def test_real_cn_review_adjudication_cannot_discard_raw_critic_issue(tmp_path,monkeypatch,critic_rejects):
    from pipeline.agent_harness import CodexRunner
    monkeypatch.setattr(cn,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    monkeypatch.setattr(cn,'build',lambda *args,**kwargs:None)
    monkeypatch.setattr(cn,'source_data',lambda source:({}, {}, {}, {}))
    monkeypatch.setattr(cn,'check_batch_links',lambda *args:None)
    monkeypatch.setattr(cn,'allocate_new_ids',lambda value,*args,**kwargs:value)
    output=tmp_path/'decisions.json';output.write_text(json.dumps(artifact('zh')))
    run=tmp_path/'runs/caller';calls=[]
    async def call(self,job,prompt,schema,effort,**options):
        calls.append(job);packet=options['workspace_context']['dictionary_objection_review']
        assert effort=='low' and options['tool_profile']=='workspace'
        if job.startswith('review'):
            value={'approved':not critic_rejects,'issues':['Setup entry setup-entry sense setup-sense explanation objection.'] if critic_rejects else [],'prior_issue_assessments':[]}
        else:
            assert job.startswith('adjudicate') and len(packet['rows'])==1
            # A clean vote with no exact assessment must fail in the real branch.
            value={'approved':True,'issues':[],'prior_issue_assessments':[]}
        _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value);return value
    monkeypatch.setattr(CodexRunner,'call',call)
    if critic_rejects:
        with pytest.raises(Exception):await cn.review(output,run,'gpt-6-luna',objection_accountability=a.marker())
        assert calls==['review-00-0','adjudicate-00-0']
        assert json.loads(output.read_text())==artifact('zh')
    else:
        await cn.review(output,run,'gpt-6-luna')
        assert calls==['review-00-0']
        assert json.loads(output.read_text())['dictionary_accountability_records']
        await cn.review(output,run,'gpt-6-luna')
        assert calls==['review-00-0']

@pytest.mark.asyncio
@pytest.mark.parametrize('language',['zh','ja'])
async def test_saved_final_contract_replay_rejects_changed_entry_and_foreign_scope(tmp_path,monkeypatch,language):
    module=cn if language=='zh' else ja;monkeypatch.setattr(module,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    run=tmp_path/'runs/caller';proposal=artifact(language)
    class Runner:
        async def call(self,job,prompt,schema,effort,**options):
            value={'approved':True,'issues':[],'prior_issue_assessments':[]}
            _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value);return value
    verdict,contract=await module._accountability_critic(Runner(),run,'review','Setup review',proposal,[],a.marker(),language)
    records=module._merge_accountability_record([],contract,proposal,verdict)
    module._validate_accountability_records(records,proposal)
    altered=copy.deepcopy(proposal);replace(altered,language,'Unreviewed changed prose.')
    with pytest.raises(ValueError):module._validate_accountability_records(records,altered)
    foreign=copy.deepcopy(records);foreign[0]['active_entry_ids']=['foreign-id']
    with pytest.raises(ValueError):module._validate_accountability_records(foreign,proposal)
    forged=copy.deepcopy(records);forged[0]['review']['issues']=['Unauthenticated verdict mutation.']
    with pytest.raises(Exception):module._validate_accountability_records(forged,proposal)

@pytest.mark.asyncio
async def test_real_ja_occurrence_correction_pairs_actual_producer_and_critic(tmp_path,monkeypatch):
    from pipeline.agent_harness import CodexRunner
    monkeypatch.setattr(ja,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    monkeypatch.setattr(ja,'validate',lambda *args,**kwargs:None)
    row={'id':'setup-occurrence','sentence':'SETUP','start':0,'end':5,'surface':'SETUP','layer':'segment','annotation':{}}
    before={'candidate_id':row['id'],'entry_ids':['ja-grammar-setup'],'context_en':'Setup old contextual note.'}
    registry={'data':{'entries':artifact('ja')['entries'],'assignments':[before]}}
    changed=dict(before,context_en='Setup corrected contextual note.');result={'entries':[],'assignments':[changed]}
    run=tmp_path/'runs/caller';calls=[]
    async def call(self,job,prompt,schema,effort,**options):
        calls.append(job);assert effort=='low' and options['tool_profile']=='workspace'
        value={'approved':True,'issues':[],'prior_issue_assessments':[]} if job.endswith('/accountability-review') else result
        _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value);return value
    monkeypatch.setattr(CodexRunner,'call',call)
    await ja.edit_occurrence_notes(registry,[row],[{'id':'edit','candidate_id':row['id'],'reason':'Setup correction.'}],run,1,a.marker())
    assert registry['data']['assignments']==[changed]
    record=registry['dictionary_accountability_records'][0]
    assert a.resolve_verified_worker_contract(record['contract']['writer'])['job'].endswith('/review')
    assert a.resolve_verified_worker_contract(record['contract']['writer'])['result']==record['proposal']==result
    assert [j.split('/')[-1] for j in calls]==['propose','review','accountability-review']
    ja._validate_accountability_records([record],registry['data'])
    broken=copy.deepcopy(registry['data']);broken['assignments'][0]['entry_ids']=['foreign-entry']
    with pytest.raises(ValueError):ja._validate_accountability_records([record],broken)

@pytest.mark.asyncio
async def test_real_cn_repair_pairs_complete_producer_and_accounts_both_prior_critics(tmp_path,monkeypatch):
    from pipeline.agent_harness import CodexRunner
    monkeypatch.setattr(cn,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    monkeypatch.setattr(cn,'build',lambda *args,**kwargs:None);monkeypatch.setattr(cn,'source_data',lambda source:({}, {}, {}, {}));monkeypatch.setattr(cn,'check_batch_links',lambda *args:None);monkeypatch.setattr(cn,'allocate_new_ids',lambda value,*args,**kwargs:value)
    base=artifact('zh');changed=copy.deepcopy(base);replace(changed,'zh','Setup corrected own explanation.')
    output=tmp_path/'decisions.json';output.write_text(json.dumps(base));run=tmp_path/'runs/caller';calls=[]
    issue='Setup entry setup-entry sense setup-sense explanation correction.'
    async def call(self,job,prompt,schema,effort,**options):
        calls.append(job)
        if job.startswith('repair'):value=changed
        else:
            packet=options['workspace_context']['dictionary_objection_review'];rows=packet['rows']
            if job=='review-00-0':value={'approved':False,'issues':[issue],'prior_issue_assessments':[]}
            elif job=='adjudicate-00-0':
                path=leaf('zh');text='Setup original explanation.'
                value={'approved':False,'issues':[issue],'prior_issue_assessments':[{'issue_id':rows[0]['issue_id'],'disposition':'unresolved','proposal_paths':[path],'proposal_spans':[{'path':path,'start':0,'end':len(text),'quote':text}],'rationale':'Setup prior correction remains unresolved.','current_issue_index':0}]}
            else:
                assert job=='review-00-1' and len(rows)==2
                path=leaf('zh');text='Setup corrected own explanation.'
                value={'approved':True,'issues':[],'prior_issue_assessments':[{'issue_id':row['issue_id'],'disposition':'resolved_change','proposal_paths':[path],'proposal_spans':[{'path':path,'start':0,'end':len(text),'quote':text}],'rationale':'Setup own explanation changed.','changed_fields':[{'path':path,'old_value_digest':digest('Setup original explanation.'),'current_value_digest':digest(text)}]} for row in rows]}
        _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value);return value
    monkeypatch.setattr(CodexRunner,'call',call)
    await cn.review(output,run,'gpt-6-luna',objection_accountability=a.marker())
    assert calls==['review-00-0','adjudicate-00-0','repair-00-0','review-00-1']
    record=json.loads(output.read_text())['dictionary_accountability_records'][0]
    assert a.resolve_verified_worker_contract(record['contract']['writer'])['job']=='repair-00-0'
    assert a.resolve_verified_worker_contract(record['contract']['writer'])['result']==record['proposal']==changed
    stripped=copy.deepcopy(record);stripped['contract']['writer']=None
    with pytest.raises(ValueError):cn._validate_accountability_records([stripped],changed)
    assert len(record['review']['prior_issue_assessments'])==2

@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[False,True])
async def test_real_ja_routing_canonical_complete_producer_and_legacy_alias_contrast(tmp_path,monkeypatch,enabled):
    from pipeline.agent_harness import CodexRunner
    monkeypatch.setattr(ja,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    for field,name in [('REGISTRY','registry.json'),('REQUESTS','requests.json'),('OUTPUT','published.json')]:monkeypatch.setattr(ja,field,tmp_path/name)
    row={'id':'setup-new-occurrence','source':'setup-source','sentence':'SETUP','start':0,'end':5,'surface':'SETUP','layer':'segment','annotation':{}}
    monkeypatch.setattr(ja,'candidates',lambda:({'setup-source':{'text':'SETUP'}},[row]));monkeypatch.setattr(ja,'build',lambda:None)
    base=artifact('ja');ja.atomic_json(ja.REGISTRY,{'reviewed':True,'source_fingerprint':'old-position','review_digest':ja.decision_digest(base),'data':base})
    run=tmp_path/'runs/caller';calls=[]
    async def call(self,job,prompt,schema,effort,**options):
        calls.append(job)
        if job.endswith('/accountability-review'):
            packet=options['workspace_context']['dictionary_objection_review'];value={'approved':True,'issues':[],'prior_issue_assessments':[]}
            assert packet['proposal']['assignments'][0]['candidate_id']==row['id']
        else:
            assignment={'candidate_id':row['id'] if enabled else 'C0','entry_ids':['ja-grammar-setup'],'context_en':'Setup context.'}
            value={'entries':base['entries'] if enabled else [],'assignments':[assignment]}
        if enabled:_write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value)
        return value
    monkeypatch.setattr(CodexRunner,'call',call)
    await ja.update(run_dir=run,**({} if enabled else {'objection_accountability':None}))
    after=ja.words.read(ja.REGISTRY)
    assert after['data']['entries']==base['entries']
    assert after['data']['assignments'][0]['candidate_id']==row['id']
    assert len(calls)==(3 if enabled else 2)
    if enabled:
        contract=after['dictionary_accountability_records'][0]['contract']
        assert a.resolve_verified_worker_contract(contract['writer'])['result']==contract['source']['proposal']==after['data']

@pytest.mark.asyncio
@pytest.mark.parametrize('language',['zh','ja'])
async def test_existing_content_cache_cannot_hide_new_authenticated_prior_objection(tmp_path,monkeypatch,language):
    from pipeline.agent_harness import CodexRunner
    module=cn if language=='zh' else ja;monkeypatch.setattr(module,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    proposal=artifact(language);run=tmp_path/'runs/caller';calls=[]
    class First:
        async def call(self,job,prompt,schema,effort,**options):
            value={'approved':False,'issues':['Setup retained own-pattern objection.'],'prior_issue_assessments':[]}
            _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value);return value
    _,prior=await module._accountability_critic(First(),run,'original','Setup prior independent critic',proposal,[],a.marker(),language)
    async def call(self,job,prompt,schema,effort,**options):
        calls.append(job);packet=options['workspace_context']['dictionary_objection_review'];assert len(packet['rows'])==1
        value={'approved':True,'issues':[],'prior_issue_assessments':[]}
        _write_job(run,job,prompt,Path(schema).read_text(),options['workspace_context'],value);return value
    monkeypatch.setattr(CodexRunner,'call',call)
    if language=='ja':
        for field,name in [('REGISTRY','registry.json'),('REQUESTS','requests.json'),('OUTPUT','published.json')]:monkeypatch.setattr(ja,field,tmp_path/name)
        monkeypatch.setattr(ja,'build',lambda:None);monkeypatch.setattr(ja,'candidates',lambda:({},[]))
        ja.atomic_json(ja.REGISTRY,{'reviewed':True,'source_fingerprint':ja.decision_digest([]),'review_digest':ja.decision_digest(proposal),'data':proposal})
        with pytest.raises(Exception):await ja.update(run_dir=run,objection_accountability=a.marker(),accountability_sources=[prior])
    else:
        monkeypatch.setattr(cn,'build',lambda *args,**kwargs:None);monkeypatch.setattr(cn,'source_data',lambda source:({}, {}, {}, {}));monkeypatch.setattr(cn,'check_batch_links',lambda *args:None)
        decisions=copy.deepcopy(proposal);decisions.update(reviewed=True,review_digest=cn.decision_digest(proposal['entries']),reviewed_word_fingerprints={'字':cn.decision_digest(proposal['entries'])})
        output=tmp_path/'decisions.json';output.write_text(json.dumps(decisions))
        with pytest.raises(Exception):await cn.review(output,run,'gpt-6-luna',objection_accountability=a.marker(),accountability_sources=[prior])
    assert len(calls)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('language',['zh','ja'])
async def test_prepaid_producer_must_bind_exact_artifact_before_any_critic_call(tmp_path,monkeypatch,language):
    module=cn if language=='zh' else ja;monkeypatch.setattr(module,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    class Forbidden:
        async def call(self,*args,**kwargs):raise AssertionError('critic must not run for invalid producer setup')
    bad={'job':'producer','result':{'entries':[]}}
    with pytest.raises(ValueError,match='producer does not bind'):
        await module._accountability_critic(Forbidden(),tmp_path/'runs/caller','critic','Setup critic',artifact(language),[],a.marker(),language,writer=bad)

@pytest.mark.asyncio
@pytest.mark.parametrize('language',['zh','ja'])
async def test_current_history_uses_verified_descriptors_without_recursive_worker_contexts(tmp_path,monkeypatch,language):
    module=cn if language=='zh' else ja;monkeypatch.setattr(module,'ROOT',tmp_path);monkeypatch.setattr(a,'ROOT',tmp_path)
    run=tmp_path/'runs/caller';proposal=artifact(language);sources=[];sizes=[]
    class Runner:
        async def call(self,job,prompt,schema,effort,**options):
            context=options['workspace_context'];assert context['dictionary_objection_source_contract_version']==3
            packet=context['dictionary_objection_review'];sizes.append(len(json.dumps(context)))
            for prior in packet['source_contracts']:
                assert prior['critic']['provider']==a.WORKER_PROVIDER
                assert 'workspace_context' not in prior['critic'] and 'prompt' not in prior['critic']
            path=leaf(language);text=resolve_text(proposal,language)
            value={'approved':False,'issues':['Setup unresolved own explanation.'],'prior_issue_assessments':[{'issue_id':row['issue_id'],'disposition':'unresolved','proposal_paths':[path],'proposal_spans':[{'path':path,'start':0,'end':len(text),'quote':text}],'rationale':'Setup bounded unresolved history.','current_issue_index':0} for row in packet['rows']]}
            _write_job(run,job,prompt,Path(schema).read_text(),context,value);return value
    for index in range(4):
        _,contract=await module._accountability_critic(Runner(),run,f'critic-{index}','Setup critic',proposal,sources,a.marker(),language)
        assert contract['critic']['provider']==a.WORKER_PROVIDER;sources.append(contract)
    # Bounded setup artifact: history contains semantic source rows and short
    # references, never recursively copied old prompt/schema/workspace payloads.
    assert sizes[-1]<16000 and sizes[-1]<sizes[0]*6
    a.authenticate_source_contract(sources[-1])
    broken=copy.deepcopy(sources[-1]);broken['critic']['contract_sha256']='0'*64
    with pytest.raises(ValueError):a.authenticate_source_contract(broken)

def resolve_text(proposal,language):
    return proposal['entries'][0]['senses'][0]['definition'] if language=='zh' else proposal['entries'][0]['explanation_en']
