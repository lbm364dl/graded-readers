"""Shared lifecycle binding and cache eligibility for authenticated run lessons."""
from copy import deepcopy
from pathlib import Path


def validate_run_lessons(*args, **kwargs):
    from pipeline.annotation_run_lessons import validate_run_lessons as validate
    return validate(*args, **kwargs)


def load_run_lessons(*args, **kwargs):
    from pipeline.annotation_run_lessons import load_run_lessons as load
    return load(*args, **kwargs)


def _selection_context(context):
    # A saved substantive snapshot belongs to its own envelope. Independently
    # selecting additional current sources must not compare them to that snapshot.
    result = deepcopy(context)
    result.pop('reviewed_run_grammar', None)
    if isinstance(result.get('chunk_review_context'), dict):
        result['chunk_review_context'].pop('reviewed_run_grammar', None)
    return result


def _source_key(row):
    from pipeline.annotation_adjudication import digest
    # Routing issue IDs and their derived reference wrappers can differ when
    # importing the very same authenticated writer/critic pair for a new finding.
    receipt = {key: value for key, value in row['import_receipt'].items()
               if key not in {'issue_ids', 'references', 'reference_digest', 'evidence_digest'}}
    return digest({'source_run_relpath': row['source_run_relpath'],
                   'expected_context': row['expected_context'], 'receipt': receipt})


def _envelope(context):
    from pipeline.annotation_run_lessons import RUN_LESSON_FIELD
    return context.get(RUN_LESSON_FIELD, context.get('chunk_review_context', {}).get(RUN_LESSON_FIELD))


def _assert_snapshot_envelope(context):
    from pipeline.annotation_run_lessons import RUN_LESSON_FIELD, RunLessonError
    nested = context.get('chunk_review_context', {})
    if isinstance(nested, dict) and context.get(RUN_LESSON_FIELD) is not None and nested.get(RUN_LESSON_FIELD) is not None and context[RUN_LESSON_FIELD] != nested[RUN_LESSON_FIELD]:
        raise RunLessonError('Conflicting explicit run lesson envelopes')
    for container in (context, context.get('chunk_review_context', {})):
        if isinstance(container, dict) and 'reviewed_run_grammar' in container and container.get(RUN_LESSON_FIELD) is None:
            raise RunLessonError('Run grammar content requires its authenticated lesson envelope')


def _current_packets(run_dir, *, candidate, source_text, language, representation,
                     context, envelope=None):
    _assert_snapshot_envelope(context)
    kwargs = dict(candidate=candidate, source_text=source_text, language=language,
                  representation=representation)
    packets = []
    explicit = _envelope(context)
    if explicit is not None:
        packets.append(validate_run_lessons(run_dir, explicit, context=context, **kwargs)['packet'])
    clean = _selection_context(context)
    if envelope is not None:
        packets.append(validate_run_lessons(run_dir, envelope, context=clean, **kwargs)['packet'])
    registered = load_run_lessons(run_dir, context=clean, **kwargs)
    if registered is not None:
        packets.append(validate_run_lessons(run_dir, registered, context=clean, **kwargs)['packet'])
    return packets, clean, kwargs


def resolve_run_lesson_context(run_dir, *, candidate, source_text, language,
                               representation, context, envelope=None,
                               current_review=None):
    """Authenticate and union explicit/current registered sources, bounded to three."""
    from pipeline.annotation_run_lessons import RunLessonError
    packets, clean, kwargs = _current_packets(run_dir, candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=context, envelope=envelope)
    if not packets:
        if 'reviewed_run_grammar' in context or 'reviewed_run_grammar' in context.get('chunk_review_context', {}):
            raise RunLessonError('Run grammar content requires its authenticated lesson envelope')
        return {'packet': {}, 'references': {}, 'lessons': []}
    combined = deepcopy(packets[0]);combined['lessons'] = []
    seen = set()
    for packet in packets:
        for row in packet['lessons']:
            key = _source_key(row)
            if key not in seen:
                combined['lessons'].append(deepcopy(row));seen.add(key)
    return validate_run_lessons(run_dir, combined, context=clean,
        current_review=current_review, **kwargs)


def bind_lifecycle_run_lessons(harness, index, source_text, candidate, *,
                               language, representation, context=None,
                               current_review=None):
    from pipeline.annotation_run_lessons import RUN_LESSON_FIELD
    context = dict(context or {})
    _assert_snapshot_envelope(context)
    position = getattr(harness, '_annotation_source_positions', {}).get(index)
    binding_context = dict(context)
    if position is not None:
        binding_context['annotation_source_position'] = position
    envelope = getattr(harness, '_annotation_run_lessons', {}).get(index)
    if getattr(harness, 'run_dir', None) is None:
        if _envelope(context) is not None or envelope is not None:
            from pipeline.annotation_run_lessons import RunLessonError
            raise RunLessonError('Run lesson binding requires coordinator run directory')
        return context, {}
    bound = resolve_run_lesson_context(harness.run_dir, candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=binding_context, envelope=envelope, current_review=current_review)
    if not bound['packet']:
        return context, {}
    context = binding_context
    context[RUN_LESSON_FIELD] = bound['packet']
    context['reviewed_run_grammar'] = bound['lessons']
    context['annotation_run_lesson_validation'] = {
        'run_dir': str(Path(harness.run_dir).resolve()),
        'candidate': candidate, 'source_text': source_text, 'language': language,
        'representation': representation, 'envelope': bound['packet'], 'lessons': bound['lessons'],
        'context': dict(binding_context)}
    return context, bound['references']


def current_run_lesson_context_eligibility(run_dir, *, candidate, source_text,
                                         language, representation, context,
                                         old_context, envelope=None,
                                         terminal_context=None):
    """Compare knowledge consumed by already verified proofs with current sources.

    A terminal_context is usable only after the caller independently replays the
    full terminal chain. Neither a result flag nor a prior vote establishes that.
    """
    packets, clean, kwargs = _current_packets(run_dir, candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=context, envelope=envelope)
    consumed = set()
    for saved in (old_context, terminal_context):
        if saved is None:
            continue
        _assert_snapshot_envelope(saved)
        packet = _envelope(saved)
        if packet is not None:
            bound = validate_run_lessons(run_dir, packet, context=saved, **kwargs)
            consumed.update(_source_key(row) for row in bound['packet']['lessons'])
    current = {_source_key(row) for packet in packets for row in packet['lessons']}
    return current <= consumed


def current_run_lesson_eligibility(harness, index, source_text, candidate, *,
                                  language, representation, old_context):
    position = getattr(harness, '_annotation_source_positions', {}).get(index)
    context = {'annotation_source_position': position} if position is not None else {}
    return current_run_lesson_context_eligibility(harness.run_dir, candidate=candidate,
        source_text=source_text, language=language, representation=representation,
        context=context, old_context=old_context,
        envelope=getattr(harness, '_annotation_run_lessons', {}).get(index))
