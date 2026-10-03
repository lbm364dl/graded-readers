from scripts.enrich_chinese_immersion_help import (
    apply_cards,
    build_help_targets,
    merge_targeted_repairs,
    validate_and_prune_optional_explanations,
    validate_card_set,
)


def _segments():
    return [
        {
            "text": "他", "type": "word", "meaning_en": "he",
            "learning_focus": "target", "lookup_reason": "",
            "story_term": "", "story_term_meaning_en": "",
        },
        {
            "text": "叫", "type": "word", "meaning_en": "to tell",
            "learning_focus": "target", "lookup_reason": "",
            "story_term": "", "story_term_meaning_en": "",
        },
        {
            "text": "刘备", "type": "name", "meaning_en": "Liu Bei",
            "learning_focus": "lookup", "lookup_reason": "proper_name",
            "story_term": "", "story_term_meaning_en": "",
        },
        {
            "text": "招兵", "type": "word", "meaning_en": "recruit",
            "learning_focus": "lookup", "lookup_reason": "story_term",
            "story_term": "招兵", "story_term_meaning_en": "recruit soldiers",
        },
        {
            "text": "。", "type": "punctuation", "meaning_en": "",
            "learning_focus": "not_applicable", "lookup_reason": "",
            "story_term": "", "story_term_meaning_en": "",
        },
    ]


def test_help_targets_skip_known_words_names_and_punctuation():
    targets = build_help_targets("他叫刘备招兵。", _segments())

    assert [target["surface"] for target in targets] == ["招兵"]
    assert targets[0]["segment_indices"] == [3]


def test_card_validation_and_application_keep_chinese_help_selective():
    segments = _segments()
    targets = build_help_targets("他叫刘备招兵。", segments)
    result = {
        "cards": [{
            "target_id": "help_000",
            "surface": "招兵",
            "explanation_zh": "叫人来一起做事",
            "example_zh": "他想招兵。",
        }]
    }

    assert validate_card_set(result, targets, "hsk1") == []
    enriched = apply_cards(segments, targets, result)
    assert enriched[0]["explanation_zh"] == ""
    assert enriched[2]["explanation_zh"] == ""
    assert enriched[3]["explanation_zh"] == "叫人来一起做事"


def test_card_validation_rejects_advanced_meta_definition():
    targets = build_help_targets("他叫刘备招兵。", _segments())
    result = {
        "cards": [{
            "target_id": "help_000",
            "surface": "招兵",
            "explanation_zh": "这是征募士兵的军事行为",
            "example_zh": "",
        }]
    }

    issues = validate_card_set(result, targets, "hsk1")
    assert issues
    assert issues[0]["above_level_words"]


def test_targeted_repair_cannot_regress_a_passing_card():
    previous = {"cards": [
        {"target_id": "help_000", "surface": "甲"},
        {"target_id": "help_001", "surface": "乙"},
    ]}
    candidate = {"cards": [
        {"target_id": "help_000", "surface": "changed"},
        {"target_id": "help_001", "surface": "fixed"},
    ]}

    merged = merge_targeted_repairs(
        previous, candidate, [{"target_id": "help_001", "problem": "bad"}]
    )

    assert merged["cards"][0] == previous["cards"][0]
    assert merged["cards"][1] == candidate["cards"][1]


def test_invalid_optional_explanation_is_dropped_when_example_passes():
    targets = build_help_targets("他叫刘备招兵。", _segments())
    result = {"cards": [{
        "target_id": "help_000",
        "surface": "招兵",
        "explanation_zh": "征募军队",
        "example_zh": "他想招兵。",
    }]}

    issues = validate_and_prune_optional_explanations(result, targets, "hsk1")

    assert issues == []
    assert result["cards"][0]["explanation_zh"] == ""


def test_circular_optional_explanation_is_dropped_when_example_passes():
    targets = build_help_targets("他叫刘备招兵。", _segments())
    result = {"cards": [{
        "target_id": "help_000",
        "surface": "招兵",
        "explanation_zh": "招兵是叫人来一起做事",
        "example_zh": "他想招兵。",
    }]}

    issues = validate_and_prune_optional_explanations(result, targets, "hsk3")

    assert issues == []
    assert result["cards"][0]["explanation_zh"] == ""
