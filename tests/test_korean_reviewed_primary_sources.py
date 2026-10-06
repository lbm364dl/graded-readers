import copy
import json
import pytest
from pipeline import korean_lexical_research as research


def registry(tmp_path):
    entries = [{'id': f'stdict-{n}/명', 'headword': '동형어', 'pos': '명',
                'meaning_en': meaning,
                'primary_url': f'https://stdict.korean.go.kr/search/searchView.do?word_no={n}',
                'evidence_en': 'Exact independently reviewed record.'}
               for n, meaning in [(1, 'first sense'), (2, 'different sense')]]
    proposal = {'entries': entries, 'unresolved': []}
    review = {'approved': True, 'issues': []}
    primary = [{'headword_request': '동형어', 'records': [
        {'primary_url': e['primary_url'], 'text': e['meaning_en'], 'content_sha256': 'a'*64}
        for e in entries]}]
    row = {'headwords': ['동형어'], 'proposal': proposal,
           'proposal_digest': research.fingerprint(proposal), 'review': review,
           'review_digest': research.fingerprint(review), 'primary_evidence': primary,
           'primary_evidence_digest': research.fingerprint(primary)}
    path = tmp_path/'registry.json'
    path.write_text(json.dumps({'schema_version': 1, 'reviews': [row]}))
    return path, row


def test_exact_identity_reuses_captured_record_not_same_spelling_homonym(tmp_path):
    path, row = registry(tmp_path)
    result = research.reviewed_primary_sources({'stdict-2/명'}, path)
    assert len(result) == 1
    assert result[0]['lexical_id'] == 'stdict-2/명'
    assert result[0]['primary_records'][0]['text'] == 'different sense'
    assert result[0]['provenance']['primary_evidence_digest'] == row['primary_evidence_digest']
    assert 'not approval' in result[0]['scope']
    assert research.reviewed_primary_sources({'stdict-2/동', '동형어'}, path) == []


def test_absent_primary_text_or_unknown_identity_does_not_manufacture_source(tmp_path):
    path, row = registry(tmp_path)
    assert research.reviewed_primary_sources({'stdict-999/명'}, path) == []
    row['primary_evidence'][0]['records'] = []
    row['primary_evidence_digest'] = research.fingerprint(row['primary_evidence'])
    path.write_text(json.dumps({'schema_version': 1, 'reviews': [row]}))
    assert research.reviewed_primary_sources({'stdict-1/명'}, path) == []


@pytest.mark.parametrize('field', ['proposal', 'review', 'primary_evidence'])
def test_changed_editorial_or_primary_evidence_fails_authentication(tmp_path, field):
    path, row = registry(tmp_path)
    if field == 'proposal': row[field]['entries'][0]['meaning_en'] = 'changed'
    elif field == 'review': row[field]['approved'] = False
    else: row[field][0]['records'][0]['text'] = 'changed'
    path.write_text(json.dumps({'schema_version': 1, 'reviews': [row]}))
    with pytest.raises(ValueError):
        research.reviewed_primary_sources({'stdict-1/명'}, path)


def test_actual_reviewed_stdict_identity_has_retained_primary_without_network():
    sources = research.reviewed_primary_sources({'stdict-30494/명'})
    assert sources[0]['headword'] == '관상가'
    assert '사람의 얼굴을 보고' in sources[0]['primary_records'][0]['text']
    assert sources[0]['reviewed_lexical_entry']['meaning_en'] != 'fortune teller'


def test_displayed_explicit_lexical_identity_and_expression_are_included_not_grammar():
    candidate = {'segments': [{'lexical_id': 'stdict-1/명', 'form_steps': [
        {'lexical_id': 'stdict-2/명', 'grammar_entry_ids': ['connective-go']},
        {'form': '동형어', 'grammar_entry_ids': ['ending']}]}],
        'expression_links': [{'entry_id': 'stdict-3/명'}]}
    assert research.candidate_lexical_identities(candidate) == {
        'stdict-1/명', 'stdict-2/명', 'stdict-3/명'}
