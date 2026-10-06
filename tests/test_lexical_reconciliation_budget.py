import importlib.util,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from pipeline import chunk_scheduler as scheduler
@pytest.mark.asyncio
async def test_distinct_two_job_phase_persists_without_discovery_reset(tmp_path):
 from pipeline.agent_harness import CachedCallUnavailable
 class Runner:
  model='gpt-6-luna';benchmark_effort=None
  def __init__(self):self.paid=[];self.cache={}
  async def call(self,job,prompt,schema,effort=None,**options):
   if options.get('cache_only'):
    if job in self.cache:return self.cache[job]
    raise CachedCallUnavailable('fixture cache miss')
   self.paid.append(job);return {'fixture_only':True}
 original=Runner();schema=tmp_path/'schema.json';schema.write_text('{}');h=SimpleNamespace(run_dir=tmp_path,runner=original,annotation_active_indices=[22],_annotation_source_positions={21:{'fixture_scope':'exact'}},level=4)
 with scheduler.bounded_chunk_jobs(h,phase='reconciliation',job_limit=2):
  await h.runner.call('resolver','payload',schema,'high',tool_profile='workspace');await h.runner.call('critic','payload',schema,'low',tool_profile='workspace')
  with pytest.raises(scheduler.ChunkJobLimitReached,match='limit'):await h.runner.call('third','payload',schema,'low',tool_profile='workspace')
 assert original.paid==['resolver','critic'];ledger=json.loads(next((tmp_path/'annotation-admission').glob('*.json')).read_text());assert ledger['scope']['phase']=='reconciliation' and len(ledger['requests'])==2
 with scheduler.bounded_chunk_jobs(h,phase='reconciliation',job_limit=2):
  with pytest.raises(scheduler.ChunkJobLimitReached,match='investigation'):await h.runner.call('resolver','payload',schema,'low',tool_profile='workspace')
  original.cache['resolver']={'auth_fixture_only':True};assert await h.runner.call('resolver','payload',schema,'low',tool_profile='workspace')==original.cache['resolver']
 assert original.paid==['resolver','critic']
