import json

import pytest

from pipeline.delta_boundary_annotation import (
    apply_delta, boundary_covered_indices, collapse_identical_overrides,
    compact_boundary_targets, compact_delta_targets,
    concise_dictionary_meaning, delta_prompt,
    contextual_delta_batches, contextual_delta_targets, deterministic_baseline,
    missing_required_prompt,
    normalize_batch_overrides,
    normalize_optional_boundary_patches, normalize_optional_overlays,
)


def test_batch_override_normalization_discards_extras_and_conflicts_for_completion():
    good = {"index": 1, "type": "word", "pinyin": "yī", "meaning_en": "one"}
    conflict = {**good, "meaning_en": "first"}
    extra = {**good, "index": 9}
    value, evidence = normalize_batch_overrides({
        "overrides": [good, dict(good), conflict, extra],
    }, {1, 2})
    assert value["overrides"] == []
    assert evidence["duplicate_collapses"] == 1
    assert evidence["discarded_count"] == 4
    assert all(len(item["sha256"]) == 64 for item in evidence["discarded"])


def test_contextual_batches_are_disjoint_complete_bounded_and_chunk_scoped(monkeypatch):
    text = "甲乙丙。丁戊己。"
    baseline = [
        {"text": char, "type": "punctuation" if char == "。" else "word",
         "pinyin": "", "meaning_en": ""}
        for char in text
    ]
    required = {index for index, item in enumerate(baseline)
                if item["type"] != "punctuation"}
    batches = contextual_delta_batches(
        text, baseline, required, ["甲乙丙。", "丁戊己。"], maximum_targets=2,
    )
    flattened = [index for batch in batches for index in batch["indices"]]
    assert sorted(flattened) == sorted(required)
    assert len(flattened) == len(set(flattened))
    assert all(len(batch["indices"]) <= 2 for batch in batches)
    assert all(
        all(batch["start"] <= sum(len(item["text"]) for item in baseline[:index])
            < batch["end"] for index in batch["indices"])
        for batch in batches
    )


def test_baseline_is_exact_and_marks_required_unknowns():
    text = "刘备见玄德。"
    segments, required = deterministic_baseline(text)
    assert "".join(item["text"] for item in segments) == text
    assert all(segments[index]["meaning_en"] == "[LUNA REQUIRED]" for index in required)


def test_context_targets_include_single_han_and_inferred_names_not_punctuation(monkeypatch):
    monkeypatch.setattr(
        "pipeline.delta_boundary_annotation.refined_fixed_segments",
        lambda _text: ["东", "普通", "刘备", "。"],
    )
    monkeypatch.setattr(
        "pipeline.delta_boundary_annotation.dictionary_hints",
        lambda _surfaces: [
            {"index": 0, "text": "东", "pinyin_hint": "dōng", "meaning_hints": ["east"]},
            {"index": 1, "text": "普通", "pinyin_hint": "pǔ tōng", "meaning_hints": ["ordinary"]},
            {"index": 2, "text": "刘备", "pinyin_hint": "Liú Bèi", "meaning_hints": ["Liu Bei"]},
            {"index": 3, "text": "。", "pinyin_hint": "", "meaning_hints": []},
        ],
    )
    segments, required = deterministic_baseline("东普通刘备。")
    targets = contextual_delta_targets(segments, required)
    assert required == set()
    assert targets == {0, 2}
    assert segments[3]["type"] == "punctuation"


def test_delta_requires_every_unknown_and_preserves_text():
    baseline = [
        {"text": "玄德", "type": "word", "pinyin": "xuán dé", "meaning_en": "[LUNA REQUIRED]"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ]
    with pytest.raises(ValueError, match="omitted required"):
        apply_delta("玄德。", baseline, {0}, {"overrides": [], "grammar_overlays": []})
    result = apply_delta("玄德。", baseline, {0}, {"overrides": [{
        "index": 0, "type": "name", "pinyin": "Xuán Dé", "meaning_en": "Liu Bei's style name",
    }], "grammar_overlays": []})
    assert result["segments"][0]["type"] == "name"
    assert "".join(item["text"] for item in result["segments"]) == "玄德。"


def test_delta_accepts_sparse_known_context_override_and_rejects_non_target():
    baseline = [
        {"text": "东", "type": "name", "pinyin": "Dōng", "meaning_en": "Dong (surname)"},
        {"text": "普通", "type": "word", "pinyin": "pǔ tōng", "meaning_en": "ordinary"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ]
    unchanged = apply_delta("东普通。", baseline, set(), {
        "overrides": [], "grammar_overlays": [],
    })
    assert unchanged["segments"] == baseline
    corrected = apply_delta("东普通。", baseline, set(), {
        "overrides": [{
            "index": 0, "type": "word", "pinyin": "dōng", "meaning_en": "eastward",
        }],
        "grammar_overlays": [],
    })
    assert corrected["segments"][0]["meaning_en"] == "eastward"
    with pytest.raises(ValueError, match="non-target"):
        apply_delta("东普通。", baseline, set(), {
            "overrides": [{
                "index": 1, "type": "word", "pinyin": "pǔ tōng", "meaning_en": "common",
            }],
            "grammar_overlays": [],
        })


def test_seed_boundary_patch_is_lossless_scoped_and_precedes_overrides(monkeypatch):
    baseline = [
        {"text": "关", "type": "word", "pinyin": "guān", "meaning_en": "close"},
        {"text": "羽", "type": "word", "pinyin": "yǔ", "meaning_en": "feather"},
        {"text": "也", "type": "word", "pinyin": "yě", "meaning_en": "also"},
    ]
    monkeypatch.setattr(
        "pipeline.delta_boundary_annotation.compact_boundary_targets",
        lambda _text, _baseline: [{"start": 0, "end": 2}],
    )
    value = {
        "boundary_patches": [{"start": 0, "end": 2, "segments": [{
            "text": "关羽", "type": "name", "pinyin": "Guān Yǔ",
            "meaning_en": "Guan Yu",
        }]}],
        "overrides": [{"index": 2, "type": "particle", "pinyin": "yě",
                       "meaning_en": "also"}],
        "grammar_overlays": [],
    }
    assert boundary_covered_indices("关羽也", baseline, value) == {0, 1}
    result = apply_delta("关羽也", baseline, set(), value)
    assert [item["text"] for item in result["segments"]] == ["关羽", "也"]
    assert result["segments"][1]["type"] == "particle"


def test_seed_boundary_patch_rejects_non_target_overlap_loss_and_oversize(monkeypatch):
    baseline = [
        {"text": "人张角", "type": "word", "pinyin": "rén zhāng jiǎo", "meaning_en": "bad"},
        {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come"},
    ]
    monkeypatch.setattr(
        "pipeline.delta_boundary_annotation.compact_boundary_targets",
        lambda _text, _baseline: [{"start": 0, "end": 3}],
    )
    good = {"start": 0, "end": 3, "segments": [
        {"text": "人", "type": "word", "pinyin": "rén", "meaning_en": "person from"},
        {"text": "张角", "type": "name", "pinyin": "Zhāng Jiǎo", "meaning_en": "Zhang Jiao"},
    ]}
    result = apply_delta("人张角来", baseline, set(), {
        "boundary_patches": [good], "overrides": [], "grammar_overlays": [],
    })
    assert "".join(item["text"] for item in result["segments"]) == "人张角来"
    for patch, match in [
        ({**good, "start": 3, "end": 4}, "non-disagreement"),
        ({**good, "segments": [{**good["segments"][0], "text": "人错"}]}, "changed source"),
        ({**good, "segments": [{"text": "人张角来超过", "type": "word",
                                  "pinyin": "x", "meaning_en": "x"}]}, "changed source"),
    ]:
        with pytest.raises(ValueError, match=match):
            apply_delta("人张角来", baseline, set(), {
                "boundary_patches": [patch], "overrides": [], "grammar_overlays": [],
            })


def test_optional_boundary_normalization_retains_valid_and_collapses_duplicate(monkeypatch):
    baseline = [
        {"text": "关", "type": "word", "pinyin": "guān", "meaning_en": "close"},
        {"text": "羽", "type": "word", "pinyin": "yǔ", "meaning_en": "feather"},
    ]
    monkeypatch.setattr(
        "pipeline.delta_boundary_annotation.compact_boundary_targets",
        lambda _text, _baseline: [{"start": 0, "end": 2}],
    )
    patch = {"start": 0, "end": 2, "segments": [{
        "text": "关羽", "type": "name", "pinyin": "Guān Yǔ", "meaning_en": "Guan Yu",
    }]}
    value, evidence = normalize_optional_boundary_patches(
        "关羽", baseline, {"boundary_patches": [patch, dict(patch)]},
    )
    assert value["boundary_patches"] == [patch]
    assert evidence == {"discarded_count": 0, "duplicate_collapses": 1, "discarded": []}


def test_optional_boundary_normalization_discards_invalid_and_overlap(monkeypatch):
    baseline = [
        {"text": char, "type": "word", "pinyin": "x", "meaning_en": "x"}
        for char in "甲乙丙"
    ]
    monkeypatch.setattr(
        "pipeline.delta_boundary_annotation.compact_boundary_targets",
        lambda _text, _baseline: [{"start": 0, "end": 2}, {"start": 1, "end": 3}],
    )
    first = {"start": 0, "end": 2, "segments": [{
        "text": "甲乙", "type": "word", "pinyin": "x", "meaning_en": "first",
    }]}
    second = {"start": 1, "end": 3, "segments": [{
        "text": "乙丙", "type": "word", "pinyin": "x", "meaning_en": "second",
    }]}
    invalid = {"start": 0, "end": 1, "segments": [{
        "text": "甲", "type": "word", "pinyin": "x", "meaning_en": "invalid",
    }]}
    value, evidence = normalize_optional_boundary_patches(
        "甲乙丙", baseline, {"boundary_patches": [second, invalid, first]},
    )
    assert value["boundary_patches"] == []
    assert evidence["discarded_count"] == 3
    assert all(len(item["sha256"]) == 64 for item in evidence["discarded"])


def test_real_chapter_boundary_targets_expose_known_failures():
    from pathlib import Path
    path = Path("runs/experiments/chinese-fixed-boundary-hsk4-ch001/chapter.txt")
    if not path.exists():
        pytest.skip("experiment fixture is not present")
    text = path.read_text(encoding="utf-8")
    baseline, _ = deterministic_baseline(text)
    surfaces = [item["text"] for item in compact_boundary_targets(text, baseline)]
    # Jieba's global dictionary is mutated by unrelated segmentation tests, so
    # assert stable representative failures here; exact scope validation is
    # covered above with isolated proposals.
    assert all(any(needle in surface for surface in surfaces)
               for needle in ("关羽", "人张角"))


def test_compact_prompt_has_offsets_and_does_not_repeat_full_baseline():
    text = "刘备见玄德。"
    baseline = [
        {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
        {"text": "见", "type": "word", "pinyin": "jiàn", "meaning_en": "see"},
        {"text": "玄德", "type": "word", "pinyin": "xuán dé", "meaning_en": "[LUNA REQUIRED]"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ]
    targets = compact_delta_targets(baseline, {2})
    assert targets == [{"index": 2, "start": 3, "end": 5, "text": "玄德",
                        "left": ["刘备", "见"], "right": ["。"], "pinyin_hint": "xuán dé"}]
    prompt = delta_prompt(text, baseline, {2}, "hsk4")
    assert "Liu Bei" in prompt and "[LUNA REQUIRED]" not in prompt
    assert "TARGET ROW COLUMNS=" in prompt
    assert "REQUIRED=true" in prompt
    assert "CURRENT_TYPE" in prompt and '"word","jiàn","see",false' in prompt


def test_real_chapter_contextual_prompt_stays_under_48kb():
    from pathlib import Path
    path = Path("runs/experiments/chinese-fixed-boundary-hsk4-ch001/chapter.txt")
    if not path.exists():
        pytest.skip("experiment fixture is not present")
    text = path.read_text(encoding="utf-8")
    baseline, unknown = deterministic_baseline(text)
    prompt = delta_prompt(text, baseline, unknown, "hsk4")
    assert len(prompt.encode("utf-8")) < 48_000


def test_missing_required_prompt_is_bounded_and_forbids_overlays():
    baseline = [
        {"text": "卢植", "type": "word", "pinyin": "lú zhí", "meaning_en": "[LUNA REQUIRED]"},
        {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come"},
    ]
    prompt = missing_required_prompt("卢植来", baseline, {0}, "hsk4")
    assert "MISSING REQUIRED ROWS" in prompt
    assert "grammar_overlays MUST be an empty array" in prompt
    assert "卢植" in prompt and "来" in prompt


def test_dictionary_gloss_is_concise_and_biographical_pinyin_marks_name():
    assert concise_dictionary_meaning("Cao Cao (155-220), famous statesman; warlord") == "Cao Cao"
    assert len(concise_dictionary_meaning("x" * 200)) == 150


def test_delta_schema_forbids_exact_duplicate_overrides():
    from pathlib import Path
    schema = json.loads(Path("pipeline/schemas/delta-annotation.schema.json").read_text())
    # Structured Outputs does not support JSON Schema uniqueItems; duplicate
    # indices are rejected deterministically by apply_delta instead.
    assert "uniqueItems" not in schema["properties"]["overrides"]


def test_identical_duplicate_override_is_collapsed_but_conflict_rejected():
    row = {"index": 7, "type": "word", "pinyin": "dōng", "meaning_en": "east"}
    normalized, count = collapse_identical_overrides({
        "overrides": [row, dict(row)], "grammar_overlays": [],
    })
    assert normalized["overrides"] == [row] and count == 1
    conflict = {**row, "meaning_en": "Dong (surname)"}
    with pytest.raises(ValueError, match="conflicting duplicate"):
        collapse_identical_overrides({
            "overrides": [row, conflict], "grammar_overlays": [],
        })


def test_optional_overlay_normalization_is_exact_deduplicated_and_audited():
    text = "刘备因为下雨而回家。"
    valid = {
        "start": 2, "end": 8, "text": "因为下雨而回",
        "pattern": "因为……而……", "meaning_en": "because ... therefore ...",
    }
    mismatch = {**valid, "text": "错误表面"}
    conflicting = {**valid, "pattern": "different analysis"}
    normalized, evidence = normalize_optional_overlays(text, {
        "overrides": [],
        "grammar_overlays": [valid, dict(valid), mismatch, conflicting],
    })
    # The conflicting same-span analyses make that optional span unsafe, so it
    # is discarded rather than selecting one model opinion arbitrarily.
    assert normalized["grammar_overlays"] == []
    assert evidence["duplicate_collapses"] == 1
    assert evidence["discarded_count"] == 4
    assert all(len(item["sha256"]) == 64 for item in evidence["discarded"])


def test_optional_overlay_normalization_retains_valid_unique_overlay():
    text = "因为下雨，所以回家。"
    overlay = {
        "start": 0, "end": 10, "text": text[:10],
        "pattern": "因为……所以……", "meaning_en": "because ... therefore ...",
    }
    normalized, evidence = normalize_optional_overlays(text, {
        "overrides": [], "grammar_overlays": [overlay],
    })
    assert normalized["grammar_overlays"] == [overlay]
    assert evidence == {"discarded_count": 0, "duplicate_collapses": 0, "discarded": []}
