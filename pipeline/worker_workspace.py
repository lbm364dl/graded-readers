"""Inspectable worker inputs and local validation, without removing tool access."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
from jsonschema import validate

VERSION = 'worker-workspace-v2'
RECEIPT_SCHEMA = {'type': 'object', 'properties': {'candidate_path': {'type': 'string', 'minLength': 1}},
                  'required': ['candidate_path'], 'additionalProperties': False}
ROOT = Path(__file__).resolve().parents[1]


def hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def build(path, prompt, schema, *, context=None):
    path.mkdir(parents=True, exist_ok=True)
    files = {}
    def put(name, content):
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        files[name] = hash_bytes(target.read_bytes())
    def put_json(name, value):
        put(name, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    instructions, inventory, contexts = [], [], []
    pieces = prompt.split('\nINPUT:\n')
    if context is not None:
        pieces.append(json.dumps(context, ensure_ascii=False))
    instructions.append(pieces[0])
    for number, piece in enumerate(pieces[1:], 1):
        stripped = piece.lstrip()
        try:
            value, end = json.JSONDecoder().raw_decode(stripped)
        except ValueError:
            instructions.append('\nINPUT:\n' + piece)
            continue
        instructions.append(stripped[end:])
        contexts.append(value)
        fields = value if isinstance(value, dict) else {'value': value}
        for index, (key, item) in enumerate(fields.items()):
            stem = re.sub(r'[^\w.-]', '_', str(key))[:90]
            name = f'inputs/{number:02d}-{index:02d}-{stem}.json'
            entry = {'field': key, 'input_block': number, 'path': name}
            # Keep full records independently readable; the index retains identity
            # and labels, never manufactured definitions or inferred matches.
            rows = None
            if isinstance(item, dict) and item.get('format') == 'lossless_reference_rows':
                if len(set(item['columns'])) != len(item['columns']) or any(len(row) != len(item['columns']) for row in item['rows']):
                    raise ValueError('Malformed reference rows')
                rows = [dict(zip(item['columns'], row)) for row in item['rows']]
            if rows is not None and all(isinstance(row, dict) and 'id' in row for row in rows):
                refs = []
                for row_index, row in enumerate(rows):
                    record_path = f'references/{number:02d}-{index:02d}-{stem}/{row_index:05d}.json'
                    put_json(record_path, row)
                    refs.append({**{k: row[k] for k in ('id', 'headword', 'title', 'name', 'pattern', 'kind') if k in row}, 'path': record_path})
                put_json(name, refs)
                entry['record_count'] = len(refs)
                entry['description'] = 'Index of complete per-entry reference files'
            else:
                put_json(name, item)
            inventory.append(entry)
    # Historical source prompts are retained for cache replay; this explicit
    # current instruction supersedes obsolete tool bans inside task text.
    instruction = '\n'.join(instructions)
    instruction = re.sub(r'(?m)^Offline drafting, annotation and review roles.*(?:\n|$)', '', instruction)
    for obsolete, current in (('JSON only; no tools.', 'JSON only.'),
                              ('JSON only, no tools.', 'JSON only.'),
                              ('JSON only and do not call tools.', 'JSON only.'),
                              ('Do not call any tools, including resource listing.', ''),
                              ('No tools.', ''), ('no tools.', '')):
        instruction = instruction.replace(obsolete, current)
    instruction += '\n\nCURRENT WORKER POLICY: Tools are enabled. Earlier offline/no-tools clauses are obsolete and superseded. Use file inspection, scripts, local validation, web research and other available tools when useful. Preserve authoritative inputs; write drafts and scratch files in this workspace. New research is evidence for review, not permission to invent approved IDs or bypass publication checks. Write the requested JSON to candidate.json. Run the supplied validation command, inspect its output, and correct every reported error before submitting. Your final response is only {"candidate_path":"candidate.json"}; the runner consumes your actual file. This submission rule supersedes earlier instructions to repeat the full JSON in the final response.\n'
    put('TASK.txt', instruction)
    put_json('INDEX.json', inventory)
    put_json('schema.json', schema)
    put_json('receipt.schema.json', RECEIPT_SCHEMA)
    put_json('validation-context.json', [{k: v[k] for k in ('chunk_text', 'language', 'surfaces', 'annotation', 'annotation_validation') if k in v} for v in contexts if isinstance(v, dict)])
    command = 'cd ' + shlex.quote(str(ROOT)) + ' && ' + shlex.quote(str(ROOT / '.venv/bin/python')) + ' -m pipeline.worker_workspace validate --workspace ' + shlex.quote(str(path.resolve())) + ' --candidate ' + shlex.quote(str(path.resolve() / 'candidate.json'))
    put('README.txt', 'Read TASK.txt and INDEX.json. Repository root: ' + str(ROOT) + '. Repository paths in the task are relative to that root; input/reference paths in INDEX.json are relative to this workspace. Inspect relevant data and full reference entries as needed. Use your tools freely to investigate and verify. Save candidate.json and check it with:\n' + command + '\nThe local check is feedback, not independent publication approval. Submit only {"candidate_path":"candidate.json"}; do not reproduce the file contents.\n')
    manifest = {'version': VERSION, 'files': files}
    (path / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return ('Your task and organized inputs are in this working directory. Read README.txt, TASK.txt and INDEX.json. '
            'Use tools to inspect references, investigate and test your work. Write candidate.json matching schema.json; return only {"candidate_path":"candidate.json"}.\n'), hash_bytes((path / 'manifest.json').read_bytes())


def verify(path, expected):
    manifest_path = path / 'manifest.json'
    if hash_bytes(manifest_path.read_bytes()) != expected:
        raise ValueError('Worker workspace manifest changed')
    manifest = json.loads(manifest_path.read_text())
    for name, expected_hash in manifest['files'].items():
        target = (path / name).resolve()
        if not target.is_relative_to(path.resolve()) or hash_bytes(target.read_bytes()) != expected_hash:
            raise ValueError(f'Worker input changed: {name}')


def check(path, candidate):
    value = json.loads(candidate.read_text())
    validate(value, json.loads((path / 'schema.json').read_text()))
    for context in json.loads((path / 'validation-context.json').read_text()):
        if context.get('annotation_validation', {}).get('language') == 'ko':
            from pipeline.korean_agent_harness import validate_annotation_chunk
            from pipeline.korean_annotation_chunks import decode
            from pipeline import korean_contracts, korean_dictionary
            args = dict(context['annotation_validation'])
            args.pop('language')
            text = args.pop('chunk_text')
            validate_annotation_chunk(decode(value, source_text=text), text,
                words=korean_dictionary._registry(korean_dictionary.WORDS),
                catalog=korean_contracts.lexical_catalog(), run_dir=path, **args)
            continue
        if context.get('language') == 'zh-fixed':
            from pipeline.fixed_boundary_annotation import validate_result
            validate_result(context['chunk_text'], context['surfaces'], value)
            continue
        if context.get('language') == 'zh-patch':
            from pipeline.fixed_boundary_annotation import apply_correction_patches
            apply_correction_patches(context['chunk_text'], context['annotation'], value)
            continue
        if 'chunk_text' in context and isinstance(value, dict) and 'segments' in value:
            if context.get('language') in ('zh', 'ja'):
                if context['language'] == 'zh':
                    from pipeline.agent_harness import ChapterHarness
                    issues = ChapterHarness.annotation_contract_issues(context['chunk_text'], value)
                else:
                    from pipeline.japanese_agent_harness import JapaneseChapterHarness
                    issues = JapaneseChapterHarness.annotation_contract_issues(context['chunk_text'], value)
                if issues:
                    raise ValueError(json.dumps(issues, ensure_ascii=False))
                continue
            from pipeline.korean_annotation_chunks import decode, FORMATS
            if value.get('format') in FORMATS:
                from pipeline.korean_contracts import check_reconstruction
                decoded = decode(value, source_text=context['chunk_text'])
                check_reconstruction(decoded['segments'], context['chunk_text'])
                for segment in decoded['segments']:
                    if segment['type'] == 'punctuation' and any(c.isalnum() for c in segment['text']):
                        raise ValueError('Words cannot hide in punctuation')
    return value


def submit(path, receipt):
    validate(receipt, RECEIPT_SCHEMA)
    candidate = (path / receipt['candidate_path']).resolve()
    if not candidate.is_relative_to(path.resolve()):
        raise ValueError('Submitted candidate must be in its worker workspace')
    if not candidate.is_file():
        raise ValueError(f'Submitted candidate file does not exist: {receipt["candidate_path"]!r}. '
                         'Write the JSON file in the task workspace, validate it, then submit its relative path.')
    # The worker can run the complete stage gate through `check`. Submission
    # preserves a schema-valid proposal; the owning harness runs its gate and
    # routes typed failures to the appropriate research/repair workflow.
    # Treating a missing lexical identity as a generic runner failure would
    # bypass that workflow and repeat an impossible annotation repair.
    value = json.loads(candidate.read_text())
    validate(value, json.loads((path / 'schema.json').read_text()))
    return value, {'artifact_path': str(candidate.relative_to(path.resolve())),
                   'artifact_digest': hash_bytes(candidate.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate'])
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    check(args.workspace, args.candidate)
    print('Schema and applicable source/link checks passed. Independent review is still required.')


if __name__ == '__main__':
    main()
