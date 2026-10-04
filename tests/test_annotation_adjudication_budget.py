import copy

import pytest

from pipeline.annotation_adjudication_budget import AdjudicationBudget, MAX_ADJUDICATION_CANDIDATES


def test_unchanged_candidate_cannot_repeat_route_and_rejection_consumes_no_slot():
    budget = AdjudicationBudget()
    candidate = {'segments': [{'text': '押', 'meaning_en': 'push'}], 'grammar_links': []}
    before = copy.deepcopy(candidate)
    assert budget.claim(candidate)
    assert not budget.claim(copy.deepcopy(candidate))
    # Object identity and dictionary insertion order do not create new evidence.
    assert not budget.claim({'grammar_links': [], 'segments': candidate['segments']})
    assert budget.claimed_count == 1
    assert candidate == before


def test_changed_candidate_can_receive_fresh_adjudication_after_repair():
    budget = AdjudicationBudget()
    original = {'segments': [{'text': '押', 'meaning_en': 'push'}]}
    derived = {'segments': [{'text': '押', 'meaning_en': 'pushed'}]}
    assert budget.claim(original)
    assert budget.claim(derived)
    assert not budget.claim(original)
    assert budget.claimed_count == 2


def test_shared_three_candidate_limit_stops_distinct_candidate_churn():
    budget = AdjudicationBudget()
    assert MAX_ADJUDICATION_CANDIDATES == 3
    for index in range(3):
        assert budget.claim({'segments': [{'meaning_en': str(index)}]})
    assert not budget.claim({'segments': [{'meaning_en': 'fourth'}]})
    assert budget.claimed_count == 3
    assert AdjudicationBudget().claim({'segments': [{'meaning_en': 'fourth'}]})


def test_invalid_non_json_candidate_never_consumes_runtime_budget():
    budget = AdjudicationBudget()
    with pytest.raises(ValueError):
        budget.claim({'meaning_en': float('nan')})
    assert budget.claimed_count == 0


def test_preflight_eligibility_does_not_reserve_and_claim_consumes_once():
    budget = AdjudicationBudget()
    candidate = {'meaning_en': 'push'}
    assert budget.can_claim(candidate)
    assert budget.can_claim(candidate)
    assert budget.claimed_count == 0
    assert budget.claim(candidate)
    assert not budget.can_claim(candidate)
    assert not budget.claim(candidate)
    assert budget.claimed_count == 1
