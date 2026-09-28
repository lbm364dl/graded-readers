from copy import deepcopy

import pytest

from pipeline.dictionary_editor import normalize_duplicate_citation_markers, validate_citations


def test_only_already_structured_verified_duplicate_markers_are_removed():
    row = dict(explanation_en='Useful explanation. [c1][c2]', caveat_en='A limit. [c1]',
               claim_ids=['c1', 'c2'], parts=[dict(text='字', contribution_en='A part. [c1]', claim_ids=['c1'])])
    original = deepcopy(row)
    dossier = dict(claims=[dict(id='c1'), dict(id='c2')])
    cleaned = normalize_duplicate_citation_markers(row, dossier)
    assert cleaned['explanation_en'] == 'Useful explanation.'
    assert cleaned['caveat_en'] == 'A limit.'
    assert cleaned['parts'][0]['contribution_en'] == 'A part.'
    assert cleaned['claim_ids'] == row['claim_ids']
    assert row == original
    validate_citations(cleaned, dossier)


@pytest.mark.parametrize('declared, known', [([], ['c1']), (['c1'], [])])
def test_unknown_or_unstructured_marker_cannot_be_silently_removed(declared, known):
    row = dict(explanation_en='Unverified. [c1]', caveat_en='', claim_ids=declared, parts=[])
    dossier = dict(claims=[dict(id=c) for c in known])
    assert normalize_duplicate_citation_markers(row, dossier)['explanation_en'] == row['explanation_en']
