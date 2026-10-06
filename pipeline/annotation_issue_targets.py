"""Exact defect targets and separate supporting context for annotation reviews."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


ISSUE_TARGET_GUIDANCE = '''Return each annotation issue as an object with candidate_paths and supporting_paths. candidate_paths contains exact candidate-relative JSON Pointer fields that locate the defect; for missing annotation layers or boundary defects use existing scalar fields as diagnostic anchors, not invented fields or a claim that their meaning is wrong. These paths locate affected material and do not authorize overwriting it; the explicit repair plan and boundary route govern insertion or regrouping. supporting_paths contains fields cited as context or evidence, never additional defect targets. Resolve every pointer against the supplied immutable candidate. Use distinct, nonoverlapping paths to specific fields inside segments, grammar_links, or grammar_overlays. Do not point to the whole candidate, an entire collection, or an entire segment/link. Preserve source text, source offsets and tap boundaries; paths do not authorize boundary changes. If you cannot identify an exact defective field, retain uncertainty rather than inventing a target. Numerical indices or source surfaces mentioned in prose do not add targets. Keep all genuinely defective fields in candidate_paths, including multiple defects. For source-position issues retain exact start/end/segment_text and bind the defective fields to that same source position.'''

_PATHS_SCHEMA = {'type': 'array', 'items': {'type': 'string', 'pattern': '^/'}, 'uniqueItems': True}
ISSUE_TARGET_SCHEMA_FIELDS = {
    'candidate_paths': {**_PATHS_SCHEMA, 'minItems': 1},
    'supporting_paths': dict(_PATHS_SCHEMA),
}
MISSING_LAYER_SCHEMA_FIELDS = {
    'missing_layer': {'type': 'object', 'additionalProperties': False,
        'required': ['version', 'anchor_paths', 'collection_path', 'identity',
                     'identity_status', 'identity_digest', 'source_interval'],
        'properties': {
            'version': {'const': 1},
            'anchor_paths': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'uniqueItems': True},
            'collection_path': {'type': 'string', 'pattern': '^/'},
            'identity': {'type': 'string', 'minLength': 1},
            'identity_status': {'enum': ['approved', 'provisional', 'reviewed_for_run']},
            'identity_digest': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'source_interval': {'type': 'object', 'additionalProperties': False,
                'required': ['start', 'end', 'surface'], 'properties': {
                    'start': {'type': 'integer', 'minimum': 0},
                    'end': {'type': 'integer', 'minimum': 1},
                    'surface': {'type': 'string', 'minLength': 1}}},
        }}
}


def missing_layer_review_schema(schema: dict, *, policy_version: int) -> dict:
    """Return an opt-in typed review schema; historical schema bytes stay fixed."""
    if type(policy_version) is not int or policy_version != 1:
        raise ValueError('unknown missing-layer review schema version')
    result = deepcopy(schema)
    try:
        issue_schema = result['properties']['issues']['items']
        issue_schema['properties'].update(MISSING_LAYER_SCHEMA_FIELDS)
    except (KeyError, TypeError) as exc:
        raise ValueError('review schema does not expose an issue-item object') from exc
    return result


class IssueTargetError(ValueError):
    """A typed review finding does not bind exact immutable candidate fields."""


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + '/') or right.startswith(left + '/')


def issue_target_paths(issue: Any, candidate: Any, *, source_text: str | None = None,
                       representation: str | None = None,
                       missing_layer_policy_version: int | None = None,
                       grammar_knowledge: dict | None = None,
                       run_dir=None, language: str | None = None) -> list[str] | None:
    """Return validated canonical targets, or None for a historical untyped issue."""
    if not isinstance(issue, dict) or 'candidate_paths' not in issue:
        if isinstance(issue, dict) and 'supporting_paths' in issue:
            raise IssueTargetError('Supporting paths require canonical candidate_paths')
        return None
    from pipeline.annotation_review_ledger import LedgerProtocolError, resolve_pointer
    targets = issue['candidate_paths']
    supporting = issue.get('supporting_paths', [])
    for label, paths in [('candidate_paths', targets), ('supporting_paths', supporting)]:
        if not isinstance(paths, list) or (label == 'candidate_paths' and not paths):
            raise IssueTargetError(f'{label} must be a list of exact field paths')
        if any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths):
            raise IssueTargetError(f'{label} must contain distinct string paths')
        for path in paths:
            if not re.fullmatch(r'/(?:segments|grammar_links|grammar_overlays)/(?:0|[1-9]\d*)/[^/]+(?:/[^/]+)*', path):
                raise IssueTargetError(f'Issue target is not a specific annotation field: {path!r}')
            try:
                value = resolve_pointer(candidate, path)
            except (LedgerProtocolError, TypeError) as exc:
                raise IssueTargetError(f'Unresolvable issue path {path!r}') from exc
            if isinstance(value, (dict, list)):
                raise IssueTargetError(f'Issue path must resolve a specific field value: {path!r}')
    if any(_overlap(left, right) for left in targets for right in supporting):
        raise IssueTargetError('Defect and supporting paths must be disjoint')
    if 'missing_layer' in issue:
        if missing_layer_policy_version != 1:
            raise IssueTargetError('Missing-layer declarations require explicit policy version 1')
        try:
            from pipeline.annotation_missing_layer import validate_declaration
            validate_declaration(issue, candidate, representation, source_text, grammar_knowledge,
                                 run_dir=run_dir, language=language)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise IssueTargetError(str(exc)) from exc
    else:
        _validate_source_identity(issue, targets, candidate, source_text, representation)
    return list(targets)


def _validate_source_identity(issue: dict, paths: list[str], candidate: Any,
                              source_text: str | None, representation: str | None) -> None:
    """Relate explicit diagnostic fields to immutable source nodes, not prose."""
    owners = {(path.split('/')[1], int(path.split('/')[2])) for path in paths}
    surface = issue.get('segment_text')
    has_offsets = 'start' in issue or 'end' in issue
    if not has_offsets:
        if surface is None:
            return
        if not isinstance(surface, str) or not surface:
            raise IssueTargetError('Structured source surface must be nonempty text')
        values = []
        for collection, index in sorted(owners):
            row = candidate[collection][index]
            value = row.get('surface', row.get('text', row.get('display_form')))
            values.append(value)
        boundary = issue.get('problem') in {'under_grouped', 'over_grouped', 'boundary', 'source_boundary'}
        if all(value == surface for value in values):
            return
        if boundary and owners and all(kind == 'segments' for kind, _ in owners):
            indices = sorted(index for _, index in owners)
            if indices == list(range(indices[0], indices[-1] + 1)) and ''.join(value or '' for value in values) == surface:
                return
        raise IssueTargetError('Canonical targets do not match their structured source surface')
    start, end = issue.get('start'), issue.get('end')
    if (not isinstance(source_text, str) or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source_text) or not isinstance(surface, str)
            or source_text[start:end] != surface):
        raise IssueTargetError('Structured source range does not match immutable source text')
    segments = candidate.get('segments', [])
    intervals = {}
    reconstructed = []
    cursor = 0
    for index, row in enumerate(segments):
        text = row.get('surface') if representation == 'japanese-annotation' else row.get('text')
        if not isinstance(text, str):
            raise IssueTargetError('Cannot map candidate source positions')
        intervals[('segments', index)] = (cursor, cursor + len(text))
        reconstructed.append(text)
        cursor += len(text)
    if ''.join(reconstructed) != source_text:
        raise IssueTargetError('Candidate segments do not reconstruct immutable source text')
    for collection in ('grammar_overlays', 'grammar_links'):
        for index, row in enumerate(candidate.get(collection, [])):
            if collection == 'grammar_overlays':
                left, right = row.get('start'), row.get('end')
                if (type(left) is int and type(right) is int and 0 <= left < right <= len(source_text)
                        and row.get('text') == source_text[left:right]):
                    intervals[(collection, index)] = (left, right)
            else:
                first, last = row.get('segment_index'), row.get('display_end_segment_index', row.get('segment_index'))
                if ('segments', first) in intervals and ('segments', last) in intervals:
                    left, right = intervals[('segments', first)][0], intervals[('segments', last)][1]
                    if row.get('display_form') == source_text[left:right]:
                        intervals[(collection, index)] = (left, right)
    boundary = issue.get('problem') in {'under_grouped', 'over_grouped', 'boundary', 'source_boundary'}
    covered = set()
    for owner in owners:
        interval = intervals.get(owner)
        if interval is None:
            raise IssueTargetError('Canonical target has no exact source identity')
        left, right = interval
        if boundary:
            valid = max(left, start) < min(right, end)
        else:
            valid = start <= left < right <= end
        if not valid:
            raise IssueTargetError('Canonical target belongs to a different source position')
        covered.update(range(max(left, start), min(right, end)))
    if covered != set(range(start, end)):
        raise IssueTargetError('Canonical targets do not cover the structured source interval')


def validate_issue_targets(review: Any, candidate: Any, *, source_text: str | None = None,
                           representation: str | None = None, require_typed: bool = False,
                           missing_layer_policy_version: int | None = None,
                           grammar_knowledge: dict | None = None, run_dir=None,
                           language: str | None = None) -> None:
    """Validate new typed issues before repair; preserve legacy issue formats."""
    if not isinstance(review, dict) or not isinstance(review.get('issues', []), list):
        raise IssueTargetError('Annotation review issues must be a list')
    for issue in review.get('issues', []):
        if require_typed and (not isinstance(issue, dict) or 'candidate_paths' not in issue
                              or 'supporting_paths' not in issue):
            raise IssueTargetError('New annotation issues require candidate_paths and supporting_paths')
        issue_target_paths(issue, candidate, source_text=source_text, representation=representation,
            missing_layer_policy_version=missing_layer_policy_version,
            grammar_knowledge=grammar_knowledge, run_dir=run_dir, language=language)
