import json
from pathlib import Path

import pytest

from scripts import generate_app_content_json as publication
from pipeline.korean_readability import (
    classify_segment, diagnostics, validate_chapter, vocabulary,
)
from pipeline.publish_honggildong_smoke import build as publish_korean
from pipeline.korean_dictionary import build_assets
from pipeline.korean_sentence_breakdowns import build as build_breakdowns


def pilot_chapter():
    source = Path("tests/fixtures/korean_manual_annotations.json")
    return json.loads(source.read_text(encoding="utf-8"))["chapters"][0]


def test_korean_pilot_has_reviewed_tap_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "ASSET_ROOT", tmp_path)
    entries = publication.build_language("korean", {"l1": 1})
    assert len(entries) == 1
    chapter = entries[0]["chapters"][0]
    annotation = json.loads((tmp_path / "annotations" /
        Path(chapter["annotationAsset"]).name).read_text())
    assert annotation["text"] == chapter["content"]
    assert "".join(segment["text"] for segment in annotation["segments"]) == chapter["content"]
    assert any(segment.get("lookup_reason") == "proper_name" for segment in annotation["segments"])


def test_korean_publication_rejects_unreviewed_or_shifted_segments(tmp_path):
    source = Path("content/korean/honggildong/l1.annotations.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    chapter = [{"title": "1. 길동의 집", "content": document["chapters"][0]["text"]}]
    original = document["chapters"][0]["segments"][0]["text"]
    document["chapters"][0]["segments"][0]["text"] = "changed"
    (tmp_path / "l1.review.json").write_bytes(Path("content/korean/honggildong/l1.review.json").read_bytes())
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="do not reconstruct"):
        publication.load_korean_annotations(path, chapter, book_id="honggildong",
                                            level_key="l1", require_complete=True)
    document["chapters"][0]["segments"][0]["text"] = original
    document["chapters"][0]["annotation_audit"]["all_reviewed"] = False
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="unreviewed"):
        publication.load_korean_annotations(path, chapter, book_id="honggildong",
                                            level_key="l1", require_complete=True)


def test_korean_vocabulary_preserves_homonym_and_part_of_speech():
    grades = vocabulary()
    assert grades["아이01/명"] == "A"
    assert grades["아이02/감"] == "B"
    assert grades["수02/의"] == "A"
    assert grades["수02/명"] == "C"


def test_korean_level_gate_accepts_reviewed_inflections_and_story_term():
    result = validate_chapter(pilot_chapter())
    assert result["beginner_vocabulary"] == 14
    assert result["story_terms"] == ["벼슬/명"]
    assert result["passes"]


def test_korean_level_gate_rejects_homonym_substitution_and_above_level_load():
    chapter = pilot_chapter()
    chapter["segments"][5]["lexical"]["id"] = "아이02/감"
    chapter["segments"][12]["lexical"]["id"] = "수02/명"
    result = diagnostics(chapter)
    assert not result["passes"]
    assert len(result["above_beginner"]) == 2
    with pytest.raises(ValueError, match="readability gate failed"):
        validate_chapter(chapter)


def test_korean_level_gate_rejects_unreviewed_lexeme_and_long_sentence():
    chapter = pilot_chapter()
    chapter["segments"][7]["lexical"]["id"] = "살다/동"
    with pytest.raises(ValueError, match="unresolved Korean vocabulary"):
        validate_chapter(chapter)
    chapter = pilot_chapter()
    for segment in chapter["segments"]:
        if segment["text"] == ".":
            segment["text"] = ","
    chapter["text"] = "".join(segment["text"] for segment in chapter["segments"])
    assert diagnostics(chapter)["longest_sentence_eojeol"] > 12
    with pytest.raises(ValueError, match="readability gate failed"):
        validate_chapter(chapter)


def test_normal_app_builder_rejects_stale_reviewed_content(tmp_path):
    source = Path("content/korean/honggildong/l1.annotations.json")
    document = json.loads(source.read_text())
    chapter = document["chapters"][0]
    word = next(segment for segment in chapter["segments"] if segment["type"] == "word")
    word["meaning_en"] = "changed after review"
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(document, ensure_ascii=False))
    (tmp_path / "l1.review.json").write_bytes(source.with_name("l1.review.json").read_bytes())
    chapters = [{"title": chapter["title"], "content": chapter["text"]}]
    with pytest.raises(ValueError, match="evidence is stale"):
        publication.load_korean_annotations(path, chapters, book_id="honggildong",
                                            level_key="l1", require_complete=True)
    chapter["source_alignment"]["reviewed"] = False
    path.write_text(json.dumps(document, ensure_ascii=False))
    with pytest.raises(ValueError, match="source alignment"):
        publication.load_korean_annotations(path, chapters, book_id="honggildong",
                                            level_key="l1", require_complete=True)


def test_korean_publisher_writes_only_first_chapter(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "ASSET_ROOT", tmp_path)
    result = publish_korean()
    assert result["passes"]
    entries = json.loads((tmp_path / "content_ko.json").read_text(encoding="utf-8"))
    assert len(entries) == len(entries[0]["chapters"]) == 1
    assert len(list((tmp_path / "annotations").glob("*.json"))) == 1
    words = json.loads((tmp_path / "usage_dictionary_ko.json").read_text())
    grammar = json.loads((tmp_path / "grammar_dictionary_ko.json").read_text())
    assert {entry["id"] for entry in words["entries"]} >= {
        use["entry_id"] for use in words["occurrences"]
    }
    assert {entry["id"] for entry in grammar["entries"]} >= {
        use["entry_id"] for use in grammar["occurrences"]
    }
    assert all(use["sentence"] in words["sources"][use["source"]]["text"]
               for use in words["occurrences"])


def test_korean_dictionary_rejects_stale_or_missing_grammar_links(tmp_path):
    chapter = pilot_chapter()
    chapter["grammar_links"] = [link for link in chapter["grammar_links"]
                                if link["segment_index"] != 3]
    with pytest.raises(ValueError, match="grammar tap has no linked lesson"):
        build_assets(chapter, tmp_path)
    assert not list(tmp_path.iterdir())
    chapter = pilot_chapter()
    chapter["grammar_links"][0]["entry_id"] = "unknown-pattern"
    with pytest.raises(ValueError, match="invalid Korean grammar occurrence"):
        build_assets(chapter, tmp_path)


def test_korean_dictionary_keeps_repeated_word_positions_distinct(tmp_path):
    words, grammar = build_assets(pilot_chapter(), tmp_path)
    father = [use for use in words["occurrences"] if use["entry_id"] == "아버지/명"]
    assert len(father) == 3
    assert len({use["start"] for use in father}) == 3
    topic = [use for use in grammar["occurrences"]
             if use["entry_id"] == "topic-eun-neun"]
    assert len(topic) == 3
    assert len({use["start"] for use in topic}) == 3


def test_korean_focus_distinguishes_story_and_above_level_vocabulary():
    segments = pilot_chapter()["segments"]
    assert classify_segment(segments[2])["lookup_reason"] == "proper_name"
    assert classify_segment(segments[16])["lookup_reason"] == "story_term"
    assert classify_segment(segments[14])["learning_focus"] == "target"
    above = {**segments[14], "lexical": {"kind": "vocabulary", "id": "아이02/감"}}
    assert classify_segment(above)["curriculum_status"] == "above_level"
    assert classify_segment(above)["lookup_reason"] == "above_level"
    assert classify_segment(segments[3])["curriculum_status"] == "not_applicable"


def test_korean_form_chains_require_reviewed_complete_stages(tmp_path):
    chapter = pilot_chapter()
    assert chapter["segments"][7]["form_steps"][-1]["form"] == "살았습니다"
    chapter["segments"][7]["form_steps"][-1]["form"] = "살았어요"
    with pytest.raises(ValueError, match="does not end at tap surface"):
        build_assets(chapter, tmp_path)
    chapter = pilot_chapter()
    chapter["segments"][7]["form_steps"] = []
    with pytest.raises(ValueError, match="form review is incomplete"):
        build_assets(chapter, tmp_path)


def test_korean_construction_stage_requires_exact_complete_phrase(tmp_path):
    chapter = pilot_chapter()
    _, grammar = build_assets(chapter, tmp_path)
    construction = next(use for use in grammar["occurrences"]
                        if use["entry_id"] == "ability-eul-su-eopda")
    assert construction["display_form"] == "부를 수 없었습니다"
    assert construction["display_meaning_en"] == "could not call"
    chapter["grammar_links"] = [
        {key: value for key, value in link.items()
         if key != "display_meaning_en"}
        if link["entry_id"] == "ability-eul-su-eopda" else link
        for link in chapter["grammar_links"]
    ]
    with pytest.raises(ValueError, match="lacks complete form"):
        build_assets(chapter, tmp_path)
    chapter = pilot_chapter()
    construction = next(link for link in chapter["grammar_links"]
                        if link["entry_id"] == "ability-eul-su-eopda")
    construction["display_form"] = "부를 수 있다"
    with pytest.raises(ValueError, match="invalid Korean construction stage"):
        build_assets(chapter, tmp_path)
    chapter = pilot_chapter()
    simple = next(link for link in chapter["grammar_links"]
                  if link["entry_id"] == "object-eul-reul")
    simple["display_form"] = "벼슬을"
    with pytest.raises(ValueError, match="unexpected Korean construction stage"):
        build_assets(chapter, tmp_path)


def test_korean_sentence_breakdowns_are_selective_and_source_bound(tmp_path):
    data = json.loads(Path("tests/fixtures/korean_manual_breakdowns.json").read_text())
    result = build_breakdowns(pilot_chapter(), tmp_path, data=data)
    assert len(result["breakdowns"]) == 2
    assert result["breakdowns"][0]["start"] == 22
    assert not any(item["start"] == 0 for item in result["breakdowns"])
    chapter = pilot_chapter()
    chapter["segments"][14]["text"] = "낮은"
    chapter["text"] = "".join(segment["text"] for segment in chapter["segments"])
    with pytest.raises(ValueError, match="lost source alignment"):
        build_breakdowns(chapter, tmp_path, data=data)
