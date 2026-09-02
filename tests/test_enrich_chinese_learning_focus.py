from scripts.enrich_chinese_learning_focus import (
    apply_planned_story_term_semantics,
    contains_han,
    normalize_nested_story_terms,
    normalize_role_spans,
    role_prompt,
    validate_roles,
)

import pytest


def test_story_term_english_fields_reject_han_text():
    assert contains_han("eighteen-chi steel spear") is False
    assert contains_han("丈八 steel spear") is True


def test_reviewed_plan_canonicalizes_selected_story_term_meaning():
    roles = {"story_terms": [{
        "text": "双股剑",
        "meaning_en": "a double-bladed sword",
    }]}
    focus_plan = {"story_terms": [{
        "surface": "双股剑",
        "meaning_en": "Liu Bei’s paired swords.",
    }]}

    assert apply_planned_story_term_semantics(roles, focus_plan) == 1
    assert roles["story_terms"][0]["meaning_en"] == "Liu Bei’s paired swords."


def test_role_span_normalization_orders_valid_agent_terms():
    segments = [
        {"text": "刘备", "type": "name"},
        {"text": "招兵", "type": "word"},
        {"text": "。", "type": "punctuation"},
    ]
    result = {
        "story_terms": [
            {"start_segment": 1, "end_segment": 2, "text": "招兵"},
            {"start_segment": 0, "end_segment": 1, "text": "刘备"},
        ]
    }

    corrections = normalize_role_spans(result, segments)

    assert corrections == 0
    assert [item["text"] for item in result["story_terms"]] == ["刘备", "招兵"]


def test_nested_story_term_normalization_keeps_lexical_core():
    result = {"story_terms": [
        {"start_segment": 9, "end_segment": 13, "text": "一起去投军"},
        {"start_segment": 2, "end_segment": 3, "text": "投军"},
    ]}

    dropped = normalize_nested_story_terms(result)

    assert dropped == 1
    assert [item["text"] for item in result["story_terms"]] == ["投军"]


def test_role_prompt_forbids_sentence_and_punctuation_spans():
    prompt = role_prompt(
        "刘备结为兄弟。",
        [{"text": "刘备", "type": "name", "meaning_en": "Liu Bei"}],
        "hsk3",
    )
    assert "Never select a sentence" in prompt
    assert "containing punctuation" in prompt
    assert "smallest reusable lexical or fixed phrase" in prompt


def test_story_term_rejects_clause_with_personal_pronoun():
    segments = [
        {"text": "民心", "type": "word"},
        {"text": "已经", "type": "word"},
        {"text": "向着", "type": "word"},
        {"text": "我们", "type": "word"},
    ]
    result = {
        "story_terms": [{
            "start_segment": 0,
            "end_segment": 4,
            "text": "民心已经向着我们",
            "meaning_en": "the people already support us",
            "importance_en": "Motivates the rebellion.",
        }],
        "name_corrections": [],
    }

    with pytest.raises(ValueError, match="personal pronoun"):
        validate_roles(result, segments, verifier=False)
