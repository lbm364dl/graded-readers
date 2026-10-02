"""Bound curriculum generation jobs; retain whole-chapter evaluation and review."""
import asyncio
from pathlib import Path
from jsonschema import validate, ValidationError
from pipeline import korean_contracts as contracts, korean_curriculum as curriculum

BATCH = contracts.obj({'bindings': contracts.CURRICULUM_BINDINGS['properties']['bindings']})
SUMMARY = contracts.obj({key: contracts.CURRICULUM_BINDINGS['properties'][key]
    for key in ('level_reason_en', 'prose_revision_reason_en')})
REPAIRS = contracts.obj({'entries': {'type': 'array', 'items': contracts.obj({
    'kind': {'type': 'string', 'enum': ['vocabulary', 'grammar']}, 'entry_id': contracts.NONEMPTY})}})


def key(binding):
    return binding['kind'], binding['entry_id']


def combine(batches, summary):
    bindings = [entry for batch in batches for entry in batch['bindings']]
    if len({key(b) for b in bindings}) != len(bindings):
        raise ValueError('Duplicate binding across curriculum jobs')
    return {**summary, 'bindings': bindings}


def producer(harness, prompt, context, *, batch_size=16):
    from pipeline.korean_agent_harness import digest, payload, read, save, LEXICAL_REFERENCE
    required = sorted(curriculum.required_bindings(context['chapter']))
    groups = [required[i:i + batch_size] for i in range(0, len(required), batch_size)]
    batch_schema = harness.run_dir / 'curriculum-batch.schema.json'
    summary_schema = harness.run_dir / 'curriculum-summary.schema.json'
    repair_schema = harness.run_dir / 'curriculum-repairs.schema.json'
    for path, schema in [(batch_schema, BATCH), (summary_schema, SUMMARY), (repair_schema, REPAIRS)]:
        save(path, schema)
    instructions = prompt.split('\nINPUT:\n', 1)[0]
    inputs = {**context, 'chapter': curriculum.chapter_view(context['chapter']),
        'primary_lexical_reference': [e for e in read(LEXICAL_REFERENCE)['entries']
            if e['entry_id'] in context['word_requests']]}

    async def produce(job, issues):
        previous, lineage, selected = None, None, set(required)
        old_job = job.rsplit('-', 1)[0] + '-' + str(int(job.rsplit('-', 1)[1]) - 1)
        old_path = harness.run_dir / 'agents' / old_job
        repair_evidence = {}
        if issues and (old_path / 'result.json').exists() and (old_path / 'meta.json').exists():
            old_meta = read(old_path / 'meta.json')
            if old_meta.get('kind') == 'curriculum_assembly':
                previous = replay(harness.run_dir, old_meta)
                lineage = old_meta['batches']
                selection_job = job + '-repair-plan'
                selection = await harness.runner.call(selection_job, harness.policy
                    + '\nSelect exact curriculum identities affected by these reviewer objections. '
                    'Select every affected identity, including comparable occurrences; retain unrelated mappings. '
                    'Do not change bindings or invent identities. Output JSON only, no tools. '
                    + payload(required=required, previous=previous, issues=issues), repair_schema, 'medium', tool_profile='offline')
                validate(selection, REPAIRS)
                selected = {key(e) for e in selection['entries']}
                if not selected or not selected <= set(required):
                    raise ValueError('Invalid curriculum repair selection')
                repair_evidence = {'repair_plan_job': selection_job, 'repair_plan_digest': digest(selection)}

        async def batch(number, identities):
            identity_set = set(identities)
            if lineage is not None and not identity_set & selected:
                record = lineage[number]
                return read(harness.run_dir / 'agents' / record['job'] / 'result.json'), record
            old_bindings = [b for b in previous['bindings'] if key(b) in identity_set] if previous else []
            errors = issues
            for attempt in range(3):
                batch_job = f'{job}-bindings-{number:03d}-{attempt}'
                value = await harness.runner.call(batch_job, harness.policy + '\n' + instructions
                    + '\nThis job supplies ONLY the requested identities, not the chapter assessment. '
                    'Use the complete chapter context for meanings and optional grammar rationales. '
                    'Do not return other identities. Preserve previous bindings outside repair_identities exactly. '
                    'This is proposal generation: final whole-chapter computation and independent review remain required. '
                    + payload(**inputs, requested_identities=identities, repair_identities=sorted(identity_set & selected),
                        previous_bindings=old_bindings, issues=errors), batch_schema, 'medium', tool_profile='offline')
                try:
                    validate(value, BATCH)
                    if {key(b) for b in value['bindings']} != identity_set or len(value['bindings']) != len(identities):
                        raise ValueError('Curriculum batch must cover exactly its requested identities')
                    if any(b != next(n for n in value['bindings'] if key(n) == key(b))
                           for b in old_bindings if key(b) not in selected):
                        raise ValueError('Curriculum repair changed an unrelated retained binding')
                    fragment = {**context['chapter'], 'segments': [s for s in context['chapter']['segments']
                        if (s.get('lexical', {}).get('kind'), s.get('lexical', {}).get('id')) in identity_set],
                        'grammar_links': [g for g in context['chapter']['grammar_links'] if ('grammar', g['entry_id']) in identity_set]}
                    curriculum.evaluate_bindings(fragment, {**value, 'level_reason_en': 'Provisional binding batch.',
                        'prose_revision_reason_en': ''}, level=harness.level, max_extra_ratio=1.0)
                    return value, {'job': batch_job, 'digest': digest(value)}
                except (ValueError, ValidationError) as error:
                    errors = [str(error)]
            raise ValueError(f'Curriculum batch {number} failed validation: {errors}')

        results = await asyncio.gather(*(batch(i, group) for i, group in enumerate(groups)), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        values, records = zip(*results)
        provisional = combine(values, {'level_reason_en': 'Provisional complete bindings.', 'prose_revision_reason_en': ''})
        evaluation = curriculum.evaluate_bindings(context['chapter'], provisional, level=harness.level)
        summary_job = job + '-assessment'
        summary = await harness.runner.call(summary_job, harness.policy + '\n' + instructions
            + '\nAssess only whole-chapter learner suitability using these complete proposed bindings and computed grades. '
            'Do not regenerate bindings. Judge optional grammar frequency, variety, complexity and dependence honestly. '
            'No grammar quota or chapter length quota. Set concrete prose repairs only for real excessive difficulty. '
            'Output the two assessment fields only; no tools. '
            + payload(chapter=inputs['chapter'], bindings=provisional['bindings'], computed_evaluation=evaluation,
                target_level=harness.level, target_goals=context['target_goals']), summary_schema, 'high', tool_profile='offline')
        validate(summary, SUMMARY)
        value = combine(values, summary)
        save(harness.run_dir / 'agents' / job / 'result.json', value)
        save(harness.run_dir / 'agents' / job / 'meta.json', {'return_code': 0,
            'kind': 'curriculum_assembly', 'batches': list(records),
            'assessment_job': summary_job, 'assessment_digest': digest(summary), **repair_evidence})
        return value
    return produce


def replay(run_dir: Path, meta):
    from pipeline.korean_agent_harness import digest, read
    from pipeline.agent_harness import CodexRunner
    def verified(job, expected_digest, schema):
        if Path(job).name != job:
            raise ValueError('Invalid curriculum worker job')
        path = run_dir / 'agents' / job
        worker_meta, value = read(path / 'meta.json'), read(path / 'result.json')
        if worker_meta.get('return_code') != 0 or digest(value) != expected_digest:
            raise ValueError('Curriculum worker changed after review')
        CodexRunner._check_tool_profile(path, 'offline', worker_meta)
        validate(value, schema)
        return value
    values = [verified(row['job'], row['digest'], BATCH) for row in meta['batches']]
    summary = verified(meta['assessment_job'], meta['assessment_digest'], SUMMARY)
    if 'repair_plan_job' in meta:
        verified(meta['repair_plan_job'], meta['repair_plan_digest'], REPAIRS)
    return combine(values, summary)
