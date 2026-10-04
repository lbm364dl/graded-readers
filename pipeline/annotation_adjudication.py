"""Evidence-bound, independent adjudication for repeated annotation reviews.

This module does not replace deterministic annotation gates. It can clear only
linguistic review findings, and only when each one is tied to exact candidate
fields and supplied authoritative linguistic references.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import validate

from pipeline.agent_harness import CodexRunner
from pipeline.annotation_review_ledger import resolve_pointer, LedgerProtocolError


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


LINGUISTIC_REFERENCE_KINDS = frozenset({
    'approved_lesson', 'primary_source', 'explicit_review_policy',
})
REFERENCE_KINDS = LINGUISTIC_REFERENCE_KINDS | frozenset({'source_context', 'clause'})


OUTPUT_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'type': 'object', 'additionalProperties': False,
    'required': ['classifications', 'new_issues', 'prose_revision_reason'],
    'properties': {
        'classifications': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['issue_id', 'disposition', 'target_binding', 'candidate_paths',
                         'evidence_refs', 'reason', 'diagnosis'],
            'properties': {
                'issue_id': {'type': 'string', 'minLength': 1},
                'disposition': {'enum': ['unsupported', 'actionable', 'uncertain']},
                'target_binding': {'enum': ['exact', 'ambiguous', 'unbound']},
                'candidate_paths': {'type': 'array', 'items': {'type': 'string'}},
                'evidence_refs': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False,
                    'required': ['reference_id', 'path'],
                    'properties': {'reference_id': {'type': 'string'}, 'path': {'type': 'string'}},
                }},
                'reason': {'type': 'string'}, 'diagnosis': {'type': 'string'},
            },
        }},
        'new_issues': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['issue_id', 'issue', 'target_binding', 'candidate_paths',
                         'evidence_refs', 'reason', 'diagnosis'],
            'properties': {
                'issue_id': {'type': 'string', 'minLength': 1},
                'issue': {'type': 'string', 'minLength': 1},
                'target_binding': {'enum': ['exact', 'ambiguous', 'unbound']},
                'candidate_paths': {'type': 'array', 'items': {'type': 'string'}},
                'evidence_refs': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False,
                    'required': ['reference_id', 'path'],
                    'properties': {'reference_id': {'type': 'string'}, 'path': {'type': 'string'}},
                }},
                'reason': {'type': 'string'}, 'diagnosis': {'type': 'string'},
            },
        }},
        'prose_revision_reason': {'type': 'string'},
    },
}

INSTRUCTIONS = """Independently adjudicate the current rejected annotation review. This is a linguistic evidence review, not a vote among prior reviewers. Review every current issue exactly once. Classify it unsupported only when a supplied authoritative linguistic reference demonstrates that the current candidate field is acceptable; prior reviews, approvals, and the candidate's own explanation are never linguistic evidence. A draft lesson proposal is not an approved lesson. A source passage can ground application but is not by itself proof that a disputed linguistic analysis is valid. For an annotation category field only, an approved lexical lesson's own lexical-kind value may support the same candidate category when its headword and reading exactly match the targeted segment. Generic reference-wrapper kinds, titles, and other metadata are never linguistic evidence.

For each classification, use candidate-relative JSON Pointers such as /segments/3/meaning_en. The host resolves every pointer and reference path and attaches exact values; do not quote or invent observed values. Bind to the precise field named by the issue. Occurrence meaning belongs to its occurrence meaning field, not a form-step meaning; a form-stage issue must point to that exact stage. If an issue explicitly names a segment, link, stage, or stable identity, the pointer must match it. If the target is unclear, mark uncertain. Never use suffix guesses or infer that an unrelated changed field resolves an issue.

Use evidence_refs only for supplied reference IDs and paths. Approved lesson, primary source, and explicit review-policy documents may establish linguistic facts. Source context/clause documents may establish what the passage says, but alone cannot establish that a disputed analysis is linguistically valid. If the evidence is incomplete, conflicting, or the target cannot be anchored, choose uncertain. Actionable means a concrete mismatch exists; give a narrow repair diagnosis. Report new defects separately. Preserve any prose revision request: do not clear a review that asks for prose revision. Do not override a deterministic contract or validation failure. Stylistic alternatives alone are not defects. Return only the requested JSON."""

# Adjudication must use the same semantic criteria as the critic and repairer;
# merely supplying a large policy document as reference data is insufficient.
from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE

INSTRUCTIONS += '\n\n' + FORM_STAGE_EVIDENCE_GUIDANCE + """

Before classifying a meaning finding actionable, compare the candidate's exact
English with the linked lesson's explanation, including any supported glosses.
Identify the actual words or semantic contribution that contradict that lesson.
A diagnosis that merely asks for the contribution already expressed is not a
repair diagnosis. If the reference supports that contribution and the objection
only prefers another English phrasing, classify unsupported with that reference.
Do not confuse a dependent intended-action gloss with an assertion that an
intention was completed, or an embedded speculative question with an assertion
that its event will happen. Conversely, preserve defects whose exact wording
adds an unsupported action, modality, tense, or construction-level contribution.
"""


class AdjudicationError(ValueError):
    """The adjudication output cannot be safely bound to its evidence."""


def _prose_requests(review: Any) -> list[tuple[str, Any]]:
    if not isinstance(review, dict):
        return []
    return [(key, value) for key, value in review.items()
            if re.search(r'prose|paragraph|rewrite|revision', str(key), re.I)
            and bool(value)]


def normalize_review(language: str, review: Any) -> dict:
    """Normalize review issue coverage without discarding language-specific fields."""
    if language not in {'zh', 'ja', 'ko'} or not isinstance(review, dict):
        raise AdjudicationError('Expected a zh/ja/ko review object')
    raw_issues = review.get('issues', [])
    if not isinstance(raw_issues, list):
        raise AdjudicationError('Review issues must be a list')
    rows = []
    for index, issue in enumerate(raw_issues):
        identity = f'issue-{index:03d}-{digest(issue)[:12]}'
        rows.append({'issue_id': identity, 'issue': issue})
    verdict = review.get('verdict')
    if verdict is None:
        verdict = 'pass' if review.get('approved') is True else 'revise'
    if verdict not in {'pass', 'revise'}:
        raise AdjudicationError(f'Unrecognized review verdict: {verdict!r}')
    if verdict == 'revise' and not rows and not _prose_requests(review):
        raise AdjudicationError('A rejected review with no issues or prose request cannot be adjudicated clear')
    return {'language': language, 'verdict': verdict, 'issues': rows,
            'prose_requests': _prose_requests(review), 'raw_review_digest': digest(review),
            'raw_review': review}


def _reference_index(reference_input: Any) -> dict[str, dict]:
    if not isinstance(reference_input, dict):
        raise AdjudicationError('Known references must be an object keyed by reference ID')
    rows = {}
    for identity, row in reference_input.items():
        if (not isinstance(identity, str) or not identity or not isinstance(row, dict)
                or row.get('kind') not in REFERENCE_KINDS or 'content' not in row):
            raise AdjudicationError(f'Malformed known reference: {identity!r}')
        rows[identity] = {'kind': row['kind'], 'content': row['content'],
                          'content_digest': digest(row['content'])}
    return rows


def _explicit_anchors(issue: Any) -> list[tuple[str, int]]:
    """Recognize every explicit numeric segment/link reference in reviewer text."""
    found: list[tuple[str, int]] = []
    if isinstance(issue, dict):
        for key, collection in (('segment_index', 'segments'), ('segment', 'segments'),
                                ('link_index', 'grammar_links'), ('grammar_link_index', 'grammar_links'),
                                ('stage_index', 'form_steps')):
            value = issue.get(key)
            if type(value) is int and value >= 0:
                found.append((collection, value))
        for key, collection in (('segment_indices', 'segments'), ('link_indices', 'grammar_links'),
                                ('stage_indices', 'form_steps')):
            values = issue.get(key)
            if isinstance(values, list):
                found.extend((collection, value) for value in values if type(value) is int and value >= 0)
        for key in ('candidate_path', 'field_path', 'path'):
            value = issue.get(key)
            if isinstance(value, str) and value.startswith('/'):
                tokens = value.split('/')
                if len(tokens) >= 3 and tokens[1] in {'segments', 'grammar_links'} and tokens[2].isdigit():
                    found.append((tokens[1], int(tokens[2])))
    text = json.dumps(issue, ensure_ascii=False) if not isinstance(issue, str) else issue
    multi_patterns = ((r'\bsegments?\s+(?:index\s*)?(\d+(?:\s*(?:,|and|&)\s*\d+)*)', 'segments'),
                      (r'\b(?:grammar\s+)?links?\s+(\d+(?:\s*(?:,|and|&)\s*\d+)*)', 'grammar_links'),
                      (r'\bstages?\s+(?:index\s*)?(\d+(?:\s*(?:,|and|&)\s*\d+)*)', 'form_steps'),
                      (r'\bform[- _]steps?\s+(?:index\s*)?(\d+(?:\s*(?:,|and|&)\s*\d+)*)', 'form_steps'))
    for pattern, collection in multi_patterns:
        for match in re.finditer(pattern, text, re.I):
            # A ranged overlay cited to explain a local defect is context, not
            # another defective tap. Keep structured target indices above and
            # ordinary multi-target findings authoritative. In particular,
            # "At segment 4 ... overlay spanning segments 2–4" targets 4.
            prefix = text[max(0, match.start() - 160):match.start()]
            following = text[match.end():]
            if (collection == 'segments'
                    and re.match(r'\s*[-–—]\s*\d+', following)
                    and re.search(
                        r'\b(?:overlay|construction|span)\b[^.;\n]{0,140}'
                        r'\b(?:on|spanning|spans|covers?|across|over|from)\s*$',
                        prefix, re.I)):
                continue
            found.extend((collection, int(number)) for number in re.findall(r'\d+', match.group(1)))
    return list(dict.fromkeys(found))


def _explicit_paths(issue: Any) -> list[str]:
    if not isinstance(issue, dict):
        return []
    paths = []
    for key in ('candidate_path', 'field_path', 'path'):
        value = issue.get(key)
        if isinstance(value, str) and value.startswith('/'):
            paths.append(value)
    for key in ('affected_paths', 'candidate_paths'):
        value = issue.get(key)
        if isinstance(value, list):
            paths.extend(path for path in value if isinstance(path, str) and path.startswith('/'))
    return list(dict.fromkeys(paths))


def _path_is_anchor(path: str, anchors: list[tuple[str, int]]) -> bool:
    if not anchors:
        return True
    tokens = path.split('/')
    root_collections = {'segments', 'grammar_links', 'grammar_overlays'}
    root_anchors = {(collection, index) for collection, index in anchors if collection in root_collections}
    stage_anchors = {index for collection, index in anchors if collection == 'form_steps'}
    root_match = (len(tokens) >= 3 and tokens[1] in root_collections
                  and tokens[2].isdigit()
                  and (tokens[1], int(tokens[2])) in root_anchors)
    stage_match = any(tokens[pos] == 'form_steps' and pos + 1 < len(tokens)
                      and tokens[pos + 1].isdigit() and int(tokens[pos + 1]) in stage_anchors
                      for pos in range(len(tokens) - 1))
    if stage_anchors and tokens[1:2] == ['segments']:
        return (not root_anchors or root_match) and stage_match
    if root_anchors:
        return root_match
    return stage_match


def _path_matches_issue_scope(path: str, issue: Any) -> bool:
    text = json.dumps(issue, ensure_ascii=False) if not isinstance(issue, str) else issue
    lower = text.casefold()
    path_lower = path.casefold()
    mentions_stage = any(term in lower for term in (
        'form step', 'form-step', 'form_step', 'stage meaning', 'displayed stage',
        'intermediate form', 'prefinal', 'incomplete stage', 'incomplete-stage',
        'inflectional stem', 'bare stem'))
    mentions_occurrence = any(term in lower for term in ('occurrence meaning', 'meaning_en', 'occurrence gloss', 'candidate meaning'))
    mentions_meaning = any(term in lower for term in ('meaning', 'gloss', 'translation'))
    # Occurrence-level target wording is more specific than incidental mentions
    # of a stage in an explanation or contrast.
    if mentions_occurrence and '/form_steps/' in path_lower:
        return False
    if mentions_stage and not mentions_occurrence and '/form_steps/' not in path_lower:
        return False
    if mentions_meaning and not mentions_stage and not any(
            token in path_lower for token in ('/meaning', '/gloss', '/translation')):
        return False
    if ('grammar link' in lower or 'grammar_links' in lower):
        anchors = _explicit_anchors(issue)
        allows_multiple_targets = (any(kind == 'segments' for kind, _ in anchors)
                                   and any(kind in {'grammar_links', 'grammar_overlays'}
                                           for kind, _ in anchors))
        if '/grammar_links/' not in path_lower and '/grammar_overlays/' not in path_lower:
            if not (allows_multiple_targets and '/segments/' in path_lower):
                return False
    return True


def _surface_anchors(issue: Any, candidate: Any) -> tuple[list[tuple[str, int]], bool]:
    """Bind exact quoted source surfaces only when they identify one source position."""
    text = json.dumps(issue, ensure_ascii=False) if not isinstance(issue, str) else issue
    segments = candidate.get('segments') if isinstance(candidate, dict) else None
    if not isinstance(segments, list):
        return [], False
    surfaces = set(re.findall(r'[“"「『]([^”"」』]{1,80})[”"」』]', text))
    matches = []
    for index, segment in enumerate(segments):
        if isinstance(segment, dict):
            surface = segment.get('surface', segment.get('text'))
            if isinstance(surface, str) and surface in surfaces:
                matches.append(('segments', index))
    if len(matches) == 1:
        return matches, False
    if len(matches) > 1:
        return matches, True
    return [], False


def _source_span_anchors(issue: Any, candidate: Any,
                         source_text: str | None,
                         representation: str) -> tuple[list[tuple[str, int]], bool]:
    if not isinstance(issue, dict) or ('start' not in issue and 'end' not in issue):
        return [], False
    start, end = issue.get('start'), issue.get('end')
    surface = issue.get('segment_text')
    if (source_text is None or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source_text)
            or not isinstance(surface, str) or source_text[start:end] != surface):
        return [], True
    problem = str(issue.get('problem', '')).casefold()
    if 'grammar' in problem:
        overlays = candidate.get('grammar_overlays') if isinstance(candidate, dict) else None
        matches = [index for index, row in enumerate(overlays or [])
                   if isinstance(row, dict) and row.get('start') == start and row.get('end') == end
                   and row.get('text') == surface]
        return ([('grammar_overlays', matches[0])] if len(matches) == 1 else [], len(matches) != 1)
    segments = candidate.get('segments') if isinstance(candidate, dict) else None
    if not isinstance(segments, list):
        return [], True
    cursor = 0
    matching = []
    reconstructed = []
    for index, row in enumerate(segments):
        text = row.get('surface') if isinstance(row, dict) and representation == 'japanese-annotation' else (
            row.get('text') if isinstance(row, dict) else None)
        if not isinstance(text, str):
            return [], True
        left, right = cursor, cursor + len(text)
        reconstructed.append(text)
        if (left, right) == (start, end) and text == surface:
            matching.append(index)
        cursor = right
    if ''.join(reconstructed) != source_text:
        return [], True
    return ([('segments', matching[0])] if len(matching) == 1 else [], len(matching) != 1)


def _issue_contract(review_issue: Any) -> bool:
    if isinstance(review_issue, dict):
        return (review_issue.get('problem') == 'contract'
                or review_issue.get('contract_violation') is True
                or review_issue.get('category') in {'contract', 'schema', 'source_boundary'})
    return isinstance(review_issue, str) and bool(re.search(
        r'\b(?:schema|source[- ](?:text|boundary)|tap[- ]boundary|contract)\s+(?:failure|violation|error)\b',
        review_issue, re.I))


def _substantive_evidence(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_substantive_evidence(item) for item in value)
    if isinstance(value, dict):
        return any(_substantive_evidence(item) for key, item in value.items()
                   if str(key).casefold() not in {
                       'id', 'entry_id', 'name', 'title', 'type', 'kind', 'url',
                       'source_url', 'sha256', 'digest', 'path', 'author', 'reviewed',
                       'review_digest', 'metadata',
                       'issue_ids', 'source_refs', '_annotation_research_fact',
                   })
    return False


def _substantive_reference(
    row: dict, *, reference_content: Any = None, candidate: Any = None,
    candidate_paths: list[str] | None = None, representation: str = '',
) -> bool:
    pointer = row.get('path', '')
    if isinstance(pointer, str) and pointer:
        final_key = pointer.rsplit('/', 1)[-1].replace('~1', '/').replace('~0', '~').casefold()
        if final_key in {'id', 'entry_id', 'name', 'title', 'type', 'kind', 'url',
                         'source_url', 'sha256', 'digest', 'path', 'author',
                         'reviewed', 'review_digest', 'metadata', 'issue_ids',
                         'source_refs', '_annotation_research_fact'}:
            return _approved_lexical_category_match(
                row, reference_content=reference_content, candidate=candidate,
                candidate_paths=candidate_paths or [], representation=representation)
    return _substantive_evidence(row.get('value'))


def _approved_lexical_category_match(
    row: dict, *, reference_content: Any, candidate: Any,
    candidate_paths: list[str], representation: str,
) -> bool:
    """Treat an approved lexical kind as evidence only for its exact segment category."""
    if (representation != 'chinese-annotation'
            or row.get('reference_kind') != 'approved_lesson'
            or row.get('path') != '/kind'
            or not isinstance(reference_content, dict)
            or not isinstance(candidate, dict)
            or not isinstance(candidate.get('segments'), list)):
        return False
    category = row.get('value')
    if category not in {'word', 'particle', 'name'}:
        return False
    headword = reference_content.get('headword')
    reading = reference_content.get('reading')
    identity = reference_content.get('id')
    if not all(isinstance(value, str) and value.strip()
               for value in (headword, reading, identity)):
        return False
    if not candidate_paths:
        return False
    for path in candidate_paths:
        match = re.fullmatch(r'/segments/(\d+)/type', path)
        if not match:
            return False
        index = int(match.group(1))
        if index >= len(candidate['segments']):
            return False
        segment = candidate['segments'][index]
        if not (isinstance(segment, dict)
                and segment.get('type') == category
                and segment.get('text') == headword
                and isinstance(segment.get('pinyin'), str)
                and segment['pinyin'].strip().casefold() == reading.strip().casefold()):
            return False
    return True


def _validate_output(output: dict, inputs: dict) -> dict:
    validate(output, OUTPUT_SCHEMA)
    normalized = inputs['normalized_review']
    current = inputs['candidate']
    expected_rows = normalized['issues']
    expected_ids = {row['issue_id'] for row in expected_rows}
    actual_rows = output['classifications']
    actual_ids = [row['issue_id'] for row in actual_rows]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
        raise AdjudicationError('Every current review issue must be classified exactly once')
    refs = _reference_index(inputs['known_reference_input'])
    issue_by_id = {row['issue_id']: row['issue'] for row in expected_rows}
    verified = []
    for row in actual_rows:
        issue = issue_by_id[row['issue_id']]
        if not row['reason'].strip():
            raise AdjudicationError('Every issue classification requires an evidence-based reason')
        observations = []
        for path in row['candidate_paths']:
            if not path.startswith('/'):
                raise AdjudicationError('Candidate paths must be candidate-relative JSON Pointers')
            try:
                value = resolve_pointer(current, path)
            except (LedgerProtocolError, TypeError) as exc:
                raise AdjudicationError(f'Unresolvable candidate path {path!r}: {exc}') from exc
            observations.append({'path': path, 'value': value, 'value_digest': digest(value)})
        anchors = _explicit_anchors(issue)
        source_anchors, source_ambiguous = _source_span_anchors(
            issue, current, inputs.get('source_text'), inputs['representation'])
        if source_anchors and not source_ambiguous:
            surface_anchors, surface_ambiguous = [], False
        else:
            surface_anchors, surface_ambiguous = _surface_anchors(issue, current)
        anchors = list(dict.fromkeys(anchors + source_anchors + surface_anchors))
        anchors_observed = all(any(_path_is_anchor(obs['path'], [anchor])
                                   for obs in observations) for anchor in anchors)
        explicitly_bound = bool(anchors)
        if row['target_binding'] != 'exact' or not observations or surface_ambiguous or source_ambiguous:
            disposition = 'uncertain'
        elif ((anchors and not anchors_observed)
              or (anchors and not all(_path_is_anchor(obs['path'], anchors) for obs in observations))
              or (_explicit_paths(issue) and set(_explicit_paths(issue)) !=
                  {obs['path'] for obs in observations})
              or (not _explicit_paths(issue) and not all(
                  _path_matches_issue_scope(obs['path'], issue) for obs in observations))
              or (not explicitly_bound and not _explicit_paths(issue))):
            disposition = 'uncertain'
        else:
            disposition = row['disposition']
        refs_resolved = []
        for ref in row['evidence_refs']:
            identity, path = ref['reference_id'], ref['path']
            if identity not in refs:
                raise AdjudicationError(f'Unknown evidence reference ID: {identity}')
            content = refs[identity]['content']
            if (isinstance(content, dict) and content.get('_annotation_research_fact') is True
                    and row['issue_id'] not in content.get('issue_ids', [])):
                raise AdjudicationError('Reviewed research was cited outside its verified issue scope')
            try:
                evidence_value = resolve_pointer(content, path)
            except (LedgerProtocolError, TypeError) as exc:
                raise AdjudicationError(f'Unresolvable reference path {identity}:{path}: {exc}') from exc
            refs_resolved.append({'reference_id': identity, 'reference_kind': refs[identity]['kind'],
                                  'path': path, 'value': evidence_value,
                                  'value_digest': digest(evidence_value)})
        if disposition == 'unsupported':
            if _issue_contract(issue):
                raise AdjudicationError('Deterministic contract findings cannot be overridden')
            if not any(ref['reference_kind'] in LINGUISTIC_REFERENCE_KINDS
                       and _substantive_reference(
                           ref, reference_content=refs[ref['reference_id']]['content'],
                           candidate=current, candidate_paths=row['candidate_paths'],
                           representation=inputs['representation'])
                       for ref in refs_resolved):
                disposition = 'uncertain'
            if not row['reason'].strip():
                raise AdjudicationError('Unsupported findings require a substantive explanation')
        if disposition == 'actionable' and (not row['diagnosis'].strip() or not refs_resolved):
            raise AdjudicationError('Actionable findings require a concrete diagnosis and evidence reference')
        verified.append({**row, 'disposition': disposition,
                         'candidate_observations': observations,
                         'resolved_evidence_refs': refs_resolved})

    new_issues = []
    seen_new_ids = set()
    for row in output['new_issues']:
        if row['issue_id'] in expected_ids or row['issue_id'] in seen_new_ids:
            raise AdjudicationError('New issue IDs must be unique and distinct from current finding IDs')
        seen_new_ids.add(row['issue_id'])
        observations = []
        for path in row['candidate_paths']:
            try:
                value = resolve_pointer(current, path)
            except (LedgerProtocolError, TypeError) as exc:
                raise AdjudicationError(f'Unresolvable new-issue path {path!r}') from exc
            observations.append({'path': path, 'value': value, 'value_digest': digest(value)})
        if not observations or row['target_binding'] != 'exact':
            raise AdjudicationError('New issues need an exact candidate binding')
        if not row['reason'].strip() or not row['diagnosis'].strip():
            raise AdjudicationError('New issues require an evidence-based reason and concrete diagnosis')
        resolved_refs = []
        for ref in row['evidence_refs']:
            if ref['reference_id'] not in refs:
                raise AdjudicationError(f'Unknown evidence reference ID: {ref["reference_id"]}')
            value = resolve_pointer(refs[ref['reference_id']]['content'], ref['path'])
            resolved_refs.append({'reference_id': ref['reference_id'],
                                  'reference_kind': refs[ref['reference_id']]['kind'],
                                  'path': ref['path'], 'value': value,
                                  'value_digest': digest(value)})
        if not resolved_refs:
            raise AdjudicationError('New issues must cite supplied evidence')
        new_issues.append({**row, 'candidate_observations': observations,
                           'resolved_evidence_refs': resolved_refs})

    prose_request = bool(normalized['prose_requests']) or bool(output['prose_revision_reason'].strip())
    gate = inputs['deterministic_gate_evidence']
    gate_passed = (isinstance(gate, dict) and gate.get('passed') is True
                   and not gate.get('issues')
                   and gate.get('candidate_digest') == inputs['candidate_digest']
                   and (inputs['source_text'] is None
                        or gate.get('source_text_digest') == digest(inputs['source_text'])))
    if not gate_passed:
        status = 'blocked_by_gate'
    elif prose_request:
        status = 'blocked_by_prose_request'
    elif any(row['disposition'] == 'uncertain' for row in verified):
        status = 'uncertain'
    elif new_issues or any(row['disposition'] == 'actionable' for row in verified):
        status = 'actionable'
    else:
        status = 'cleared'
    repair_diagnoses = [
        {'issue_id': row['issue_id'], 'diagnosis': row['diagnosis'],
         'paths': [obs['path'] for obs in row['candidate_observations']],
         'observations': row['candidate_observations'],
         'resolved_evidence_refs': row['resolved_evidence_refs'],
         'path_history': _prior_path_history(inputs['prior_history'], current,
                                              [obs['path'] for obs in row['candidate_observations']],
                                              inputs['source_text'], inputs['representation'])}
        for row in verified if row['disposition'] == 'actionable'
    ]
    repair_diagnoses.extend(
        {'issue_id': row['issue_id'], 'diagnosis': row['diagnosis'],
         'paths': [obs['path'] for obs in row['candidate_observations']],
         'observations': row['candidate_observations'],
         'resolved_evidence_refs': row['resolved_evidence_refs'],
         'path_history': _prior_path_history(inputs['prior_history'], current,
                                              [obs['path'] for obs in row['candidate_observations']],
                                              inputs['source_text'], inputs['representation'])}
        for row in new_issues)
    return {
        'status': status, 'approved': status == 'cleared',
        'effective_review_kind': 'adjudicated' if status == 'cleared' else None,
        'preserved_rejected_review_digest': normalized['raw_review_digest'],
        'classifications': verified, 'new_issues': new_issues,
        'repair_diagnoses': repair_diagnoses,
        'prose_revision_reason': output['prose_revision_reason'],
        'prose_requests': normalized['prose_requests'],
        'candidate_digest': inputs['candidate_digest'],
        'review_digest': inputs['review_digest'], 'history_digest': inputs['history_digest'],
        'context_digest': inputs['context_digest'], 'references_digest': inputs['references_digest'],
        'gate_digest': inputs['gate_digest'], 'input_digest': inputs['input_digest'],
        'instructions_digest': inputs['instructions_digest'],
    }


def validate_adjudication_output(output: dict, inputs: dict) -> dict:
    """Pure, deterministic worker-workspace validation hook."""
    return _validate_output(output, inputs)


def _build_inputs(*, language: str, representation: str, candidate: Any,
                  current_review: dict, prior_history: Any, context: Any,
                  known_reference_input: dict, deterministic_gate_evidence: dict,
                  normal_review_receipt: dict, source_text: str | None) -> dict:
    normalized = normalize_review(language, current_review)
    if representation not in {'chinese-fixed', 'chinese-annotation', 'japanese-annotation',
                              'korean-flat', 'korean-v4'}:
        raise AdjudicationError(f'Unsupported annotation representation: {representation!r}')
    if normalized['verdict'] != 'revise':
        raise AdjudicationError('Adjudication is only for a currently rejected review')
    if not isinstance(normal_review_receipt, (dict, list)) or not normal_review_receipt:
        raise AdjudicationError('Normal rejected-review receipt must be a nonempty receipt or composite receipt')
    if isinstance(normal_review_receipt, dict) and 'review_digest' in normal_review_receipt:
        if normal_review_receipt['review_digest'] != digest(current_review):
            raise AdjudicationError('Normal review receipt does not bind the supplied current review')
    references = _reference_index(known_reference_input)
    input_value = {
        'language': language, 'representation': representation,
        'candidate': candidate, 'current_review': current_review,
        'normalized_review': normalized, 'prior_history': prior_history,
        'context': context, 'known_reference_input': known_reference_input,
        'reference_manifest': references,
        'deterministic_gate_evidence': deterministic_gate_evidence,
        'normal_review_receipt': normal_review_receipt,
        'source_text': source_text,
        'candidate_digest': digest(candidate), 'review_digest': digest(current_review),
        'history_digest': digest(prior_history), 'context_digest': digest(context),
        'references_digest': digest(known_reference_input),
        'gate_digest': digest(deterministic_gate_evidence),
        'normal_review_receipt_digest': digest(normal_review_receipt),
        'instructions_digest': digest(INSTRUCTIONS), 'effort': 'low',
        'model': 'gpt-6-luna', 'tool_profile': 'research',
    }
    input_value['input_digest'] = digest(input_value)
    return input_value


def _preflight_status(inputs: dict) -> str | None:
    gate = inputs['deterministic_gate_evidence']
    gate_passed = (isinstance(gate, dict) and gate.get('passed') is True
                   and not gate.get('issues')
                   and gate.get('candidate_digest') == inputs['candidate_digest']
                   and (inputs['source_text'] is None
                        or gate.get('source_text_digest') == digest(inputs['source_text'])))
    if not gate_passed:
        return 'blocked_by_gate'
    if inputs['normalized_review']['prose_requests']:
        return 'blocked_by_prose_request'
    return None


def _preflight_result(inputs: dict, status: str) -> dict:
    return {
        'status': status, 'approved': False, 'effective_review_kind': None,
        'preflight_block': True, 'job': f"annotation-adjudication-{inputs['input_digest']}",
        'effort': None, 'model': None, 'tool_profile': None, 'worker_tool_profile': None,
        'normal_review_receipt': inputs['normal_review_receipt'],
        'normal_review_receipt_digest': inputs['normal_review_receipt_digest'],
        'candidate_digest': inputs['candidate_digest'], 'review_digest': inputs['review_digest'],
        'history_digest': inputs['history_digest'], 'context_digest': inputs['context_digest'],
        'references_digest': inputs['references_digest'], 'gate_digest': inputs['gate_digest'],
        'input_digest': inputs['input_digest'], 'instructions_digest': inputs['instructions_digest'],
    }


def _historical_issue_paths(issue: Any, snapshot: Any, source_text: str | None,
                            representation: str) -> list[str]:
    """Resolve an old issue only from explicit paths or a unique structural anchor."""
    explicit = _explicit_paths(issue)
    if explicit:
        paths = []
        for path in explicit:
            try:
                resolve_pointer(snapshot, path)
            except (LedgerProtocolError, TypeError):
                continue
            paths.append(path)
        return paths
    anchors = _explicit_anchors(issue)
    source_anchors, source_ambiguous = _source_span_anchors(
        issue, snapshot, source_text, representation)
    if source_ambiguous:
        return []
    anchors.extend(source_anchors)
    if not anchors:
        anchors, ambiguous = _surface_anchors(issue, snapshot)
        if ambiguous:
            return []
    text = json.dumps(issue, ensure_ascii=False) if not isinstance(issue, str) else issue
    lower = text.casefold()
    paths = []
    for collection, index in anchors:
        if collection == 'segments':
            segments = snapshot.get('segments') if isinstance(snapshot, dict) else None
            if not isinstance(segments, list) or index >= len(segments) or not isinstance(segments[index], dict):
                continue
            segment = segments[index]
            if any(token in lower for token in ('form step', 'form-step', 'form_step', 'stage')):
                stage_ids = [n for kind, n in anchors if kind == 'form_steps']
                steps = segment.get('form_steps')
                if not isinstance(steps, list) or not stage_ids:
                    continue
                paths.extend(f'/segments/{index}/form_steps/{stage_index}'
                             for stage_index in stage_ids if stage_index < len(steps))
            elif any(token in lower for token in ('meaning', 'gloss', 'translation')):
                paths.extend(f'/segments/{index}/{key}' for key in segment
                             if any(token in key.casefold() for token in ('meaning', 'gloss', 'translation')))
        elif collection == 'grammar_links':
            links = snapshot.get('grammar_links') if isinstance(snapshot, dict) else None
            if isinstance(links, list) and index < len(links):
                paths.append(f'/grammar_links/{index}')
    return list(dict.fromkeys(paths))


def _prior_path_history(prior_history: Any, current_candidate: Any,
                        diagnosis_paths: list[str], source_text: str | None,
                        representation: str) -> list[dict]:
    """Carry exact prior observations as unverified history, never as linguistic proof."""
    if not isinstance(prior_history, list):
        return []
    history = []
    for attempt_index, attempt in enumerate(prior_history):
        if not isinstance(attempt, dict):
            continue
        snapshot = attempt.get('annotation', attempt.get('candidate'))
        review = attempt.get('review')
        if not isinstance(snapshot, (dict, list)) or not isinstance(review, dict):
            continue
        issues = review.get('issues', [])
        if not isinstance(issues, list):
            continue
        for issue_index, issue in enumerate(issues):
            paths = _historical_issue_paths(issue, snapshot, source_text, representation)
            overlapping = [path for path in paths if any(
                path == target or path.startswith(target + '/') or target.startswith(path + '/')
                for target in diagnosis_paths)]
            if not overlapping:
                continue
            path_values = []
            for path in overlapping:
                row = {'path': path}
                try:
                    row['original_value'] = resolve_pointer(snapshot, path)
                except (LedgerProtocolError, TypeError):
                    pass
                try:
                    row['current_value'] = resolve_pointer(current_candidate, path)
                except (LedgerProtocolError, TypeError):
                    pass
                path_values.append(row)
            history.append({
                'finding_id': f'prior-{attempt_index:03d}-{issue_index:03d}-{digest(issue)[:10]}',
                'disposition': 'prior_review_unverified', 'bound': True,
                'reason': json.dumps(issue, ensure_ascii=False),
                'evidence': 'Prior reviewer wording is retained for continuity only; it is not linguistic proof.',
                'path_values': path_values,
            })
    return history


def _worker_prompt(inputs: dict) -> str:
    context = inputs.get('context')
    policy = context.get('annotation_reviewed_reference_policy') if isinstance(context, dict) else None
    if policy is not None and policy != REVIEWED_REFERENCE_POLICY:
        raise AdjudicationError('Reviewed-reference approval policy is not host-authenticated')
    return (INSTRUCTIONS + ('\n\n' + policy if policy else '') + '\nThe full candidate, review, history, and organized references are in the immutable annotation_adjudication input. Return the required JSON object.\n')


async def _adjudicate_once(runner: Any, run_dir: Path, *, language: str,
        representation: str, candidate: Any, current_review: dict, prior_history: Any,
        context: Any, known_reference_input: dict, deterministic_gate_evidence: dict,
        normal_review_receipt: dict, source_text: str | None = None) -> dict:
    """Run one cached, tools-enabled Luna-low adjudication and persist its receipt."""
    if isinstance(runner, CodexRunner) and (runner.model != 'gpt-6-luna'
            or (runner.benchmark_effort or 'low') != 'low'):
        raise AdjudicationError('Adjudication requires shared gpt-6-luna low policy')
    if isinstance(runner, CodexRunner) and runner.legacy_tool_restrictions:
        raise AdjudicationError('Adjudication requires the organized tools-enabled workspace profile')
    inputs = _build_inputs(language=language, representation=representation, candidate=candidate,
        current_review=current_review, prior_history=prior_history, context=context,
        known_reference_input=known_reference_input,
        deterministic_gate_evidence=deterministic_gate_evidence,
        normal_review_receipt=normal_review_receipt, source_text=source_text)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    preflight = _preflight_status(inputs)
    if preflight is not None:
        blocked = _preflight_result(inputs, preflight)
        agent_dir = run_dir / 'agents' / blocked['job']
        agent_dir.mkdir(parents=True, exist_ok=True)
        _write_json(agent_dir / 'adjudication-input.json', inputs)
        _write_json(agent_dir / 'verified-adjudication.json', blocked)
        return blocked
    job = f"annotation-adjudication-{inputs['input_digest']}"
    agent_dir = run_dir / 'agents' / job
    agent_dir.mkdir(parents=True, exist_ok=True)
    _write_json(agent_dir / 'adjudication-input.json', inputs)
    schema_path = run_dir / 'annotation-adjudication.schema.json'
    _write_json(schema_path, OUTPUT_SCHEMA)
    prompt = _worker_prompt(inputs)
    output = await runner.call(job, prompt, schema_path, 'low', tool_profile='research',
        workspace_context={'annotation_adjudication': inputs,
                           'annotation_adjudication_validation': {'input_field': 'annotation_adjudication'}})
    verified = _validate_output(output, inputs)
    meta = json.loads((agent_dir / 'meta.json').read_text(encoding='utf-8'))
    worker_profile = meta.get('tool_profile')
    if (meta.get('return_code') != 0 or meta.get('model') != 'gpt-6-luna'
            or meta.get('effort') != 'low' or worker_profile not in {'workspace', 'research'}):
        raise AdjudicationError('Adjudication worker did not complete under the shared Luna-low tools-enabled policy')
    verified.update({'job': job, 'effort': 'low', 'model': 'gpt-6-luna',
                     'tool_profile': 'research',
                     'worker_tool_profile': worker_profile,
                     'normal_review_receipt': normal_review_receipt,
                     'normal_review_receipt_digest': inputs['normal_review_receipt_digest'],
                     'verified_result_digest': digest(verified)})
    _write_json(agent_dir / 'result.json', output)
    _write_json(agent_dir / 'verified-adjudication.json', verified)
    return verified


def _verify_adjudication_once(run_dir: Path, evidence: dict, *, language: str,
        representation: str, candidate: Any, current_review: dict, prior_history: Any,
        context: Any, known_reference_input: dict, deterministic_gate_evidence: dict,
        normal_review_receipt: dict, source_text: str | None = None) -> dict:
    """Replay a saved receipt; no model call is made and inputs must match exactly."""
    inputs = _build_inputs(language=language, representation=representation, candidate=candidate,
        current_review=current_review, prior_history=prior_history, context=context,
        known_reference_input=known_reference_input,
        deterministic_gate_evidence=deterministic_gate_evidence,
        normal_review_receipt=normal_review_receipt, source_text=source_text)
    run_dir = Path(run_dir).resolve(strict=True)
    job = evidence.get('job') if isinstance(evidence, dict) else None
    if not isinstance(job, str) or Path(job).name != job or job != f"annotation-adjudication-{inputs['input_digest']}":
        raise AdjudicationError('Adjudication job identity does not match exact inputs')
    agents_dir = run_dir / 'agents'
    if agents_dir.is_symlink():
        raise AdjudicationError('Run agents directory cannot be a symlink')
    agents_real = agents_dir.resolve(strict=True)
    job_dir = agents_dir / job
    if job_dir.is_symlink() or not job_dir.resolve(strict=True).is_relative_to(agents_real):
        raise AdjudicationError('Adjudication job escaped the run agents directory')
    for filename in ('adjudication-input.json', 'result.json', 'verified-adjudication.json', 'meta.json'):
        if evidence.get('preflight_block') and filename in {'result.json', 'meta.json'}:
            continue
        target = job_dir / filename
        if not target.exists() or target.is_symlink() or not target.resolve(strict=True).is_relative_to(agents_real):
            raise AdjudicationError(f'Adjudication artifact path is unsafe: {filename}')
    stored_inputs = json.loads((job_dir / 'adjudication-input.json').read_text(encoding='utf-8'))
    if evidence.get('preflight_block'):
        status = _preflight_status(inputs)
        if status is None:
            raise AdjudicationError('Preflight block no longer follows from current gate/review evidence')
        expected = _preflight_result(inputs, status)
        stored = json.loads((job_dir / 'verified-adjudication.json').read_text(encoding='utf-8'))
        if stored_inputs != inputs or stored != expected or evidence != expected:
            raise AdjudicationError('Saved adjudication preflight block does not replay exactly')
        return expected
    output = json.loads((job_dir / 'result.json').read_text(encoding='utf-8'))
    stored = json.loads((job_dir / 'verified-adjudication.json').read_text(encoding='utf-8'))
    meta = json.loads((job_dir / 'meta.json').read_text(encoding='utf-8'))
    if stored_inputs != inputs:
        raise AdjudicationError('Persisted adjudication inputs changed')
    worker_profile = evidence.get('worker_tool_profile')
    if (meta.get('return_code') != 0 or meta.get('model') != 'gpt-6-luna'
            or meta.get('effort') != 'low' or worker_profile not in {'workspace', 'research'}
            or meta.get('tool_profile') != worker_profile):
        raise AdjudicationError('Adjudication worker receipt violates model/effort/tool policy')
    CodexRunner._check_tool_profile(job_dir, worker_profile, meta)
    replayed = _validate_output(output, inputs)
    expected = {**replayed, 'job': job, 'effort': 'low', 'model': 'gpt-6-luna',
                'tool_profile': 'research', 'worker_tool_profile': worker_profile,
                'normal_review_receipt': normal_review_receipt,
                'normal_review_receipt_digest': inputs['normal_review_receipt_digest']}
    expected['verified_result_digest'] = digest(replayed)
    if stored != expected or evidence != expected:
        raise AdjudicationError('Saved adjudication evidence does not replay exactly')
    return expected


REVIEWED_REFERENCE_POLICY = """The coordinator has authenticated the added approved_lesson references against their researcher and independent reviewer artifacts. These lessons are approved evidence for the stated run and issue scope, even when an internal legacy lesson ID contains DRAFT. This is distinct from an unreviewed draft proposal and from publication into a dictionary registry. Judge the lesson's actual linguistic explanation and its applicability to the current field; do not discard it because its legacy ID has not been promoted. The researcher's or reviewer's approval verdict alone is not linguistic evidence: cite substantive lesson content, preserve its issue scope, and retain genuine uncertainty when the content does not support the analysis."""


def _research_inputs(original: dict, evidence: dict, references: dict,
                     *, policy_version: int = 1) -> dict:
    """Add only independently verified knowledge; preserve the review/candidate."""
    if not references or set(references) & set(original['known_reference_input']):
        raise AdjudicationError('Reviewed research references must be new and nonempty')
    context = original['context']
    if not isinstance(context, dict):
        context = {'original_context': context}
    context = {**context, 'annotation_reference_research_digest': digest(evidence)}
    if policy_version == 2:
        context['annotation_reviewed_reference_policy'] = REVIEWED_REFERENCE_POLICY
    elif policy_version != 1:
        raise AdjudicationError('Unknown reviewed-reference policy version')
    return {**original,
            'known_reference_input': {**original['known_reference_input'], **references},
            'context': context}


def _research_receipt(result: dict, initial: dict, research: dict,
                      *, version: int = 1) -> dict:
    chain = {'initial_adjudication': initial, 'reference_research': research}
    return {**result, **chain, 'reference_research_version': version,
            'reference_research_chain_digest': digest({'result': result, **chain})}


async def adjudicate_annotation_review(runner: Any, run_dir: Path, **inputs) -> dict:
    """Adjudicate, then research an unresolved analysis once before retrying.

    All languages use this same bounded path. Research never changes a candidate
    or an ordinary review and must pass its own independent evidence review.
    """
    inputs.setdefault('source_text', None)
    initial = await _adjudicate_once(runner, run_dir, **inputs)
    if initial['status'] != 'uncertain':
        return initial
    from pipeline.annotation_research import research_uncertain_review
    research = await research_uncertain_review(runner, run_dir,
        initial_adjudication=initial, **inputs)
    if not research['references']:
        result = initial
    else:
        enriched = _research_inputs(inputs, research['evidence'], research['references'],
                                    policy_version=2)
        result = await _adjudicate_once(runner, run_dir, **enriched)
    wrapped = _research_receipt(result, initial, research['evidence'], version=2)
    _write_json(Path(run_dir) / 'agents' / result['job'] /
                'reference-research-adjudication.json', wrapped)
    return wrapped


def verify_adjudication_evidence(run_dir: Path, evidence: dict, **inputs) -> dict:
    """Replay ordinary or research-enriched evidence, without any model calls."""
    inputs.setdefault('source_text', None)
    markers = {'reference_research_version', 'initial_adjudication',
               'reference_research', 'reference_research_chain_digest'}
    present = markers & evidence.keys()
    if not present:
        return _verify_adjudication_once(run_dir, evidence, **inputs)
    if present != markers or evidence['reference_research_version'] not in (1, 2):
        raise AdjudicationError('Incomplete or unknown research-adjudication receipt')
    initial = _verify_adjudication_once(run_dir, evidence['initial_adjudication'], **inputs)
    if initial['status'] != 'uncertain':
        raise AdjudicationError('Reference research requires an unresolved initial adjudication')
    from pipeline.annotation_research import verify_research_evidence
    research = verify_research_evidence(run_dir, evidence['reference_research'],
        initial_adjudication=initial, **inputs)
    result_evidence = {key: value for key, value in evidence.items() if key not in markers}
    if not research['references']:
        if result_evidence != initial:
            raise AdjudicationError('Unresolved research cannot change the initial adjudication')
        result = initial
    else:
        enriched = _research_inputs(inputs, research['evidence'], research['references'],
                                    policy_version=evidence['reference_research_version'])
        result = _verify_adjudication_once(run_dir, result_evidence, **enriched)
    expected = _research_receipt(result, initial, research['evidence'],
                                 version=evidence['reference_research_version'])
    if evidence != expected:
        raise AdjudicationError('Research-adjudication chain does not replay exactly')
    return expected


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
