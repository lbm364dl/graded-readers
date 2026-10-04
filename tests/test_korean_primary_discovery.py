from urllib.parse import parse_qs, urlsplit

import pytest

import pipeline.korean_primary_discovery as discovery


def heading(number, word, suffix=''):
    return f'''<dl><dt><a href="javascript:checkSubmit('{number}','Y');"><span class="word_type1_17">{word}<sup>1</sup></span></a>{suffix}</dt><dd>Example: target</dd></dl>'''


def test_exact_result_heading_excludes_examples_sidebar_original_script_and_other_words():
    content = heading(999, 'target') + '<div class="search_result">' + heading(11, 'target') + heading(12, 'target') + heading(13, 'other', '<span>(target)</span>') + heading(14, 'target longer') + '<dl><dd>' + heading(15, 'target') + '</dd></dl></div>'
    records = discovery.basic_result_records(content, 'target')
    # Nested dl headings inside an example do not constitute top-level results.
    assert [r['record_word_no'] for r in records] == ['11', '12']
    assert all(r['headword'] == 'target' for r in records)
    assert all(parse_qs(urlsplit(r['primary_url']).query)['ParaWordNo'][0] == r['record_word_no'] for r in records)


def test_space_in_a_real_headword_is_preserved_and_homonym_marker_is_separate():
    content = '<div class="search_result">' + heading(11, 'a word') + heading(12, 'aword') + '</div>'
    assert [r['record_word_no'] for r in discovery.basic_result_records(content, 'a word')] == ['11']
    assert discovery.basic_result_records(content, 'a word1') == []


def test_discovery_uses_observed_official_form_and_records_provenance(monkeypatch):
    content = '<div class="search_result">' + heading(123, 'target') + '</div>'
    calls = []
    def retrieve(url):
        calls.append(url)
        return content
    monkeypatch.setattr(discovery, 'read_primary', retrieve)
    result = discovery.basic_discovery('target')
    parsed = urlsplit(calls[0])
    assert parsed.path == '/eng/dicMarinerSearch/search'
    assert parse_qs(parsed.query, keep_blank_values=True) == {
        'nation': ['eng'], 'nationCode': ['6'], 'ParaWordNo': [''], 'mainSearchWord': ['target']}
    assert result['request_method'] == 'GET'
    assert len(result['search_content_sha256']) == 64
    assert result['discovery_only'] and not result['complete_dictionary_coverage']
    assert result['records'][0]['record_word_no'] == '123'


def test_no_matches_and_retrieval_failure_do_not_become_record_attestation(monkeypatch):
    monkeypatch.setattr(discovery, 'read_primary', lambda url: '<p>target in an example only</p>')
    assert discovery.basic_discovery('target')['records'] == []
    def unavailable(url):
        raise OSError('Unavailable')
    monkeypatch.setattr(discovery, 'read_primary', unavailable)
    result = discovery.basic_discovery('target')
    assert result['records'] == [] and result['retrieval_error'] == 'OSError'
    assert 'search_content_sha256' not in result


@pytest.mark.parametrize('word', ['', ' ', None])
def test_discovery_requires_a_headword(word):
    with pytest.raises(ValueError):
        discovery.basic_discovery(word)
