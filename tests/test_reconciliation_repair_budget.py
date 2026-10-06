"""Shared phase admission on real harness types; no invented C/J discovery history."""
import json
from pathlib import Path
import pytest
from pipeline import chunk_scheduler as scheduler
from pipeline.annotation_reference_carry_callers import register_chunk_positions
from pipeline.agent_harness import CachedCallUnavailable,ChapterHarness
from pipeline.japanese_agent_harness import JapaneseChapterHarness
from pipeline.korean_agent_harness import KoreanHarness
class Runner:
 model='gpt-6-luna';benchmark_effort=None
 def __init__(self):self.paid=[];self.cache={}
 async def call(self,job,prompt,schema,effort=None,**options):
  if options.get('cache_only'):
   if job in self.cache:return self.cache[job]
   raise CachedCallUnavailable('fixture cache miss')
  self.paid.append(job);return {'protocol_fixture':True}
def harness(cls,tmp_path):
 h=object.__new__(cls);h.run_dir=tmp_path;h.runner=Runner();h.annotation_active_indices=[1];h.level=4
 register_chunk_positions(h,['XX','YY'],parent_text='XXYY');return h
@pytest.mark.asyncio
@pytest.mark.parametrize('cls',[ChapterHarness,JapaneseChapterHarness,KoreanHarness])
async def test_actual_harness_admission_phase_persists_separately_from_annotation(cls,tmp_path):
 h=harness(cls,tmp_path);original=h.runner;schema=tmp_path/'schema.json';schema.write_text('{}')
 # Shared new opt-in phase; no assertion these languages possess Korean discovery jobs.
 with scheduler.bounded_chunk_jobs(h,phase='reconciliation-repair',job_limit=2):
  for job in ['patch','fresh-full-critic']:await h.runner.call(job,'exact request',schema,'high',tool_profile='workspace')
  with pytest.raises(scheduler.ChunkJobLimitReached):await h.runner.call('extra','exact request',schema,'low',tool_profile='workspace')
 assert h.runner is original and original.paid==['patch','fresh-full-critic']
 with scheduler.bounded_chunk_jobs(h,phase='reconciliation-repair',job_limit=2):
  with pytest.raises(scheduler.ChunkJobLimitReached,match='investigation'):await h.runner.call('patch','exact request',schema,'high',tool_profile='workspace')
  original.cache['patch']={'verified_fixture':True}
  assert await h.runner.call('patch','exact request',schema,'low',tool_profile='workspace')==original.cache['patch']
 with scheduler.bounded_chunk_jobs(h,phase='annotation',job_limit=6):await h.runner.call('ordinary-review','annotation request',schema,'low',tool_profile='workspace')
 ledgers=[json.loads(p.read_text()) for p in (tmp_path/'annotation-admission').glob('*.json')]
 assert {r['scope']['phase']:len(r['requests']) for r in ledgers}=={'reconciliation-repair':2,'annotation':1}
 assert next(r for r in ledgers if r['scope']['phase']=='annotation')['limit']==6
@pytest.mark.asyncio
async def test_korean_spent_discovery_unchanged_by_distinct_repair_phase(tmp_path):
 h=harness(KoreanHarness,tmp_path);original=h.runner;schema=tmp_path/'schema.json';schema.write_text('{}')
 with scheduler.bounded_chunk_jobs(h,phase='discovery',job_limit=2):
  for job in ['discovery','triage']:await h.runner.call(job,'original',schema,'low',tool_profile='workspace')
 discovery=next(p for p in (tmp_path/'annotation-admission').glob('*.json') if json.loads(p.read_text())['scope']['phase']=='discovery');before=discovery.read_bytes()
 with scheduler.bounded_chunk_jobs(h,phase='reconciliation-repair',job_limit=2):
  for job in ['patch','critic']:await h.runner.call(job,'new bounded phase',schema,'low',tool_profile='workspace')
 assert discovery.read_bytes()==before
 with scheduler.bounded_chunk_jobs(h,phase='discovery',job_limit=2):
  with pytest.raises(scheduler.ChunkJobLimitReached):await h.runner.call('rediscovery','original',schema,'low',tool_profile='workspace')
 assert original.paid==['discovery','triage','patch','critic']
