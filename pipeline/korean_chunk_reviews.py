"""Independent occurrence reviews bound to exact chunk content and chapter context."""
from pathlib import Path

from jsonschema import validate

from pipeline import korean_contracts as contracts

INSTRUCTIONS = '''Review only the supplied Korean annotation chunk independently.
Read chapter_text and the source plan for surrounding narrative context, but report
concrete defects only in this chunk. Check every occurrence, lexical sense, grammar
function, complete-form meaning, inflection chain, and English explanation. Check
that linked approved lessons actually explain the occurrence. Do not invent lexical
entries for productive grammar or infer idiom meanings from component glosses.
context.draft_grammar_ids explicitly identifies proposed new functions awaiting
chapter-wide identity coordination and independently reviewed dictionary lessons.
For these IDs, verify the actual formation, role and complete meaning using the
linguistic references and tools; do not reject merely because their later-stage
dictionary entry is absent. Reject an unsupported or incorrect analysis, and do
not treat a draft identity as approved dictionary content. Existing approved
lessons must still cover the linked function; a new ID cannot excuse a false role.
The exact source and structural checks have passed; focus on linguistic correctness
and useful learner explanations. Cite the exact affected form and local segment
index in each actionable issue. Do not demand paraphrasing correct explanations,
later-stage dictionary definitions, sentence-help records or curriculum bindings.
If the prose itself prevents an honest annotation, identify the exact wording and
reason; never conceal the issue by approving false analysis. Review all comparable
occurrences within this chunk. Approve only with an empty issues list. A separate
whole-chapter review still checks consistency, assembled links and overall quality.
Set prose_revision_reason_en only when the actual prose must change, identifying
the exact wording and necessary repair; otherwise leave it empty. An annotation
error alone is not a reason to rewrite sound prose. A prose revision requires
approved=false and a concrete issue. Do not rewrite any output yourself.
'''


async def review_chunk(runner, run_dir, *, annotation, text, context, policy):
    from pipeline.korean_agent_harness import digest, payload, save
    inputs = {'annotation': annotation, 'text': text, 'context': context}
    identity = digest({'inputs': inputs, 'instructions': policy + INSTRUCTIONS})
    job = f'annotation-local-review-{identity}'
    review = await runner.call(job, policy + '\n' + INSTRUCTIONS + payload(**inputs),
        contracts.schema_path('chunk-review'), 'low', tool_profile='offline',
        workspace_context={'chunk_review_input': inputs})
    validate(review, contracts.CHUNK_REVIEW)
    if review['prose_revision_reason_en'] and (review['approved'] or not review['issues']):
        raise ValueError('Korean chunk prose revision must be an explicit rejected review')
    save(run_dir / 'agents' / job / 'review-input.json', inputs)
    return review, {'job': job, 'input_digest': digest(inputs), 'review_digest': digest(review)}


def verify_review(run_dir, evidence, *, annotation, text, chapter_text, source_start, expected_context=None):
    from pipeline.agent_harness import CodexRunner
    from pipeline.korean_agent_harness import approved, digest, read
    job = evidence['job']
    if Path(job).name != job or not job.startswith('annotation-local-review-'):
        raise ValueError('Invalid Korean chunk review job')
    root = run_dir / 'agents' / job
    inputs, review, meta = (read(root / name) for name in
                            ('review-input.json', 'result.json', 'meta.json'))
    validate(review, contracts.CHUNK_REVIEW)
    if (not approved(review) or review['prose_revision_reason_en'] or meta.get('return_code') != 0
            or digest(review) != evidence['review_digest']
            or digest(inputs) != evidence['input_digest']
            or inputs['annotation'] != annotation or inputs['text'] != text
            or inputs['context']['chapter_text'] != chapter_text
            or inputs['context']['source_start'] != source_start):
        raise ValueError('Korean chunk independent review is stale, rejected or mismatched')
    if any(inputs['context'].get(k) != v for k, v in (expected_context or {}).items()):
        raise ValueError('Korean chunk review planning context changed')
    CodexRunner._check_tool_profile(root, 'offline', meta)
    if meta.get('workspace_digest'):
        workspace = root / 'workspace'
        rows = read(workspace / 'INDEX.json')
        recorded = [read(workspace / row['path']) for row in rows if row['field'] == 'chunk_review_input']
        if recorded != [inputs]:
            raise ValueError('Korean chunk review inputs differ from the worker evidence')
    return review


def verify_assembly_reviews(run_dir, meta, *, expected_context=None):
    from pipeline.korean_agent_harness import read_annotation_chunk
    records = meta['chunks']
    if meta.get('chunk_reviews_version') != 1 or len(meta.get('chunk_reviews', [])) != len(records):
        raise ValueError('Korean chunk review coverage is incomplete')
    chapter_text = ''.join(record['text'] for record in records)
    start = 0
    for record, proof in zip(records, meta['chunk_reviews']):
        if Path(record['job']).name != record['job']:
            raise ValueError('Invalid Korean chunk proposal job')
        value = read_annotation_chunk(run_dir / 'agents' / record['job'] / 'result.json', record)
        verify_review(run_dir, proof, annotation=value, text=record['text'],
            chapter_text=chapter_text, source_start=start, expected_context=expected_context)
        start += len(record['text'])


def stage_evidence(run_dir, job):
    from pipeline.korean_agent_harness import digest, read
    path = run_dir / 'agents' / str(job) / 'meta.json'
    meta = read(path) if path.exists() else {}
    if 'chunk_reviews_version' in meta:
        return {'chunk_reviews_digest': digest(meta.get('chunk_reviews', []))}
    return {}
