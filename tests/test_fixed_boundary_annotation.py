import json

import pytest

from pipeline.fixed_boundary_annotation import (
    apply_correction_patches, correction_prompt, dictionary_hints, fixed_segments,
    prompt_for, review_scoped_correction,
    refined_fixed_segments, validate_result,
)
from pipeline.agent_harness import CHINESE_PINYIN_POLICY


def test_fixed_generation_and_correction_prompts_enforce_neutral_directional_lai():
    generation = prompt_for("海水泛上来。", ["海水", "泛上来", "。"], "hsk3")
    annotation = {"segments": [
        {"text": "泛上来", "type": "word", "pinyin": "fàn shànglái",
         "meaning_en": "surge up"},
    ], "grammar_overlays": []}
    correction = correction_prompt("泛上来", annotation)

    assert CHINESE_PINYIN_POLICY in generation
    assert CHINESE_PINYIN_POLICY in correction


def test_fixed_segments_are_lossless_and_pos_aware():
    text = "张飞带着刘备走。“好！”\n"
    segments = fixed_segments(text)
    assert "".join(segments) == text
    assert "张飞" in segments
    assert "张飞带" not in segments
    assert "。" in segments and "\n" in segments
    assert "“" in segments and "”" in segments


def test_validate_strips_indices_and_accepts_exact_overlay():
    chunk, surfaces = "刘备来了。", ["刘备", "来", "了", "。"]
    value = {
        "segments": [
            {"index": 0, "text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
            {"index": 1, "text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come"},
            {"index": 2, "text": "了", "type": "particle", "pinyin": "le", "meaning_en": "completed action"},
            {"index": 3, "text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
        ],
        "grammar_overlays": [{"start": 2, "end": 4, "text": "来了",
                              "grammar_candidate_key": "test.completed_action",
                              "pattern": "V + 了", "meaning_en": "completed action"}],
    }
    result = validate_result(chunk, surfaces, value)
    assert "index" not in result["segments"][0]


def test_validate_rejects_boundary_change_and_bad_offset():
    chunk, surfaces = "刘备。", ["刘备", "。"]
    base = {"segments": [
        {"index": 0, "text": "刘", "type": "name", "pinyin": "Liú", "meaning_en": "Liu"},
        {"index": 1, "text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    with pytest.raises(ValueError, match="changed fixed segment"):
        validate_result(chunk, surfaces, base)


def test_refinement_splits_compositional_phrases_but_preserves_compounds():
    text = "长江东，夕阳红，分久，父亡，家贫，喜相逢，付笑谈中。"
    segments = refined_fixed_segments(text)
    assert "".join(segments) == text
    for surface in ("长江东", "夕阳红", "分久", "父亡", "家贫"):
        assert surface not in segments
    assert "相逢" in segments
    assert "笑谈" in segments


def test_refinement_preserves_agent_reviewed_names_and_short_story_terms():
    text = "关羽在桃园祭告天地。"
    segments = refined_fixed_segments(
        text,
        protected_names={"关羽"},
        protected_compounds={"桃园", "祭告天地"},
    )
    assert "关羽" in segments
    assert "桃园" in segments
    assert "祭告天地" in segments
    assert "".join(segments) == text


def test_refinement_restores_protected_word_starting_inside_token():
    segments = refined_fixed_segments("外面有乱事。", protected_compounds={"乱事"})
    assert "有乱" not in segments
    assert "乱事" in segments
    assert "".join(segments) == "外面有乱事。"


def test_refinement_splits_contextual_false_compound_maqi():
    segments = refined_fixed_segments("他们没有马骑。")
    assert "马骑" not in segments
    assert "马" in segments and "骑" in segments
    assert "".join(segments) == "他们没有马骑。"


def test_correction_patches_apply_adjacent_split_and_merge_losslessly():
    base = {"segments": [
        {"text": "张", "type": "word", "pinyin": "zhāng", "meaning_en": "Zhang"},
        {"text": "飞怒", "type": "word", "pinyin": "fēi nù", "meaning_en": "became angry"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    correction = {"patches": [{"start_index": 0, "end_index": 2, "segments": [
        {"text": "张飞", "type": "name", "pinyin": "Zhāng Fēi", "meaning_en": "Zhang Fei"},
        {"text": "怒", "type": "word", "pinyin": "nù", "meaning_en": "be angry"},
    ]}], "grammar_overlays": []}
    result = apply_correction_patches("张飞怒。", base, correction)
    assert [item["text"] for item in result["segments"]] == ["张飞", "怒", "。"]


@pytest.mark.parametrize("patch, message", [
    ({"start_index": 0, "end_index": 1, "segments": [
        {"text": "错", "type": "word", "pinyin": "cuò", "meaning_en": "wrong"},
    ]}, "changed source text"),
    ({"start_index": 0, "end_index": 2, "segments": [
        {"text": "刘备", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ]}, "lexical metadata"),
])
def test_correction_rejects_changed_text_and_invalid_metadata(patch, message):
    base = {"segments": [
        {"text": "刘", "type": "name", "pinyin": "Liú", "meaning_en": "Liu"},
        {"text": "备", "type": "name", "pinyin": "Bèi", "meaning_en": "Bei"},
    ], "grammar_overlays": []}
    with pytest.raises(ValueError, match=message):
        apply_correction_patches("刘备", base, {"patches": [patch], "grammar_overlays": []})


def test_correction_rejects_overlapping_or_unsorted_ranges():
    seg = lambda text: {"text": text, "type": "word", "pinyin": "x", "meaning_en": "x"}
    base = {"segments": [seg("甲"), seg("乙"), seg("丙")], "grammar_overlays": []}
    correction = {"patches": [
        {"start_index": 1, "end_index": 3, "segments": [seg("乙丙")]},
        {"start_index": 0, "end_index": 1, "segments": [seg("甲")]},
    ], "grammar_overlays": []}
    with pytest.raises(ValueError, match="non-overlapping"):
        apply_correction_patches("甲乙丙", base, correction)


def test_correction_rejects_character_offset_shaped_patch_index():
    # Character offset 4 exists in the source, but only token indices 0..2 exist.
    base = {"segments": [
        {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
        {"text": "来了", "type": "word", "pinyin": "lái le", "meaning_en": "arrived"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    patch = {"start_index": 4, "end_index": 5, "segments": [{
        "text": "。", "type": "punctuation", "pinyin": "", "meaning_en": "",
    }]}
    with pytest.raises(ValueError, match="patch ranges"):
        apply_correction_patches("刘备来了。", base,
                                 {"patches": [patch], "grammar_overlays": []})


def _word(text, pinyin="x", meaning="old"):
    return {"text": text, "type": "word", "pinyin": pinyin,
            "meaning_en": meaning}


def test_review_scope_applies_targeted_split_merge_and_meaning_only():
    annotation = {"segments": [
        _word("转头"), _word("结"), _word("党"), _word("进"),
    ], "grammar_overlays": []}
    correction = {"patches": [
        {"start_index": 0, "end_index": 1,
         "segments": [_word("转", "zhuǎn", "turn"), _word("头", "tóu", "head")]},
        {"start_index": 1, "end_index": 3,
         "segments": [_word("结党", "jié dǎng", "form a faction")]},
        {"start_index": 3, "end_index": 4,
         "segments": [_word("进", "jìn", "enter")]},
    ], "grammar_overlays": []}
    findings = {"issues": [
        {"segment_text": "转 / 头", "problem": "over_grouped"},
        {"segment_text": "结党", "problem": "under_grouped"},
        {"segment_text": "进", "problem": "meaning"},
    ]}
    scoped, evidence = review_scoped_correction(
        "转头结党进", annotation, correction, findings
    )
    result = apply_correction_patches("转头结党进", annotation, scoped)
    assert [item["text"] for item in result["segments"]] == ["转", "头", "结党", "进"]
    assert result["segments"][-1]["meaning_en"] == "enter"
    assert evidence["accepted_patch_count"] == 3


def test_review_scope_prevents_xhigh_regression_of_unrelated_tokens():
    annotation = {"segments": [
        _word("张角", "Zhāng Jiǎo", "Zhang Jiao"),
        _word("卷", "juàn", "measure word for books"),
        _word("进", "jìn", "go forward"),
    ], "grammar_overlays": []}
    correction = {"patches": [
        {"start_index": 0, "end_index": 1,
         "segments": [_word("张角", "Zhāng Jué", "Zhang Jiao")]},
        {"start_index": 1, "end_index": 2,
         "segments": [_word("卷", "juǎn", "to roll")]},
        {"start_index": 2, "end_index": 3,
         "segments": [_word("进", "jìn", "enter")]},
    ], "grammar_overlays": []}
    scoped, evidence = review_scoped_correction(
        "张角卷进", annotation, correction,
        {"issues": [{"segment_text": "进", "problem": "meaning"}]},
    )
    result = apply_correction_patches("张角卷进", annotation, scoped)
    assert result["segments"][:2] == annotation["segments"][:2]
    assert result["segments"][2]["meaning_en"] == "enter"
    assert len(evidence["discarded_patches"]) == 2


def test_review_scope_discards_ambiguous_duplicate_surface():
    annotation = {"segments": [
        _word("进"), _word("山"), _word("再"), _word("进"), _word("山"),
    ], "grammar_overlays": []}
    correction = {"patches": [{
        "start_index": 0, "end_index": 1,
        "segments": [_word("进", "jìn", "enter")],
    }], "grammar_overlays": []}
    scoped, evidence = review_scoped_correction(
        "进山再进山", annotation, correction,
        {"issues": [{"segment_text": "进", "problem": "meaning"}]},
    )
    assert scoped["patches"] == []
    assert evidence["discarded_issue_scopes"][0]["reason"] == "ambiguous_surface"


def test_review_offsets_target_only_one_duplicate_occurrence():
    annotation = {"segments": [
        _word("进", "jìn", "advance"), _word("山", "shān", "mountain"),
        _word("再", "zài", "again"), _word("进", "jìn", "advance"),
        _word("山", "shān", "mountain"),
    ], "grammar_overlays": []}
    correction = {"patches": [
        {"start_index": 0, "end_index": 1,
         "segments": [_word("进", "jìn", "wrong first mutation")]},
        {"start_index": 3, "end_index": 4,
         "segments": [_word("进", "jìn", "enter")]}],
        "grammar_overlays": []}
    scoped, evidence = review_scoped_correction(
        "进山再进山", annotation, correction,
        {"issues": [{"start": 3, "end": 4, "segment_text": "进",
                     "problem": "meaning"}]},
    )
    result = apply_correction_patches("进山再进山", annotation, scoped)
    assert result["segments"][0] == annotation["segments"][0]
    assert result["segments"][3]["meaning_en"] == "enter"
    assert len(evidence["discarded_patches"]) == 1


def test_review_bad_offset_fails_closed_without_legacy_surface_fallback():
    annotation = {"segments": [_word("进"), _word("山")],
                  "grammar_overlays": []}
    correction = {"patches": [{"start_index": 0, "end_index": 1,
                                "segments": [_word("进", "jìn", "enter")]}],
                  "grammar_overlays": []}
    scoped, evidence = review_scoped_correction(
        "进山", annotation, correction,
        {"issues": [{"start": 1, "end": 2, "segment_text": "进",
                     "problem": "meaning"}]},
    )
    assert scoped["patches"] == []
    assert evidence["discarded_issue_scopes"][0]["reason"] == "invalid_offset"


def test_review_offsets_authorize_exact_merge_and_split_spans():
    annotation = {"segments": [_word("转头"), _word("结"), _word("党")],
                  "grammar_overlays": []}
    correction = {"patches": [
        {"start_index": 0, "end_index": 1,
         "segments": [_word("转", "zhuǎn", "turn"), _word("头", "tóu", "head")]},
        {"start_index": 1, "end_index": 3,
         "segments": [_word("结党", "jié dǎng", "form a faction")]},
    ], "grammar_overlays": []}
    scoped, _ = review_scoped_correction(
        "转头结党", annotation, correction,
        {"issues": [
            {"start": 0, "end": 2, "segment_text": "转头", "problem": "over_grouped"},
            {"start": 2, "end": 4, "segment_text": "结党", "problem": "under_grouped"},
        ]},
    )
    result = apply_correction_patches("转头结党", annotation, scoped)
    assert [item["text"] for item in result["segments"]] == ["转", "头", "结党"]


def test_review_scope_preserves_overlays_without_grammar_issue():
    old = {"start": 0, "end": 2, "text": "如果",
           "grammar_candidate_key": "test.if_then", "pattern": "如果…就…",
           "meaning_en": "if...then..."}
    annotation = {"segments": [_word("如果"), _word("来")],
                  "grammar_overlays": [old]}
    scoped, evidence = review_scoped_correction(
        "如果来", annotation,
        {"patches": [], "grammar_overlays": [{
            "start": 2, "end": 3, "text": "来",
            "grammar_candidate_key": "test.bad", "pattern": "bad",
            "meaning_en": "unrelated",
        }]},
        {"issues": [{"segment_text": "来", "problem": "meaning"}]},
    )
    assert scoped["grammar_overlays"] == [old]
    assert evidence["discarded_overlay_count"] == 1


def test_review_scope_changes_only_overlay_overlapping_grammar_issue():
    target = {"start": 0, "end": 2, "text": "如果",
              "grammar_candidate_key": "test.if_then", "pattern": "如果…就…",
              "meaning_en": "old target"}
    unrelated = {"start": 3, "end": 5, "text": "才来",
                 "grammar_candidate_key": "test.cai", "pattern": "才",
                 "meaning_en": "only then"}
    annotation = {"segments": [_word("如果"), _word("他"), _word("才来")],
                  "grammar_overlays": [target, unrelated]}
    replacement = {**target, "meaning_en": "if...then..."}
    injected = {"start": 2, "end": 3, "text": "他",
                "grammar_candidate_key": "test.bad", "pattern": "bad",
                "meaning_en": "unrelated"}
    scoped, evidence = review_scoped_correction(
        "如果他才来", annotation,
        {"patches": [], "grammar_overlays": [replacement, injected]},
        {"issues": [{"segment_text": "如果", "problem": "grammar"}]},
    )
    assert scoped["grammar_overlays"] == [unrelated, replacement]
    assert evidence["discarded_overlay_count"] == 1


def test_correction_prompt_exposes_independent_boundaries_and_name_spans():
    annotation = {"segments": [
        {"text": "张飞", "type": "name", "pinyin": "Zhāng Fēi", "meaning_en": "Zhang Fei"},
        {"text": "怒", "type": "word", "pinyin": "nù", "meaning_en": "be angry"},
    ], "grammar_overlays": []}
    prompt = correction_prompt("张飞怒", annotation)
    assert "non_authoritative_boundary_proposals_v1" in prompt
    assert "Neither tokenizer is authoritative" in prompt
    assert '"selected_boundaries":null' in prompt
    assert "COLUMNS=[TOKEN_INDEX,CHAR_START,CHAR_END,TEXT,TYPE,PINYIN,MEANING_EN]" in prompt
    assert '[0,0,2,"张飞","name","Zhāng Fēi","Zhang Fei"]' in prompt
    assert "They are NEVER character offsets" in prompt
    assert "start_index=12,end_index=14" in prompt
    assert "Convert the desired span to TOKEN_INDEX" in prompt


def test_compact_correction_prompt_retains_complete_token_and_evidence_contract():
    annotation = {"segments": [
        {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
        {"text": "来了", "type": "word", "pinyin": "lái le", "meaning_en": "arrived"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": [{
        "start": 2, "end": 4, "text": "来了",
        "grammar_candidate_key": "test.completed_action", "pattern": "V + 了",
        "meaning_en": "completed action",
    }]}
    findings = {"verdict": "revise", "issues": [{
        "segment_text": "来了", "problem": "under_grouped",
        "explanation": "check local word boundary", "suggested_fix": "review",
    }]}
    prompt = correction_prompt("刘备来了。", annotation, findings)
    table_text = prompt.split(
        "COLUMNS=[TOKEN_INDEX,CHAR_START,CHAR_END,TEXT,TYPE,PINYIN,MEANING_EN]\n", 1
    )[1].split("\n\nCURRENT GRAMMAR OVERLAYS:", 1)[0]
    rows = json.loads(table_text)
    assert rows == [
        [0, 0, 2, "刘备", "name", "Liú Bèi", "Liu Bei"],
        [1, 2, 4, "来了", "word", "lái le", "arrived"],
        [2, 4, 5, "。", "punctuation", "", ""],
    ]
    evidence_text = prompt.split("COMPACT BOUNDARY EVIDENCE JSON:\n", 1)[1].split(
        "\n\nINDEPENDENT REVIEW FINDINGS:", 1
    )[0]
    evidence = json.loads(evidence_text)
    assert evidence["contract"] == "non_authoritative_boundary_proposals_v1"
    assert evidence["text_length"] == len("刘备来了。")
    assert isinstance(evidence["plain_jieba_boundaries"], list)
    assert isinstance(evidence["refined_boundaries"], list)
    assert isinstance(evidence["gazetteer_spans"], list)
    assert evidence["selected_boundaries"] is None
    assert json.dumps(findings, ensure_ascii=False, separators=(",", ":")) in prompt


def test_compact_correction_prompt_materially_reduces_redundant_serialization():
    text = "刘备来到城里，关羽也来到城里。" * 12
    surfaces = fixed_segments(text)
    annotation = {"segments": [
        {"text": surface,
         "type": "punctuation" if all(not char.isalnum() for char in surface) else "word",
         "pinyin": "" if all(not char.isalnum() for char in surface) else "pinyin",
         "meaning_en": "" if all(not char.isalnum() for char in surface) else "meaning"}
        for surface in surfaces
    ], "grammar_overlays": []}
    compact = correction_prompt(text, annotation)
    indexed = [{"index": index, **segment}
               for index, segment in enumerate(annotation["segments"])]
    # The old prompt serialized this full metadata object and then repeated its
    # index/text/type in a second table. This lower bound omits even the former
    # verbose proposal JSON, so beating it demonstrates real de-duplication.
    redundant_lower_bound = (
        json.dumps(indexed, ensure_ascii=False)
        + json.dumps(indexed, ensure_ascii=False)
        + compact[:compact.index("CHUNK:\n")]
    )
    assert len(compact.encode("utf-8")) < len(redundant_lower_bound.encode("utf-8"))


def test_refinement_generalizes_fresh_audit_patterns():
    text = "因是如此，当立，破敌之围，先用火攻，再行斩杀；少时改姓。"
    segments = refined_fixed_segments(text)
    assert "".join(segments) == text
    for pair in (("因", "是"), ("当", "立"), ("之", "围")):
        assert any(tuple(segments[i:i + 2]) == pair for i in range(len(segments) - 1))
    for word in ("火攻", "斩杀", "少时", "改姓"):
        assert word in segments


def test_dictionary_hints_cover_known_words_and_fallback_pinyin(tmp_path):
    dictionary = tmp_path / "dictionary.json"
    dictionary.write_text('{"刘备":{"p":"Liú Bèi","d":["Liu Bei","extra","ignored"]}}')
    hints = dictionary_hints(["刘备", "玄德", "。"], dictionary)
    assert hints[0]["meaning_hints"] == ["Liu Bei", "extra"]
    assert hints[1]["pinyin_hint"] == "xuán dé"
    assert hints[1]["meaning_hints"] == []
    assert hints[2]["pinyin_hint"] == "" and hints[2]["meaning_hints"] == []
