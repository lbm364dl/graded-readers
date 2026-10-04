import asyncio
import copy
import json

import pytest

from pipeline.korean_agent_harness import digest, save
from pipeline.korean_chunk_reviews import review_chunk, verify_review
from pipeline.worker_workspace import build


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

    rejected_runner = Runner(tmp_path, {'approved': False, 'issues': ['Incomplete form stage'],
                                        'prose_revision_reason_en': ''})
    _, _, rejected_evidence = run_review(tmp_path, rejected_runner, annotation=annotation,
        text='갔다', context=context)
    rejected_compatible = {**rejected_evidence,
                           'form_review_guidance_digest': prior_complete_digest}
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify(tmp_path, args, rejected_compatible)


def test_rejected_local_review_cannot_approve_publication(tmp_path):
    runner = Runner(tmp_path, {'approved': False, 'issues': ['Wrong tense at segment 0'],
                               'prose_revision_reason_en': ''})
    args, review, evidence = run_review(tmp_path, runner)
    assert not review['approved']
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        verify(tmp_path, args, evidence)


def test_prose_repair_requires_explicit_rejection(tmp_path):
    runner = Runner(tmp_path, {'approved': True, 'issues': [],
                               'prose_revision_reason_en': 'Change the actual wording.'})
    with pytest.raises(ValueError, match='explicit rejected review'):
        run_review(tmp_path, runner)
    runner.review.update(approved=False, issues=['Actual prose contradicts context.'])
    _, review, _ = run_review(tmp_path, runner)
    assert review['prose_revision_reason_en']


def test_rewriting_sidecar_and_digest_cannot_change_reviewed_workspace_inputs(tmp_path):
    args, _, evidence = run_review(tmp_path)
    altered = copy.deepcopy(args)
    altered['annotation']['segments'][0]['meaning'] = 'unreviewed'
    inputs = {k: altered[k] for k in ('annotation', 'text', 'context')}
    save(tmp_path / 'agents' / evidence['job'] / 'review-input.json', inputs)
    forged = {**evidence, 'input_digest': digest(inputs)}
    with pytest.raises(ValueError, match='differ from the worker evidence'):
        verify(tmp_path, altered, forged)


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
         {'approved': False, 'issues': ['Wrong sense'], 'prose_revision_reason_en': ''})
    with pytest.raises(ValueError, match='stale, rejected or mismatched'):
        publication.verify_run(tmp_path)
