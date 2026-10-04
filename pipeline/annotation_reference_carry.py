"""Authenticated run research across derived candidates; never candidate approval.

The additive v1 envelope is absent from historical inputs. Descriptors point at
retained exact research/adjudication receipts, not worker state or registry edits.
"""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path

CARRY_FIELD = 'reviewed_annotation_research'
CARRY_VERSION = 1
CARRIED_RESEARCH_GUIDANCE = ('The coordinator authenticated the retained research and independent critic for the exact source position and compatible targets. These run facts are linguistic evidence, not candidate approval or promotion into the published registry. Compare their substantive claims and citations with the complete displayed form and exact current target; retain every new finding and any unresolved contribution. Prior verdicts are not linguistic proof.')
ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_FIELDS = {'meaning_en', 'context_en', 'display_meaning_en', 'explanation_en',
                   'definition_en', 'gloss', 'meaning', 'translation_en'}

class ResearchCarryError(ValueError):
    pass

def _digest(value):
    from pipeline.annotation_adjudication import digest
    return digest(value)

def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def _position(context, source_text=None):
    if not isinstance(context, dict):
        return None
    if 'annotation_source_position' in context:
        value = context['annotation_source_position']
        required = {'chunk_index', 'source_text_digest', 'parent_text_digest', 'source_start'}
        if (not isinstance(value, dict) or set(value) != required
                or any(type(value.get(key)) is not int or value[key] < 0 for key in ('chunk_index', 'source_start'))
                or any(not isinstance(value.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', value[key]) for key in ('source_text_digest', 'parent_text_digest'))
                or (source_text is not None and value['source_text_digest'] != _digest(source_text))):
            raise ResearchCarryError('Research carry requires a complete exact source position')
        parent = context.get('chapter_text')
        if isinstance(parent, str) and (_digest(parent) != value['parent_text_digest'] or (source_text is not None and parent[value['source_start']:value['source_start']+len(source_text)] != source_text)):
            raise ResearchCarryError('Research carry position differs from the available complete parent source')
        return value
    nested = context.get('chunk_review_context')
    if isinstance(nested, dict):
        return _position(nested, source_text)
    if 'source_start' in context or 'chapter_text' in context:
        start, parent = context.get('source_start'), context.get('chapter_text')
        if (type(start) is not int or start < 0 or not isinstance(parent, str)
                or (source_text is not None and parent[start:start + len(source_text)] != source_text)):
            raise ResearchCarryError('Research carry source position does not reconstruct the exact source')
        return {'source_start': start, 'parent_text_digest': _digest(parent),
                'source_text_digest': _digest(source_text)}
    return None

def _sources(carry):
    if carry is None:
        return []
    if not isinstance(carry, dict) or carry.get('version') != CARRY_VERSION:
        raise ResearchCarryError('Unknown research carry version')
    rows = carry.get('sources')
    if not isinstance(rows, list) or len(rows) > 3:
        raise ResearchCarryError('Research carry source bound exceeded')
    if len({_digest(row) for row in rows}) != len(rows):
        raise ResearchCarryError('Repeated research carry source')
    return rows

def carry_from_adjudication(run_dir, evidence, existing=None):
    """Return source descriptors; bind/replay them before exposing any fact."""
    sources = copy.deepcopy(_sources(existing))
    if isinstance(evidence, dict) and evidence.get('reference_research', {}).get('approved') is True:
        root = Path(run_dir).resolve(strict=True)
        if not root.is_relative_to(ROOT):
            raise ResearchCarryError('Research carry source escaped repository')
        descriptor = {'run_relpath': str(root.relative_to(ROOT)),
                      'adjudication_job': evidence.get('job'), 'receipt_digest': _digest(evidence)}
        if descriptor not in sources:
            sources.append(descriptor)
    result = {'version': CARRY_VERSION, 'sources': sources}
    _sources(result)
    return result

def _authenticate_impl(descriptor):
    if not isinstance(descriptor, dict) or set(descriptor) != {'run_relpath', 'adjudication_job', 'receipt_digest'}:
        raise ResearchCarryError('Malformed research carry source descriptor')
    relative = Path(descriptor['run_relpath'])
    job = descriptor['adjudication_job']
    if relative.is_absolute() or '..' in relative.parts or not isinstance(job, str) or Path(job).name != job:
        raise ResearchCarryError('Unsafe research carry source path')
    root = (ROOT / relative).resolve(strict=True)
    if not root.is_relative_to(ROOT):
        raise ResearchCarryError('Research carry source escaped repository')
    path = root/'agents'/job/'reference-research-adjudication.json'
    if path.is_symlink():
        raise ResearchCarryError('Research carry receipt cannot be a symlink')
    evidence = _read(path)
    if _digest(evidence) != descriptor['receipt_digest'] or evidence.get('job') != job:
        raise ResearchCarryError('Research carry receipt changed')
    original = evidence.get('initial_adjudication')
    if not isinstance(original, dict):
        raise ResearchCarryError('Research carry lacks original immutable adjudication')
    original_job = original.get('job')
    if not isinstance(original_job, str) or Path(original_job).name != original_job:
        raise ResearchCarryError('Unsafe original research adjudication job')
    agents = (root/'agents').resolve(strict=True)
    if not (root/'agents'/original_job).resolve(strict=True).is_relative_to(agents):
        raise ResearchCarryError('Original research job escaped its evidence root')
    inputs = _read(root/'agents'/original_job/'adjudication-input.json')
    keys = ('language', 'representation', 'candidate', 'current_review', 'prior_history',
            'context', 'known_reference_input', 'deterministic_gate_evidence',
            'normal_review_receipt', 'source_text')
    kwargs = {key: inputs[key] for key in keys}
    from pipeline.annotation_adjudication import verify_adjudication_evidence
    verified = verify_adjudication_evidence(root, evidence, **kwargs)
    research = verified['reference_research']
    if research.get('approved') is not True or not research.get('references'):
        raise ResearchCarryError('Research carry source lacks independently approved facts')
    return inputs, research

def _authenticate(descriptor):
    try:
        return _authenticate_impl(descriptor)
    except ResearchCarryError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ResearchCarryError('Research carry original receipt or immutable inputs do not authenticate') from exc


def _tap_geometry(value):
    """Conservatively preserve every tap/overlay identity and source boundary.

    A repeated spelling at the same array index is not the same source interval
    after a preceding merge. Comparing the complete structural geometry avoids
    inferring offsets from language-specific surface fields. English semantic
    values may change; source strings, identities, forms, ranges and ordering do
    not. Boundary repair therefore requires separately re-established evidence.
    """
    if isinstance(value, dict):
        return {key: _tap_geometry(item) for key, item in value.items()
                if key not in SEMANTIC_FIELDS}
    if isinstance(value, list):
        return [_tap_geometry(item) for item in value]
    return value


def _masked_record(candidate, paths):
    from pipeline.annotation_review_ledger import resolve_pointer
    roots = sorted({'/'+'/'.join(path.split('/')[1:3]) for path in paths})
    records = {root: copy.deepcopy(resolve_pointer(candidate, root)) for root in roots}
    for path in paths:
        parts = path.split('/')[1:]
        root = '/'+ '/'.join(parts[:2])
        if parts[-1] not in SEMANTIC_FIELDS:
            continue  # Structural targets never acquire semantic-change permission.
        current = records[root]
        for part in parts[2:-1]:
            current = current[int(part)] if isinstance(current, list) else current[part]
        last = parts[-1]
        if isinstance(current, list): current[int(last)] = {'_carried_semantic_value': True}
        else: current[last] = {'_carried_semantic_value': True}
    return records

def bind_carried_research(run_dir, carry, *, candidate, source_text, language,
                          representation, context, current_review=None):
    """Authenticate facts and bind exact compatible targets for this candidate.

    ``packet`` is independent of the current reviewer wording. References are
    rebound only to current issues naming a compatible canonical target. Facts
    on unchanged source/identity/geometry remain evidence after semantic repair;
    they cannot authorize another field or approve the annotation.
    """
    _ = run_dir  # Source descriptors retain their original evidence roots.
    sources = copy.deepcopy(_sources(carry))
    facts = []
    position = _position(context, source_text)
    from pipeline.annotation_adjudication import normalize_review
    from pipeline.annotation_issue_targets import issue_target_paths
    normalized = normalize_review(language, current_review) if current_review is not None else None
    current_issues = []
    if normalized:
        for row in normalized['issues']:
            paths = issue_target_paths(row['issue'], candidate, source_text=source_text, representation=representation)
            current_issues.append((row['issue_id'], paths or []))
    references = {}
    for descriptor in sources:
        original, research = _authenticate(descriptor)
        original_position = _position(original['context'], original['source_text'])
        if (original['language'] != language or original['representation'] != representation
                or original['source_text'] != source_text or position is None
                or original_position is None or original_position != position):
            continue
        if _tap_geometry(original['candidate']) != _tap_geometry(candidate):
            continue
        issues = {row['issue_id']: row['issue'] for row in normalize_review(language, original['current_review'])['issues']}
        classifications = {row['issue_id']: row for row in original['initial_adjudication']['classifications']} if 'initial_adjudication' in original else {}
        # Research inputs retain the exact initial classifications, including legacy scope.
        research_inputs = research.get('inputs', {})
        classifications.update({row['issue_id']: row for row in research_inputs.get('initial_adjudication', {}).get('classifications', [])})
        for identity, reference in research['references'].items():
            content = reference['content']
            paths = set()
            for issue_id in content.get('issue_ids', reference.get('issue_ids', [])):
                issue = issues.get(issue_id)
                canonical = issue_target_paths(issue, original['candidate'], source_text=source_text, representation=representation) if issue is not None else None
                paths.update(canonical if canonical is not None else classifications.get(issue_id, {}).get('candidate_paths', []))
            paths = sorted(paths)
            if not paths:
                continue
            try:
                if _masked_record(original['candidate'], paths) != _masked_record(candidate, paths):
                    continue
            except (ValueError, TypeError, KeyError, IndexError):
                continue
            facts.append({'source': descriptor, 'original_reference_id': identity,
                          'candidate_paths': paths, 'reference': copy.deepcopy(reference)})
            applicable = [issue_id for issue_id, targets in current_issues if targets and set(targets) <= set(paths)]
            if not applicable:
                continue
            new_id = 'carried-research-' + _digest([descriptor, identity])[:24]
            new_content = copy.deepcopy(content)
            new_content.update(_annotation_research_fact=True, issue_ids=applicable,
                               carried_research_version=CARRY_VERSION,
                               carried_source=descriptor, carried_candidate_paths=paths)
            references[new_id] = {'kind': 'approved_lesson', 'content': new_content, 'issue_ids': applicable}
    packet = {'version': CARRY_VERSION, 'sources': sources, 'facts': facts,
              'scope': 'Authenticated run research for exact compatible source positions and targets. Facts are linguistic evidence, never candidate approval or published registry promotion.'} if facts else {}
    if isinstance(carry, dict) and 'facts' in carry and carry != packet:
        raise ResearchCarryError('Research carry packet differs from authenticated compatible facts')
    return {'packet': packet, 'references': references}


def _manifest_scope(*, language, representation, source_text, context):
    position = _position(context, source_text)
    if position is None:
        return None
    return {'language': language, 'representation': representation,
            'source_text_digest': _digest(source_text), 'position': position}


def _register_carried_research_locked(run_dir, carry, *, candidate, source_text, language,
                              representation, context):
    """Register named authenticated provenance for exact-position resume only.

    Publication replays the selected immutable packet, never this mutable index.
    Missing compatibility registers nothing and never becomes an approval.
    """
    scope = _manifest_scope(language=language, representation=representation,
                            source_text=source_text, context=context)
    bound = bind_carried_research(run_dir, carry, candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=context)
    if scope is None or not bound['packet']:
        return None
    from pipeline.worker_paths import checked_directory, checked_regular_file
    root = checked_directory(Path(run_dir).absolute(), create=True)
    directory = checked_directory(root/'annotation-research', create=True)
    path = directory/'run-carry-sources.json'
    document = _read(checked_regular_file(path)) if path.exists() else {'version': 1, 'scopes': {}}
    if document.get('version') != 1 or not isinstance(document.get('scopes'), dict):
        raise ResearchCarryError('Malformed research carry resume index')
    key = _digest(scope)
    row = document['scopes'].get(key)
    if row is not None and row.get('scope') != scope:
        raise ResearchCarryError('Research carry resume scope changed')
    sources = list(row['carry']['sources']) if row else []
    for descriptor in _sources(carry):
        if descriptor not in sources:
            sources.append(descriptor)
    envelope = {'version': 1, 'sources': sources}
    _sources(envelope)
    document['scopes'][key] = {'scope': scope, 'carry': envelope}
    from pipeline.annotation_research import _write_json
    _write_json(path, document)
    return envelope


def load_carried_research(run_dir, *, candidate, source_text, language,
                          representation, context):
    """Load/revalidate only the exact registered source position, no job search."""
    scope = _manifest_scope(language=language, representation=representation,
                            source_text=source_text, context=context)
    root = Path(run_dir).absolute()
    path = root/'annotation-research'/'run-carry-sources.json'
    if scope is None or not path.exists():
        return None
    from pipeline.worker_paths import checked_regular_file, checked_directory
    checked_directory(root); checked_directory(path.parent)
    document = _read(checked_regular_file(path))
    if document.get('version') != 1 or not isinstance(document.get('scopes'), dict):
        raise ResearchCarryError('Malformed research carry resume index')
    row = document['scopes'].get(_digest(scope))
    if row is None:
        return None
    if not isinstance(row, dict) or row.get('scope') != scope:
        raise ResearchCarryError('Research carry resume position changed')
    carry = row.get('carry')
    bound = bind_carried_research(run_dir, carry, candidate=candidate, source_text=source_text,
        language=language, representation=representation, context=context)
    return copy.deepcopy(carry) if bound['packet'] else None


def register_carried_research(run_dir, carry, *, candidate, source_text, language,
                              representation, context):
    """Serialize descriptor-index merges across concurrent chunk coordinators."""
    import fcntl
    import os
    import stat
    from pipeline.worker_paths import checked_directory
    root = checked_directory(Path(run_dir).absolute(), create=True)
    directory = checked_directory(root/'annotation-research', create=True)
    lock_path = directory/'.run-carry-sources.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(fd)
        observed = lock_path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
                or (metadata.st_dev, metadata.st_ino) != (observed.st_dev, observed.st_ino)):
            raise ResearchCarryError('Research carry lock must be an owned single-link regular file')
        fcntl.flock(fd, fcntl.LOCK_EX)
        return _register_carried_research_locked(run_dir, carry, candidate=candidate,
            source_text=source_text, language=language, representation=representation,
            context=context)
    finally:
        os.close(fd)

# Scoped to the additive carry contract, so historical and unrelated cached
# reviewer prompts keep their exact fingerprints. This restates source-span
# representation invariants; it does not introduce a new linguistic criterion.
SOURCE_SPAN_REVIEW_GUIDANCE = '''Before objecting to an attachment, inspect the exact complete recorded display form and source range. A published first-segment index identifies the beginning of that complete span; it need not be the tap containing the grammatical ending. An included attachment is valid when the representation contract permits it and the complete recorded range preserves the intended occurrence. Do not quote only the ending-bearing tap as though it were the whole displayed construction. Distinguish an actual outside-span attachment, incorrect range, or omitted contribution from a valid complete-span record. Supporting source taps are context, not additional defect targets.'''
CARRIED_RESEARCH_GUIDANCE += '\n' + SOURCE_SPAN_REVIEW_GUIDANCE
