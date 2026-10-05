"""Inspectable worker inputs and local validation, without removing tool access."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
from jsonschema import ValidationError, validate
from pipeline.worker_paths import atomic_write_managed, checked_directory

VERSION = 'worker-workspace-v2'
RECEIPT_SCHEMA = {'type': 'object', 'properties': {'candidate_path': {'type': 'string', 'minLength': 1}},
                  'required': ['candidate_path'], 'additionalProperties': False}
ROOT = Path(__file__).resolve().parents[1]


class CandidateSubmissionError(ValueError):
    """A worker candidate was rejected by local submission validation."""

    def __init__(self, message, *, artifact_path=None, artifact_bytes=None,
                 category='candidate_submission'):
        super().__init__(message)
        self.artifact_path = artifact_path
        self.artifact_bytes = artifact_bytes
        self.category = category

    def record(self):
        return {'category': self.category, 'artifact_path': self.artifact_path,
                'diagnostic': str(self),
                'artifact_sha256': hash_bytes(self.artifact_bytes) if self.artifact_bytes is not None else None}


def hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _remove_obsolete_managed_inputs(path, previous_manifest, current_files):
    """Remove only unchanged, obsolete input assets named by the old manifest."""
    if path.is_symlink() or not isinstance(previous_manifest, dict):
        return
    old_files = previous_manifest.get('files')
    if not isinstance(old_files, dict):
        return
    root = path.resolve()
    for name, expected_hash in old_files.items():
        if (not isinstance(name, str) or name in current_files
                or not name.startswith(('inputs/', 'references/'))
                or '\\' in name or not isinstance(expected_hash, str)
                or re.fullmatch(r'[0-9a-f]{64}', expected_hash) is None):
            continue
        parts = name.split('/')
        if len(parts) < 2 or any(part in {'', '.', '..'} for part in parts):
            continue
        target = path.joinpath(*parts)
        current = path
        unsafe = False
        for part in parts:
            current = current / part
            if current.is_symlink():
                unsafe = True
                break
        if unsafe or not target.is_file():
            continue
        try:
            resolved = target.resolve(strict=True)
            if not resolved.is_relative_to(root):
                continue
            if hash_bytes(target.read_bytes()) != expected_hash:
                continue
            target.unlink()
        except (OSError, RuntimeError):
            # A changed or inaccessible workspace asset is user evidence.
            continue


def build(path, prompt, schema, *, context=None, submission_repair=None):
    path = checked_directory(Path(path).absolute(), create=True)
    previous_manifest = None
    previous_manifest_path = path / 'manifest.json'
    if previous_manifest_path.is_file() and not previous_manifest_path.is_symlink():
        try:
            previous_manifest = json.loads(previous_manifest_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            previous_manifest = None
    files = {}
    def put(name, content):
        if isinstance(content, bytes):
            encoded = content
        else:
            encoded = content.encode('utf-8')
        target = atomic_write_managed(path, name, encoded)
        files[name] = hash_bytes(target.read_bytes())
    def put_json(name, value):
        put(name, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    instructions, inventory, contexts = [], [], []
    seen_input_blocks = set()
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
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False)
        if identity in seen_input_blocks:
            continue
        seen_input_blocks.add(identity)
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
    instruction += '\n\nCURRENT WORKER POLICY: Tools are enabled. Earlier offline/no-tools clauses are obsolete and superseded. Use file inspection, scripts, local validation, web research and other available tools when useful. Preserve authoritative inputs; write drafts and scratch files in this workspace. New research is evidence for review, not permission to invent approved IDs or bypass publication checks. Repository source, validators, schemas, tests, approved dictionaries and run records are coordinator-owned. Do not edit them or change their permissions to make a draft pass. If validation exposes a pipeline defect, retain an exact reproducer and proposed fix in this workspace for the maintenance lane; do not apply it to shared files. Keep all draft and scratch changes inside this workspace. Write the requested JSON to candidate.json. Run the supplied validation command, inspect its output, and correct every reported error before submitting. Your final response is only {"candidate_path":"candidate.json"}; the runner consumes your actual file. This submission rule supersedes earlier instructions to repeat the full JSON in the final response.\n'
    if submission_repair is not None:
        rejected_bytes = submission_repair.get('artifact_bytes')
        rejected_name = 'rejected-candidate-attempt-01.json' if rejected_bytes is not None else None
        if rejected_name:
            put(rejected_name, rejected_bytes)
        put_json('submission-rejection.json', submission_repair['record'])
        prior_note = (f'inspect {rejected_name} as the exact prior artifact. '
                      if rejected_name else
                      'the prior candidate file was unavailable, so follow the file diagnostic. ')
        instruction += (
            '\n\nSUBMISSION REPAIR: The immediately preceding candidate was rejected by the '
            'local submission gate. Read submission-rejection.json for the exact diagnostic '
            'and ' + prior_note + 'Correct only the '
            'reported schema, file, or scoped patch validation errors; preserve source text, '
            'tap boundaries and all unimplicated content. Re-run the supplied local validation '
            'command before returning a new candidate. A successful submission is still only '
            'a validated proposal; independent review and publication gates remain required.\n'
        )
    put('TASK.txt', instruction)
    put_json('INDEX.json', inventory)
    put_json('schema.json', schema)
    put_json('receipt.schema.json', RECEIPT_SCHEMA)
    put_json('validation-context.json', [{k: v[k] for k in ('chunk_text', 'language', 'surfaces', 'annotation', 'annotation_validation', 'annotation_patch_validation', 'annotation_plan_validation', 'annotation_adjudication_validation', 'annotation_research_validation', 'annotation_research_claim_revision_validation', 'lexical_request_reconciliation_validation', 'dictionary_research_revision_validation', 'dictionary_objection_accountability_validation', 'annotation_issue_targets_validation', 'annotation_run_lesson_validation', 'annotation_run_knowledge_selection_validation') if k in v} for v in contexts if isinstance(v, dict)])
    command = 'cd ' + shlex.quote(str(ROOT)) + ' && ' + shlex.quote(str(ROOT / '.venv/bin/python')) + ' -m pipeline.worker_workspace validate --workspace ' + shlex.quote(str(path.resolve())) + ' --candidate ' + shlex.quote(str(path.resolve() / 'candidate.json'))
    put('README.txt', 'Read TASK.txt and INDEX.json. Repository root: ' + str(ROOT) + '. Repository paths in the task are relative to that root; input/reference paths in INDEX.json are relative to this workspace. Inspect relevant data and full reference entries as needed. Use your tools freely to investigate and verify. Save candidate.json and check it with:\n' + command + '\nThe local check is feedback, not independent publication approval. Submit only {"candidate_path":"candidate.json"}; do not reproduce the file contents.\n')
    _remove_obsolete_managed_inputs(path, previous_manifest, files)
    manifest = {'version': VERSION, 'files': files}
    atomic_write_managed(path, 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode('utf-8'))
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
    validation_contexts = json.loads((path / 'validation-context.json').read_text())
    input_values = {}
    index_path = path / 'INDEX.json'
    if index_path.exists():
        for row in json.loads(index_path.read_text(encoding='utf-8')):
            input_values[row['field']] = json.loads((path / row['path']).read_text(encoding='utf-8'))
    for context in validation_contexts:
        reconciliation = context.get('lexical_request_reconciliation_validation')
        if reconciliation is not None:
            from pipeline.lexical_request_reconciliation import authenticate_packet_origin, authenticate_archived_sources, check_result, check_review
            immutable = reconciliation['inputs']
            if any(input_values.get(key) != item for key, item in immutable.items()):
                raise ValueError('Reconciliation immutable worker inputs changed')
            authenticate_packet_origin(Path(reconciliation['origin_run_dir']), immutable)
            authenticate_archived_sources(immutable)
            if reconciliation['mode'] == 'resolver':
                check_result(value, immutable)
            elif reconciliation['mode'] == 'review':
                if input_values.get('reconciliation_result') != reconciliation['result']:
                    raise ValueError('Reconciliation result input changed')
                check_review(value, reconciliation['result'], immutable)
            else:
                raise ValueError('Unknown reconciliation stage')
        lesson_context = context.get('annotation_run_lesson_validation')
        if lesson_context is not None:
            from pipeline.annotation_run_lessons import validate_run_lessons, RunLessonError
            bound_lessons = validate_run_lessons(Path(lesson_context['run_dir']), lesson_context['envelope'],
                candidate=lesson_context['candidate'], source_text=lesson_context['source_text'],
                language=lesson_context['language'], representation=lesson_context['representation'],
                context=lesson_context['context'])
            if bound_lessons['lessons'] != lesson_context['lessons']:
                raise RunLessonError('Run lesson content differs from authenticated writer/critic proof')
            if ('reviewed_run_grammar' in input_values
                    and input_values['reviewed_run_grammar'] != bound_lessons['lessons']):
                raise RunLessonError('Explicit run grammar differs from authenticated lesson content')
        review_targets_context = context.get('annotation_issue_targets_validation')
        if review_targets_context is not None:
            from pipeline.annotation_issue_targets import validate_issue_targets
            validate_issue_targets(value, review_targets_context['candidate'],
                source_text=review_targets_context.get('source_text'),
                representation=review_targets_context.get('representation'),
                require_typed=review_targets_context.get('require_typed', True))
            continue
        adjudication_context = context.get('annotation_adjudication_validation')
        if adjudication_context is not None:
            input_field = adjudication_context.get('input_field')
            if not isinstance(input_field, str) or input_field not in input_values:
                raise ValueError('Adjudication validation input is missing from INDEX.json')
            from pipeline.annotation_adjudication import validate_adjudication_output
            validate_adjudication_output(value, input_values[input_field])
            continue
        objection_context=context.get('dictionary_objection_accountability_validation')
        if objection_context is not None:
            if not isinstance(objection_context,dict) or set(objection_context)!={'input_field'}:raise ValueError('Invalid dictionary objection gate')
            field=objection_context['input_field']
            if field!='dictionary_objection_review' or field not in input_values:raise ValueError('Dictionary objection input missing')
            from pipeline.dictionary_objection_accountability import validate_submission
            validate_submission(value,input_values[field])
            continue
        dictionary_context=context.get('dictionary_research_revision_validation')
        if dictionary_context is not None:
            field=dictionary_context['input_field']
            if field not in input_values:raise ValueError('Dictionary research revision input missing')
            from pipeline.dictionary_research_revision import validate_submission
            validate_submission(value,input_values[field])
            continue
        claim_context = context.get('annotation_research_claim_revision_validation')
        if claim_context is not None:
            field=claim_context['input_field']
            if field not in input_values:raise ValueError('Research claim revision input missing')
            from pipeline.annotation_research_claim_revision import validate_claim_revision
            validate_claim_revision(value,input_values[field],request_digest=claim_context['request_digest'],stage=claim_context['stage'])
            continue
        research_context = context.get('annotation_research_validation')
        if research_context is not None:
            input_field = research_context.get('input_field')
            if not isinstance(input_field, str) or input_field not in input_values:
                raise ValueError('Annotation research validation input is missing from INDEX.json')
            from pipeline.annotation_research import validate_research_submission
            validate_research_submission(value, input_values[input_field])
            continue
        selection_context=context.get('annotation_run_knowledge_selection_validation')
        if selection_context is not None:
            from pipeline.annotation_run_knowledge_selection import validate_selection
            if input_values.get('annotation_run_knowledge_selection')!=selection_context['inputs']:raise ValueError('Selection request differs from immutable workspace input')
            validate_selection(selection_context['inputs'],value)
            continue
        plan_context = context.get('annotation_plan_validation')
        if plan_context is not None:
            from pipeline.annotation_repairs import _validate_plan
            if plan_context.get('target_contract_version') == 2 and 'repair_dependency_constraints' not in input_values:
                raise ValueError('New repair plan contract requires typed immutable dependency constraints')
            if plan_context.get('target_contract_version') in (1, 2):
                _validate_plan(value, plan_context['issue_count'],
                               context.get('candidate', input_values.get('candidate')),
                               context.get('representation', input_values.get('representation')),
                               target_contract_version=plan_context['target_contract_version'],
                               dependency_constraints=input_values.get('repair_dependency_constraints')
                               if plan_context['target_contract_version'] == 2 else None)
            else:
                # Preserve replayability for workspace plans created before
                # operation/path feasibility was checked at submission time.
                _validate_plan(value, plan_context['issue_count'])
            continue
        patch_context = context.get('annotation_patch_validation')
        if patch_context is not None:
            if patch_context.get('request_binding_policy_version') == 3:
                from pipeline.annotation_edits import candidate_digest
                base = patch_context['base_candidate']
                expected = candidate_digest(base)
                schema = json.loads((path / 'schema.json').read_text())
                if (input_values.get('candidate') != base
                        or input_values.get('base_digest') != expected
                        or schema.get('properties', {}).get('base_digest', {}).get('const') != expected
                        or input_values.get('repair_plan') != patch_context['repair_plan']
                        or input_values.get('allowed_targets') != patch_context['allowed_targets']):
                    raise ValueError('Annotation patch request differs from canonical immutable workspace inputs')
            _check_annotation_patch(path, value, patch_context)
            continue
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


def _check_annotation_patch(workspace, patch, context):
    """Apply a scoped semantic patch and validate its derived annotation."""
    from pipeline.annotation_edits import apply_edits, validate_issue_target_coverage

    derived = apply_edits(context['base_candidate'], patch,
        allowed_targets=context['allowed_targets'], representation=context['representation'])
    if context.get('target_contract_version') == 1:
        validate_issue_target_coverage(context['base_candidate'], derived, patch['edits'],
            context['repair_plan'], representation=context['representation'])
    language = context['language']
    chunk_text = context.get('chunk_text')
    if not isinstance(chunk_text, str):
        raise ValueError('Annotation patch validation requires authoritative chunk_text')
    if language == 'ko':
        from pipeline import korean_contracts, korean_dictionary
        from pipeline.korean_agent_harness import validate_annotation_chunk

        validate(derived, korean_contracts.ANNOTATION)
        gate = dict(context['candidate_gate'])
        validate_annotation_chunk(derived, chunk_text,
            words=korean_dictionary._registry(korean_dictionary.WORDS),
            catalog=korean_contracts.lexical_catalog(), run_dir=workspace, **gate)
        return
    if language == 'zh':
        from pipeline.agent_harness import ChapterHarness

        schema = json.loads((ROOT / 'pipeline/schemas/annotation.schema.json').read_text(encoding='utf-8'))
        validate(derived, schema)
        issues = ChapterHarness.annotation_contract_issues(chunk_text, derived)
    elif language == 'ja':
        from pipeline.japanese_agent_harness import JapaneseChapterHarness

        schema = json.loads((ROOT / 'pipeline/schemas/japanese-annotation.schema.json').read_text(encoding='utf-8'))
        validate(derived, schema)
        issues = JapaneseChapterHarness.annotation_contract_issues(chunk_text, derived)
    else:
        raise ValueError(f'Unsupported annotation patch language: {language!r}')
    if issues:
        raise ValueError(json.dumps(issues, ensure_ascii=False))


def _schema_diagnostic(error):
    """Report the matching anyOf branch's problem, not a misleading sibling."""
    while error.parent is not None and error.parent.validator != 'anyOf':
        error = error.parent
    if error.parent is not None and error.parent.validator == 'anyOf':
        error = error.parent
    path = '.'.join(str(part) for part in error.absolute_path) or '<root>'
    branches = (error.schema.get('anyOf') if error.validator == 'anyOf' and isinstance(error.schema, dict)
                else error.schema)
    if error.validator == 'anyOf' and isinstance(error.instance, dict) and isinstance(branches, list):
        actual_type = error.instance.get('type')
        selected = next((index for index, branch in enumerate(branches)
                         if actual_type in branch.get('properties', {}).get('type', {}).get('enum', [])), None)
        if selected is not None:
            prefix = list(error.absolute_schema_path) + [selected]
            details = [child.message for child in error.context
                       if list(child.absolute_schema_path)[:len(prefix)] == prefix]
            if details:
                return (f'Candidate at {path} with type {actual_type!r} fails its matching '
                        f'schema branch: ' + '; '.join(details))
    return f'Candidate schema validation failed at {path}: {error.message}'


def submit(path, receipt):
    try:
        validate(receipt, RECEIPT_SCHEMA)
    except ValidationError as error:
        raise CandidateSubmissionError(
            f'Invalid submission receipt: {_schema_diagnostic(error)}',
            category='invalid_receipt') from error
    artifact_path = receipt['candidate_path']
    candidate = (path / artifact_path).resolve()
    if not candidate.is_relative_to(path.resolve()):
        raise CandidateSubmissionError('Submitted candidate must be in its worker workspace',
            artifact_path=artifact_path, category='unsafe_candidate_path')
    if not candidate.is_file():
        raise CandidateSubmissionError(f'Submitted candidate file does not exist: {artifact_path!r}. '
            'Write the JSON file in the task workspace, validate it, then submit its relative path.',
            artifact_path=artifact_path, category='missing_candidate')
    # Ordinary schema-valid proposals remain proposals; only explicit semantic
    # patch contexts also validate the derived annotation before returning the
    # patch. This preserves later independent review and publication gates.
    try:
        candidate_bytes = candidate.read_bytes()
    except OSError as error:
        raise CandidateSubmissionError(
            f'Could not read submitted candidate {artifact_path!r}: {error}',
            artifact_path=artifact_path, category='unreadable_candidate') from error
    try:
        value = json.loads(candidate_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CandidateSubmissionError(
            f'Could not parse submitted candidate {artifact_path!r}: {error}',
            artifact_path=artifact_path, artifact_bytes=candidate_bytes,
            category='unreadable_candidate') from error
    try:
        schema = json.loads((path / 'schema.json').read_text(encoding='utf-8'))
        validate(value, schema)
    except ValidationError as error:
        raise CandidateSubmissionError(_schema_diagnostic(error), artifact_path=artifact_path,
            artifact_bytes=candidate_bytes, category='schema_rejection') from error
    validation_contexts = json.loads((path / 'validation-context.json').read_text(encoding='utf-8'))
    plan_validation = next((context['annotation_plan_validation']
                            for context in validation_contexts
                            if context.get('annotation_plan_validation') is not None), None)
    patch_validation = any(context.get('annotation_patch_validation') is not None
                           for context in validation_contexts)
    adjudication_validation = any(context.get('annotation_adjudication_validation') is not None
                                  for context in validation_contexts)
    dictionary_revision_validation=any(context.get('dictionary_research_revision_validation') is not None for context in validation_contexts)
    claim_revision_validation = any(context.get('annotation_research_claim_revision_validation') is not None for context in validation_contexts)
    research_validation = any(context.get('annotation_research_validation') is not None
                              for context in validation_contexts)
    review_targets_validation = any(context.get('annotation_issue_targets_validation') is not None
                                    for context in validation_contexts)
    run_lesson_validation = any(context.get('annotation_run_lesson_validation') is not None
                                for context in validation_contexts)
    selection_validation=any(context.get('annotation_run_knowledge_selection_validation') is not None for context in validation_contexts)
    reconciliation_validation=any(context.get('lexical_request_reconciliation_validation') is not None for context in validation_contexts)
    if reconciliation_validation or dictionary_revision_validation or plan_validation is not None or patch_validation or adjudication_validation or research_validation or claim_revision_validation or review_targets_validation or run_lesson_validation or selection_validation:
        try:
            check(path, candidate)
        except (ValidationError, ValueError, KeyError, TypeError, IndexError) as error:
            from pipeline.annotation_edits import AnnotationEditError
            lesson_error = False
            if run_lesson_validation:
                from pipeline.annotation_run_lessons import RunLessonError
                lesson_error = isinstance(error, RunLessonError)
            category = ('lexical_request_reconciliation_rejection' if reconciliation_validation else 'dictionary_research_revision_rejection' if dictionary_revision_validation else
                        'annotation_run_knowledge_selection_rejection' if selection_validation else
                        'plan_contract_rejection' if plan_validation is not None else
                        'annotation_patch_contract_rejection'
                        if isinstance(error, AnnotationEditError) else
                        'annotation_adjudication_rejection'
                        if adjudication_validation else
                        'annotation_run_lesson_rejection'
                        if lesson_error else
                        'annotation_research_rejection'
                        if research_validation else
                        'annotation_issue_targets_rejection'
                        if review_targets_validation else
                        'derived_annotation_rejection')
            raise CandidateSubmissionError(
                f'Submitted dictionary revision failed authenticated scope validation: {error}'
                if dictionary_revision_validation else
                f'Submitted annotation plan failed issue coverage validation: {error}'
                if plan_validation is not None else
                f'Submitted annotation adjudication failed evidence validation: {error}'
                if adjudication_validation else
                f'Submitted targeted annotation research failed citation validation: {error}'
                if research_validation else
                f'Submitted run lesson inputs failed authenticated evidence validation: {error}'
                if lesson_error else
                f'Submitted annotation review failed target validation: {error}'
                if review_targets_validation else
                f'Submitted annotation patch failed derived-candidate validation: {error}',
                artifact_path=artifact_path, artifact_bytes=candidate_bytes,
                category=category) from error
    return value, {'artifact_path': str(candidate.relative_to(path.resolve())),
                   'artifact_digest': hash_bytes(candidate_bytes)}


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
