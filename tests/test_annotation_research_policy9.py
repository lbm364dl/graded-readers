"""Portable canonical-module regression tests for research policy 9."""
from __future__ import annotations
import asyncio
import pytest
from pipeline import annotation_research as research
from pipeline import annotation_adjudication as adjudication

P_MEANING = '/segments/1/meaning_en'
P_LINK = '/grammar_links/0/segment_index'

def request(dispositions):
    candidate = {'segments': [
        {'text': '열', 'meaning_en': 'ten'},
        {'text': '달', 'meaning_en': 'months'},
        {'text': '잠에서', 'meaning_en': 'from sleep'}],
        'grammar_links': [{'entry_id': 'source-eseo', 'segment_index': 2,
            'display_end_segment_index': -1, 'display_form': '', 'display_meaning_en': ''}]}
    issue = {'explanation': 'The counter gloss and a link attachment are questioned.',
        'candidate_paths': [P_MEANING, P_LINK],
        'supporting_paths': ['/segments/0/meaning_en']}
    review = {'approved': False, 'issues': [issue], 'prose_revision_reason_en': ''}
    normalized = research.normalize_review('ko', review)['issues']
    initial = {'status': 'uncertain', 'approved': False, 'prose_requests': [],
        'prose_revision_reason': '', 'classifications': [{
            'issue_id': normalized[0]['issue_id'], 'disposition': 'uncertain',
            'candidate_paths': [P_MEANING, P_LINK], 'reason': 'Only one target lacks binding.',
            'target_dispositions': [
                {'path': P_MEANING, 'disposition': dispositions[0], 'reason': 'meaning disposition'},
                {'path': P_LINK, 'disposition': dispositions[1], 'reason': 'link disposition'}]}]}
    source = '열달잠에서'
    return {'language': 'ko', 'representation': 'korean-flat', 'candidate': candidate,
        'current_review': review, 'prior_history': [], 'context': {'source_id': 'fixture'},
        'source_text': source,
        'deterministic_gate_evidence': {'passed': True, 'issues': [],
            'candidate_digest': research._digest(candidate), 'source_text_digest': research._digest(source)},
        'initial_adjudication': initial,
        'known_reference_input': {'lesson': {'kind': 'approved_lesson',
            'content': {'explanation_en': '열 is ten; 달 can count months.'}}},
        'normal_review_receipt': {'review_digest': research._digest(review)}}

def simple_language_request(language, representation):
    candidate = {'segments': [{'text': '借', 'meaning_en': 'borrowed'}], 'grammar_overlays': []}
    issue = {'explanation': 'The occurrence meaning remains uncertain.',
        'candidate_paths': ['/segments/0/meaning_en'], 'supporting_paths': []}
    review = {'approved': False, 'issues': [issue], 'prose_revision_reason_en': ''}
    issue_id = research.normalize_review(language, review)['issues'][0]['issue_id']
    source = '借'
    return {'language': language, 'representation': representation, 'candidate': candidate,
        'current_review': review, 'prior_history': [], 'context': {}, 'source_text': source,
        'deterministic_gate_evidence': {'passed': True, 'issues': [],
            'candidate_digest': research._digest(candidate), 'source_text_digest': research._digest(source)},
        'initial_adjudication': {'status': 'uncertain', 'approved': False,
            'prose_requests': [], 'prose_revision_reason': '', 'classifications': [{
                'issue_id': issue_id, 'disposition': 'uncertain',
                'candidate_paths': ['/segments/0/meaning_en'], 'reason': 'Exact occurrence evidence is missing.',
                'target_dispositions': [{'path': '/segments/0/meaning_en', 'disposition': 'uncertain',
                    'reason': 'Exact occurrence evidence is missing.'}]}]},
        'known_reference_input': {'lesson': {'kind': 'approved_lesson', 'content': {'explanation_en': 'Fact.'}}},
        'normal_review_receipt': {'review_digest': research._digest(review)}}

def test_mixed_issue_research_task_contains_only_host_uncertain_path():
    inputs = research._build_inputs(**request(['unsupported', 'uncertain']), policy_version=9)
    packet = inputs['uncertainty_tasks']
    assert packet['version'] == 2
    task, = packet['tasks']
    assert task['candidate_paths'] == [P_LINK]
    assert task['candidate_observations'] == [{'path': P_LINK, 'value': 2}]
    assert task['context_paths'] == [P_MEANING]
    assert task['context_observations'] == [
        {'path': P_MEANING, 'value': 'months'},
        {'path': '/segments/0/meaning_en', 'value': 'ten'}]
    assert task['target_dispositions'][0]['path'] == P_LINK
    assert task['current_review_issue']['candidate_paths'] == [P_MEANING, P_LINK]

def test_all_uncertain_paths_remain_targets_and_context_is_not_promoted():
    inputs = research._build_inputs(**request(['uncertain', 'uncertain']), policy_version=9)
    task, = inputs['uncertainty_tasks']['tasks']
    assert task['candidate_paths'] == [P_MEANING, P_LINK]
    assert task['context_paths'] == []
    assert [x['path'] for x in task['candidate_observations']] == [P_MEANING, P_LINK]

def test_v9_fails_closed_without_exact_host_target_dispositions():
    req = request(['unsupported', 'uncertain'])
    req['initial_adjudication']['classifications'][0].pop('target_dispositions')
    with pytest.raises(research.AnnotationResearchError, match='host target dispositions'):
        research._build_inputs(**req, policy_version=9)

def test_historical_policy8_packet_and_reference_identity_keep_golden_contract():
    req = request(['unsupported', 'uncertain'])
    inputs = research._build_inputs(**req, policy_version=8)
    packet = inputs['uncertainty_tasks']
    assert inputs['research_policy_version'] == 8
    assert packet['version'] == 1
    task, = packet['tasks']
    assert task['candidate_paths'] == [P_MEANING, P_LINK]
    assert 'target_dispositions' not in task and 'context_paths' not in task
    assert inputs['initial_adjudication_digest'] == 'd68efc1befe8fe1f38654c9de7435948b7846e3451e97aaafe5fd2f8dff4f34e'
    issue_id, = inputs['issue_ids']
    facts, gaps = research._validate_research_output({'findings': [{
        'issue_id': issue_id, 'status': 'supported', 'fact': 'A historical issue-wide fact.',
        'citations': [{'reference_id': 'lesson', 'path': '/explanation_en'}], 'gap': ''}]},
        inputs['issue_ids'], inputs['known_reference_input'], policy_version=8)
    assert not gaps
    reference_id, = research._reference_rows(inputs, facts)
    assert reference_id == 'annotation-research-8b88e177a7899be28b8c9109'
    content = research._reference_rows(inputs, facts)[reference_id]['content']
    assert content['issue_ids'] == [issue_id]
    assert 'research_policy_version' not in content and 'target_paths' not in content

def test_v9_research_fact_is_bound_to_uncertain_target_and_not_context_sibling():
    inputs = research._build_inputs(**request(['unsupported', 'uncertain']), policy_version=9)
    issue_id = inputs['issue_ids'][0]
    facts, unresolved = research._validate_research_output({
        'findings': [{'issue_id': issue_id, 'candidate_path': P_LINK, 'status': 'supported',
            'fact': 'The exact source link is attached to the from-source marker.',
            'citations': [{'reference_id': 'lesson', 'path': '/explanation_en'}], 'gap': ''}]},
        inputs['issue_ids'], inputs['known_reference_input'], policy_version=9,
        target_paths_by_issue=research._target_paths_by_issue(inputs))
    assert not unresolved
    assert facts[0]['target_paths'] == [P_LINK]
    refs = research._reference_rows(inputs, facts)
    ref, = refs.values()
    content = ref['content']
    assert content['research_policy_version'] == 9
    assert content['target_paths'] == [P_LINK]
    assert adjudication._research_fact_applies_to_target(content, issue_id, P_LINK)
    assert not adjudication._research_fact_applies_to_target(content, issue_id, P_MEANING)
    assert not adjudication._research_fact_applies_to_target(content, 'another-issue', P_LINK)

def test_v9_can_support_one_uncertain_field_while_preserving_sibling_gap():
    inputs = research._build_inputs(**request(['uncertain', 'uncertain']), policy_version=9)
    issue_id = inputs['issue_ids'][0]
    facts, gaps = research._validate_research_output({'findings': [
        {'issue_id': issue_id, 'candidate_path': P_MEANING, 'status': 'supported',
         'fact': 'The cited lesson supports this occurrence meaning.',
         'citations': [{'reference_id': 'lesson', 'path': '/explanation_en'}], 'gap': ''},
        {'issue_id': issue_id, 'candidate_path': P_LINK, 'status': 'unresolved',
         'fact': '', 'citations': [], 'gap': 'The supplied lesson does not address this attachment.'}]},
        inputs['issue_ids'], inputs['known_reference_input'], policy_version=9,
        target_paths_by_issue=research._target_paths_by_issue(inputs))
    assert [row['target_paths'] for row in facts] == [[P_MEANING]]
    assert [row['target_paths'] for row in gaps] == [[P_LINK]]
    references = research._reference_rows(inputs, facts)
    reference, = references.values()
    assert adjudication._research_fact_applies_to_target(reference['content'], issue_id, P_MEANING)
    assert not adjudication._research_fact_applies_to_target(reference['content'], issue_id, P_LINK)

def test_v9_invalid_output_fallback_keeps_one_gap_per_exact_field():
    inputs = research._build_inputs(**request(['uncertain', 'uncertain']), policy_version=9)
    facts, gaps, error = research._resolve_research_output(
        {'findings': []}, inputs['issue_ids'], inputs['known_reference_input'],
        policy_version=9, target_paths_by_issue=research._target_paths_by_issue(inputs))
    assert not facts and error
    assert [(row['candidate_path'], row['target_paths']) for row in gaps] == [
        (P_MEANING, [P_MEANING]), (P_LINK, [P_LINK])]

def test_historical_research_facts_keep_issue_level_replay_contract():
    historical = {'_annotation_research_fact': True, 'issue_ids': ['issue-1'], 'fact': 'old receipt'}
    assert adjudication._research_fact_applies_to_target(historical, 'issue-1', P_MEANING)
    assert not adjudication._research_fact_applies_to_target(historical, 'issue-2', P_MEANING)

def test_researcher_and_critic_are_directed_to_check_each_exact_target_and_citation():
    inputs = research._build_inputs(**request(['unsupported', 'uncertain']), policy_version=9)
    assert 'every candidate_path' in research._research_prompt(inputs)
    critic = research._review_prompt(inputs)
    assert 'exact candidate_paths' in critic
    assert 'reject an accurate fact about a context path' in critic

@pytest.mark.parametrize(('language', 'representation'), [
    ('zh', 'chinese-annotation'), ('ja', 'japanese-annotation'), ('ko', 'korean-flat')])
def test_shared_policy_scopes_targets_for_each_language_caller(language, representation):
    if language == 'ko':
        req = request(['unsupported', 'uncertain'])
        target, other = P_LINK, P_MEANING
    else:
        candidate = {'segments': [{'text': '借', 'meaning_en': 'borrowed'}], 'grammar_overlays': []}
        issue = {'explanation': 'The occurrence meaning remains uncertain.',
            'candidate_paths': ['/segments/0/meaning_en'], 'supporting_paths': []}
        review = {'approved': False, 'issues': [issue], 'prose_revision_reason_en': ''}
        identity = research.normalize_review(language, review)['issues'][0]['issue_id']
        source = '借'
        req = {'language': language, 'representation': representation, 'candidate': candidate,
            'current_review': review, 'prior_history': [], 'context': {}, 'source_text': source,
            'deterministic_gate_evidence': {'passed': True, 'issues': [],
                'candidate_digest': research._digest(candidate), 'source_text_digest': research._digest(source)},
            'initial_adjudication': {'status': 'uncertain', 'approved': False,
                'prose_requests': [], 'prose_revision_reason': '', 'classifications': [{
                    'issue_id': identity, 'disposition': 'uncertain',
                    'candidate_paths': ['/segments/0/meaning_en'], 'reason': 'Exact occurrence evidence is missing.',
                    'target_dispositions': [{'path': '/segments/0/meaning_en', 'disposition': 'uncertain',
                        'reason': 'Exact occurrence evidence is missing.'}]}]},
            'known_reference_input': {'lesson': {'kind': 'approved_lesson', 'content': {'explanation_en': 'Fact.'}}},
            'normal_review_receipt': {'review_digest': research._digest(review)}}
        target, other = '/segments/0/meaning_en', '/segments/1/meaning_en'
    inputs = research._build_inputs(**req, policy_version=9)
    task, = inputs['uncertainty_tasks']['tasks']
    assert task['candidate_paths'] == [target]
    assert inputs['uncertainty_tasks']['version'] == 2
    fact = {'_annotation_research_fact': True, 'research_policy_version': 9,
        'issue_ids': [task['issue_id']], 'target_paths': task['candidate_paths']}
    assert adjudication._research_fact_applies_to_target(fact, task['issue_id'], target)
    assert not adjudication._research_fact_applies_to_target(fact, task['issue_id'], other)

class ReplayRunner:
    model = 'gpt-6-luna'
    benchmark_effort = None
    legacy_tool_restrictions = False
    def __init__(self, root, issue_id, reference_id='lesson', fact=None):
        self.root, self.issue_id = root, issue_id
        self.reference_id = reference_id
        self.fact = fact or 'Lesson says 에서 marks the source relation; the candidate link points to the 잠에서 token at index 2.'
        self.contexts = []
    async def call(self, job, prompt, schema_path, effort, *, tool_profile, workspace_context):
        import hashlib, json
        from pipeline.worker_workspace import build
        from pipeline.annotation_research import _request_fingerprint
        self.contexts.append((job, prompt, workspace_context))
        if job.startswith('annotation-uncertainty-research-'):
            task_packet = workspace_context['annotation_uncertainty_tasks']
            task = next(row for row in task_packet['tasks'] if row['issue_id'] == self.issue_id)
            result = {'findings': [{'issue_id': self.issue_id, 'candidate_path': path,
                'status': 'supported', 'fact': self.fact,
                'citations': [{'reference_id': self.reference_id, 'path': '/explanation_en'}], 'gap': ''}
                for path in task['candidate_paths']]}
        else:
            result = {'approved': True, 'issues': []}
        job_dir = self.root / 'agents' / job
        workspace = job_dir / 'workspace'
        workspace.mkdir(parents=True, exist_ok=True)
        schema_text = schema_path.read_text(encoding='utf-8')
        _, workspace_digest = build(workspace, prompt, json.loads(schema_text), context=workspace_context)
        artifact = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        (workspace / 'candidate.json').write_text(artifact, encoding='utf-8')
        (job_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False) + '\n', encoding='utf-8')
        meta = {'job': job, 'return_code': 0, 'model': self.model, 'effort': 'low',
            'tool_profile': tool_profile, 'fingerprint': _request_fingerprint(prompt, schema_text, workspace_context),
            'workspace_digest': workspace_digest, 'artifact_path': 'candidate.json',
            'artifact_digest': hashlib.sha256(artifact.encode()).hexdigest()}
        (job_dir / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
        return result

@pytest.mark.parametrize(('language', 'representation'), [
    ('zh', 'chinese-annotation'), ('ja', 'japanese-annotation'), ('ko', 'korean-flat')])
def test_v9_full_research_critic_receipt_replays_with_field_bound_reference(tmp_path, monkeypatch,
        language, representation):
    req = request(['unsupported', 'uncertain']) if language == 'ko' else simple_language_request(language, representation)
    if language == 'ko':
        req['representation'] = representation
    issue_id = research.normalize_review(language, req['current_review'])['issues'][0]['issue_id']
    monkeypatch.setattr(research, 'verify_adjudication_evidence',
        lambda *_args, **_kwargs: {'status': 'uncertain'})
    import pipeline.annotation_research as canonical_research
    monkeypatch.setattr(canonical_research, 'validate_research_submission',
        research.validate_research_submission)
    runner = ReplayRunner(tmp_path, issue_id, fact='The supplied lesson supports this exact candidate field.')
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **req))
    assert result['status'] == 'approved'
    assert len(runner.contexts) == 2
    research_ctx = runner.contexts[0][2]['annotation_uncertainty_tasks']
    critic_ctx = runner.contexts[1][2]['annotation_uncertainty_tasks']
    assert research_ctx == critic_ctx
    task, = research_ctx['tasks']
    expected_path = P_LINK if language == 'ko' else '/segments/0/meaning_en'
    assert task['candidate_paths'] == [expected_path]
    assert task['context_paths'] == ([P_MEANING] if language == 'ko' else [])
    assert 'exact candidate_paths' in runner.contexts[1][1]
    reference, = result['references'].values()
    assert reference['content']['target_paths'] == [expected_path]
    assert research.verify_research_evidence(tmp_path, result['evidence'], **req) == result
    cached = asyncio.run(research.research_uncertain_review(runner, tmp_path, **req))
    assert cached == result
    assert len(runner.contexts) == 2


def test_duplicate_or_omitted_host_target_rows_fail_closed():
    req = request(['unsupported', 'uncertain'])
    req['initial_adjudication']['classifications'][0]['target_dispositions'].append(
        {'path': P_LINK, 'disposition': 'uncertain', 'reason': 'duplicate path'})
    with pytest.raises(research.AnnotationResearchError, match='exactly cover'):
        research._build_inputs(**req, policy_version=9)

def test_each_all_uncertain_field_gets_its_own_exact_reference_scope():
    inputs = research._build_inputs(**request(['uncertain', 'uncertain']), policy_version=9)
    issue_id = inputs['issue_ids'][0]
    facts, _ = research._validate_research_output({'findings': [{
        'issue_id': issue_id, 'candidate_path': path, 'status': 'supported',
        'fact': 'A cited formation fact jointly addresses both exact fields.',
        'citations': [{'reference_id': 'lesson', 'path': '/explanation_en'}], 'gap': ''}
        for path in [P_MEANING, P_LINK]]},
        inputs['issue_ids'], inputs['known_reference_input'], policy_version=9,
        target_paths_by_issue=research._target_paths_by_issue(inputs))
    references = list(research._reference_rows(inputs, facts).values())
    assert [row['content']['target_paths'] for row in references] == [[P_MEANING], [P_LINK]]
    for path, reference in zip([P_MEANING, P_LINK], references):
        assert adjudication._research_fact_applies_to_target(reference['content'], issue_id, path)
        other_path = P_LINK if path == P_MEANING else P_MEANING
        assert not adjudication._research_fact_applies_to_target(reference['content'], issue_id, other_path)
        assert not adjudication._research_fact_applies_to_target(reference['content'], issue_id, '/grammar_links/1/segment_index')
