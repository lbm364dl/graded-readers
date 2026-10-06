import pytest

from scripts.enrich_chinese_subsegments import (
    apply_component_hsk_focus,
    candidate_listing,
    discard_invalid_optional_items,
    validate_result,
)


def test_component_focus_requires_reviewed_parts_and_exact_grammar_span():
    segments = [
        {
            "text": "泛上来", "type": "word", "story_term": "",
            "lookup_reason": "unlisted",
            "subsegments": [
                {"start": 0, "end": 1, "text": "泛", "meaning_en": "rise"},
                {"start": 1, "end": 3, "text": "上来", "meaning_en": "up"},
            ],
        },
        {
            "text": "黄巾军", "type": "word", "story_term": "",
            "lookup_reason": "story_term",
            "subsegments": [
                {"start": 0, "end": 2, "text": "黄巾", "meaning_en": "Yellow Turbans"},
                {"start": 2, "end": 3, "text": "军", "meaning_en": "army"},
            ],
        },
    ]
    overlays = [{"start": 0, "end": 3, "text": "泛上来"}]

    changed = apply_component_hsk_focus(segments, overlays, "hsk3")

    assert changed == 1
    assert segments[0]["lookup_reason"] == "unlisted_component"
    assert segments[0]["hsk_evidence"].startswith("reviewed_components:泛=")
    assert segments[1]["lookup_reason"] == "story_term"


def test_candidate_listing_deduplicates_surfaces_and_lists_exact_lookups():
    segments = [
        {"text": "同心协力", "type": "idiom", "meaning_en": "work together"},
        {"text": "。", "type": "punctuation", "meaning_en": ""},
        {"text": "同心协力", "type": "idiom", "meaning_en": "work together"},
    ]
    candidates = candidate_listing(
        segments,
        {"同心协力", "同心", "协力"},
        set("同心协力"),
    )

    assert len(candidates) == 1
    assert candidates[0]["occurrence_indices"] == [0, 2]
    assert {item["text"] for item in candidates[0]["available_lookups"]} >= {
        "同心",
        "协力",
    }


def test_validate_result_requires_complete_contiguous_dictionary_parts():
    segments = [
        {"text": "同心协力", "type": "idiom", "meaning_en": "work together"},
    ]
    candidates = candidate_listing(
        segments,
        {"同心协力", "同心", "协力"},
        set("同心协力"),
    )
    valid = {
        "items": [{
            "segment_index": 0,
            "surface": "同心协力",
            "composition_en": "Being of one mind and combining effort gives the idiom its sense.",
            "subsegments": [
                {
                    "start": 0, "end": 2, "text": "同心",
                    "meaning_en": "of one mind",
                },
                {
                    "start": 2, "end": 4, "text": "协力",
                    "meaning_en": "combine efforts",
                },
            ],
        }],
        "notes": [],
    }

    validate_result(valid, segments, candidates, verifier=False)

    invalid = {**valid, "items": [{
        **valid["items"][0],
        "subsegments": [{
            "start": 0, "end": 2, "text": "同心",
            "meaning_en": "of one mind",
        }],
    }]}
    with pytest.raises(ValueError, match="fewer than two"):
        validate_result(invalid, segments, candidates, verifier=False)


def test_discard_invalid_optional_items_keeps_agent_meanings_and_valid_shapes():
    segments = [
        {"text": "三路夹攻", "type": "phrase", "meaning_en": "attack from three directions"},
        {"text": "一会儿", "type": "phrase", "meaning_en": "a short while"},
    ]
    candidates = candidate_listing(
        segments,
        {"三路夹攻", "夹攻", "一会儿", "一会", "会儿"},
        set("三路夹攻一会儿"),
    )
    result = {
        "verdict": "revised",
        "items": [
            {
                "segment_index": 0,
                "surface": "三路夹攻",
                "composition_en": "An attack from three directions.",
                "subsegments": [
                    {"start": 0, "end": 1, "text": "三", "meaning_en": "three"},
                    {
                        "start": 1,
                        "end": 2,
                        "text": "路",
                        "meaning_en": "directions or forces",
                    },
                    {
                        "start": 2,
                        "end": 4,
                        "text": "夹攻",
                        "meaning_en": "attack from both sides",
                    },
                ],
            },
            {
                "segment_index": 1,
                "surface": "一会儿",
                "composition_en": "A short while.",
                "subsegments": [
                    {
                        "start": 0,
                        "end": 3,
                        "text": "一会儿",
                        "meaning_en": "a short while",
                    },
                    {"start": 1, "end": 3, "text": "会儿", "meaning_en": "while"},
                ],
            },
        ],
        "notes": [],
    }

    cleaned = discard_invalid_optional_items(
        result, segments, candidates, set(), verifier=True
    )

    assert [item["surface"] for item in cleaned["items"]] == ["三路夹攻"]
    assert cleaned["items"][0]["subsegments"][1]["meaning_en"] == "directions or forces"
    assert "一会儿" in cleaned["notes"][-1]


def test_discard_invalid_optional_items_rejects_invalid_required_surface():
    segments = [{"text": "一会儿", "type": "phrase", "meaning_en": "a short while"}]
    candidates = candidate_listing(
        segments,
        {"一会儿", "一会", "会儿"},
        set("一会儿"),
    )
    result = {
        "items": [
            {
                "segment_index": 0,
                "surface": "一会儿",
                "composition_en": "A short while.",
                "subsegments": [
                    {
                        "start": 0,
                        "end": 3,
                        "text": "一会儿",
                        "meaning_en": "a short while",
                    },
                    {"start": 1, "end": 3, "text": "会儿", "meaning_en": "while"},
                ],
            }
        ],
        "notes": [],
    }

    with pytest.raises(ValueError, match="not exact and contiguous"):
        discard_invalid_optional_items(
            result, segments, candidates, {"一会儿"}, verifier=False
        )
