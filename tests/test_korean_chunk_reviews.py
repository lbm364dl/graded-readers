import asyncio
import copy
import json

import pytest

from pipeline.korean_agent_harness import digest, save
from pipeline.korean_chunk_reviews import review_chunk, verify_review
from pipeline.worker_workspace import build


def issue(explanation, path='/segments/0/text', supporting_paths=None):
    return {'explanation': explanation, 'candidate_paths': [path],
            'supporting_paths': supporting_paths or []}


class Runner:
    def __init__(self, root, review=None):
        self.root = root
        self.calls = []
        self.prompts = []
        self.review = review or {'approved': True, 'issues': [], 'prose_revision_reason_en': ''}

    async def call(self, job, prompt, schema, effort, **kwargs):
        assert effort == 'low'
        self.calls.append(job)
        self.prompts.append(prompt)
        root = self.root / 'agents' / job
        _, manifest = build(root / 'workspace', prompt, json.loads(schema.read_text()),
                            context=kwargs['workspace_context'])
        save(root / 'result.json', self.review)
        save(root / 'meta.json', {'return_code': 0, 'workspace_digest': manifest,
                                 'tool_profile': 'workspace'})
        return copy.deepcopy(self.review)


def run_review(tmp_path, runner=None, legacy_guidance=False, **changes):
    args = {'annotation': {'segments': [{'text': '아이'}]}, 'text': '아이',
            'context': {'chapter_text': '아이 아이', 'source_start': 0}, 'policy': 'Review honestly.'}
    args.update(changes)
    runner = runner or Runner(tmp_path)
    if legacy_guidance:
        from pipeline.korean_chunk_reviews import review_request
        from pipeline.korean_agent_harness import payload
        from pipeline import korean_contracts as contracts
        from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
        inputs, instructions, identity = review_request(args['annotation'], args['text'], args['context'], args['policy'])
        job = 'annotation-local-review-' + identity
        review = asyncio.run(runner.call(job,args['policy']+'\n'+instructions+payload(**inputs), contracts.schema_path('chunk-review-targeted'),'low',tool_profile='offline',workspace_context={'chunk_review_input':inputs}))
        save(tmp_path/'agents'/job/'review-input.json',inputs)
        evidence={'job':job,'input_digest':digest(inputs),'review_digest':digest(review),'form_review_guidance_digest':digest(FORM_STAGE_EVIDENCE_GUIDANCE),'issue_targets_version':1}
    else:
        review, evidence = asyncio.run(review_chunk(runner, tmp_path, **args))
    return args, review, evidence


def verify(tmp_path, args, evidence):
    return verify_review(tmp_path, evidence, annotation=args['annotation'], text=args['text'],
        chapter_text=args['context']['chapter_text'], source_start=args['context']['source_start'])


def test_review_binds_exact_occurrence_context_and_preserves_duplicate_positions(tmp_path):
    args, _, evidence = run_review(tmp_path)
    assert verify(tmp_path, args, evidence)['approved']
    _, _, repeated = run_review(tmp_path)
    assert repeated == evidence
    for changes in [
        {'context': {'chapter_text': '아이 아이', 'source_start': 3}},
        {'context': {'chapter_text': '아이 changes', 'source_start': 0}},
        {'annotation': {'segments': [{'text': '아이', 'meaning': 'changed'}]}},
    ]:
        _, _, changed = run_review(tmp_path, **changes)
        assert changed['job'] != evidence['job']
        altered = {**args, **changes}
        with pytest.raises(ValueError, match='stale, rejected or mismatched'):
            verify(tmp_path, altered, evidence)


def test_typed_review_pinpoints_repeated_occurrence_and_keeps_support_as_context(tmp_path):
    annotation = {'segments': [{'text': '아이', 'meaning_en': 'child'},
                               {'text': '아이', 'meaning_en': 'adult'}]}
    finding = issue('Only the second occurrence has the wrong meaning.',
                    '/segments/1/meaning_en', ['/segments/0/meaning_en'])
    runner = Runner(tmp_path, {'approved': False, 'issues': [finding],
                               'prose_revision_reason_en': ''})
    args, review, evidence = run_review(tmp_path, runner, annotation=annotation,
        text='아이아이', context={'chapter_text': '아이아이', 'source_start': 0})
    assert evidence['issue_targets_version'] == 1
    assert verify_review(tmp_path, evidence, annotation=annotation, text=args['text'],
        chapter_text=args['text'], source_start=0, require_approved=False) == review
    assert review['issues'][0]['candidate_paths'] == ['/segments/1/meaning_en']
    from pipeline.annotation_issue_targets import ISSUE_TARGET_GUIDANCE
    assert ISSUE_TARGET_GUIDANCE in runner.prompts[0]
    inputs = json.loads((tmp_path / 'agents' / evidence['job'] / 'review-input.json').read_text())
    assert inputs['issue_targets_version'] == 1
    with pytest.raises(ValueError, match='target version changed'):
        verify_review(tmp_path, {k: v for k, v in evidence.items() if k != 'issue_targets_version'},
            annotation=annotation, text=args['text'], chapter_text=args['text'],
            source_start=0, require_approved=False)


@pytest.mark.parametrize('target,supporting', [
    ('/segments/1/meaning_en', []), ('/segments/0', []),
    ('/segments/0/meaning_en', ['/segments/0/meaning_en']),
    ('/segments/0/form_steps', []),
])
def test_new_review_rejects_invalid_or_overlapping_paths_before_receipt(tmp_path, target, supporting):
    annotation = {'segments': [{'text': '아이', 'meaning_en': 'child', 'form_steps': []}]}
    runner = Runner(tmp_path, {'approved': False, 'issues': [issue('Wrong meaning.', target, supporting)],
                              'prose_revision_reason_en': ''})
    with pytest.raises(ValueError):
        run_review(tmp_path, runner, annotation=annotation)
    assert not list((tmp_path / 'agents').glob('*/review-input.json'))


def test_legacy_string_review_receipt_still_replays_without_new_marker(tmp_path):
    from pipeline import korean_contracts as contracts
    inputs = {'annotation': {'segments': [{'text': '아이'}]}, 'text': '아이',
              'context': {'chapter_text': '아이 아이', 'source_start': 0}}
    runner = Runner(tmp_path, {'approved': False, 'issues': ['Wrong sense at segment 0'],
                              'prose_revision_reason_en': ''})
    job = 'annotation-local-review-legacy'
    review = asyncio.run(runner.call(job, 'Historical review.', contracts.schema_path('chunk-review'),
        'low', workspace_context={'chunk_review_input': inputs}))
    save(tmp_path / 'agents' / job / 'review-input.json', inputs)
    evidence = {'job': job, 'input_digest': digest(inputs), 'review_digest': digest(review)}
    assert verify_review(tmp_path, evidence, annotation=inputs['annotation'], text='아이',
        chapter_text='아이 아이', source_start=0, require_approved=False) == review


def test_korean_review_prompt_compares_complete_copula_then_past_stages(tmp_path):
    annotation = {'segments': [{
        'text': '아들이었다', 'type': 'word', 'meaning_en': 'was a son',
        'lemma': '아들', 'lexical_kind': 'vocabulary', 'lexical_id': '아들/명',
        'form_steps': [
            {'form': '아들이다', 'label': 'Copula', 'meaning_en': 'is a son',
             'grammar_entry_ids': ['copula-ida']},
            {'form': '아들이었다', 'label': 'Past copula', 'meaning_en': 'was a son',
             'grammar_entry_ids': ['past-ass-eoss']},
        ],
    }]}
    context = {'chapter_text': '그는 아들이었다.', 'source_start': 3,
        'approved_grammar': [
            {'id': 'copula-ida', 'title_en': 'Is or was', 'pattern': '이다',
             'explanation_en': 'Connects a noun to what someone or something is. It can take past and polite endings.'},
            {'id': 'past-ass-eoss', 'title_en': 'Past time', 'pattern': '-았/었-',
             'explanation_en': 'Places the action or state before now.'},
        ]}
    runner = Runner(tmp_path)
    run_review(tmp_path, runner, annotation=annotation, text='아들이었다.', context=context)
    prompt = runner.prompts[-1]
    assert 'A non-past intermediate can correctly precede an explicit past transformation' in prompt
    assert 'a final past stage still glossed as present' in prompt
    assert 'Do not report a defect by restating a correct submitted field' in prompt
    assert '아들이다' in prompt and 'is a son' in prompt
    assert '아들이었다' in prompt and 'was a son' in prompt
    assert 'It can take past and polite endings' in prompt
    assert 'A label such as “honorific” or “past” does not make a prefinal stem or bound inflection complete' in prompt
    assert 'complete citation-form intermediate followed by an observed past connective can be valid' in prompt

    wrong_meaning = copy.deepcopy(annotation)
    wrong_meaning['segments'][0]['form_steps'][1]['meaning_en'] = 'is a son'
    run_review(tmp_path, runner, annotation=wrong_meaning, text='아들이었다.', context=context)
    wrong_meaning_prompt = runner.prompts[-1]
    assert '"meaning_en": "is a son"' in wrong_meaning_prompt
    assert 'a final past stage still glossed as present' in wrong_meaning_prompt

    # A wrong link remains actionable: the same instruction explicitly tells
    # the reviewer to report a mismatched past lesson instead of overlooking it.
    wrong_link = copy.deepcopy(annotation)
    wrong_link['segments'][0]['form_steps'][1]['grammar_entry_ids'] = ['copula-ida']
    run_review(tmp_path, runner, annotation=wrong_link, text='아들이었다.', context=context)
    wrong_prompt = runner.prompts[-1]
    assert 'copula-ida' in wrong_prompt
    assert 'a wrong or missing past link' in wrong_prompt


def test_form_chain_review_proof_tracks_current_completeness_guidance_and_can_only_be_reseeded(tmp_path):
    from pipeline.korean_chunk_reviews import StaleFormReviewGuidanceError

    annotation = {'segments': [{'text': '갔다', 'form_steps': [
        {'form': '갔다', 'reading': '가-+-았-+-다', 'label': 'past',
         'meaning_en': 'went', 'grammar_entry_ids': ['past-ass-eoss']}]}]}
    context = {'chapter_text': '갔다', 'source_start': 0}
    args, _, evidence = run_review(tmp_path, legacy_guidance=True, annotation=annotation, text='갔다', context=context)
    assert evidence['form_review_guidance_digest']
    assert verify(tmp_path, args, evidence)['approved']

    stale = dict(evidence)
    stale.pop('form_review_guidance_digest')
    with pytest.raises(StaleFormReviewGuidanceError, match='predates the current complete-stage guidance'):
        verify(tmp_path, args, stale)
    # Stale local approval can supply the exact candidate for fresh review only;
    # all review, input and artifact digests remain mandatory.
    assert verify_review(tmp_path, stale, annotation=annotation, text='갔다',
        chapter_text='갔다', source_start=0, allow_stale_form_guidance=True)['approved']
    with pytest.raises(ValueError, match='mismatched'):
        verify_review(tmp_path, {**stale, 'review_digest': '0' * 64}, annotation=annotation,
            text='갔다', chapter_text='갔다', source_start=0, allow_stale_form_guidance=True)


def test_prior_complete_form_guidance_is_compatible_but_other_or_changed_proofs_are_not(tmp_path):
    from pipeline.annotation_review_guidance import FORM_STAGE_COMPATIBLE_REVIEW_DIGESTS
    from pipeline.korean_chunk_reviews import StaleFormReviewGuidanceError

    annotation = {'segments': [{'text': '갔다', 'form_steps': [
        {'form': '갔다', 'reading': '가-+-았-+-다', 'label': 'past',
         'meaning_en': 'went', 'grammar_entry_ids': ['past-ass-eoss']}]}]}
    context = {'chapter_text': '갔다', 'source_start': 0}
    args, _, evidence = run_review(tmp_path, legacy_guidance=True, annotation=annotation, text='갔다', context=context)
    assert FORM_STAGE_COMPATIBLE_REVIEW_DIGESTS == {
        "4f0ac6c6d35d8f450482f608436a9fa57cab7822c0ebf96758f56fe5f9e96a3d",
        "2da6beaba39d3641ea15f889ed3c139a78ad6cae37a041d523b956a3aebfbf43",
    }
    prior_complete_digest = next(iter(FORM_STAGE_COMPATIBLE_REVIEW_DIGESTS))

    # The accepted prior digest had the same complete-form/stem rule. All exact
    # reviewed-content and workspace bindings still apply.
    compatible = {**evidence, 'form_review_guidance_digest': prior_complete_digest}
    assert verify(tmp_path, args, compatible)['approved']
    with pytest.raises(ValueError, match='mismatched'):
        verify(tmp_path, {**args, 'text': '가다'}, compatible)

    # A pre-completeness / missing / arbitrary digest never enters the narrow
    # compatibility window and cannot be used to retain an approval.
    for stale_digest in ('0' * 64, None):
        stale = dict(evidence)
        if stale_digest is None:
            stale.pop('form_review_guidance_digest')
        else:
            stale['form_review_guidance_digest'] = stale_digest
        with pytest.raises(StaleFormReviewGuidanceError):
            verify(tmp_path, args, stale)

    rejected_runner = Runner(tmp_path, {'approved': False, 'issues': [issue('Incomplete form stage', '/segments/0/form_steps/0/form')],
                                        'prose_revision_reason_en': ''})
    _, _, rejected_evidence = run_review(tmp_path, rejected_runner, legacy_guidance=True, annotation=annotation,
        text='갔다', context=context)
    rejected_compatible = {**rejected_evidence,
                           'form_review_guidance_digest': prior_complete_digest}
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify(tmp_path, args, rejected_compatible)


def test_rejected_local_review_cannot_approve_publication(tmp_path):
    runner = Runner(tmp_path, {'approved': False, 'issues': [issue('Wrong tense at segment 0')],
                               'prose_revision_reason_en': ''})
    args, review, evidence = run_review(tmp_path, runner)
    assert not review['approved']
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify(tmp_path, args, evidence)


def test_single_chunk_review_verification_binds_real_parent_and_never_claims_chapter_approval(tmp_path):
    from pipeline.korean_chunk_reviews import verify_chunk_review

    annotation = {'segments': [{'text': '사람', 'type': 'word', 'meaning_en': 'person',
        'lemma': '사람', 'lexical_kind': 'vocabulary', 'lexical_id': '사람/명',
        'story_importance_en': '', 'form_steps': []}],
        'grammar_links': [], 'inflected_segment_indices': []}
    text = '사람'
    prefix, suffix = '앞 문장. ', ' 뒤 문장.'
    parent = prefix + text + suffix
    context = {'chapter_text': parent, 'source_start': len(prefix)}
    runner = Runner(tmp_path)
    review, proof = asyncio.run(review_chunk(runner, tmp_path, annotation=annotation,
        text=text, context=context, policy='Review this chunk.'))
    assert review['approved']

    verified = verify_chunk_review(tmp_path, proof, annotation=annotation, text=text,
        chapter_text=parent, source_start=len(prefix))
    assert verified['scope'] == 'chunk'
    assert verified['kind'] == 'ordinary'
    assert 'chapter_approved' not in verified
    wrapped = {'kind': 'ordinary', 'normal_review': proof}
    assert verify_chunk_review(tmp_path, wrapped, annotation=annotation, text=text,
        chapter_text=parent, source_start=len(prefix))['review']['approved']

    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify_chunk_review(tmp_path, proof, annotation=annotation, text=text,
            chapter_text=parent, source_start=len(prefix) + 1)
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify_chunk_review(tmp_path, proof, annotation=annotation, text=text,
            chapter_text=parent + ' changed', source_start=len(prefix))


def test_prose_repair_requires_explicit_rejection(tmp_path):
    runner = Runner(tmp_path, {'approved': True, 'issues': [],
                               'prose_revision_reason_en': 'Change the actual wording.'})
    with pytest.raises(ValueError, match='explicit rejected review'):
        run_review(tmp_path, runner)
    runner.review.update(approved=False, issues=[issue('Actual prose contradicts context.')])
    _, review, _ = run_review(tmp_path, runner)
    assert review['prose_revision_reason_en']


def test_rewriting_sidecar_and_digest_cannot_change_reviewed_workspace_inputs(tmp_path):
    args, _, evidence = run_review(tmp_path)
    altered = copy.deepcopy(args)
    altered['annotation']['segments'][0]['meaning'] = 'unreviewed'
    inputs = {k: altered[k] for k in ('annotation', 'text', 'context')}
    inputs['issue_targets_version'] = 1
    save(tmp_path / 'agents' / evidence['job'] / 'review-input.json', inputs)
    forged = {**evidence, 'input_digest': digest(inputs)}
    with pytest.raises(ValueError, match='differ from the worker evidence'):
        verify(tmp_path, altered, forged)


def test_korean_adjudication_replay_only_accepts_primary_sources_from_original_review_context(tmp_path, monkeypatch):
    import pipeline.annotation_adjudication as adjudication
    from pipeline.korean_agent_harness import annotation_chunk_record
    from pipeline.korean_chunk_reviews import verify_assembly_reviews, verify_chunk_review

    annotation = {'segments': [{'text': '갈지도', 'type': 'word',
        'meaning_en': 'whether', 'lemma': '가다', 'lexical_kind': 'vocabulary',
        'lexical_id': '가다/동', 'story_importance_en': '', 'form_steps': []}],
        'grammar_links': [], 'inflected_segment_indices': []}
    text = '갈지도'
    source = {'reference_id': 'nikl-grammar-86133', 'title': '-을지',
              'url': 'https://krdict.korean.go.kr/eng/dicSearch/SearchView?ParaWordNo=86133',
              'excerpt_en': 'A connective ending for a vague doubt about an assumption.'}
    context = {'chapter_text': text, 'source_start': 0, 'approved_words': [],
        'approved_grammar': [], 'linguistic_reference': {'entries': []},
        'lexical_reference': {'entries': []}, 'official_primary_sources': [source]}
    policy = 'Review this Korean occurrence.'
    rejected_runner = Runner(tmp_path, {'approved': False,
        'issues': [issue('The component gloss needs review.', '/segments/0/meaning_en')], 'prose_revision_reason_en': ''})
    review, evidence = asyncio.run(review_chunk(rejected_runner, tmp_path,
        annotation=annotation, text=text, context=context, policy=policy))
    record = annotation_chunk_record('proposal-0', text, annotation)
    save(tmp_path / 'agents/proposal-0/result.json', annotation)
    save(tmp_path / 'agents/proposal-0/meta.json', {'return_code': 0})
    expected_refs = {
        'linguistic-reference': {'kind': 'primary_source', 'content': context['linguistic_reference']},
        'lexical-reference': {'kind': 'primary_source', 'content': context['lexical_reference']},
        'review-policy': {'kind': 'explicit_review_policy', 'content': policy},
        'nikl-grammar-86133': {'kind': 'primary_source', 'content': source},
    }
    replay = {'current_review': review, 'prior_history': [],
        'context': {'chunk_review_context': context, 'review_policy': policy},
        'known_reference_input': expected_refs,
        'deterministic_gate_evidence': {'passed': True, 'issues': [],
            'candidate_digest': digest(annotation), 'source_text_digest': digest(text)},
        'normal_review_receipt': {'kind': 'composite', 'review_digest': digest(review),
                                  'components': evidence}}
    proof = {'kind': 'adjudicated', 'normal_review': evidence,
             'adjudication': {'job': 'adjudication-proof'}, 'replay_inputs': replay}
    meta = {'chunk_reviews_version': 2, 'chunks': [record], 'chunk_reviews': [proof]}

    def verify_replay(*args, **kwargs):
        assert kwargs['known_reference_input'] == expected_refs
        return {'status': 'cleared', 'approved': True}
    monkeypatch.setattr(adjudication, 'verify_adjudication_evidence', verify_replay)
    verify_assembly_reviews(tmp_path, meta, expected_reference_sources={
        'approved_words': {}, 'approved_grammar': {},
        'linguistic_reference': context['linguistic_reference'],
        'lexical_reference': context['lexical_reference'],
        'review-policy': policy})

    # The extracted single-chunk API supports an actual parent chapter and
    # nonzero offset while retaining the exact same adjudicated proof checks.
    parent_prefix, parent_suffix = 'Earlier context. ', ' Later context.'
    parent = parent_prefix + text + parent_suffix
    parent_context = {**context, 'chapter_text': parent, 'source_start': len(parent_prefix)}
    parent_review, parent_evidence = asyncio.run(review_chunk(rejected_runner, tmp_path,
        annotation=annotation, text=text, context=parent_context, policy=policy))
    parent_replay = {
        'current_review': parent_review,
        'prior_history': [],
        'context': {'chunk_review_context': parent_context, 'review_policy': policy},
        'known_reference_input': expected_refs,
        'deterministic_gate_evidence': {'passed': True, 'issues': [],
            'candidate_digest': digest(annotation), 'source_text_digest': digest(text)},
        'normal_review_receipt': {'kind': 'composite', 'review_digest': digest(parent_review),
                                  'components': parent_evidence},
    }
    parent_proof = {'kind': 'adjudicated', 'normal_review': parent_evidence,
        'adjudication': {'job': 'adjudication-proof'}, 'replay_inputs': parent_replay}
    verified_chunk = verify_chunk_review(tmp_path, parent_proof,
        annotation=annotation, text=text, chapter_text=parent,
        source_start=len(parent_prefix))
    assert verified_chunk['scope'] == 'chunk'
    assert verified_chunk['kind'] == 'adjudicated'
    assert verified_chunk['adjudication']['approved'] is True
    assert 'chapter_approved' not in verified_chunk
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify_chunk_review(tmp_path, parent_proof, annotation=annotation,
            text=text, chapter_text=parent, source_start=len(parent_prefix) + 1)
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify_chunk_review(tmp_path, parent_proof, annotation=annotation,
            text=text, chapter_text=parent + ' changed', source_start=len(parent_prefix))
    tampered_chunk = copy.deepcopy(parent_proof)
    tampered_chunk['replay_inputs']['known_reference_input']['unreviewed'] = {
        'kind': 'primary_source',
        'content': {'reference_id': 'unreviewed', 'excerpt_en': 'claim'}}
    with pytest.raises(ValueError, match='references differ from the reviewed inputs'):
        verify_chunk_review(tmp_path, tampered_chunk, annotation=annotation,
            text=text, chapter_text=parent, source_start=len(parent_prefix))

    # Omitting a pinned source or smuggling an unreviewed one both fail.
    tampered = copy.deepcopy(meta)
    tampered['chunk_reviews'][0]['replay_inputs']['known_reference_input'].pop('nikl-grammar-86133')
    with pytest.raises(ValueError, match='references differ from the reviewed inputs'):
        verify_assembly_reviews(tmp_path, tampered)
    tampered = copy.deepcopy(meta)
    tampered['chunk_reviews'][0]['replay_inputs']['known_reference_input']['unreviewed'] = {
        'kind': 'primary_source', 'content': {'reference_id': 'unreviewed', 'excerpt_en': 'claim'}}
    with pytest.raises(ValueError, match='references differ from the reviewed inputs'):
        verify_assembly_reviews(tmp_path, tampered)


def test_publication_requires_every_chunk_review_and_replays_rejections(tmp_path):
    from tests.test_korean_pipeline import make_reviewed_run
    from pipeline.korean_agent_harness import read, annotation_chunk_record
    from pipeline import korean_contracts as contracts, korean_publication as publication
    make_reviewed_run(tmp_path)
    raw = read(tmp_path / 'agents/annotation/result.json')
    text = ''.join(s['text'] for s in raw['segments'])
    texts = contracts.annotation_chunks(text)
    values = contracts.slice_annotations(raw, texts)
    records, proofs, start = [], [], 0
    for index, (chunk_text, value) in enumerate(zip(texts, values)):
        job = f'proposal-{index}'
        save(tmp_path / 'agents' / job / 'result.json', value)
        save(tmp_path / 'agents' / job / 'meta.json', {'return_code': 0})
        records.append(annotation_chunk_record(job, chunk_text, value))
        _, _, proof = run_review(tmp_path, annotation=value, text=chunk_text,
            context={'chapter_text': text, 'source_start': start, 'target_level': 1,
                     'source_plan': read(tmp_path / 'source-plan.json'),
                     'lexical_plan': read(tmp_path / 'lexical-plan.json')})
        proofs.append(proof)
        start += len(chunk_text)
    meta = {'return_code': 0, 'kind': 'annotation_assembly', 'chunks': records,
            'chunk_reviews_version': 1, 'chunk_reviews': proofs}
    path = tmp_path / 'agents/annotation/meta.json'
    save(path, meta)
    publication.verify_run(tmp_path)
    report = read(tmp_path / 'report.json')
    report['stages']['annotation']['chunk_reviews_digest'] = digest(proofs)
    save(tmp_path / 'report.json', report)
    save(path, {k: v for k, v in meta.items() if not k.startswith('chunk_reviews')})
    with pytest.raises(ValueError, match='evidence changed after chapter approval'):
        publication.verify_run(tmp_path)
    del report['stages']['annotation']['chunk_reviews_digest']
    save(tmp_path / 'report.json', report)
    save(path, {**meta, 'chunk_reviews': proofs[:-1]})
    with pytest.raises(ValueError, match='review coverage is incomplete'):
        publication.verify_run(tmp_path)
    save(path, meta)
    save(tmp_path / 'agents' / proofs[0]['job'] / 'result.json',
         {'approved': False, 'issues': [issue('Wrong sense', '/segments/0/meaning_en')], 'prose_revision_reason_en': ''})
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        publication.verify_run(tmp_path)


def test_expanded_approved_grammar_inventory_preserves_subset_review_receipt(tmp_path):
    from pipeline.korean_chunk_reviews import verify_chunk_review
    used = {'id': 'used', 'explanation_en': 'A previously approved lesson.'}
    prerequisite = {'id': 'possessive-ui', 'explanation_en': 'Connects a possessor to its noun.'}
    registry = {row['id']: row for row in (used, prerequisite)}
    annotation = {'segments': [{'text': '아이', 'lexical_id': 'draft-function'}],
                  'grammar_links': [{'entry_id': 'draft-function'}]}
    context = {'chapter_text': '아이', 'source_start': 0, 'target_level': 'l1',
               'approved_grammar': [used], 'draft_grammar_ids': ['draft-function']}
    runner = Runner(tmp_path)
    args, _, evidence = run_review(tmp_path, runner, annotation=annotation, text='아이', context=context)
    input_path = tmp_path / 'agents' / evidence['job'] / 'review-input.json'
    original_bytes = input_path.read_bytes()
    proof = {'kind': 'ordinary', **evidence}
    replay = verify_chunk_review(tmp_path, proof, annotation=annotation, text='아이',
        chapter_text='아이', source_start=0, expected_context={'target_level': 'l1'},
        expected_reference_sources={'approved_grammar': registry})
    assert replay['review']['approved']
    assert input_path.read_bytes() == original_bytes
    assert len(runner.calls) == 1
    assert json.loads(original_bytes)['context']['approved_grammar'] == [used]

    expanded = {**context, 'approved_grammar': list(registry.values())}
    _, _, fresh = run_review(tmp_path, runner, annotation=annotation, text='아이', context=expanded)
    fresh_inputs = json.loads((tmp_path / 'agents' / fresh['job'] / 'review-input.json').read_text())
    assert fresh['job'] != evidence['job']
    assert fresh_inputs['context']['draft_grammar_ids'] == ['draft-function']
    assert {entry['id'] for entry in fresh_inputs['context']['approved_grammar']} == {'used', 'possessive-ui'}
    assert input_path.read_bytes() == original_bytes

@pytest.mark.parametrize('adjudicated', [False, True])
def test_checkpoint_approval_replay_preserves_original_context_with_additive_sources(tmp_path, monkeypatch, adjudicated):
    from pipeline.korean_agent_harness import reusable_checkpoint_approval
    import pipeline.annotation_adjudication as host
    annotation = {'segments': [{'text': '아이', 'meaning_en': 'child', 'lexical_id': 'used-missing'}], 'grammar_links': []}
    context = {'chapter_text': '아이 아이', 'source_start': 3,
        'approved_words': [], 'approved_grammar': [],
        'lexical_candidates': [{'id': 'prior-unused', 'headword': '아이', 'pos': 'noun', 'meaning': 'child'}],
        'linguistic_reference': {}, 'lexical_reference': {},
        'official_primary_sources': [{'reference_id': 'original-primary', 'record': 30494}]}
    policy = 'Review honestly.'
    verdict = {'approved': not adjudicated, 'issues': [] if not adjudicated else
        [issue('Check this meaning.', '/segments/0/meaning_en')], 'prose_revision_reason_en': ''}
    runner = Runner(tmp_path, verdict)
    _, normal = asyncio.run(review_chunk(runner, tmp_path, annotation=annotation,
        text='아이', context=context, policy=policy))
    proof = {'kind': 'ordinary', **normal}
    if adjudicated:
        references = {key.replace('_', '-'): {'kind': 'primary_source', 'content': context[key]}
                      for key in ('linguistic_reference', 'lexical_reference')}
        references['review-policy'] = {'kind': 'explicit_review_policy', 'content': policy}
        references['original-primary'] = {'kind': 'primary_source',
            'content': context['official_primary_sources'][0]}
        proof = {'kind': 'adjudicated', 'normal_review': normal,
            'adjudication': {'job': 'exact-proof'}, 'replay_inputs': {
                'current_review': verdict, 'prior_history': [],
                'context': {'chunk_review_context': context, 'review_policy': policy},
                'known_reference_input': references, 'deterministic_gate_evidence': {},
                'normal_review_receipt': {}}}
        def replay(_run, evidence, **kwargs):
            assert evidence == {'job': 'exact-proof'}
            assert kwargs['candidate'] == annotation
            assert kwargs['context']['chunk_review_context'] == context
            return {'status': 'cleared', 'approved': True}
        monkeypatch.setattr(host, 'verify_adjudication_evidence', replay)
    row = {'review_context': context, 'review': proof}
    current = {**context, 'official_primary_sources': context['official_primary_sources'] +
        [{'reference_id': 'verified-extra', 'record': 50000}]}
    assert reusable_checkpoint_approval(tmp_path, row, annotation=annotation,
        text='아이', context=current, policy=policy) == proof
    assert runner.calls == [normal['job']]
    expanded = {**current, 'lexical_candidates': context['lexical_candidates'] +
        [{'id': 'new-unused', 'headword': '아이', 'pos': 'noun', 'meaning': 'alternative child'}]}
    assert reusable_checkpoint_approval(tmp_path, row, annotation=annotation, text='아이', context=expanded, policy=policy) == proof
    assert runner.calls == [normal['job']]
    for candidates in ([], [{'id': 'prior-unused', 'headword': '아이', 'pos': 'noun', 'meaning': 'changed'}],
                       context['lexical_candidates'] + [{'id': 'prior-unused', 'meaning': 'conflict'}]):
        with pytest.raises(ValueError):
            reusable_checkpoint_approval(tmp_path, row, annotation=annotation, text='아이', context={**current, 'lexical_candidates': candidates}, policy=policy)
    for changed in ({**current, 'source_start': 0}, {**current, 'chapter_text': '아이아이'},
                    {**current, 'lexical_reference': {'changed': True}}):
        with pytest.raises(ValueError, match='context changed'):
            reusable_checkpoint_approval(tmp_path, row, annotation=annotation,
                text='아이', context=changed, policy=policy)
    with pytest.raises(ValueError, match='policy changed'):
        reusable_checkpoint_approval(tmp_path, row, annotation=annotation,
            text='아이', context=current, policy=policy + ' New rule.')
    altered = copy.deepcopy(annotation)
    altered['segments'][0]['meaning_en'] = 'adult'
    with pytest.raises(ValueError, match='policy changed'):
        reusable_checkpoint_approval(tmp_path, row, annotation=altered,
            text='아이', context=current, policy=policy)
    # Previously supplied evidence cannot be replaced by additive context.
    with pytest.raises(ValueError, match='primary evidence changed'):
        reusable_checkpoint_approval(tmp_path,
            row, annotation=annotation,
            text='아이', context={**context, 'official_primary_sources': []}, policy=policy)

@pytest.mark.parametrize('owner', ['segment', 'stage', 'expression'])
@pytest.mark.parametrize('reference_field', ['approved_words', 'lexical_candidates'])
def test_checkpoint_new_used_word_reference_requires_fresh_review(tmp_path, owner, reference_field):
    from pipeline.korean_agent_harness import reusable_checkpoint_approval
    annotation = {'segments': [{'text': '아이', 'meaning_en': 'child', 'form_steps': []}],
                  'grammar_links': [], 'expression_links': []}
    if owner == 'segment':
        annotation['segments'][0]['lexical_id'] = 'used'
    elif owner == 'stage':
        annotation['segments'][0]['form_steps'] = [{'form': '아이', 'lexical_id': 'used'}]
    else:
        annotation['expression_links'] = [{'entry_id': 'used'}]
    context = {'chapter_text': '아이', 'source_start': 0, reference_field: []}
    _, proof = asyncio.run(review_chunk(Runner(tmp_path), tmp_path, annotation=annotation,
        text='아이', context=context, policy='Review honestly.'))
    row = {'review_context': context, 'review': proof}
    assert reusable_checkpoint_approval(tmp_path, row, annotation=annotation, text='아이',
        context={**context, reference_field: [{'id': 'unused'}]}, policy='Review honestly.') == proof
    with pytest.raises(ValueError, match='used reference added'):
        reusable_checkpoint_approval(tmp_path, row, annotation=annotation, text='아이',
            context={**context, reference_field: [{'id': 'used'}]}, policy='Review honestly.')
    if owner == 'stage':
        stale = {key: value for key, value in proof.items() if key != 'form_review_guidance_digest'}
        with pytest.raises(ValueError, match='guidance digest'):
            reusable_checkpoint_approval(tmp_path, {**row, 'review': stale},
                annotation=annotation, text='아이', context=context, policy='Review honestly.')


def test_carry_checkpoint_reuses_authenticated_packet_without_new_review(tmp_path, monkeypatch):
    from pipeline import annotation_reference_carry as carry
    from pipeline.annotation_adjudication import normalize_review
    from pipeline.korean_agent_harness import reusable_checkpoint_approval
    annotation = {'segments': [{'text': '아이', 'meaning_en': 'child'}], 'grammar_links': []}
    context = {'chapter_text': '아이 아이', 'source_start': 3,
        'approved_words': [], 'approved_grammar': [], 'linguistic_reference': {}, 'lexical_reference': {}}
    original_issue = {'approved': False, 'issues': [issue('A lexical claim.', '/segments/0/meaning_en')],
        'prose_revision_reason_en': ''}
    identity = normalize_review('ko', original_issue)['issues'][0]['issue_id']
    original = {'language': 'ko', 'representation': 'korean-flat', 'candidate': annotation,
        'source_text': '아이', 'context': context, 'current_review': original_issue}
    research = {'references': {'fact': {'kind': 'approved_lesson', 'content': {
        '_annotation_research_fact': True, 'issue_ids': [identity], 'fact': 'Child is the lexical meaning.'}}}}
    monkeypatch.setattr(carry, '_authenticate', lambda descriptor: (original, research))
    envelope = {'version': 1, 'sources': [{'run_relpath': 'runs/exact',
        'adjudication_job': 'annotation-adjudication-exact', 'receipt_digest': 'a'*64}]}
    carry.register_carried_research(tmp_path, envelope, candidate=annotation, source_text='아이',
        language='ko', representation='korean-flat', context=context)
    loaded = carry.load_carried_research(tmp_path, candidate=annotation, source_text='아이',
        language='ko', representation='korean-flat', context=context)
    packet = carry.bind_carried_research(tmp_path, loaded, candidate=annotation, source_text='아이',
        language='ko', representation='korean-flat', context=context)['packet']
    fresh_context = {**context, carry.CARRY_FIELD: packet}
    runner = Runner(tmp_path, {'approved': True, 'issues': [], 'prose_revision_reason_en': ''})
    _, normal = asyncio.run(review_chunk(runner, tmp_path, annotation=annotation,
        text='아이', context=fresh_context, policy='Review honestly.'))
    proof = {'kind': 'ordinary', **normal};row = {'review_context': fresh_context, 'review': proof}
    assert reusable_checkpoint_approval(tmp_path, row, annotation=annotation, text='아이',
        context=fresh_context, policy='Review honestly.') == proof
    assert runner.calls == [normal['job']]
    forged = copy.deepcopy(fresh_context);forged[carry.CARRY_FIELD]['facts'][0]['reference']['content']['fact'] = 'forged'
    with pytest.raises(ValueError):
        reusable_checkpoint_approval(tmp_path, row, annotation=annotation, text='아이',
            context=forged, policy='Review honestly.')


def test_complete_assembly_checks_each_exact_chunk_current_research(tmp_path, monkeypatch):
    import pipeline.korean_agent_harness as harness
    import pipeline.annotation_reference_carry_callers as callers
    annotation = {'segments': [{'text': '아이', 'meaning_en': 'child'}], 'grammar_links': []}
    monkeypatch.setattr(harness, 'read', lambda path: {'context': {'chapter_text': '아이아이'}})
    monkeypatch.setattr(harness, 'read_annotation_chunk', lambda *args: annotation)
    seen = []
    def eligible(run, **kwargs):
        seen.append(kwargs)
        return kwargs['context']['source_start'] == 0
    monkeypatch.setattr(callers, 'current_carry_eligibility', eligible)
    meta = {'chunks': [{'job': 'a', 'text': '아이'}, {'job': 'b', 'text': '아이'}],
        'chunk_reviews': [{'job': 'normal-a'}, {'normal_review': {'job': 'normal-b'},
                           'adjudication': {'job': 'terminal-b'}}]}
    assert not harness.current_assembly_carry_eligibility(tmp_path, meta)
    assert [row['context']['source_start'] for row in seen] == [0, 2]
    assert seen[1]['terminal_evidence'] == {'job': 'terminal-b'}
    monkeypatch.setattr(callers, 'current_carry_eligibility', lambda *args, **kwargs: True)
    assert harness.current_assembly_carry_eligibility(tmp_path, meta)
