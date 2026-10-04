"""Prototype a fail-closed ledger for reconciling repeated annotation reviews.

This module is deliberately separate from production annotation callers. It
binds each historical finding to immutable candidate snapshots and verifies the
reviewer's JSON-pointer observations before computing a pass/fail outcome.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import validate

from pipeline.agent_harness import CodexRunner


def canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _pointer_list_schema() -> dict:
    return {'type': 'array', 'minItems': 1, 'uniqueItems': True,
            'items': {'type': 'string', 'minLength': 1}}


LEDGER_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'type': 'object', 'additionalProperties': False,
    'required': ['ledger_clear', 'findings', 'new_issues', 'repair_diagnoses',
                 'prose_revision_reason_en'],
    'properties': {
        'ledger_clear': {'type': 'boolean'},
        'findings': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['finding_id', 'disposition', 'original_paths',
                         'current_paths', 'reason', 'evidence'],
            'properties': {
                'finding_id': {'type': 'string', 'minLength': 1},
                'disposition': {'enum': ['resolved', 'unresolved', 'unsupported']},
                'original_paths': _pointer_list_schema(),
                'current_paths': _pointer_list_schema(),
                'reason': {'type': 'string', 'minLength': 1},
                'evidence': {'type': 'string', 'minLength': 1},
            },
        }},
        'new_issues': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['issue_id', 'issue', 'paths', 'reason', 'evidence'],
            'properties': {
                'issue_id': {'type': 'string', 'minLength': 1},
                'issue': {'type': 'string', 'minLength': 1},
                'paths': _pointer_list_schema(),
                'reason': {'type': 'string', 'minLength': 1},
                'evidence': {'type': 'string', 'minLength': 1},
            },
        }},
        'repair_diagnoses': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['finding_ids', 'new_issue_ids', 'diagnosis', 'paths'],
            'properties': {
                'finding_ids': {'type': 'array', 'uniqueItems': True,
                                'items': {'type': 'string', 'minLength': 1}},
                'new_issue_ids': {'type': 'array', 'uniqueItems': True,
                                  'items': {'type': 'string', 'minLength': 1}},
                'diagnosis': {'type': 'string', 'minLength': 1},
                'paths': _pointer_list_schema(),
            },
        }},
        'prose_revision_reason_en': {'type': 'string'},
    },
}


INSTRUCTIONS = """Independently review the current annotation candidate and reconcile every historical finding.
For each supplied finding_id return exactly one disposition: resolved, unresolved,
or unsupported. Do not omit a finding because another review omitted it. Use exact
JSON Pointer paths into the supplied original and current candidate snapshots.
Pointers are relative to the candidate object itself and begin at paths such as
`/segments/0/...` or `/grammar_links/0/...`; never prefix a path with the input
wrapper key or candidate digest. The host resolves each path and records the exact
value, so return paths only and do not retype values. Each historical finding's
original context is stored by its `original_context_digest`; do not prefix field
pointers with the context-map key either. Before marking a finding
unresolved, verify that the current value actually exhibits the alleged defect.
When a finding supplies `affected_paths`, include every one of those candidate
paths in both `original_paths` and `current_paths`. Those paths bind the finding
to the exact annotation fields under review; changes elsewhere cannot resolve it.
Findings without supplied affected paths are unbound legacy triage only and can
never make the ledger clear.
An alternate suggested gloss alone is not a defect: compare a form's own meaning
with its supplied lesson and role, allow contextual tense where the form permits
it, and do not reject a defensible complete-form variant without a concrete mismatch.
A resolved finding must describe a relevant field
change and why that change resolves the reported defect. An unchanged field cannot
be called resolved. Unsupported is distinct: use it only when evidence shows the
historical objection itself was mistaken, and explain that evidence. If uncertain,
leave the finding unresolved. For every unresolved finding or new issue, provide a
concise current diagnosis for the normal repair plan. Group historical findings under
one diagnosis only when they describe the same actionable defect; restate the present
mismatch rather than copying historical wording. Report all new defects separately
with exact current field observations. Do not infer that successful validation or a previous approval
resolves a linguistic finding. Set ledger_clear true only when every historical finding is resolved
or supported as unsupported, there are no new issues, and no prose revision is needed.
This ledger never approves a candidate: a separate fresh independent linguistic
review and all normal schema, source, and publication gates remain required."""


class LedgerProtocolError(ValueError):
    """Reviewer output failed exact finding or candidate-evidence binding."""


def _pointer_tokens(pointer: str) -> list[str]:
    if pointer == '':
        return []
    if not pointer.startswith('/'):
        raise LedgerProtocolError(f'Invalid JSON Pointer: {pointer!r}')
    tokens = []
    for token in pointer[1:].split('/'):
        # Reject malformed escapes instead of silently normalizing them.
        i = 0
        while i < len(token):
            if token[i] == '~':
                if i + 1 >= len(token) or token[i + 1] not in '01':
                    raise LedgerProtocolError(f'Invalid JSON Pointer escape: {pointer!r}')
                i += 2
            else:
                i += 1
        tokens.append(token.replace('~1', '/').replace('~0', '~'))
    return tokens


def resolve_pointer(document: Any, pointer: str) -> Any:
    value = document
    for token in _pointer_tokens(pointer):
        if isinstance(value, dict):
            if token not in value:
                raise LedgerProtocolError(f'JSON Pointer does not exist: {pointer!r}')
            value = value[token]
        elif isinstance(value, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith('0')):
                raise LedgerProtocolError(f'Invalid array index in JSON Pointer: {pointer!r}')
            index = int(token)
            if index >= len(value):
                raise LedgerProtocolError(f'JSON Pointer index is out of range: {pointer!r}')
            value = value[index]
        else:
            raise LedgerProtocolError(f'JSON Pointer crosses a scalar value: {pointer!r}')
    return value


def _resolve_paths(paths: list[str], candidate: Any, label: str) -> dict[str, Any]:
    checked = {}
    for path in paths:
        if path in checked:
            raise LedgerProtocolError(f'Duplicate {label} pointer: {path}')
        if not path.startswith('/'):
            raise LedgerProtocolError(f'{label} pointers must be candidate-relative JSON Pointers')
        checked[path] = resolve_pointer(candidate, path)
    return checked


def verify_ledger_output(output: dict, *, current_candidate: Any,
                         historical_findings: list[dict], context: dict | None = None,
                         prose_before: Any = None, prose_after: Any = None,
                         effort: str = 'low', instructions_digest: str = '') -> dict:
    """Verify exact evidence and return clearance without approving publication."""
    validate(output, LEDGER_SCHEMA)
    expected = {row['finding_id']: row for row in historical_findings}
    if len(expected) != len(historical_findings):
        raise LedgerProtocolError('Historical finding IDs must be unique')
    actual_rows = output['findings']
    actual = {row['finding_id']: row for row in actual_rows}
    if len(actual) != len(actual_rows) or actual.keys() != expected.keys():
        raise LedgerProtocolError('Ledger must cover every historical finding ID exactly once')

    verified_findings = []
    unbound_finding_ids = []
    for finding_id, old in expected.items():
        row = actual[finding_id]
        original = _resolve_paths(row['original_paths'], old['original_candidate'], 'original')
        current = _resolve_paths(row['current_paths'], current_candidate, 'current')
        common = original.keys() & current.keys()
        if not common:
            raise LedgerProtocolError(f'Finding {finding_id} observations need a shared field path')
        affected_paths = old.get('affected_paths') or []
        if not isinstance(affected_paths, list) or any(
                not isinstance(path, str) or not path.startswith('/') for path in affected_paths):
            raise LedgerProtocolError(f'Finding {finding_id} affected_paths must be candidate-relative JSON Pointers')
        if len(set(affected_paths)) != len(affected_paths):
            raise LedgerProtocolError(f'Finding {finding_id} affected_paths must be unique')
        missing_original = set(affected_paths) - original.keys()
        missing_current = set(affected_paths) - current.keys()
        if missing_original or missing_current:
            raise LedgerProtocolError(
                f'Finding {finding_id} observations omit affected paths: '
                f'original={sorted(missing_original)}, current={sorted(missing_current)}')
        if affected_paths:
            relevant_paths = set(affected_paths)
        else:
            relevant_paths = common
            unbound_finding_ids.append(finding_id)
        changed = any(original[path] != current[path] for path in relevant_paths)
        if row['disposition'] == 'resolved' and not changed:
            raise LedgerProtocolError(f'Finding {finding_id} cannot be resolved without a relevant changed field')
        verified_findings.append({**row,
            'original_observations': [{'path': path, 'observed_value': value}
                                      for path, value in original.items()],
            'current_observations': [{'path': path, 'observed_value': value}
                                     for path, value in current.items()],
            'affected_paths': affected_paths,
            'bound': bool(affected_paths),
            'original_candidate_digest': canonical_digest(old['original_candidate']),
            'observed_change': changed})

    verified_new = []
    new_issue_ids = set()
    for row in output['new_issues']:
        if row['issue_id'] in new_issue_ids:
            raise LedgerProtocolError('New issue IDs must be unique')
        new_issue_ids.add(row['issue_id'])
        current = _resolve_paths(row['paths'], current_candidate, 'new issue')
        if not current:
            raise LedgerProtocolError('New issue must identify an exact current candidate field')
        verified_new.append({**row, 'observations': [
            {'path': path, 'observed_value': value} for path, value in current.items()]})

    unresolved_ids = {row['finding_id'] for row in verified_findings
                      if row['disposition'] == 'unresolved'}
    diagnosed_findings: list[str] = []
    diagnosed_new: list[str] = []
    diagnoses = []
    old_text = {row['finding_id']: row['text'] for row in historical_findings}
    for group in output['repair_diagnoses']:
        finding_ids = group['finding_ids']
        issue_ids = group['new_issue_ids']
        if not finding_ids and not issue_ids:
            raise LedgerProtocolError('Repair diagnosis must refer to a current issue')
        if any(identity not in unresolved_ids for identity in finding_ids):
            raise LedgerProtocolError('Repair diagnosis references a finding that is not unresolved')
        if any(identity not in new_issue_ids for identity in issue_ids):
            raise LedgerProtocolError('Repair diagnosis references an unknown new issue')
        if finding_ids and group['diagnosis'].strip() in {old_text[identity].strip() for identity in finding_ids}:
            raise LedgerProtocolError('Repair diagnosis must describe the current defect, not copy historical wording')
        current = _resolve_paths(group['paths'], current_candidate, 'repair diagnosis')
        diagnosed_findings.extend(finding_ids)
        diagnosed_new.extend(issue_ids)
        diagnoses.append({**group, 'observations': [
            {'path': path, 'observed_value': value} for path, value in current.items()]})
    if (set(diagnosed_findings) != unresolved_ids
            or len(diagnosed_findings) != len(unresolved_ids)
            or set(diagnosed_new) != new_issue_ids
            or len(diagnosed_new) != len(new_issue_ids)):
        raise LedgerProtocolError('Repair diagnoses must cover every unresolved or new issue exactly once')

    prose_changed = (canonical_digest(prose_before) != canonical_digest(prose_after)
                     if prose_before is not None or prose_after is not None else False)
    has_prose_request = bool(output['prose_revision_reason_en'].strip())
    ledger_clear = (all(row['disposition'] != 'unresolved' for row in verified_findings)
                    and not unbound_finding_ids and not verified_new
                    and not has_prose_request and not prose_changed)
    if output['ledger_clear'] is not ledger_clear:
        raise LedgerProtocolError('Reviewer ledger_clear claim contradicts the verified finding ledger')
    return {
        'approved': False,
        'ledger_clear': ledger_clear,
        'requires_independent_review': True,
        'findings': verified_findings,
        'unbound_finding_ids': unbound_finding_ids,
        'new_issues': verified_new,
        'repair_diagnoses': diagnoses,
        'prose_revision_reason_en': output['prose_revision_reason_en'],
        'prose_changed': prose_changed,
        'candidate_digest': canonical_digest(current_candidate),
        'context_digest': canonical_digest(context or {}),
        'effort': effort,
        'instructions_digest': instructions_digest,
    }


def _make_input(current_candidate: Any, historical_findings: list[dict], context: dict | None,
                prose_before: Any, prose_after: Any, review_instructions: str,
                effort: str) -> dict:
    candidates: dict[str, Any] = {}
    historical_contexts: dict[str, Any] = {}
    rows = []
    for finding in historical_findings:
        required = {'finding_id', 'text', 'original_candidate'}
        if not isinstance(finding, dict) or not required <= finding.keys():
            raise ValueError('Each historical finding needs finding_id, text, and original_candidate')
        candidate = finding['original_candidate']
        candidate_digest = canonical_digest(candidate)
        candidates.setdefault(candidate_digest, candidate)
        original_context = finding.get('original_context', {})
        context_digest = canonical_digest(original_context)
        historical_contexts.setdefault(context_digest, original_context)
        affected_paths = finding.get('affected_paths', [])
        if not isinstance(affected_paths, list) or any(
                not isinstance(path, str) or not path.startswith('/') for path in affected_paths):
            raise ValueError('affected_paths must be a list of candidate-relative JSON Pointers')
        if len(set(affected_paths)) != len(affected_paths):
            raise ValueError('affected_paths must be unique')
        for path in affected_paths:
            resolve_pointer(candidate, path)
        rows.append({'finding_id': finding['finding_id'], 'text': finding['text'],
                     'original_candidate_digest': candidate_digest,
                     'original_context_digest': context_digest,
                     'affected_paths': list(affected_paths)})
    if len({row['finding_id'] for row in rows}) != len(rows):
        raise ValueError('Historical finding IDs must be unique')
    return {
        'current_candidate_digest': canonical_digest(current_candidate),
        'current_candidate': current_candidate,
        'historical_candidates_by_digest': candidates,
        'historical_contexts_by_digest': historical_contexts,
        'historical_findings': rows,
        'context': context or {},
        'prose_before': prose_before,
        'prose_after': prose_after,
        'review_instructions': review_instructions,
        'effort': effort,
        'instructions_digest': canonical_digest({'review': review_instructions,
                                                  'ledger': INSTRUCTIONS}),
    }


def prepare_saved_smoke_case(cases_path: Path, summary_path: Path, case_name: str,
                            current_candidate_path: Path, output_path: Path) -> Path:
    """Build CLI input from the saved convergence smoke case/round artifacts.

    This is a read-only adapter: it preserves each original finding's own
    review-input candidate and requires the caller to name the current candidate
    artifact explicitly.
    """
    cases_path, summary_path = Path(cases_path), Path(summary_path)
    candidate_path, output_path = Path(current_candidate_path), Path(output_path)
    cases = json.loads(cases_path.read_text(encoding='utf-8'))
    summaries = json.loads(summary_path.read_text(encoding='utf-8'))
    case = next((row for row in cases if row.get('name') == case_name), None)
    summary = next((row for row in summaries if row.get('case') == case_name), None)
    if case is None or summary is None:
        raise ValueError(f'Unknown saved smoke case: {case_name}')
    initial_input = case.get('review_inputs')
    old_review = case.get('old_review')
    if not isinstance(initial_input, dict) or not isinstance(old_review, dict):
        raise ValueError('Saved smoke case lacks its historical review input')
    findings = []
    for index, text in enumerate(old_review.get('issues', [])):
        findings.append({'finding_id': f'historical-{index:03d}', 'text': text,
                         'original_candidate': initial_input['annotation'],
                         'original_context': initial_input.get('context', {})})
    review_inputs = []
    run_dir = summary_path.parent / case_name
    for round_row in summary.get('reviews', []):
        job = round_row.get('evidence', {}).get('job')
        if not isinstance(job, str) or Path(job).name != job:
            raise ValueError('Saved smoke review has an invalid job reference')
        review_input_path = run_dir / 'agents' / job / 'review-input.json'
        review_input = json.loads(review_input_path.read_text(encoding='utf-8'))
        legacy_digest = hashlib.sha256(json.dumps(review_input.get('annotation'),
            ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        if (canonical_digest(review_input.get('annotation')) != round_row.get('candidate_digest')
                and legacy_digest != round_row.get('candidate_digest')):
            raise ValueError('Saved smoke review input differs from its recorded candidate')
        review_inputs.append(review_input)
        for index, text in enumerate(round_row.get('review', {}).get('issues', [])):
            findings.append({'finding_id': f'round-{round_row.get("round", 0):03d}-{index:03d}',
                'text': text, 'original_candidate': review_input['annotation'],
                'original_context': review_input.get('context', {})})
    if not review_inputs:
        raise ValueError('Saved smoke summary has no fresh review rounds')
    current_candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
    latest_input = review_inputs[-1]
    level = case.get('level')
    if type(level) is not int or not 1 <= level <= 6:
        raise ValueError('Saved Korean smoke case has no valid TOPIK level')
    from pipeline.korean_agent_harness import POLICY, REVIEW_POLICY
    from pipeline.korean_levels import LEVEL_GOALS, LEVEL_POLICY
    from pipeline.korean_chunk_reviews import INSTRUCTIONS as KOREAN_REVIEW_INSTRUCTIONS
    policy = Path(POLICY).read_text(encoding='utf-8')
    if level > 1:
        policy += ('\n' + Path(LEVEL_POLICY).read_text(encoding='utf-8')
                   + f'\nRUN TARGET: TOPIK {level}. {LEVEL_GOALS[level]}\n')
    review_policy = REVIEW_POLICY.replace(
        'for its requested TOPIK learner level', f'for a TOPIK {level} learner')
    review_instructions = policy + '\n' + review_policy + '\n' + KOREAN_REVIEW_INSTRUCTIONS
    current_context = dict(latest_input.get('context', {}))
    value = _make_input(current_candidate, findings, current_context,
                        initial_input.get('text'), latest_input.get('text'),
                        review_instructions, 'low')
    value['smoke_provenance'] = {
        'case': case_name,
        'input_digest': summary.get('input_digest'),
        'source_case_file': str(cases_path),
        'source_summary_file': str(summary_path),
        'current_candidate_file': str(candidate_path),
    }
    _write_json(output_path, value)
    return output_path


async def review_finding_ledger(runner: Any, run_dir: Path, *, current_candidate: Any,
                               historical_findings: list[dict], context: dict | None = None,
                               prose_before: Any = None, prose_after: Any = None,
                               effort: str = 'low', review_instructions: str | None = None) -> dict:
    """Run reconciliation with shared tools-enabled Luna at low or medium effort.

    Pass complete verified ``repair_diagnoses`` groups to ``repair_annotation``.
    Their host-resolved observations preserve the affected field identities;
    extracting only each diagnosis string loses the evidence needed for planning.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if effort not in {'low', 'medium'}:
        raise ValueError('Finding-ledger effort must be low or medium')
    if isinstance(runner, CodexRunner) and (runner.benchmark_effort or 'low') != effort:
        raise ValueError('Requested ledger effort differs from enforced runner effort; set benchmark_effort explicitly for a medium smoke')
    if review_instructions is None:
        review_instructions = (context or {}).get('review_instructions', '')
    if not isinstance(review_instructions, str):
        raise ValueError('Review instructions must be a string')
    context_payload = dict(context or {})
    context_payload.pop('review_instructions', None)
    inputs = _make_input(current_candidate, historical_findings, context_payload,
                         prose_before, prose_after, review_instructions, effort)
    job = f"annotation-finding-ledger-{canonical_digest(inputs)}"
    agent_dir = run_dir / 'agents' / job
    agent_dir.mkdir(parents=True, exist_ok=True)
    _write_json(agent_dir / 'ledger-input.json', inputs)
    schema_path = run_dir / 'annotation-finding-ledger.schema.json'
    _write_json(schema_path, LEDGER_SCHEMA)
    prompt = (review_instructions + '\n' if review_instructions else '') + INSTRUCTIONS + '\nReturn the required JSON shape. All finding IDs and exact candidate snapshots and original per-finding review contexts are in the organized ledger input.\n'
    output = await runner.call(job, prompt, schema_path, effort, tool_profile='research',
        workspace_context={'annotation_finding_ledger': inputs})
    verified = verify_ledger_output(output, current_candidate=current_candidate,
        historical_findings=historical_findings, context=context_payload,
        prose_before=prose_before, prose_after=prose_after, effort=effort,
        instructions_digest=inputs['instructions_digest'])
    _write_json(agent_dir / 'result.json', output)
    _write_json(agent_dir / 'verified-ledger.json', verified)
    return verified


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


async def _cli(args) -> dict:
    input_value = json.loads(args.input.read_text(encoding='utf-8'))
    runner = CodexRunner(args.run_dir, 'gpt-6-luna', asyncio.Semaphore(1),
        timeout=args.timeout, benchmark_effort=args.effort)
    return await review_finding_ledger(runner, args.run_dir,
        current_candidate=input_value['current_candidate'],
        historical_findings=input_value['historical_findings'],
        context=input_value.get('context'), prose_before=input_value.get('prose_before'),
        prose_after=input_value.get('prose_after'), effort=args.effort,
        review_instructions=input_value.get('review_instructions'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
        help='JSON with current_candidate and historical_findings[{finding_id,text,original_candidate}]')
    parser.add_argument('--prepare-smoke-case', action='store_true',
        help='Prepare --input from a saved two-review convergence case')
    parser.add_argument('--cases', type=Path)
    parser.add_argument('--summary', type=Path)
    parser.add_argument('--case-name')
    parser.add_argument('--current-candidate', type=Path)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--effort', choices=('low', 'medium'), default='low')
    args = parser.parse_args()
    if args.prepare_smoke_case:
        if not all((args.cases, args.summary, args.case_name, args.current_candidate)):
            parser.error('--prepare-smoke-case requires --cases, --summary, --case-name, and --current-candidate')
        prepare_saved_smoke_case(args.cases, args.summary, args.case_name,
                                 args.current_candidate, args.input)
        return
    print(json.dumps(asyncio.run(_cli(args)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
