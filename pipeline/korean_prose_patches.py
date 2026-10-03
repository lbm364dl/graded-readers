"""Explicit prose replacements that preserve all untouched draft text."""
from pathlib import Path
import re
from jsonschema import validate

from pipeline import korean_contracts as contracts

SCHEMA = contracts.obj({
    'title': contracts.NONEMPTY,
    'length_reason_en': contracts.NONEMPTY,
    'edits': {'type': 'array', 'items': contracts.obj({
        'source_start': {'type': 'integer', 'minimum': 0},
        'source_end': {'type': 'integer', 'minimum': 0},
        'original_text': contracts.STRING,
        'replacement_text': contracts.STRING,
        'reason_en': contracts.NONEMPTY,
    })},
})


def apply_patch(previous, patch):
    """Apply verified, ordered edits; insertion/deletion are explicit choices."""
    validate(previous, contracts.PROSE)
    validate(patch, SCHEMA)
    source = previous['text']
    cursor, pieces, last_start = 0, [], -1
    for edit in patch['edits']:
        start, end = edit['source_start'], edit['source_end']
        if start < cursor or start <= last_start or end < start or end > len(source):
            raise ValueError('Korean prose edits must be ordered, disjoint and within the original draft')
        if source[start:end] != edit['original_text']:
            raise ValueError('Korean prose patch original_text differs from its exact original source span')
        if edit['original_text'] == edit['replacement_text']:
            raise ValueError('Korean prose patch must not include unchanged passages')
        pieces.extend((source[cursor:start], edit['replacement_text']))
        cursor, last_start = end, start
    pieces.append(source[cursor:])
    result = {'title': patch['title'], 'text': ''.join(pieces),
              'length_reason_en': patch['length_reason_en']}
    validate(result, contracts.PROSE)
    return result


async def produce(harness, job, previous, prompt, issues):
    from pipeline.korean_agent_harness import digest, payload, save
    schema_path = harness.run_dir / 'prose-patch.schema.json'
    save(schema_path, SCHEMA)
    patch_job = job + '-patch'
    patch = await harness.runner.call(patch_job, harness.policy + '\n' + prompt
        + '\nReturn targeted prose edits, not a regenerated chapter. Each edit must identify '
          'its exact original Unicode source_start/source_end and original_text. Replace only '
          'the smallest coherent span needed to resolve an issue, including nearby morphology '
          'when a word replacement requires it. Insert missing events or delete unsupported '
          'events explicitly. Preserve every unaffected passage. Use offsets from the ORIGINAL '
          'previous draft for all edits, ordered and non-overlapping. Return title and '
          'length_reason_en unchanged unless the reviewed issue requires their revision. '
          'source_runs are indexed copying units, not mandated edit boundaries.\n'
        + payload(previous=previous, issues=issues,
            source_runs=[[m.start(), m.end(), m.group()] for m in re.finditer(r'\s+|\S+', previous['text'])]),
        schema_path, 'low', tool_profile='offline')
    result = apply_patch(previous, patch)
    save(harness.run_dir / 'agents' / job / 'result.json', result)
    save(harness.run_dir / 'agents' / job / 'meta.json', {
        'return_code': 0, 'kind': 'prose_patch_assembly',
        'base': previous, 'base_digest': digest(previous),
        'patch_job': patch_job, 'patch_digest': digest(patch)})
    return result


def replay(run_dir, meta):
    from pipeline.korean_agent_harness import digest, read
    from pipeline.agent_harness import CodexRunner
    job = meta['patch_job']
    if Path(job).name != job:
        raise ValueError('Invalid Korean prose patch worker job')
    path = run_dir / 'agents' / job
    worker_meta, patch = read(path / 'meta.json'), read(path / 'result.json')
    if (worker_meta.get('return_code') != 0 or digest(patch) != meta['patch_digest']
            or digest(meta['base']) != meta['base_digest']):
        raise ValueError('Korean prose patch evidence changed after review')
    CodexRunner._check_tool_profile(path, 'offline', worker_meta)
    return apply_patch(meta['base'], patch)
