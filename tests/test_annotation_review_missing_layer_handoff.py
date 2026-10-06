import asyncio, json
from pathlib import Path
import pytest

def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))

def test_missing_layer_repair_context_is_opt_in_and_carries_only_source_descriptors():
 from pipeline.annotation_repair_policy_context import missing_layer_repair_context
 old={'grammar_knowledge':{'catalog_status':'reviewed','approved_entries':[]},'chunk_text':'old'}
 assert missing_layer_repair_context(old,[{'candidate_paths':['/segments/0/text']}]) is old
 packet={'lessons':[{'id':'run.grammar','source_run_relpath':'source/run','import_receipt':{'digest':'a'*64},
                    'expected_context':{'candidate_digest':'b'*64}}]}
 current={'grammar_knowledge':{'catalog_status':'reviewed','approved_entries':[]},
          'reviewed_run_lessons':packet,'chunk_text':'月'}
 result=missing_layer_repair_context(current,[{'missing_layer':{'version':1}}])
 assert result['missing_layer_authority_policy_version']==1
 assert 'missing_layer_authority_policy_version' not in current
 assert result['grammar_knowledge']['reviewed_run_lesson_sources']==[
  {'source_run_relpath':'source/run','import_receipt':{'digest':'a'*64},'expected_context':{'candidate_digest':'b'*64}}]


def test_new_review_drops_only_prior_adjudication_binding():
 from pipeline.annotation_repair_policy_context import drop_stale_adjudication_authority
 old={'prior_review_adjudication':{'status':'actionable'},'prior_review_history':[{'stage':'old'}],
      'reviewed_annotation_research':{'packet':'exact'},'reviewed_run_lessons':{'lessons':[{'id':'g'}]}}
 new=drop_stale_adjudication_authority(old)
 assert 'prior_review_adjudication' not in new
 assert new['prior_review_history']==old['prior_review_history']
 assert new['reviewed_annotation_research']==old['reviewed_annotation_research']
 assert new['reviewed_run_lessons']==old['reviewed_run_lessons']
 assert 'prior_review_adjudication' in old


@pytest.mark.parametrize('language', ['zh','ja','ko'])
def test_actual_cjk_host_grammar_contexts_validate_typed_missing_layer(language):
 import sys
 from pipeline.annotation_issue_targets import validate_issue_targets
 from pipeline.annotation_missing_layer import identity_bindings
 if language=='zh':
  from pipeline.agent_harness import chinese_semantic_repair_grammar_knowledge
  source='月月'; candidate={'segments':[{'text':'月'},{'text':'月'}], 'grammar_overlays':[
   {'start':0,'end':1,'text':'月','grammar_candidate_key':'provisional.month'}]}
  knowledge=chinese_semantic_repair_grammar_knowledge(candidate); identity='provisional.month'; rep='chinese-annotation'
 elif language=='ja':
  from pipeline.japanese_agent_harness import japanese_semantic_repair_grammar_knowledge
  knowledge=japanese_semantic_repair_grammar_knowledge()
  assert knowledge['catalog_status']=='reviewed' and knowledge['approved_entries']
  identity=knowledge['approved_entries'][0].get('id') or knowledge['approved_entries'][0].get('entry_id')
  source='月'; candidate={'segments':[{'surface':'月','lemma':'月','lemma_kana':'つき'}], 'grammar_overlays':[]}
  rep='japanese-annotation'
 else:
  from pipeline.korean_agent_harness import korean_semantic_repair_grammar_knowledge
  entry={'id':'fixture.grammar','pattern':'N + particle','meaning_en':'reviewed construction'}
  knowledge=korean_semantic_repair_grammar_knowledge({'fixture.grammar':entry},set())
  identity='fixture.grammar'; source='달'; candidate={'segments':[{'text':'달','lexical_id':'달05/명','lemma':'달'}], 'grammar_links':[]}
  rep='korean-flat'
 binding=identity_bindings(knowledge).get(identity)
 if language=='ja': assert binding is not None, 'chosen approved registry entry must bind'
 assert binding is not None
 path='/segments/1/text' if language=='zh' else ('/segments/0/lemma' if language=='ja' else '/segments/0/text')
 start=1 if language=='zh' else 0
 end=start+1
 issue={'candidate_paths':[path],'supporting_paths':[],
  'missing_layer':{'version':1,'anchor_paths':[path],
   'collection_path':'/grammar_overlays' if language in {'zh','ja'} else '/grammar_links',
   'identity':identity,'identity_status':binding['status'],'identity_digest':binding['digest'],
   'source_interval':{'start':start,'end':end,'surface':source[start:end]}}}
 review={'issues':[issue]}
 validate_issue_targets(review,candidate,source_text=source,representation=rep,require_typed=True,
  missing_layer_policy_version=1,grammar_knowledge=knowledge,language=language)
 bad=json.loads(json.dumps(review)); bad['issues'][0]['missing_layer']['identity_digest']='0'*64
 with pytest.raises(ValueError):
  validate_issue_targets(bad,candidate,source_text=source,representation=rep,require_typed=True,
   missing_layer_policy_version=1,grammar_knowledge=knowledge,language=language)


@pytest.mark.parametrize('language', ['zh','ja','ko'])
def test_actual_cjk_repair_entry_binds_marker_and_host_knowledge_before_worker(language,tmp_path):
 import sys
 from types import SimpleNamespace
 from pipeline.annotation_issue_targets import validate_issue_targets
 from pipeline.annotation_missing_layer import identity_bindings
 from pipeline.annotation_repair_policy_context import missing_layer_repair_context
 from pipeline.annotation_repairs import repair_annotation
 if language=='zh':
  from pipeline.agent_harness import chinese_semantic_repair_grammar_knowledge
  source='月月'; candidate={'segments':[{'text':'月'},{'text':'月'}], 'grammar_overlays':[
   {'start':0,'end':1,'text':'月','grammar_candidate_key':'provisional.month'}]}
  knowledge=chinese_semantic_repair_grammar_knowledge(candidate); identity='provisional.month'; rep='chinese-annotation'
 elif language=='ja':
  from pipeline.japanese_agent_harness import japanese_semantic_repair_grammar_knowledge
  knowledge=japanese_semantic_repair_grammar_knowledge()
  entry=knowledge['approved_entries'][0]; identity=entry.get('id') or entry.get('entry_id')
  source='月'; candidate={'segments':[{'surface':'月','lemma':'月','lemma_kana':'つき'}], 'grammar_overlays':[]}
  rep='japanese-annotation'
 else:
  from pipeline.korean_agent_harness import korean_semantic_repair_grammar_knowledge
  entry={'id':'fixture.grammar','pattern':'N + particle','meaning_en':'reviewed construction'}
  knowledge=korean_semantic_repair_grammar_knowledge({'fixture.grammar':entry},set())
  identity='fixture.grammar'; source='달'; candidate={'segments':[{'text':'달','lexical_id':'달05/명','lemma':'달'}], 'grammar_links':[]}
  rep='korean-flat'
 binding=identity_bindings(knowledge).get(identity); assert binding is not None
 path='/segments/1/text' if language=='zh' else ('/segments/0/lemma' if language=='ja' else '/segments/0/text')
 start=1 if language=='zh' else 0
 issue={'candidate_paths':[path],'supporting_paths':[],
  'missing_layer':{'version':1,'anchor_paths':[path],
   'collection_path':'/grammar_overlays' if language in {'zh','ja'} else '/grammar_links',
   'identity':identity,'identity_status':binding['status'],'identity_digest':binding['digest'],
   'source_interval':{'start':start,'end':start+1,'surface':source[start:start+1]}}}
 validate_issue_targets({'issues':[issue]},candidate,source_text=source,representation=rep,
  require_typed=True,missing_layer_policy_version=1,grammar_knowledge=knowledge,language=language)
 context={'chunk_text':source,'grammar_knowledge':knowledge}
 if language=='ko':
  context['candidate_gate']={'focus':{},'title':'fixture','number':1,'plan':{},'level':1,'source_id':'fixture'}
 bound=missing_layer_repair_context(context,[issue])
 class ReachedPlan:
  async def call(self,*args,**kwargs):
   assert kwargs['workspace_context']['missing_layer_authority_policy_version']==1
   assert kwargs['workspace_context']['grammar_knowledge']==knowledge
   assert kwargs['workspace_context']['repair_target_authority']
   raise RuntimeError('stop-before-worker')
 harness=SimpleNamespace(run_dir=tmp_path,runner=ReachedPlan())
 with pytest.raises(RuntimeError,match='stop-before-worker'):
  asyncio.run(repair_annotation(harness,'fixture/semantic_repair',candidate,[issue],
   representation=rep,language=language,context=bound,validate_candidate=lambda value:None))


def test_actual_korean_review_prompt_workspace_indexes_nested_lexical_records(tmp_path):
 import sys
 from pipeline import worker_workspace
 from pipeline.korean_agent_harness import korean_semantic_repair_grammar_knowledge
 from pipeline.korean_chunk_reviews import review_chunk as module_review_chunk
 calls=[]
 class Runner:
  async def call(self,job,prompt,schema,effort,**kwargs):
   calls.append((job,prompt,schema,kwargs['workspace_context']))
   return {'approved':True,'issues':[],'prose_revision_reason_en':''}
 annotation={'segments':[{'text':'달','lemma':'달','lexical_id':'달05/명'}]}
 context={'chapter_text':'달','source_start':0,'approved_grammar':[],
  'approved_words':[{'id':'달05/명','headword':'달','meaning_en':'moon'}],
  'lexical_candidates':[{'id':'달05/의','headword':'달','meaning_en':'month'}]}
 knowledge=korean_semantic_repair_grammar_knowledge({},set())
 asyncio.run(module_review_chunk(Runner(),tmp_path,annotation=annotation,text='달',context=context,
  policy='policy',candidate_linked_lexical_identity_audit_version=1,
  missing_layer_policy_version=1,grammar_knowledge=knowledge))
 job,prompt,schema,workspace_context=calls[0]
 workspace=tmp_path/'actual-prompt-workspace'
 schema_value=json.loads(Path(schema).read_text()) if isinstance(schema,(str,Path)) else schema
 worker_workspace.build(workspace,prompt,schema_value,context=workspace_context)
 index=read(workspace/'INDEX.json')
 entry=next(row for row in index if row.get('field')=='candidate_linked_lexical_identity_records')
 refs=read(workspace/entry['path'])
 assert entry['record_count']>=2
 assert all((workspace/ref['path']).is_file() for ref in refs)
 assert 'candidate_linked_lexical_identity_index' in prompt
 assert any(row.get('field')=='chunk_review_input' for row in index)


@pytest.mark.parametrize('versioned',[False,True])
def test_korean_checkpoint_reconstructs_exact_historical_or_versioned_review(tmp_path,monkeypatch,versioned):
 import sys
 from pipeline import worker_workspace
 from pipeline.korean_chunk_reviews import review_chunk
 from pipeline.korean_agent_harness import reusable_checkpoint_approval,digest,save
 from pipeline.agent_harness import CodexRunner
 annotation={'segments':[{'text':'달','lemma':'달','lexical_id':'달05/명'}]}
 context={'chapter_text':'달 달','source_start':0,'approved_grammar':[],
  'approved_words':[{'id':'달05/명','headword':'달','meaning_en':'moon'}],
  'lexical_candidates':[{'id':'달05/의','headword':'달','meaning_en':'month'}]}
 knowledge=__import__('pipeline.korean_agent_harness',fromlist=['korean_semantic_repair_grammar_knowledge']).korean_semantic_repair_grammar_knowledge({},set())
 class Runner:
  async def call(self,job,prompt,schema,effort,**kwargs):
   root=tmp_path/'agents'/job; root.mkdir(parents=True,exist_ok=True)
   manifest=worker_workspace.build(root/'workspace',prompt,json.loads(Path(schema).read_text()),context=kwargs['workspace_context'])
   save(root/'result.json',{'approved':True,'issues':[],'prose_revision_reason_en':''})
   save(root/'meta.json',{'return_code':0,'workspace_digest':manifest,'tool_profile':'workspace'})
   return read(root/'result.json')
 args={'candidate_linked_lexical_identity_audit_version':1,
       'missing_layer_policy_version':1,'grammar_knowledge':knowledge} if versioned else {}
 review,evidence=asyncio.run(review_chunk(Runner(),tmp_path,annotation=annotation,text='달',context=context,
  policy='checkpoint policy',**args))
 row={'review_context':context,'review':{'kind':'ordinary',**evidence}}
 monkeypatch.setattr(CodexRunner,'_check_tool_profile',staticmethod(lambda *a,**k:None))
 proof=reusable_checkpoint_approval(tmp_path,row,annotation=annotation,text='달',context=context,policy='checkpoint policy')
 assert proof==row['review']
 moved={**context,'source_start':2}
 with pytest.raises(ValueError,match='context changed'):
  reusable_checkpoint_approval(tmp_path,row,annotation=annotation,text='달',context=moved,policy='checkpoint policy')
