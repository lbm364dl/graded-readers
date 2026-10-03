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
        self.review = review or {'approved': True, 'issues': [], 'prose_revision_reason_en': ''}

    async def call(self, job, prompt, schema, effort, **kwargs):
        assert effort == 'low'
        self.calls.append(job)
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
