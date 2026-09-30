import json
from pathlib import Path

import pytest

from scripts import generate_app_content_json as publication
from pipeline.korean_readability import diagnostics, validate_chapter, vocabulary
from pipeline.publish_honggildong_smoke import build as publish_korean


def pilot_chapter():
    source = Path("content/korean/honggildong/l1.annotations.json")
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
    assert any(segment["text"] == "홍길동" for segment in annotation["segments"])


def test_korean_publication_rejects_unreviewed_or_shifted_segments(tmp_path):
    source = Path("content/korean/honggildong/l1.annotations.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    chapter = [{"title": "1. 길동의 집", "content": document["chapters"][0]["text"]}]
    document["chapters"][0]["segments"][0]["text"] = "옛날"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="do not reconstruct"):
        publication.load_korean_annotations(path, chapter, book_id="honggildong",
                                            level_key="l1", require_complete=True)
    document["chapters"][0]["segments"][0]["text"] = "옛날에"
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


def test_normal_app_builder_enforces_korean_level_gate(tmp_path):
    source = Path("content/korean/honggildong/l1.annotations.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    chapter = document["chapters"][0]
    chapter["segments"][5]["lexical"]["id"] = "아이02/감"
    chapter["segments"][12]["lexical"]["id"] = "수02/명"
    path = tmp_path / "above.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    chapters = [{"title": chapter["title"], "content": chapter["text"]}]
    with pytest.raises(ValueError, match="readability gate failed"):
        publication.load_korean_annotations(path, chapters, book_id="honggildong",
                                            level_key="l1", require_complete=True)
    chapter["segments"][5]["lexical"]["id"] = "아이01/명"
    chapter["segments"][12]["lexical"]["id"] = "아버지/명"
    chapter["source_alignment"]["reviewed"] = False
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
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
