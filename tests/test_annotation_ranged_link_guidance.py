import copy,importlib.util
from pathlib import Path
import pytest
from pipeline import annotation_ranged_link_guidance as m
def test_absent_preserves_historical():assert m.contextual_guidance({'chunk_review_context':{'source_start':12}})==''
@pytest.mark.parametrize('rep',sorted(m.REPRESENTATIONS))
def test_explicit_representation_binding(rep):
 ctx={'source_start':12};bound=m.with_policy(ctx,rep);assert ctx=={'source_start':12};assert m.contextual_guidance(bound)==m.guidance(rep)
 bad=copy.deepcopy(bound);bad[m.FIELD]['policy_digest']='foreign';
 with pytest.raises(ValueError):m.contextual_guidance(bad)
@pytest.mark.parametrize('version',[True,2,'1',None])
def test_strict_version(version):
 ctx=m.with_policy({},'korean-flat');ctx[m.FIELD]['version']=version
 with pytest.raises(ValueError):m.contextual_guidance(ctx)
def test_root_nested_conflict():
 ctx=m.with_policy({},'korean-flat');ctx['chunk_review_context']=m.with_policy({},'japanese-annotation')
 with pytest.raises(ValueError):m.contextual_guidance(ctx)
def test_contrasting_contracts_and_remit():
 # Instruction tests are protocol fixtures, not linguistic/source validation.
 ko=m.guidance('korean-flat');assert 'first included word' in ko and 'outside the actual represented range' in ko and 'unsupported construction' in ko
 ja=m.guidance('japanese-annotation');assert 'relative to that overlay surface' in ja and 'out-of-overlay component ranges' in ja
 zh=m.guidance('chinese-annotation');assert 'end exclusive' in zh and 'no requirement to move' in zh
 assert all('wrong grammatical analysis' in m.guidance(rep) and 'do not enlarge candidate_paths' in m.guidance(rep) for rep in m.REPRESENTATIONS)
def test_foreign_representation_and_true_marker_rejected():
 ctx=m.with_policy({},'japanese-annotation')
 with pytest.raises(ValueError):m.contextual_guidance(ctx,'korean-flat')
 ctx=m.with_policy({},'korean-flat');ctx[m.FIELD]['version']=True
 with pytest.raises(ValueError):m.with_policy(ctx,'korean-flat')
def test_actual_workspace_child_marker_auth_and_double_strip(tmp_path):
 import asyncio,json
 from tests.test_annotation_repairs import WorkspacePlannedRunner,Harness,_candidate,_plan,_target
 from pipeline.annotation_repairs import repair_annotation,replay_annotation_repair
 from pipeline.annotation_edits import candidate_digest
 from pipeline.agent_harness import CodexRunner
 candidate=_candidate();target='/segments/0/meaning_en';plan=_plan((_target('set_field',target),));patch={'base_digest':candidate_digest(candidate),'edits':[{'op':'set_field','path':target,'value':'a woman'}]}
 runner=WorkspacePlannedRunner(tmp_path,plan,patch)
 result=asyncio.run(repair_annotation(Harness(tmp_path,runner),'ranged-auth',candidate,[{'problem':'wrong_meaning','candidate_paths':[target],'supporting_paths':[]}],representation='chinese-annotation',language='zh',context=m.with_policy({'chunk_text':'她走'},'chinese-annotation'),validate_candidate=lambda value:None))
 assembly=tmp_path/'agents/ranged-auth_assembly';meta=json.loads((assembly/'meta.json').read_text());assert meta[m.FIELD]==m.marker('chinese-annotation')
 CodexRunner._check_tool_profile(assembly,'offline',meta)
 child=tmp_path/'agents/ranged-auth_plan/workspace';index=json.loads((child/'INDEX.json').read_text());markerrow=next(r for r in index if r['field']==m.FIELD)
 altered=json.loads((child/markerrow['path']).read_text());altered['policy_digest']='foreign';(child/markerrow['path']).write_text(json.dumps(altered))
 with pytest.raises(ValueError):CodexRunner._check_tool_profile(assembly,'offline',meta)
 (child/markerrow['path']).write_text(json.dumps(m.marker('chinese-annotation')))
 # Remove BOTH metadata marker and input index marker: actual consumer still
 # verifies original workspace digest, rather than treating it as legacy.
 meta.pop(m.FIELD);(assembly/'meta.json').write_text(json.dumps(meta));index.remove(markerrow);(child/'INDEX.json').write_text(json.dumps(index))
 with pytest.raises(ValueError):CodexRunner._check_tool_profile(assembly,'offline',meta)
def test_real_korean_first_range_anchor_and_source_defects(tmp_path):
 import json
 from pipeline.korean_dictionary import build_assets
 root=Path('/home/catalin/graded-readers');chapter=json.loads((root/'tests/fixtures/korean_manual_annotations.json').read_text())['chapters'][0]
 words={e['id']:e for p in (root/'content/lexicon/korean').glob('*.words.json') for e in json.loads(p.read_text())['entries']}
 grammar={e['id']:e for p in (root/'content/lexicon/korean').glob('*.grammar.json') for e in json.loads(p.read_text())['entries']}
 ranged={'segment_index':2,'entry_id':'naming-iran','context_en':'The complete recorded phrase introduces the name.','display_form':'홍길동이라는','display_end_segment_index':3,'display_meaning_en':'called Hong Gildong'}
 chapter['grammar_links'].append(ranged)
 _,output=build_assets(chapter,tmp_path,word_registry=words,grammar_registry=grammar,write=False)
 assert any(row['segment_index']==2 and row['entry_id']=='naming-iran' for row in output['occurrences'])
 assert chapter['segments'][2]['lexical']['kind']=='proper_name' # first included tap has no ending
 out=copy.deepcopy(chapter);out['grammar_links'][-1]['segment_index']=len(out['segments'])
 with pytest.raises((ValueError,IndexError)):build_assets(out,tmp_path,word_registry=words,grammar_registry=grammar,write=False)
 malformed=copy.deepcopy(chapter);malformed['grammar_links'][-1]['display_form']='not the source'
 with pytest.raises(ValueError):build_assets(malformed,tmp_path,word_registry=words,grammar_registry=grammar,write=False)
 unknown=copy.deepcopy(chapter);unknown['grammar_links'][-1]['entry_id']='unapproved-analysis'
 with pytest.raises(ValueError):build_assets(unknown,tmp_path,word_registry=words,grammar_registry=grammar,write=False)
 assert not list(tmp_path.iterdir())
def test_shared_builders_historical_absence_and_explicit_suffix():
 from pipeline import annotation_adjudication as a,annotation_research as r
 for version in range(1,8):
  assert a._contextual_instructions(version,{},'korean-flat')==a._versioned_instructions(version)
  marked=m.with_policy({},'korean-flat');assert a._contextual_instructions(version,marked,'korean-flat')==a._versioned_instructions(version)+'\n\n'+m.guidance('korean-flat')
 for version in r.SUPPORTED_RESEARCH_POLICY_VERSIONS:
  inputs={'research_policy_version':version,'context':{},'representation':'chinese-annotation'}
  oldresearch=r._research_prompt(inputs);oldcritic=r._review_prompt(inputs)
  marked={**inputs,'context':m.with_policy({},'chinese-annotation')}
  assert r._research_prompt(marked).replace('\n\n'+m.guidance('chinese-annotation'),'')==oldresearch
  assert r._review_prompt(marked).replace('\n\n'+m.guidance('chinese-annotation'),'')==oldcritic
