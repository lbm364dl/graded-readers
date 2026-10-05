"""Bound new dictionary drafting; retain the whole-delta independent review."""
import asyncio
from pathlib import Path

from jsonschema import validate, ValidationError
from pipeline import korean_contracts as contracts

REPAIRS = contracts.obj({'entries': {'type': 'array', 'items': contracts.obj({
    'kind': {'type': 'string', 'enum': ['word', 'grammar']}, 'entry_id': contracts.NONEMPTY})}})


def entries(value):
    return {(kind, entry['id']): entry for kind, field in [('word', 'words'), ('grammar', 'grammar')]
            for entry in value[field]}


def combine(values):
    result = {field: [entry for value in values for entry in value[field]] for field in ['words', 'grammar']}
    if len(entries(result)) != len(result['words']) + len(result['grammar']):
        raise ValueError('Duplicate entry across dictionary batches')
    return result


def producer(harness, prompt, context, *, batch_size=24):
    from pipeline.korean_agent_harness import digest, payload, read, save, validate_delta
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('Dictionary batch size must be positive')
    required = sorted([('word', identity) for identity in context['word_requests']]
                      + [('grammar', identity) for identity in context['grammar_requests']])
    from pipeline.annotation_dictionary_handoff import replay_handoff, DictionaryHandoffError
    handoff = context.get('reviewed_dictionary_handoff')
    def imported_entries():
        if handoff is None: return []
        retained = replay_handoff(harness.run_dir, handoff, language='ko',
            chapter_text=context['chapter']['text'], required_ids=set(context['grammar_requests']), published=harness.grammar)
        for entry in retained: validate(entry, contracts.DICTIONARY['properties']['grammar']['items'])
        return retained
    imported = imported_entries()
    retained_ids = {('grammar', entry['id']) for entry in imported}
    drafted = [row for row in required if row not in retained_ids]
    groups = [drafted[i:i + batch_size] for i in range(0, len(drafted), batch_size)]
    repair_schema = harness.run_dir / 'dictionary-repairs.schema.json'
    save(repair_schema, REPAIRS)
    instructions = prompt.split('\nINPUT:\n', 1)[0]

    async def produce(job, issues):
        previous, lineage, selected = None, None, set(required)
        evidence = {}
        prefix, attempt = job.rsplit('-', 1)
        old_job = f'{prefix}-{int(attempt) - 1}'
        old_path = harness.run_dir / 'agents' / old_job / 'meta.json'
        if issues and int(attempt) > 0 and old_path.exists():
            meta = read(old_path)
            if meta.get('kind') == 'dictionary_assembly':
                previous = replay(harness.run_dir, meta)
                if set(entries(previous)) != set(required):
                    raise ValueError('Dictionary repair requests differ from the original delta')
                lineage = meta['batches']
                selection_job = job + '-repair-plan'
                selection = await harness.runner.call(selection_job, harness.policy
                    + '\nSelect every new dictionary identity affected by the independent review issues, '
                    'including comparable entries. Preserve unrelated definitions. Do not draft entries here. '
                    'Output JSON only; no tools. '
                    + payload(required=required, previous=previous, issues=issues),
                    repair_schema, 'low', tool_profile='offline')
                validate(selection, REPAIRS)
                selected = {(entry['kind'], entry['entry_id']) for entry in selection['entries']}
                if not selected or not selected <= set(required):
                    raise ValueError('Invalid dictionary repair selection')
                if selected & retained_ids:
                    raise DictionaryHandoffError('Imported entry needs explicit independently reviewed source revision before another dictionary attempt')
                evidence = {'repair_plan_job': selection_job, 'repair_plan_digest': digest(selection)}

        async def batch(number, identities):
            identity_set = set(identities)
            if lineage is not None and not identity_set & selected:
                row = lineage[number]
                return read(harness.run_dir / 'agents' / row['job'] / 'result.json'), row
            word_requests = {identity: context['word_requests'][identity]
                             for kind, identity in identities if kind == 'word'}
            grammar_requests = {identity for kind, identity in identities if kind == 'grammar'}
            inputs = {**context, 'word_requests': word_requests, 'grammar_requests': sorted(grammar_requests)}
            from pipeline.korean_lexical_research import chapter_usage_evidence
            usages = chapter_usage_evidence(context['chapter'], word_requests)
            old_entries = {key: entry for key, entry in entries(previous).items() if key in identity_set} if previous else {}
            errors = issues
            for attempt in range(3):
                batch_job = f'{job}-entries-{number:03d}-{attempt}'
                value = await harness.runner.call(batch_job, harness.policy + '\n' + instructions
                    + '\nDraft only this batch of new reusable dictionary entries. Include each requested '
                    'identity once; return no approved or unrelated entries. Use observed contexts to cover '
                    'verified senses while keeping definitions independent of any passage. Preserve entries '
                    'outside repair_identities exactly. Final whole-delta independent review remains required. '
                    + payload(**inputs, repair_identities=sorted(identity_set & selected),
                        previous_entries=list(old_entries.values()), issues=errors,
                        **({'reviewed_lexical_usage_evidence': usages} if usages else {})),
                    contracts.schema_path('dictionary'), 'low', tool_profile='offline')
                try:
                    validate_delta(value, word_requests, grammar_requests, {}, {})
                    current = entries(value)
                    if any(current[key] != entry for key, entry in old_entries.items() if key not in selected):
                        raise ValueError('Dictionary repair changed an unrelated retained entry')
                    return value, {'job': batch_job, 'digest': digest(value)}
                except (ValueError, ValidationError) as error:
                    errors = [str(error)]
            raise ValueError(f'Dictionary batch {number} failed validation: {errors}')

        results = await asyncio.gather(*(batch(i, group) for i, group in enumerate(groups)), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        values, rows = zip(*results) if results else ((), ())
        retained = imported_entries()
        result = combine([*values, {'words': [], 'grammar': retained}])
        validate_delta(result, context['word_requests'], set(context['grammar_requests']), {}, {})
        save(harness.run_dir / 'agents' / job / 'result.json', result)
        save(harness.run_dir / 'agents' / job / 'meta.json', {
            'return_code': 0, 'kind': 'dictionary_assembly', 'batches': list(rows),
            **({'dictionary_imports_version': 1, 'reviewed_dictionary_handoff': handoff,
                'dictionary_import_scope': {'language': 'ko', 'chapter_text': context['chapter']['text'],
                    'required_ids': sorted(context['grammar_requests']), 'published': harness.grammar}}
                if handoff else {}), **evidence})
        return result
    return produce


def replay(run_dir: Path, meta):
    from pipeline.korean_agent_harness import digest, read
    from pipeline.agent_harness import CodexRunner
    def verified(job, expected, schema):
        if Path(job).name != job:
            raise ValueError('Invalid dictionary worker job')
        path = run_dir / 'agents' / job
        worker_meta, value = read(path / 'meta.json'), read(path / 'result.json')
        if worker_meta.get('return_code') != 0 or digest(value) != expected:
            raise ValueError('Dictionary worker changed after review')
        CodexRunner._check_tool_profile(path, 'offline', worker_meta)
        validate(value, schema)
        return value
    values = [verified(row['job'], row['digest'], contracts.DICTIONARY) for row in meta['batches']]
    if 'repair_plan_job' in meta:
        verified(meta['repair_plan_job'], meta['repair_plan_digest'], REPAIRS)
    if meta.get('dictionary_imports_version') is not None:
        if type(meta['dictionary_imports_version']) is not int or meta['dictionary_imports_version'] != 1: raise ValueError('Unsupported dictionary imports version')
        from pipeline.annotation_dictionary_handoff import replay_handoff
        scope = meta['dictionary_import_scope']
        retained = replay_handoff(run_dir, meta['reviewed_dictionary_handoff'], **scope)
        for entry in retained: validate(entry, contracts.DICTIONARY['properties']['grammar']['items'])
        values.append({'words': [], 'grammar': retained})
    return combine(values)
