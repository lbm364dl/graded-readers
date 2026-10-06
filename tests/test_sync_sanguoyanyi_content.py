import hashlib
import json

import pytest

from scripts.sync_sanguoyanyi_content import (
    accepted_chapter,
    chapter_run_dir,
    reviewed_annotation,
    reviewed_focus_words,
)


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_clean_sync_rejects_legacy_hsk1_prose(tmp_path):
    run = tmp_path / "chapter_001-hsk1"
    run.mkdir()
    (run / "chapter.txt").write_text(
        "东汉末年宦官专权，灾祸不断，盗贼蜂起。" * 8,
        encoding="utf-8",
    )
    _json(run / "manifest.json", {"status": "complete", "level": "hsk1"})
    _json(run / "report.json", {
        "status": "complete", "scene_verdicts": {"scene_01": "pass"},
    })
    _json(run / "outline.json", {"chapter_title": "旧稿"})

    with pytest.raises(ValueError, match="level word/sentence readability"):
        accepted_chapter(run)


def test_sync_reuses_reviewed_focus_vocabulary_plan(tmp_path):
    run = tmp_path / "chapter_001-hsk2"
    _json(run / "focus-vocabulary-plan.json", {
        "names": [{"surface": "刘备", "reason_en": "name"}],
        "story_terms": [{
            "surface": "祭告天地",
            "meaning_en": "ritual",
            "reason_en": "plot",
        }],
    })

    assert reviewed_focus_words(run) == {"刘备", "祭告天地"}


def test_sync_resolves_book_harness_source_stem_run_name(tmp_path):
    run = tmp_path / "chapter_001_taoyuan-hsk4"
    run.mkdir()

    assert chapter_run_dir(tmp_path, 1, "hsk4") == run


def test_reviewed_annotation_drops_only_trailing_file_newline(tmp_path):
    run = tmp_path / "chapter_001-hsk1"
    run.mkdir()
    raw = "刘备。\n"
    chapter = run / "chapter.txt"
    chapter.write_text(raw, encoding="utf-8")
    reader = {
        "text": raw,
        "segments": [
            {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
            {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
            {"text": "\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
        ],
        "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True},
    }
    reader_path = run / "reader.json"
    _json(reader_path, reader)
    _json(run / "annotation-report.json", {
        "status": "complete",
        "chapter_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(),
        "reader_sha256": hashlib.sha256(reader_path.read_bytes()).hexdigest(),
    })

    result = reviewed_annotation(run, "刘备。")

    assert result["text"] == "刘备。"
    assert "".join(item["text"] for item in result["segments"]) == "刘备。"


def test_reviewed_annotation_preserves_reviewed_learning_focus_audit(tmp_path):
    run = tmp_path / "chapter_001-hsk1"
    run.mkdir()
    raw = "刘备。"
    chapter = run / "chapter.txt"
    chapter.write_text(raw, encoding="utf-8")
    reader = {
        "text": raw,
        "segments": [
            {"text": "刘备", "type": "name", "pinyin": "Liú Bèi",
             "meaning_en": "Liu Bei", "learning_focus": "lookup"},
            {"text": "。", "type": "punctuation", "pinyin": "",
             "meaning_en": "", "learning_focus": "not_applicable"},
        ],
        "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True},
        "learning_focus_audit": {"role_assignment_reviewed": True},
    }
    reader_path = run / "reader.json"
    _json(reader_path, reader)
    _json(run / "annotation-report.json", {
        "status": "complete",
        "chapter_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(),
        "reader_sha256": hashlib.sha256(reader_path.read_bytes()).hexdigest(),
    })

    result = reviewed_annotation(run, raw)

    assert result["learning_focus_audit"] == {"role_assignment_reviewed": True}
    assert result["segments"][0]["learning_focus"] == "lookup"


def test_reviewed_annotation_strips_retired_chinese_first_help(tmp_path):
    run = tmp_path / "chapter_001-hsk1"
    run.mkdir()
    raw = "刘备。"
    chapter = run / "chapter.txt"
    chapter.write_text(raw, encoding="utf-8")
    reader = {
        "text": raw,
        "segments": [
            {"text": "刘备", "type": "name", "pinyin": "Liú Bèi",
             "meaning_en": "Liu Bei", "explanation_zh": "", "example_zh": ""},
            {"text": "。", "type": "punctuation", "pinyin": "",
             "meaning_en": "", "explanation_zh": "", "example_zh": ""},
        ],
        "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True},
        "immersion_help_audit": {
            "generation_reviewed": True,
            "pedagogy_reviewed": True,
        },
    }
    reader_path = run / "reader.json"
    _json(reader_path, reader)
    _json(run / "annotation-report.json", {
        "status": "complete",
        "chapter_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(),
        "reader_sha256": hashlib.sha256(reader_path.read_bytes()).hexdigest(),
    })

    result = reviewed_annotation(run, raw)

    assert "immersion_help_audit" not in result
    assert "explanation_zh" not in result["segments"][0]
    assert "example_zh" not in result["segments"][0]
