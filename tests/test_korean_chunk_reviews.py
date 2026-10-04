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


def run_review(tmp_path, runner=None, **changes):
    args = {'annotation': {'segments': [{'text': '아이'}]}, 'text': '아이',
            'context': {'chapter_text': '아이 아이', 'source_start': 0}, 'policy': 'Review honestly.'}
    args.update(changes)
    runner = runner or Runner(tmp_path)
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
    args, _, evidence = run_review(tmp_path, annotation=annotation, text='갔다', context=context)
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
    args, _, evidence = run_review(tmp_path, annotation=annotation, text='갔다', context=context)
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
    _, _, rejected_evidence = run_review(tmp_path, rejected_runner, annotation=annotation,
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
