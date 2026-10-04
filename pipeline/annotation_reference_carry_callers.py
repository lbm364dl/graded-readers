"""Shared lifecycle integration for authenticated annotation research evidence."""
from __future__ import annotations

from pipeline.annotation_reference_carry import (
    CARRY_FIELD, ResearchCarryError, bind_carried_research, carry_from_adjudication,
    load_carried_research, register_carried_research,
)


def register_chunk_positions(harness, chunks, *, parent_text=None):
    from pipeline.annotation_adjudication import digest
    joined = ''.join(chunks)
    if parent_text is not None and joined != parent_text:
        raise ResearchCarryError('Annotation chunks differ from the immutable parent text')
    parent_digest = digest(parent_text if parent_text is not None else joined)
    offset = 0
    harness._annotation_source_positions = {}
    for index, text in enumerate(chunks):
        harness._annotation_source_positions[index] = {
            'chunk_index': index, 'source_text_digest': digest(text),
            'parent_text_digest': parent_digest, 'source_start': offset}
        offset += len(text)


def validate_carry_context(run_dir, context, *, candidate, source_text, language,
                           representation, current_review=None):
    from pipeline.annotation_adjudication import digest
    if CARRY_FIELD not in context:
        return {'packet': {}, 'references': {}}
    position = context.get('annotation_source_position')
    if (not isinstance(position, dict) or set(position) != {
            'chunk_index', 'source_text_digest', 'parent_text_digest', 'source_start'}
            or any(not isinstance(position.get(key), int) or isinstance(position[key], bool)
                   or position[key] < 0 for key in ('chunk_index', 'source_start'))
            or position.get('source_text_digest') != digest(source_text)
            or not isinstance(position.get('parent_text_digest'), str)
            or len(position['parent_text_digest']) != 64):
        raise ResearchCarryError('Carried research requires the exact complete source position')
    return bind_carried_research(run_dir, context[CARRY_FIELD], candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=context, current_review=current_review)


def bind_lifecycle_carry(harness, index, source_text, candidate, *, language,
                         representation, context=None, current_review=None,
                         include_position=False):
    context = dict(context or {})
    had_position = 'annotation_source_position' in context
    carry = getattr(harness, '_annotation_research_carries', {}).get(index)
    position = getattr(harness, '_annotation_source_positions', {}).get(index)
    if carry is None and position is not None and getattr(harness, 'run_dir', None):
        carry = load_carried_research(harness.run_dir, candidate=candidate,
            source_text=source_text, language=language, representation=representation,
            context={**context, 'annotation_source_position': position})
        if carry is not None:
            if not hasattr(harness, '_annotation_research_carries'):
                harness._annotation_research_carries = {}
            harness._annotation_research_carries[index] = carry
    if position is not None and (include_position or carry):
        context['annotation_source_position'] = position
    if not carry or not carry.get('sources'):
        return context, {}
    if position is None:
        return context, {}  # Direct callers without a parent cannot carry facts.
    bound = bind_carried_research(harness.run_dir, carry, candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=context, current_review=current_review)
    if bound['packet']:
        context[CARRY_FIELD] = bound['packet']
        validate_carry_context(harness.run_dir, context, candidate=candidate,
            source_text=source_text, language=language, representation=representation,
            current_review=current_review)
    elif not include_position and not had_position:
        context.pop('annotation_source_position', None)
    return context, bound['references']


def remember_lifecycle_carry(harness, index, evidence, *, candidate, source_text,
                             language, representation, context):
    if not isinstance(evidence, dict) or evidence.get('reference_research', {}).get('approved') is not True:
        return
    carries = getattr(harness, '_annotation_research_carries', None)
    if carries is None:
        carries = harness._annotation_research_carries = {}
    carry = carry_from_adjudication(harness.run_dir, evidence, carries.get(index))
    registered = register_carried_research(harness.run_dir, carry, candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=context)
    if registered is not None:
        carries[index] = registered
