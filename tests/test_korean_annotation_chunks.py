import copy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from pipeline import korean_annotation_chunks as chunks
from pipeline.korean_agent_harness import normalize_existing, annotation_chunk_record, read_annotation_chunk, save
from pipeline.korean_dictionary import _registry, WORDS


def attached(raw):
    value = {'format': chunks.FORMAT, 'segments': copy.deepcopy(raw['segments'])}
    for index, segment in enumerate(value['segments']):
        segment.update(is_inflected=index in raw['inflected_segment_indices'], grammar_links=[], expression_links=[])
    for link in raw['grammar_links']:
        value['segments'][link['segment_index']]['grammar_links'].append({key: item for key, item in link.items()
            if key not in ('segment_index', 'display_end_segment_index')})
    for link in raw.get('expression_links', []):
        value['segments'][link['segment_index']]['expression_links'].append({key: item for key, item in link.items()
            if key not in ('segment_index', 'end_segment_index')})
    return value


def fixture():
    chapter = json.loads(Path('tests/fixtures/korean_manual_annotations.json').read_text())['chapters'][0]
    return normalize_existing(chapter, _registry(WORDS))


def test_attached_links_preserve_complete_reviewed_annotation_and_legacy_format():
    raw = fixture()
    before = copy.deepcopy(raw)
    assert chunks.decode(attached(raw)) == raw
    assert chunks.decode(raw) == raw
    assert raw == before
    legacy = attached(raw)
    legacy['format'] = chunks.LEGACY_FORMAT
    assert chunks.decode(legacy) == raw


def source_spans(raw):
    value = attached(raw)
    value['format'] = chunks.SPAN_FORMAT
    cursor = 0
    for segment in value['segments']:
        text = segment.pop('text')
        segment.update(source_start=cursor, source_end=cursor + len(text))
        cursor += len(text)
    return value


def test_source_spans_preserve_full_annotation_links_and_proposal():
    raw = fixture()
    source = ''.join(s['text'] for s in raw['segments'])
    value = source_spans(raw)
    before = copy.deepcopy(value)
    assert chunks.decode(value, source_text=source) == raw
    assert value == before
    with pytest.raises(ValueError, match='authoritative source_text'):
        chunks.decode(value)


@pytest.mark.parametrize('damage', ['gap', 'overlap', 'empty', 'overflow', 'tail', 'copied_text'])
def test_source_spans_reject_incomplete_or_ambiguous_source_partitions(damage):
    raw = fixture()
    source = ''.join(s['text'] for s in raw['segments'])
    value = source_spans(raw)
    if damage == 'gap':
        value['segments'][1]['source_start'] += 1
    elif damage == 'overlap':
        value['segments'][1]['source_start'] -= 1
    elif damage == 'empty':
        value['segments'][0]['source_end'] = 0
    elif damage == 'overflow':
        value['segments'][-1]['source_end'] += 1
    elif damage == 'tail':
        value['segments'].pop()
    else:
        value['segments'][0]['text'] = 'rewritten source'
    before = copy.deepcopy(value)
    with pytest.raises((ValueError, ValidationError)):
        chunks.decode(value, source_text=source)
    assert value == before


def test_source_reconstruction_precedes_link_errors_without_changing_proposal():
    value = attached(fixture())
    source = ''.join(s['text'] for s in value['segments'])
    word = next(s for s in value['segments'] if s['type'] == 'word')
    word['grammar_links'] = [{'entry_id': 'subject-i-ga', 'context_en': 'Test',
                             'display_form': 'unattested form', 'display_meaning_en': 'Test'}]
    before = copy.deepcopy(value)
    with pytest.raises(ValueError, match='reconstruction differs'):
        chunks.decode(value, source_text=source + ' ')
    assert value == before
    with pytest.raises(ValueError, match='complete source span'):
        chunks.decode(value, source_text=source)
    word['grammar_links'] = []
    assert chunks.decode(value, source_text=source) == chunks.decode(value)


@pytest.mark.parametrize('form,expected', [('가는 ', 1), ('가는 곳', 2), ('가', None), ('가는 곳으로', None)])
def test_complete_forms_resolve_only_exact_existing_tap_boundaries(form, expected):
    segments = [{'text': '가는'}, {'text': ' '}, {'text': '곳'}]
    if expected is None:
        with pytest.raises(ValueError, match='Preserve tap boundaries'):
            chunks.endpoint(segments, 0, form)
    else:
        assert chunks.endpoint(segments, 0, form) == expected


def test_attached_punctuation_cannot_hide_grammar_links():
    value = attached(fixture())
    punctuation = next(segment for segment in value['segments'] if segment['type'] == 'punctuation')
    punctuation['grammar_links'] = [{'entry_id': 'subject-i-ga', 'context_en': 'Bad anchor',
        'display_form': '', 'display_meaning_en': ''}]
    with pytest.raises(ValidationError):
        chunks.decode(value)


@pytest.mark.parametrize('anchor,form,expected', [(0, '가는 곳', 2), (2, '가는 곳', None), (2, '곳', 2)])
def test_phrase_attachment_is_its_start_not_its_grammar_bearing_final_word(anchor, form, expected):
    segments = [{'text': '가는'}, {'text': ' '}, {'text': '곳'}]
    before = copy.deepcopy(segments)
    if expected is None:
        with pytest.raises(ValueError, match='Preserve tap boundaries'):
            chunks.endpoint(segments, anchor, form)
    else:
        assert chunks.endpoint(segments, anchor, form) == expected
    assert segments == before


def test_failed_anchor_reports_all_exact_alternatives_without_retargeting():
    segments = [{'text': text} for text in ('가는', ' ', '곳', ' ', '가는', ' ', '곳')]
    before = copy.deepcopy(segments)
    assert chunks.matching_starts(segments, '가는 곳') == [
        {'segment_index': 0, 'text': '가는'}, {'segment_index': 4, 'text': '가는'}]
    with pytest.raises(ValueError, match='Exact existing start segments') as error:
        chunks.endpoint(segments, 2, '가는 곳')
    assert "'segment_index': 0" in str(error.value)
    assert "'segment_index': 4" in str(error.value)
    assert chunks.matching_starts(segments, '가는 곳으로') == []
    assert chunks.matching_starts(segments, '가') == []
    assert segments == before


@pytest.mark.parametrize('anchor,form,expected', [
    (0, '가는 곳', (0, 2)), (2, '가는 곳', (0, 2)), (2, '곳', (2, 2)),
    (0, '곳', None), (2, '가는 곳으로', None), (0, '가', None)])
def test_v2_uses_only_an_exact_complete_span_containing_its_attachment(anchor, form, expected):
    segments = [{'text': '가는'}, {'text': ' '}, {'text': '곳'}]
    before = copy.deepcopy(segments)
    if expected is None:
        with pytest.raises(ValueError, match='exactly one complete source span'):
            chunks.contained_span(segments, anchor, form)
    else:
        assert chunks.contained_span(segments, anchor, form) == expected
    assert segments == before


def test_v2_repeated_positions_stay_distinct_and_overlap_is_rejected():
    segments = [{'text': text} for text in ('가는', ' ', '곳', ' ', '가는', ' ', '곳')]
    assert chunks.contained_span(segments, 2, '가는 곳') == (0, 2)
    assert chunks.contained_span(segments, 6, '가는 곳') == (4, 6)
    with pytest.raises(ValueError, match='found'):
        chunks.contained_span([{'text': '가'}, {'text': '가'}, {'text': '가'}], 1, '가가')


def test_v2_replays_contained_links_but_v1_keeps_its_strict_anchor_contract():
    raw = fixture()
    value = attached(raw)
    link = next(link for link in raw['grammar_links'] if
                link['display_form'] and link['display_end_segment_index'] > link['segment_index']
                and raw['segments'][link['display_end_segment_index']]['type'] == 'word')
    start, end = link['segment_index'], link['display_end_segment_index']
    nested = next(item for item in value['segments'][start]['grammar_links']
                  if item['entry_id'] == link['entry_id'])
    value['segments'][start]['grammar_links'].remove(nested)
    value['segments'][end]['grammar_links'].append(nested)
    before = copy.deepcopy(value)
    decoded = chunks.decode(value)
    assert sorted(decoded['grammar_links'], key=lambda item: (item['segment_index'], item['entry_id'])) == \
           sorted(raw['grammar_links'], key=lambda item: (item['segment_index'], item['entry_id']))
    assert decoded['segments'] == raw['segments']
    assert value == before
    value['format'] = chunks.LEGACY_FORMAT
    with pytest.raises(ValueError, match='Preserve tap boundaries'):
        chunks.decode(value)


@pytest.mark.parametrize('format_name', [chunks.LEGACY_FORMAT, chunks.FORMAT, chunks.SPAN_FORMAT])
def test_raw_chunk_digest_is_required_and_tampering_is_rejected(tmp_path, format_name):
    base = fixture()
    source = ''.join(s['text'] for s in base['segments'])
    raw = source_spans(base) if format_name == chunks.SPAN_FORMAT else attached(base)
    raw['format'] = format_name
    decoded = chunks.decode(raw, source_text=source)
    path = tmp_path/'result.json'
    save(path, raw)
    record = annotation_chunk_record('chunk', source, decoded, raw)
    assert read_annotation_chunk(path, record) == decoded
    with pytest.raises(ValueError, match='changed after review'):
        read_annotation_chunk(path, {key: item for key, item in record.items() if key != 'raw_digest'})
    raw['segments'][0]['meaning_en'] += ' altered'
    save(path, raw)
    with pytest.raises(ValueError, match='changed after review'):
        read_annotation_chunk(path, record)
