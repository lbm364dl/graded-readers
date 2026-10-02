"""Segment-attached worker links, replayed into the published index format."""
from copy import deepcopy

from jsonschema import validate

from pipeline import korean_contracts as contracts

FORMAT = 'segment-anchored-annotation-v1'
GRAMMAR_LINK = contracts.obj({key: value for key, value in contracts.LINK['properties'].items()
    if key not in ('segment_index', 'display_end_segment_index')})
EXPRESSION_LINK = contracts.obj({key: value for key, value in contracts.EXPRESSION_LINK['properties'].items()
    if key not in ('segment_index', 'end_segment_index')})


def segment_schema(base, punctuation=False):
    result = deepcopy(base)
    extra = {
        'is_inflected': {'type': 'boolean', **({'enum': [False]} if punctuation else {})},
        'grammar_links': {'type': 'array', 'items': GRAMMAR_LINK,
            **({'maxItems': 0} if punctuation else {})},
        'expression_links': {'type': 'array', 'items': EXPRESSION_LINK,
            **({'maxItems': 0} if punctuation else {})},
    }
    result['properties'].update(extra)
    result['required'].extend(extra)
    return result


SCHEMA = contracts.obj({
    'format': {'type': 'string', 'enum': [FORMAT]},
    'segments': {'type': 'array', 'minItems': 1, 'items': {'anyOf': [
        segment_schema(contracts.WORD_SEGMENT), segment_schema(contracts.PUNCTUATION_SEGMENT, True)]}},
})


def endpoint(segments, start, form):
    """Resolve an explicit complete form from its attached anchor, without guesses."""
    surface = ''
    for end in range(start, len(segments)):
        surface += segments[end]['text']
        if surface == form:
            return end
        if not form.startswith(surface):
            break
    raise ValueError(f'Attached Korean link form {form!r} does not match complete source segments '
        f'from anchor {start} ({segments[start]["text"]!r}). Preserve tap boundaries and supply the exact complete form.')


def decode(value):
    if value.get('format') != FORMAT:
        validate(value, contracts.ANNOTATION)
        return value
    validate(value, SCHEMA)
    segments = value['segments']
    result = {'segments': [], 'grammar_links': [],
        'inflected_segment_indices': []}
    for index, segment in enumerate(segments):
        result['segments'].append({key: deepcopy(item) for key, item in segment.items()
            if key not in ('is_inflected', 'grammar_links', 'expression_links')})
        if segment['is_inflected']:
            result['inflected_segment_indices'].append(index)
        for link in segment['grammar_links']:
            form = link['display_form']
            result['grammar_links'].append({**link, 'segment_index': index,
                'display_end_segment_index': endpoint(segments, index, form) if form else -1})
        for link in segment['expression_links']:
            result.setdefault('expression_links', []).append({**link, 'segment_index': index,
                'end_segment_index': endpoint(segments, index, link['form'])})
    validate(result, contracts.ANNOTATION)
    return result
