import asyncio,copy,json
import pytest
from pipeline import dictionary_editorial_criteria as c,usage_dictionary as zh,japanese_grammar_dictionary as ja
from tests.test_usage_dictionary import corpus
from tests.test_japanese_grammar_dictionary import fixture

@pytest.mark.parametrize('enabled',[True,False])
def test_actual_chinese_reviewer_repair_and_new_reviewer(corpus,tmp_path,monkeypatch,enabled):
 source,decisions=corpus;decisions['reviewed']=False
 path=tmp_path/'decisions.json';path.write_text(json.dumps(decisions));seen=[]
 async def call(self,job,prompt,schema,effort,**kw):
  seen.append((job,prompt));assert (c.CRITERIA in prompt)==enabled
  if job in ('review-00-0','adjudicate-00-0'):return {'approved':False,'issues':['Setup own-pattern correction']}
  if job=='repair-00-0':return {'entries':decisions['entries']}
  return {'approved':True,'issues':[]}
 monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call',call)
 kwargs={} if enabled else {'editorial_criteria':None}
 asyncio.run(zh.review(path,tmp_path/'run','gpt-6-luna',source=source,**kwargs))
 assert [x[0] for x in seen]==['review-00-0','adjudicate-00-0','repair-00-0','review-00-1']
 assert 'Setup own-pattern correction' in seen[2][1]
 if enabled:assert 'Setup own-pattern correction' in seen[3][1]
 zh.build(json.loads(path.read_text()),source)

@pytest.mark.parametrize('enabled',[True,False])
def test_actual_japanese_writer_critic_schema_and_cached_reuse(tmp_path,monkeypatch,enabled):
 from pipeline import japanese_component_links
 monkeypatch.setattr(japanese_component_links,'REGISTRY',tmp_path/'component-links.json')
 monkeypatch.setattr(ja.words,'annotations',lambda:[ja.words.read(ja.words.ANNOTATION)])
 _,rows=ja.candidates();data=fixture(rows);seen=[]
 async def call(self,job,prompt,schema,effort,**kw):
  seen.append(job);assert (c.CRITERIA in prompt)==enabled
  assert schema==ja.SCHEMA and ja.ENTRY_FOCUS_POLICY in prompt
  return copy.deepcopy(data)
 monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call',call)
 monkeypatch.setattr(ja,'REGISTRY',tmp_path/'registry.json');monkeypatch.setattr(ja,'OUTPUT',tmp_path/'published.json');monkeypatch.setattr(ja,'REQUESTS',tmp_path/'requests.json')
 kwargs={} if enabled else {'editorial_criteria':None}
 result=asyncio.run(ja.update(run_dir=tmp_path/'run',**kwargs));assert len(seen)==2
 assert asyncio.run(ja.update(run_dir=tmp_path/'run',**kwargs))==result and len(seen)==2


def test_absent_shared_marker_is_exact_prompt_identity():
 prompt='Historical writer or reviewer prompt'
 assert c.append(prompt,None)==prompt
 assert c.append(prompt).startswith(prompt) and c.CRITERIA in c.append(prompt)

@pytest.mark.parametrize('enabled',[True,False])
def test_explicit_japanese_entry_editor_passes_same_scope_to_writer_and_critic(tmp_path,monkeypatch,enabled):
 from pipeline import japanese_component_links
 monkeypatch.setattr(japanese_component_links,'REGISTRY',tmp_path/'component-links.json')
 monkeypatch.setattr(ja.words,'annotations',lambda:[ja.words.read(ja.words.ANNOTATION)])
 _,rows=ja.candidates();data=fixture(rows)
 monkeypatch.setattr(ja,'REGISTRY',tmp_path/'registry.json');monkeypatch.setattr(ja,'OUTPUT',tmp_path/'published.json');monkeypatch.setattr(ja,'REQUESTS',tmp_path/'requests.json')
 ja.words.atomic_json(ja.REGISTRY,{'reviewed':True,'source_fingerprint':ja.decision_digest(rows),'data':data,'review_digest':ja.decision_digest(data)})
 ja.words.atomic_json(ja.REQUESTS,[{'id':'setup-edit','entry_id':'ja-grammar-test','reason':'Setup own formation clarification'}]);seen=[]
 async def call(self,job,prompt,schema,effort,**kw):
  assert (c.CRITERIA in prompt)==enabled and 'Setup own formation clarification' in prompt
  assert schema==ja.SCHEMA;seen.append(job)
  return {'entries':[{**data['entries'][0],'explanation_en':'Setup changed own formation.'}],'assignments':[]}
 monkeypatch.setattr('pipeline.agent_harness.CodexRunner.call',call)
 kwargs={} if enabled else {'editorial_criteria':None}
 asyncio.run(ja.update(run_dir=tmp_path/'run',**kwargs));assert len(seen)==2
 assert ja.words.read(ja.REGISTRY)['data']['entries'][0]['explanation_en']=='Setup changed own formation.'
