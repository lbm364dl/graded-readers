"""Bounded, isolated Luna effort comparisons on frozen annotation repair cases.

Cases contain prompt, schema, source_text, lexical_plan, words and lexical_catalog.
This checks structural/identity validity, not independent linguistic approval.
Production defaults and publication evidence are never changed.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path

from pipeline.agent_harness import CodexRunner
from pipeline.korean_annotation_chunks import decode
from pipeline.korean_agent_harness import annotation_requests, digest, save
from pipeline import korean_contracts as contracts


def usage_totals(job_dir):
    totals = {}
    for path in job_dir.glob('events.attempt-*.jsonl'):
        for line in path.read_text().split('\n'):
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get('type') == 'turn.completed':
                for key, value in event.get('usage', {}).items():
                    if isinstance(value, (int, float)):
                        totals[key] = totals.get(key, 0) + value
    return totals


def check_result(value, case):
    value = decode(value, source_text=case['source_text'])
    contracts.check_reconstruction(value['segments'], case['source_text'])
    annotation_requests(value, case['lexical_catalog'], case['lexical_plan'], case['words'])
    return value


async def run(cases_path, output, *, workspace=False, efforts=None):
    cases = json.loads(cases_path.read_text())
    output.mkdir(parents=True, exist_ok=True)
    frozen = output / 'cases.json'
    if frozen.exists() and json.loads(frozen.read_text()) != cases:
        raise ValueError('Use a new output directory for different benchmark inputs')
    save(frozen, cases)
    semaphore = asyncio.Semaphore(3)
    results = []
    async def one(case, effort):
        name = case['name']
        if not name or Path(name).name != name:
            raise ValueError('Case name must be a basename')
        schema = output / f'{name}.schema.json'
        save(schema, case['schema'])
        runner = CodexRunner(output, 'gpt-6-luna', semaphore, timeout=1200,
            max_throttle_retries=0, max_launch_retries=0,
            max_process_timeout_retries=0, benchmark_effort=effort,
            legacy_tool_restrictions=not workspace)
        job = f'{name}-{effort}'
        result = {'case': name, 'effort': effort, 'case_digest': digest(case),
                  'passed_structure_and_identity': False, 'linguistic_review': 'not_performed',
                  'tools': 'workspace' if workspace else 'legacy_offline'}
        try:
            value = await runner.call(job, case['prompt'], schema, effort, tool_profile='offline')
            check_result(value, case)
            result['passed_structure_and_identity'] = True
        except Exception as error:
            result['error'] = f'{type(error).__name__}: {error}'
        job_dir = output / 'agents' / job
        meta_path = job_dir / 'meta.json'
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            result['actual_effort'] = meta['effort']
            result['seconds'] = (datetime.fromisoformat(meta['ended_at']) - datetime.fromisoformat(meta['started_at'])).total_seconds()
            result['attempt_count'] = meta['attempt_count']
        result['usage'] = usage_totals(job_dir)
        results.append(result)
        save(output / 'summary.json', sorted(results, key=lambda r:(r['case'],r['effort'])))
        print(json.dumps(result, ensure_ascii=False), flush=True)
    # Rotate order across cases to reduce systematic queue/cache-order advantage.
    efforts = efforts or ['low', 'medium', 'high']
    for index, case in enumerate(cases):
        order = efforts[index % len(efforts):] + efforts[:index % len(efforts)]
        await asyncio.gather(*(one(case, effort) for effort in order))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workspace', action='store_true', help='Enable tools and organized input files')
    parser.add_argument('--efforts', nargs='+', choices=['low', 'medium', 'high'])
    args = parser.parse_args()
    asyncio.run(run(args.cases, args.output, workspace=args.workspace, efforts=args.efforts))


if __name__ == '__main__':
    main()
