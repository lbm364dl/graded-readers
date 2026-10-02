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


def test_raw_chunk_digest_is_required_and_tampering_is_rejected(tmp_path):
    raw = attached(fixture())
    decoded = chunks.decode(raw)
    path = tmp_path/'result.json'
    save(path, raw)
    record = annotation_chunk_record('chunk', 'source', decoded, raw)
    assert read_annotation_chunk(path, record) == decoded
    with pytest.raises(ValueError, match='changed after review'):
        read_annotation_chunk(path, {key: item for key, item in record.items() if key != 'raw_digest'})
    raw['segments'][0]['meaning_en'] += ' altered'
    save(path, raw)
    with pytest.raises(ValueError, match='changed after review'):
        read_annotation_chunk(path, record)
