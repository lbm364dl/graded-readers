"""Capture actual proposed review methods; no workers or canonical module overlays."""
import ast,asyncio,importlib.util
from argparse import Namespace
from pathlib import Path
import pytest
from pipeline import agent_harness as c,japanese_agent_harness as j
from pipeline import annotation_ranged_link_guidance as g
class Captured(Exception):pass
@pytest.mark.parametrize('name,module,cls,rep,key,expected',[
 ('agent_harness.py',c,c.ChapterHarness,'chinese-annotation','text',1),
 ('japanese_agent_harness.py',j,j.JapaneseChapterHarness,'japanese-annotation','surface',2),
])
def test_actual_review_boundaries_have_representation_bound_policy(name,module,cls,rep,key,expected):
 tree=ast.parse(Path(getattr(module, 'RANGED_PROPOSAL_SOURCE', module.__file__)).read_text());node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='review_annotation')
 ns=dict(vars(module));ns.update(with_ranged_policy=g.with_policy,ranged_contextual_guidance=g.contextual_guidance)
 ns['bind_lifecycle_carry']=lambda *a,context=None,**k:(context or {},{})
 ns['bind_lifecycle_run_lessons']=lambda *a,context=None,**k:(context or {},{})
 code=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(code);exec(compile(code,str(getattr(module, 'RANGED_PROPOSAL_SOURCE', module.__file__)),'exec'),ns)
 calls=[]
 class Runner:
  async def call(self,job,prompt,schema,*a,**kwargs):
   ctx=kwargs['workspace_context'];assert ctx[g.FIELD]==g.marker(rep)
   assert g.guidance(rep) in prompt and '\n\n'+g.guidance(rep) in prompt
   if job.endswith('boundary_review'):assert 'does not add overlay review to its remit' in prompt
   calls.append(job);raise Captured
 h=object.__new__(cls);h.runner=Runner();h.args=Namespace(annotation_review_effort='low',refresh=False)
 candidate={'segments':[{key:'人','type':'word','pinyin':'rén','meaning_en':'person'}],'grammar_overlays':[]}
 with pytest.raises(Captured):asyncio.run(ns['review_annotation'](h,0,'人',candidate,'review'))
 assert len(calls)==expected
@pytest.mark.parametrize('name,rep',[('agent_harness.py','chinese-annotation'),('japanese_agent_harness.py','japanese-annotation')])
def test_historical_context_absence_not_upgraded_and_new_marker_checked(name,rep):
 module=c if name=='agent_harness.py' else j
 tree=ast.parse(Path(getattr(module,'RANGED_PROPOSAL_SOURCE',module.__file__)).read_text())
 nodes=[n for n in ast.walk(tree) if isinstance(n,ast.If) and isinstance(n.test,ast.Compare) and any(isinstance(op,ast.In) for op in n.test.ops) and 'RANGED_POLICY_FIELD' in ast.unparse(n.test)]
 assert len(nodes)==1
 ns={'replay':{'context':{}},'expected_context':{'original':True},'RANGED_POLICY_FIELD':g.FIELD,'ranged_marker':g.marker}
 # Wrap return-containing branch to exercise the actual replay policy code.
 fn=ast.FunctionDef(name='replay_policy',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[nodes[0],ast.Return(value=ast.Constant(True))],decorator_list=[])
 mod=ast.Module(body=[fn],type_ignores=[]);ast.fix_missing_locations(mod);exec(compile(mod,'replay-policy','exec'),ns)
 assert ns['replay_policy']() is True and ns['expected_context']=={'original':True}
 ns['replay']['context'][g.FIELD]=g.marker(rep);assert ns['replay_policy']() is True and ns['expected_context'][g.FIELD]==g.marker(rep)
 ns['replay']['context'][g.FIELD]=g.marker('korean-flat');assert ns['replay_policy']() is False

def test_chinese_full_overlay_can_start_before_particle_but_source_defects_reject():
 import copy
 text='来过';value={'segments':[{'text':'来','type':'word','pinyin':'lái','meaning_en':'come'},{'text':'过','type':'particle','pinyin':'guo','meaning_en':'experience marker'}],'grammar_overlays':[{'start':0,'end':2,'text':text,'grammar_candidate_key':'experience-guo','pattern':'V过','meaning_en':'have done before'}]}
 assert c.ChapterHarness.annotation_contract_issues(text,value)==[]
 assert g.marker('chinese-annotation')['representation']=='chinese-annotation'
 for edits in ({'end':3},{'text':'过'}):
  bad=copy.deepcopy(value);bad['grammar_overlays'][0].update(edits)
  assert any(x['problem']=='grammar' for x in c.ChapterHarness.annotation_contract_issues(text,bad))

def test_japanese_absolute_overlay_and_relative_components_remain_distinct():
 import copy
 # Reuse the tracked numeric-range fixture; its source gate already supports it.
 fixture=ast.parse((c.ROOT/'tests/test_japanese_agent_harness.py').read_text())
 fn=next(n for n in fixture.body if isinstance(n,ast.FunctionDef) and n.name=='test_numeric_approximation_skips_punctuation_component')
 ns={};module=ast.Module(body=[n for n in fn.body if isinstance(n,ast.Assign)],type_ignores=[]);ast.fix_missing_locations(module);exec(compile(module,'tracked-numeric-range-fixture','exec'),ns)
 prefix={'surface':'。','type':'punctuation','lemma':'','surface_kana':'','lemma_kana':'','part_of_speech':'','conjugation_form':'','meaning_en':'','story_role':'none','story_importance_en':''}
 text='。三、四十匹';overlay=ns['overlay'];overlay.update(start=1,end=len(text));value={'segments':[prefix]+ns['segments'],'grammar_overlays':[overlay]}
 assert j.JapaneseChapterHarness.annotation_reconstructs(text,value)
 # Component0 begins at0 RELATIVE to overlay, although overlay starts at1.
 assert overlay['components'][0]['start']==0 and overlay['start']==1
 for edit in ('outside','surface','component'):
  bad=copy.deepcopy(value)
  if edit=='outside':bad['grammar_overlays'][0]['end']=len(text)+1
  elif edit=='surface':bad['grammar_overlays'][0]['surface']='四十匹'
  else:bad['grammar_overlays'][0]['components'][0]['start']=1
  assert not j.JapaneseChapterHarness.annotation_reconstructs(text,bad)
