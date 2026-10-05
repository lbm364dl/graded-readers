"""Replay branch fixtures; no fabricated worker receipt or linguistic claim."""
import pytest
from pipeline.annotation_repairs import repair_annotation,replay_annotation_repair
from pipeline.annotation_edits import candidate_digest
from pipeline.annotation_ranged_link_guidance import FIELD,marker
from tests.test_annotation_repairs import PlannedRunner,Harness,_candidate,_plan,_target
@pytest.mark.asyncio
@pytest.mark.parametrize('marked',[False,True])
async def test_marker_only_missing_index_replay_preserves_legacy_and_rejects_new_missing_workspace(tmp_path,marked):
 candidate=_candidate();target=_target('set_field','/segments/0/meaning_en');plan=_plan((target,));patch={'base_digest':candidate_digest(candidate),'edits':[{**target,'value':'fixture changed'}]}
 runner=PlannedRunner(tmp_path,plan,patch);context={'chunk_text':'她走'}
 if marked:context[FIELD]=marker('chinese-annotation')
 result=await repair_annotation(Harness(tmp_path,runner),'fixture',candidate,[{'problem':'fixture correction'}],representation='chinese-annotation',language='zh',context=context,validate_candidate=lambda _:None)
 assert result['status']=='applied'
 assert not (tmp_path/'agents/fixture_plan/workspace/INDEX.json').exists()
 if marked:
  with pytest.raises(ValueError,match='Missing authenticated ranged-link workspace'):
   replay_annotation_repair(tmp_path,'fixture_assembly',validate_candidate=lambda _:None)
 else:
  assert replay_annotation_repair(tmp_path,'fixture_assembly',validate_candidate=lambda _:None)['candidate']==result['candidate']
