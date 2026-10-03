import json
from pathlib import Path

import pytest

from pipeline.agent_harness import cjk_count
from pipeline.book_continuity import (
    discover_chapters, implicated_chapters, promote_continuity_candidate,
)


def _chapter(tmp_path: Path, stem: str, level: str = "hsk4") -> Path:
    source = tmp_path / f"{stem}.txt"
    source.write_text(f"source {stem}", encoding="utf-8")
    run = tmp_path / "run" / f"{stem}-{level}"
    run.mkdir(parents=True)
    (run / "chapter.txt").write_text(f"reader {stem}", encoding="utf-8")
    return source


def test_discover_chapters_matches_book_harness_layout_and_sorts(tmp_path):
    second = _chapter(tmp_path, "chapter_002")
    first = _chapter(tmp_path, "chapter_001")
    chapters = discover_chapters([str(second), str(first)], tmp_path / "run", "hsk4")
    assert [item.source_path.name for item in chapters] == ["chapter_001.txt", "chapter_002.txt"]
    assert [item.text for item in chapters] == ["reader chapter_001", "reader chapter_002"]


def test_discover_chapters_fails_closed_without_accepted_text(tmp_path):
    source = tmp_path / "chapter_001.txt"
    source.write_text("source", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="accepted chapter missing"):
        discover_chapters([str(source)], tmp_path / "run", "hsk4")


def test_implicated_chapters_converts_pair_offsets_to_absolute_indexes():
    audits = {
        0: {"verdict": "pass", "issues": []},
        1: {"verdict": "revise", "issues": [
            {"chapter_offset": 0, "category": "timeline", "explanation": "a", "suggested_fix": "b"},
            {"chapter_offset": 1, "category": "identity", "explanation": "c", "suggested_fix": "d"},
        ]},
    }
    result = implicated_chapters(audits)
    assert sorted(result) == [1, 2]
    assert result[1][0]["pair_start"] == 2


def test_continuity_schema_is_strict_and_pair_local():
    schema = json.loads(
        (Path(__file__).parents[1] / "pipeline/schemas/continuity-review.schema.json").read_text()
    )
    issue = schema["properties"]["issues"]["items"]
    assert issue["additionalProperties"] is False
    assert issue["properties"]["chapter_offset"]["maximum"] == 1


def test_continuity_promotion_is_atomic_publisher_compatible_and_invalidates_annotations(tmp_path):
    run = tmp_path / "chapter_001-hsk4"
    run.mkdir()
    original = "刘备到达城中。\n"
    candidate = "刘备到达城中，并说明来意。\n"
    (run / "chapter.txt").write_text(original, encoding="utf-8")
    manifest = {"source": "/source/chapter_001.txt", "source_sha256": "abc", "level": "hsk4"}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = {
        "status": "complete", "chapter_cjk": cjk_count(original),
        "scene_verdicts": {"scene_01": "pass"},
    }
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run / "reader.json").write_text(json.dumps({"text": original}), encoding="utf-8")
    annotation = run / "agents" / "annotations" / "chunk_0000"
    annotation.mkdir(parents=True)
    (annotation / "result.json").write_text("{}", encoding="utf-8")
    manifest_before = (run / "manifest.json").read_bytes()

    promotion = promote_continuity_candidate(run, candidate)

    assert (run / "chapter.txt").read_text() == candidate
    assert (run / "chapter.pre-continuity.txt").read_text() == original
    assert (run / "report.pre-continuity.json").exists()
    assert not (run / "reader.json").exists()
    assert not (run / "agents" / "annotations").exists()
    assert (run / "reader.pre-continuity.json").exists()
    assert (run / "agents" / "annotations.pre-continuity").exists()
    assert (run / "manifest.json").read_bytes() == manifest_before
    updated = json.loads((run / "report.json").read_text())
    # These are exactly the fields the deterministic publisher checks.
    assert updated["status"] == "complete"
    assert updated["scene_verdicts"] == {"scene_01": "pass"}
    assert updated["chapter_cjk"] == cjk_count(candidate)
    assert updated["continuity_promotion"] == promotion
    assert promotion["annotation_rerun_required"] is True


def test_continuity_promotion_backup_is_idempotent(tmp_path):
    run = tmp_path / "chapter_001-hsk4"
    run.mkdir()
    original = "原来的章节。\n"
    (run / "chapter.txt").write_text(original, encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"source_sha256": "abc"}), encoding="utf-8")
    (run / "report.json").write_text(json.dumps({
        "status": "complete", "chapter_cjk": cjk_count(original),
        "scene_verdicts": {"scene_01": "pass"},
    }), encoding="utf-8")

    promote_continuity_candidate(run, "第一次修复。\n")
    promote_continuity_candidate(run, "第二次修复。\n")

    assert (run / "chapter.txt").read_text() == "第二次修复。\n"
    assert (run / "chapter.pre-continuity.txt").read_text() == original
    backed_report = json.loads((run / "report.pre-continuity.json").read_text())
    assert "continuity_promotion" not in backed_report
