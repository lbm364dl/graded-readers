from pipeline.chinese_readability import (
    classify_reviewed_compositional_segment,
    HSK1_MAX_ABOVE_LEVEL_WORD_RATIO,
    classify_reviewed_segment,
    lexical_tokens,
    validate_beginner_chinese,
    validate_chinese_help_text,
    validate_level_distinctiveness,
    validate_paragraph_structure,
)


def test_rejects_compressed_literary_hsk1_false_positive():
    text = (
        "东汉末年宦官专权，灾祸不断，盗贼蜂起。"
        "张角得天书，用符水治病，聚众起事。"
    )

    result = validate_beginner_chinese(text, "hsk1")

    assert result["passes"] is False
    assert result["above_level_word_ratio"] > HSK1_MAX_ABOVE_LEVEL_WORD_RATIO
    assert {"专权", "灾祸", "蜂起"} <= set(result["above_level_words"])


def test_accepts_explicit_beginner_prose_and_transparent_word_combinations():
    text = (
        "很多人没有饭吃。刘备想帮大家。他认识了关羽。"
        "他也认识了张飞。三个人天天说话。他们想帮大家。"
        "他们是好朋友。"
    )

    result = validate_beginner_chinese(text, "hsk1")

    assert result["passes"] is True
    assert result["above_level_word_tokens"] == 0


def test_rejects_beginner_prose_compressed_into_one_long_sentence():
    text = "刘备认识了关羽和张飞，他们三个人每天一起说话，也一起帮助很多人。"

    result = validate_beginner_chinese(text, "hsk1")

    assert result["passes"] is False
    assert result["sentence_pass"] is False
    assert result["long_sentences"]


def test_higher_levels_enforce_their_word_budget_with_progressive_shape_limits():
    result = validate_beginner_chinese(
        "宦官专权，灾祸不断。", "hsk2", max_above_level_word_ratio=0.12
    )

    assert result["passes"] is False
    assert result["enforced"] is True
    assert result["shape_enforced"] is True
    assert result["lexical_pass"] is False
    assert result["sentence_pass"] is True


def test_hsk2_rejects_a_synopsis_style_stacked_sentence():
    result = validate_beginner_chinese(
        "三人来到张飞家后面的桃园，在园中祭告天地，结为兄弟，立下誓言，要同心协力，报国安民。",
        "hsk2",
        allowed_words={"桃园", "祭告天地", "誓言"},
        max_above_level_word_ratio=1,
    )

    assert result["sentence_pass"] is False
    assert result["long_sentences"] == [36]
    assert result["max_sentence_cjk"] == 28


def test_paragraph_structure_rejects_wall_of_text():
    result = validate_paragraph_structure("刘备想帮助大家。" * 40, "hsk1")

    assert result["passes"] is False
    assert result["paragraph_count"] == 1
    assert result["count_pass"] is False
    assert result["length_pass"] is False


def test_paragraph_structure_accepts_short_readable_blocks():
    text = "\n\n".join(["刘备想帮助大家。他没有钱。"] * 6)

    result = validate_paragraph_structure(text, "hsk1")

    assert result["passes"] is True
    assert result["paragraph_count"] == 6


def test_segment_focus_keeps_agent_roles_separate_from_hsk_evidence():
    ordinary = classify_reviewed_segment("我", "word", "hsk1")
    name = classify_reviewed_segment("刘备", "name", "hsk1")
    story = classify_reviewed_segment(
        "告示", "word", "hsk1", is_story_term=True
    )

    assert ordinary == {
        "target_hsk_level": 1,
        "hsk_status": "in_level",
        "matched_hsk_level": 1,
        "character_hsk_level": 1,
        "hsk_evidence": "listed_hsk1",
        "learning_focus": "target",
        "lookup_reason": "",
    }
    assert name["hsk_status"] == "unlisted"
    assert name["learning_focus"] == "lookup"
    assert name["lookup_reason"] == "proper_name"
    assert story["hsk_status"] == "above_level"
    assert story["matched_hsk_level"] == 7
    assert story["lookup_reason"] == "story_term"


def test_word_level_never_uses_the_character_writing_band():
    result = classify_reviewed_segment("招", "word", "hsk1")

    assert result["hsk_status"] == "above_level"
    assert result["matched_hsk_level"] == 6
    assert result["character_hsk_level"] == 4
    assert result["hsk_evidence"] == "listed_hsk6"


def test_segment_focus_accepts_transparent_target_level_combinations():
    result = classify_reviewed_segment("好人", "word", "hsk1")

    assert result["hsk_status"] == "in_level"
    assert result["learning_focus"] == "target"
    assert result["hsk_evidence"].startswith("decomposes_into_hsk1_words:")


def test_reviewed_construction_uses_hardest_component_not_full_surface():
    in_level = classify_reviewed_compositional_segment(
        "好人", "word", "hsk1", ["好", "人"]
    )
    assert in_level["hsk_status"] == "in_level"
    assert in_level["matched_hsk_level"] == 1
    assert in_level["learning_focus"] == "target"
    assert in_level["hsk_evidence"] == "reviewed_components:好=hsk1+人=hsk1"

    above = classify_reviewed_compositional_segment(
        "招人", "word", "hsk1", ["招", "人"]
    )
    assert above["hsk_status"] == "above_level"
    assert above["matched_hsk_level"] == 6
    assert above["lookup_reason"] == "above_target_component"


def test_reviewed_construction_names_the_unlisted_component():
    result = classify_reviewed_compositional_segment(
        "泛上来", "word", "hsk3", ["泛", "上来"]
    )
    assert result["hsk_status"] == "unlisted"
    assert result["matched_hsk_level"] is None
    assert result["learning_focus"] == "lookup"
    assert result["lookup_reason"] == "unlisted_component"
    assert result["hsk_evidence"] == (
        "reviewed_components:泛=unlisted_word(character_hsk5)+上来=hsk3"
    )


def test_tokenizer_does_not_invent_cross_word_compounds():
    tokens = lexical_tokens(
        "关羽因杀了人，苏双带马来到这里，拿刀、矛和剑。",
        "hsk2",
        protected_words={"苏双"},
    )

    assert "因杀" not in tokens
    assert "双带" not in tokens
    assert "矛和" not in tokens
    assert "因" in tokens


def test_readability_gate_preserves_reviewed_multi_character_exemptions():
    result = validate_beginner_chinese(
        "苏双带着马来。",
        "hsk2",
        allowed_words={"苏双"},
        max_above_level_word_ratio=0,
    )

    assert "苏双" not in result["above_level_words"]


def test_immersion_help_allows_headword_but_no_other_advanced_words():
    accepted = validate_chinese_help_text(
        "我想去，可是我没有钱。",
        "hsk1",
        kind="example",
        taught_surfaces={"可是"},
    )
    rejected = validate_chinese_help_text(
        "这是一个表示转折关系的词。",
        "hsk1",
        kind="explanation",
        taught_surfaces={"可是"},
    )

    assert accepted["passes"] is True
    assert rejected["passes"] is False
    assert rejected["above_level_words"]


def test_punctuated_reviewed_quote_exempts_only_its_exact_han_clauses():
    quote = "不求同年同月同日生，但愿同年同月同日死"
    result = validate_beginner_chinese(
        f"他们说：{quote}。",
        "hsk3",
        allowed_words={quote},
        max_above_level_word_ratio=0,
    )

    assert result["lexical_pass"] is True


def test_short_low_difficulty_target_keeps_lower_level_signal_without_a_word_floor():
    result = validate_level_distinctiveness(
        "刘备走了。", "hsk6",
        lower_level_max_above_ratio=0.25,
    )

    assert result["lower_level_lexical_pass"] is True
    assert result["target_band_unique"] == 0
    assert result["target_band_words"] == []
    assert "passes" not in result
    assert "min_target_band_unique" not in result


def test_target_band_inventory_counts_remain_diagnostic_only():
    result = validate_level_distinctiveness(
        "这里一直下雪，天气不太好，不过大家一定会来。" * 3,
        "hsk2",
        lower_level_max_above_ratio=0.15,
    )

    assert result["lower_level_lexical_pass"] is False
    assert result["target_band_unique"] > 0
    assert len(result["target_band_words"]) == result["target_band_unique"]
    assert "passes" not in result
