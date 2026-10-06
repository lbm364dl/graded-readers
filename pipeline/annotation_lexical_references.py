"""Exact-headword reference alternatives; selection remains a worker decision."""
from copy import deepcopy


def lexical_review_references(*, used_ids, headwords, approved, catalog):
    """Preserve used records and expose distinct identities for exact lemmas.

    Never infer lemmas from inflected surfaces, merge readings or kinds, assign
    grades, or turn catalog candidates into approved dictionary records.
    """
    selected_ids = set(used_ids)
    requested = {word for word in headwords if isinstance(word, str) and word}
    approved_rows = [deepcopy(row) for identity, row in approved.items()
                     if identity in selected_ids or row.get('headword') in requested]
    candidates = [deepcopy(row) for rows in catalog.values() for row in rows
                  if row.get('id') in selected_ids or row.get('headword') in requested]
    return {'approved_words': approved_rows, 'lexical_candidates': candidates}
