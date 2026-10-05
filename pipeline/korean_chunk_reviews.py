"""Independent occurrence reviews bound to exact chunk content and chapter context."""
from pathlib import Path

from jsonschema import validate

from pipeline import korean_contracts as contracts
from pipeline.annotation_issue_targets import ISSUE_TARGET_GUIDANCE, validate_issue_targets
from pipeline.annotation_review_guidance import (
    FORM_STAGE_COMPATIBLE_REVIEW_DIGESTS,
    FORM_STAGE_EVIDENCE_GUIDANCE,
)

INSTRUCTIONS = FORM_STAGE_EVIDENCE_GUIDANCE + '\n' + '''Review only the supplied Korean annotation chunk independently.
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
Determine a particle's function from the complete predicate frame and clause,
not its spelling alone. A time adjunct and a predicate-selected complement can
use the same particle while requiring different lessons. Verify the proposed
function against primary references when it is uncertain; never force a new
function into an approved lesson that teaches another role.
Each form-step meaning describes only its complete displayed form. A possessor
or topic phrase does not by itself include the following predicate's meaning;
that belongs on the full construction or clause. Descriptions of what a modifier
does belong in its label/context, while its meaning gives the complete modified
form. Check that every occurrence gloss, including names, is useful English.
Grammar contexts must be grammatical explanations of the actual contribution,
not a stock prefix concatenated with a form gloss. These checks apply to every
comparable occurrence in the chunk.
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


class StaleFormReviewGuidanceError(ValueError):
    """The candidate's form-chain review predates the current shared guidance."""


def _has_form_steps(annotation):
    if not isinstance(annotation, dict):
        return False
    return any(isinstance(segment, dict) and segment.get('form_steps')
               for segment in annotation.get('segments', []))


def _form_guidance_digest():
    from pipeline.korean_agent_harness import digest
    return digest(FORM_STAGE_EVIDENCE_GUIDANCE)


def review_request(annotation, text, context, policy):
    from pipeline.korean_agent_harness import digest
    if any(isinstance(row, dict) and any(key in row for key in ('reviewed_run_grammar', 'reviewed_run_lessons'))
           for row in (context, context.get('chunk_review_context'))):
        from pipeline.annotation_run_lessons import validate_run_lesson_context_fields
        validate_run_lesson_context_fields(context)
    inputs = {'annotation': annotation, 'text': text, 'context': context,
              'issue_targets_version': 1}
    instructions = INSTRUCTIONS + '\n' + ISSUE_TARGET_GUIDANCE + '''
Each issue also requires explanation, describing why its candidate_paths are
defective. For an actual prose revision, point to the existing affected segment
text field; this records the source defect and does not authorize an annotation
patch to rewrite source text. Do not invent an annotation meaning defect to
make a prose finding fit the schema.
'''
    if context.get('reviewed_annotation_research'):
        from pipeline.annotation_reference_carry import CARRIED_RESEARCH_GUIDANCE
        instructions += '\n' + CARRIED_RESEARCH_GUIDANCE
    if context.get('reviewed_run_lessons'):
        from pipeline.annotation_run_lessons import run_lesson_guidance
        instructions += '\n\n' + run_lesson_guidance(context['reviewed_run_lessons'])
    identity = digest({'inputs': inputs, 'instructions': policy + instructions})
    return inputs, instructions, identity


async def review_chunk(runner, run_dir, *, annotation, text, context, policy):
    from pipeline.korean_agent_harness import digest, payload, save
    if context.get('reviewed_run_lessons'):
        from pipeline.annotation_run_lessons import validate_run_lessons
        bound_run_lessons = validate_run_lessons(run_dir, context['reviewed_run_lessons'], candidate=annotation, source_text=text,
            language='ko', representation='korean-flat', context=context)
        context = {**context, 'reviewed_run_grammar': bound_run_lessons['lessons']}
    run_lesson_gate = ({'annotation_run_lesson_validation': {'run_dir': str(Path(run_dir).resolve()),
        'candidate': annotation, 'source_text': text, 'language': 'ko', 'representation': 'korean-flat',
        'envelope': bound_run_lessons['packet'], 'lessons': bound_run_lessons['lessons'], 'context': context}}
        if context.get('reviewed_run_lessons') else {})
    inputs, instructions, identity = review_request(annotation, text, context, policy)
    if context.get('reviewed_annotation_research'):
        from pipeline.annotation_reference_carry import bind_carried_research
        bind_carried_research(run_dir, context['reviewed_annotation_research'], candidate=annotation,
            source_text=text, language='ko', representation='korean-flat', context=context)
    job = f'annotation-local-review-{identity}'
    review = await runner.call(job, policy + '\n' + instructions + payload(**inputs),
        contracts.schema_path('chunk-review-targeted'), 'low', tool_profile='offline',
        workspace_context={**run_lesson_gate, 'chunk_review_input': inputs,
            'annotation_issue_targets_validation': {'candidate': annotation,
                'source_text': text, 'representation': 'korean-flat',
                'require_typed': True}})
    validate(review, contracts.CHUNK_REVIEW_TARGETED)
    validate_issue_targets(review, annotation, source_text=text,
                           representation='korean-flat', require_typed=True)
    if review['prose_revision_reason_en'] and (review['approved'] or not review['issues']):
        raise ValueError('Korean chunk prose revision must be an explicit rejected review')
    save(run_dir / 'agents' / job / 'review-input.json', inputs)
    return review, {'job': job, 'input_digest': digest(inputs), 'review_digest': digest(review),
                    'form_review_guidance_digest': _form_guidance_digest(),
                    'issue_targets_version': 1}


def verify_review(run_dir, evidence, *, annotation, text, chapter_text, source_start,
                  expected_context=None, allow_stale_form_guidance=False,
                  require_approved=True):
    from pipeline.agent_harness import CodexRunner
    from pipeline.korean_agent_harness import approved, digest, read
    job = evidence['job']
    if Path(job).name != job or not job.startswith('annotation-local-review-'):
        raise ValueError('Invalid Korean chunk review job')
    root = run_dir / 'agents' / job
    inputs, review, meta = (read(root / name) for name in
                            ('review-input.json', 'result.json', 'meta.json'))
    if inputs.get('context', {}).get('reviewed_annotation_research'):
        from pipeline.annotation_reference_carry import bind_carried_research
        bind_carried_research(run_dir, inputs['context']['reviewed_annotation_research'], candidate=annotation,
            source_text=text, language='ko', representation='korean-flat', context=inputs['context'])
    saved_context = inputs.get('context', {})
    if any(isinstance(row, dict) and any(key in row for key in ('reviewed_run_grammar', 'reviewed_run_lessons'))
           for row in (saved_context, saved_context.get('chunk_review_context'))):
        from pipeline.annotation_run_lessons import validate_run_lesson_context_fields
        validate_run_lesson_context_fields(saved_context)
    if inputs.get('context', {}).get('reviewed_run_lessons'):
        from pipeline.annotation_run_lessons import validate_run_lessons
        validate_run_lessons(run_dir, inputs['context']['reviewed_run_lessons'], candidate=annotation,
            source_text=text, language='ko', representation='korean-flat', context=inputs['context'])
    version = evidence.get('issue_targets_version')
    if version not in (None, 1) or inputs.get('issue_targets_version') != version:
        raise ValueError('Korean chunk issue target version changed')
    validate(review, contracts.CHUNK_REVIEW if version is None else contracts.CHUNK_REVIEW_TARGETED)
    if version == 1:
        validate_issue_targets(review, annotation, source_text=text,
                               representation='korean-flat', require_typed=True)
    if ((require_approved and not approved(review))
            or (not require_approved and approved(review))
            or review['prose_revision_reason_en']
            or meta.get('return_code') != 0
            or digest(review) != evidence['review_digest']
            or digest(inputs) != evidence['input_digest']
            or inputs['annotation'] != annotation or inputs['text'] != text
            or inputs['context']['chapter_text'] != chapter_text
            or inputs['context']['source_start'] != source_start):
        raise ValueError('Korean chunk independent review is stale, rejected or mismatched')
    if any(inputs['context'].get(k) != v for k, v in (expected_context or {}).items()):
        raise ValueError('Korean chunk review planning context changed')
    accepted_form_guidance_digests = (FORM_STAGE_COMPATIBLE_REVIEW_DIGESTS |
                                       {_form_guidance_digest()})
    if (_has_form_steps(annotation)
            and evidence.get('form_review_guidance_digest') not in accepted_form_guidance_digests
            and not allow_stale_form_guidance):
        raise StaleFormReviewGuidanceError(
            'Korean form-chain review predates the current complete-stage guidance')
    CodexRunner._check_tool_profile(root, 'offline', meta)
    if meta.get('workspace_digest'):
        workspace = root / 'workspace'
        rows = read(workspace / 'INDEX.json')
        recorded = [read(workspace / row['path']) for row in rows if row['field'] == 'chunk_review_input']
        if recorded != [inputs]:
            raise ValueError('Korean chunk review inputs differ from the worker evidence')
    return review


def verify_chunk_review(run_dir, proof, *, annotation, text, chapter_text, source_start,
                        expected_context=None, allow_stale_form_guidance=False,
                        expected_reference_sources=None, allow_adjudicated=True):
    """Verify one chunk review against its actual parent text and source offset.

    This is the same proof verifier used by chapter assembly, exposed for
    narrowly scoped chunks whose full parent context is available separately.
    A successful partial-chunk check is not a chapter-assembly approval.
    """
    from pipeline.korean_agent_harness import read
    if not isinstance(proof, dict):
        raise ValueError('Korean chunk review proof is malformed')
    kind = proof.get('kind')
    if kind not in (None, 'ordinary', 'adjudicated'):
        raise ValueError('Unknown Korean chunk review proof kind')
    if not allow_adjudicated or kind != 'adjudicated':
        # Preserve the assembly verifier's legacy wrapper behavior for either
        # ordinary or adjudicated proof envelopes when adjudication replay is
        # not enabled (notably v1 assemblies).
        review_proof = proof.get('normal_review', proof)
        review = verify_review(run_dir, review_proof, annotation=annotation,
            text=text, chapter_text=chapter_text, source_start=source_start,
            expected_context=expected_context,
            allow_stale_form_guidance=allow_stale_form_guidance)
        return {'scope': 'chunk', 'kind': 'ordinary', 'review': review}

    from pipeline.annotation_adjudication import verify_adjudication_evidence
    replay = proof.get('replay_inputs')
    if not isinstance(replay, dict):
        raise ValueError('Korean adjudication proof is missing replay inputs')
    raw_review = verify_review(run_dir, proof['normal_review'], annotation=annotation,
        text=text, chapter_text=chapter_text, source_start=source_start,
        expected_context=expected_context,
        allow_stale_form_guidance=allow_stale_form_guidance,
        require_approved=False)
    if raw_review != replay.get('current_review'):
        raise ValueError('Korean rejected review differs from adjudication input')
    normal_inputs = read(run_dir / 'agents' / proof['normal_review']['job'] /
                         'review-input.json')
    adjudication_context = replay.get('context', {})
    if adjudication_context.get('chunk_review_context') != normal_inputs.get('context'):
        raise ValueError('Korean adjudication context differs from the normal review context')
    review_context = normal_inputs.get('context', {})
    expected_references = {}
    for row in review_context.get('approved_words', []):
        expected_references[f"word:{row['id']}"] = {'kind': 'approved_lesson', 'content': row}
    for row in review_context.get('approved_grammar', []):
        expected_references[f"grammar:{row['id']}"] = {'kind': 'approved_lesson', 'content': row}
    for key in ('linguistic_reference', 'lexical_reference'):
        expected_references[key.replace('_', '-')] = {
            'kind': 'primary_source', 'content': review_context[key]}
    # Only source records present in the authenticated normal-review input may
    # support adjudication; an adjudication payload cannot smuggle a URL.
    official_sources = review_context.get('official_primary_sources', [])
    if not isinstance(official_sources, list):
        raise ValueError('Korean original review has malformed primary-source inputs')
    for row in official_sources:
        if not isinstance(row, dict) or not isinstance(row.get('reference_id'), str) or not row['reference_id']:
            raise ValueError('Korean original review has an unbound primary-source input')
        identity = row['reference_id']
        if identity in expected_references:
            raise ValueError(f'Korean original review repeats a primary-source identity: {identity}')
        expected_references[identity] = {'kind': 'primary_source', 'content': row}
    official_source_ids = {row['reference_id'] for row in official_sources}
    if review_context.get('reviewed_annotation_research'):
        from pipeline.annotation_reference_carry import bind_carried_research
        expected_references.update(bind_carried_research(run_dir, review_context['reviewed_annotation_research'],
            candidate=annotation, source_text=text, language='ko', representation='korean-flat',
            context=review_context, current_review=raw_review)['references'])
    run_lesson_source_ids = set()
    if review_context.get('reviewed_run_lessons'):
        from pipeline.annotation_run_lessons import validate_run_lessons
        run_lesson_refs = validate_run_lessons(run_dir, review_context['reviewed_run_lessons'], candidate=annotation,
            source_text=text, language='ko', representation='korean-flat', context=review_context,
            current_review=raw_review)['references']
        run_lesson_source_ids = set(run_lesson_refs)
        expected_references.update(run_lesson_refs)
    expected_references['review-policy'] = {
        'kind': 'explicit_review_policy',
        'content': adjudication_context.get('review_policy')}
    if replay.get('known_reference_input') != expected_references:
        raise ValueError('Korean adjudication references differ from the reviewed inputs')
    if expected_reference_sources is not None:
        for identity, reference in expected_references.items():
            content = reference['content']
            if identity.startswith('word:'):
                current = expected_reference_sources.get('approved_words', {}).get(identity[5:])
            elif identity.startswith('grammar:'):
                current = expected_reference_sources.get('approved_grammar', {}).get(identity[8:])
            elif identity == 'linguistic-reference':
                current = expected_reference_sources.get('linguistic_reference')
            elif identity == 'lexical-reference':
                current = expected_reference_sources.get('lexical_reference')
            elif identity in official_source_ids or identity in run_lesson_source_ids or identity.startswith('carried-research-'):
                current = content
            else:
                current = expected_reference_sources.get(identity)
            if current != content:
                raise ValueError(f'Korean adjudication reference changed: {identity}')
    verified = verify_adjudication_evidence(run_dir, proof['adjudication'],
        language='ko', representation='korean-flat', candidate=annotation,
        current_review=raw_review, prior_history=replay['prior_history'],
        context=replay['context'], known_reference_input=replay['known_reference_input'],
        deterministic_gate_evidence=replay['deterministic_gate_evidence'],
        normal_review_receipt=replay['normal_review_receipt'], source_text=text)
    if verified.get('status') != 'cleared' or verified.get('approved') is not True:
        raise ValueError('Korean adjudication did not independently clear this chunk')
    return {'scope': 'chunk', 'kind': 'adjudicated', 'review': raw_review,
            'adjudication': verified}


def verify_assembly_reviews(run_dir, meta, *, expected_context=None,
                            allow_stale_form_guidance=False,
                            expected_reference_sources=None):
    from pipeline.korean_agent_harness import read, read_annotation_chunk
    records = meta['chunks']
    version = meta.get('chunk_reviews_version')
    if version not in (1, 2) or len(meta.get('chunk_reviews', [])) != len(records):
        raise ValueError('Korean chunk review coverage is incomplete')
    chapter_text = ''.join(record['text'] for record in records)
    start = 0
    for record, proof in zip(records, meta['chunk_reviews']):
        if Path(record['job']).name != record['job']:
            raise ValueError('Invalid Korean chunk proposal job')
        value = read_annotation_chunk(run_dir / 'agents' / record['job'] / 'result.json', record)
        verify_chunk_review(run_dir, proof, annotation=value, text=record['text'],
            chapter_text=chapter_text, source_start=start, expected_context=expected_context,
            allow_stale_form_guidance=allow_stale_form_guidance,
            expected_reference_sources=expected_reference_sources,
            allow_adjudicated=(version == 2))
        start += len(record['text'])


def stage_evidence(run_dir, job):
    from pipeline.korean_agent_harness import digest, read
    path = run_dir / 'agents' / str(job) / 'meta.json'
    meta = read(path) if path.exists() else {}
    if 'chunk_reviews_version' in meta:
        return {'chunk_reviews_digest': digest(meta.get('chunk_reviews', []))}
    return {}
