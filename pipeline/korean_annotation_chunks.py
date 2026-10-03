"""Segment-attached worker links, replayed into the published index format."""
from copy import deepcopy

from jsonschema import validate

from pipeline import korean_contracts as contracts

LEGACY_FORMAT = 'segment-anchored-annotation-v1'
FORMAT = 'segment-contained-annotation-v2'
SPAN_FORMAT = 'source-span-annotation-v3'
SPAN_LINK_FORMAT = 'source-span-links-annotation-v4'
FORMATS = {LEGACY_FORMAT, FORMAT, SPAN_FORMAT, SPAN_LINK_FORMAT}
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
LEGACY_SCHEMA = deepcopy(SCHEMA)
LEGACY_SCHEMA['properties']['format']['enum'] = [LEGACY_FORMAT]
SPAN_SCHEMA = deepcopy(SCHEMA)
SPAN_SCHEMA['properties']['format']['enum'] = [SPAN_FORMAT]
for schema in SPAN_SCHEMA['properties']['segments']['items']['anyOf']:
    del schema['properties']['text']
    schema['required'].remove('text')
    for key in ('source_start', 'source_end'):
        schema['properties'][key] = {'type': 'integer', 'minimum': 0}
        schema['required'].append(key)
SPAN_LINK_SCHEMA = deepcopy(SPAN_SCHEMA)
SPAN_LINK_SCHEMA['properties']['format']['enum'] = [SPAN_LINK_FORMAT]
for schema in SPAN_LINK_SCHEMA['properties']['segments']['items']['anyOf']:
    for field, surface in [('grammar_links', 'display_form'), ('expression_links', 'form')]:
        link = deepcopy(schema['properties'][field]['items'])
        schema['properties'][field]['items'] = link
        del link['properties'][surface]
        link['required'].remove(surface)
        for key in ('source_start', 'source_end'):
            link['properties'][key] = {'type': 'integer', 'minimum': -1 if field == 'grammar_links' else 0}
            link['required'].append(key)


def materialize_spans(value, source_text):
    """Decode an explicit, gapless partition of immutable Unicode source text."""
    if source_text is None:
        raise ValueError('Korean source-span annotation requires its authoritative source_text')
    result = deepcopy(value)
    cursor, boundaries = 0, []
    for index, segment in enumerate(result['segments']):
        start, end = segment.pop('source_start'), segment.pop('source_end')
        if start != cursor or end <= start or end > len(source_text):
            raise ValueError(f'Korean source span {index} [{start}:{end}] must start at {cursor}, '
                             f'be nonempty, and end within source length {len(source_text)}')
        segment['text'] = source_text[start:end]
        boundaries.append((start, end))
        cursor = end
    if cursor != len(source_text):
        raise ValueError(f'Korean source spans end at {cursor}; must cover all {len(source_text)} Unicode characters')
    if value['format'] == SPAN_LINK_FORMAT:
        starts, ends = {a for a, _ in boundaries}, {b for _, b in boundaries}
        for index, segment in enumerate(result['segments']):
            for field, surface in [('grammar_links', 'display_form'), ('expression_links', 'form')]:
                for link in segment[field]:
                    start, end = link.pop('source_start'), link.pop('source_end')
                    if field == 'grammar_links' and (start, end) == (-1, -1):
                        link[surface] = ''
                        continue
                    anchor_start, anchor_end = boundaries[index]
                    if (start not in starts or end not in ends or end <= start
                            or not start <= anchor_start < anchor_end <= end):
                        raise ValueError('Korean link source range must align with complete tap boundaries '
                                         'and contain its attached word; use (-1, -1) only for grammar form steps')
                    link[surface] = source_text[start:end]
    result['format'] = FORMAT
    validate_worker(result)
    return result


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
        f'from anchor {start} ({segments[start]["text"]!r}). Preserve tap boundaries and supply the exact complete form. '
        f'Exact existing start segments for this complete form: {matching_starts(segments, form)}. '
        'If the displayed form is correct, attach the link to its appropriate first segment, not its grammatical ending. '
        'An empty list means this form is not present at complete tap boundaries; use the actual source form, not a dictionary-form paraphrase.')


def matching_starts(segments, form):
    """Diagnostic candidates only; never retarget a link or alter its form."""
    return [{'segment_index': start, 'text': segments[start]['text']}
            for start, _ in matching_spans(segments, form)]


def matching_spans(segments, form):
    matches = []
    for start, segment in enumerate(segments):
        surface = ''
        for end, item in enumerate(segments[start:], start):
            surface += item['text']
            if surface == form:
                matches.append((start, end))
                break
            if not form.startswith(surface):
                break
    return matches


def contained_span(segments, anchor, form):
    """V2 explicitly anchors a link within its exact complete-form span."""
    matches = [(start, end) for start, end in matching_spans(segments, form)
               if start <= anchor <= end]
    if len(matches) != 1:
        raise ValueError(f'Korean complete link form {form!r} needs exactly one complete source span '
                         f'containing attached segment {anchor} ({segments[anchor]["text"]!r}); '
                         f'found {matches}. Preserve tap boundaries and the actual source form; '
                         'attach the link within the intended occurrence. Do not use a dictionary-form paraphrase.')
    return matches[0]


def validate_worker(value):
    if value.get('format') in (SPAN_FORMAT, SPAN_LINK_FORMAT):
        validate(value, SPAN_LINK_SCHEMA if value['format'] == SPAN_LINK_FORMAT else SPAN_SCHEMA)
        return
    if value.get('format') not in FORMATS:
        validate(value, contracts.ANNOTATION)
        return
    schema = deepcopy(SCHEMA)
    schema['properties']['format']['enum'] = [LEGACY_FORMAT, FORMAT]
    validate(value, schema)


def decode(value, *, source_text=None):
    validate_worker(value)
    if value.get('format') in (SPAN_FORMAT, SPAN_LINK_FORMAT):
        value = materialize_spans(value, source_text)
    if source_text is not None:
        contracts.check_reconstruction(value['segments'], source_text)
    if value.get('format') not in FORMATS:
        return value
    contained = value['format'] == FORMAT
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
            first, last = (contained_span(segments, index, form) if contained else
                           (index, endpoint(segments, index, form))) if form else (index, -1)
            result['grammar_links'].append({**link, 'segment_index': first,
                'display_end_segment_index': last})
        for link in segment['expression_links']:
            first, last = (contained_span(segments, index, link['form']) if contained else
                           (index, endpoint(segments, index, link['form'])))
            result.setdefault('expression_links', []).append({**link, 'segment_index': first,
                'end_segment_index': last})
    validate(result, contracts.ANNOTATION)
    return result
