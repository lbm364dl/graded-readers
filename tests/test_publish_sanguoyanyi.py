import hashlib
import json
from pathlib import Path
import zipfile

import pytest
import pipeline.publish_sanguoyanyi as publisher_module

from pipeline.agent_harness import digest
from pipeline.publish_sanguoyanyi import (
    PublicationError, TRADITIONAL, audit_runs, publish,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path, lengths=(2, 3), annotated=True):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    source_chapters = []
    for number in range(1, 3):
        path = source_dir / f"chapter_{number:03d}.txt"
        path.write_text(f"第{number}回 國軍原文", encoding="utf-8")
        source_chapters.append({
            "number": number,
            "title": f"第{number}回",
            "file": path.name,
            "characters": 6,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    _write_json(source_dir / "manifest.json", {
        "chapter_count": 2, "chapters": source_chapters,
    })

    run_dir = tmp_path / "runs"
    for number in range(1, 3):
        source = source_dir / f"chapter_{number:03d}.txt"
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        for level, count in zip(("hsk1", "hsk2"), lengths):
            current = run_dir / f"chapter_{number:03d}-{level}"
            current.mkdir(parents=True)
            # Repeated simplified diagnostic markers plus natural-looking prose.
            text = "国军" * count + "来了。\n"
            (current / "chapter.txt").write_text(text, encoding="utf-8")
            _write_json(current / "manifest.json", {
                "status": "complete", "level": level,
                "source": str(source.resolve()), "source_sha256": source_sha,
            })
            _write_json(current / "report.json", {
                "status": "complete", "chapter_cjk": len("国军") * count + 2,
                "scene_verdicts": {"scene_01": "pass"},
            })
            _write_json(current / "outline.json", {"chapter_title": f"故事{number}"})
            if annotated:
                segments = [
                    {"text": "国军", "type": "word", "pinyin": "guó jūn", "meaning_en": "national army"}
                    for _ in range(count)
                ] + [
                    {"text": "来了", "type": "word", "pinyin": "lái le", "meaning_en": "arrived"},
                    {"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
                ]
                _write_json(current / "reader.json", {
                    "title": f"故事{number}", "text": text, "segments": segments,
                    "grammar_overlays": [{
                        "start": 2 * count, "end": 2 * count + 2, "text": "来了",
                        "grammar_candidate_key": "test.completed_action",
                        "pattern": "V了", "meaning_en": "completed action or new state",
                    }],
                    "annotation_audit": {"all_reviewed": True, "chunks": 1},
                })
                reader_path = current / "reader.json"
                _write_json(current / "annotation-report.json", {
                    "status": "complete",
                    "chapter_sha256": hashlib.sha256(
                        (current / "chapter.txt").read_bytes()
                    ).hexdigest(),
                    "reader_sha256": hashlib.sha256(reader_path.read_bytes()).hexdigest(),
                    "chunks": 1,
                })
    return source_dir, run_dir


def test_full_canonical_audit_fails_closed_through_source_verifier(tmp_path, monkeypatch):
    def reject(_path):
        raise publisher_module.ExtractionError("deliberate source drift")

    monkeypatch.setattr(publisher_module, "verify_source_manifest", reject)
    with pytest.raises(PublicationError, match="source corpus completeness.*source drift"):
        audit_runs([], tmp_path, ["hsk1"], expected_chapters=120)


def test_publisher_accepts_only_consistent_reviewed_remediation_audit(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    for reader_path in run_dir.glob("*/reader.json"):
        reader = json.loads(reader_path.read_text())
        reader["annotation_audit"].update({
            "acceptance_state": "reviewed_and_remediated",
            "final_review_verdict": "revise", "remediation_calls": 2,
        })
        _write_json(reader_path, reader)
        report_path = reader_path.parent / "annotation-report.json"
        report = json.loads(report_path.read_text())
        report["reader_sha256"] = hashlib.sha256(reader_path.read_bytes()).hexdigest()
        _write_json(report_path, report)
    audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)

    reader_path = next(run_dir.glob("*/reader.json"))
    reader = json.loads(reader_path.read_text())
    reader["annotation_audit"]["remediation_calls"] = 0
    _write_json(reader_path, reader)
    report_path = reader_path.parent / "annotation-report.json"
    report = json.loads(report_path.read_text())
    report["reader_sha256"] = hashlib.sha256(reader_path.read_bytes()).hexdigest()
    _write_json(report_path, report)
    with pytest.raises(PublicationError, match="remediation evidence is inconsistent"):
        audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)


def test_audit_and_publish_complete_ordered_books(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    audit = audit_runs(
        [run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2
    )
    output = tmp_path / "output"
    report = publish(audit, output)

    assert report["status"] == "complete"
    markdown = (output / "hsk2_sanguoyanyi.md").read_text(encoding="utf-8")
    assert "**HSK Level 2**" in markdown
    assert markdown.count("\n## ") == 2
    assert markdown.index("## 第1章") < markdown.index("## 第2章")
    annotations = json.loads(
        (output / "hsk1_sanguoyanyi_annotations.json").read_text(encoding="utf-8")
    )
    assert annotations["chapter_count"] == 2
    assert "".join(x["text"] for x in annotations["chapters"][0]["segments"]) \
        == annotations["chapters"][0]["text"]
    epub = output / "hsk1_sanguoyanyi.epub"
    with zipfile.ZipFile(epub) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert len([name for name in archive.namelist()
                    if name.startswith("EPUB/chapter_")]) == 2
        chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")
        assert "Vocabulary annotations" in chapter
        assert "国军" in chapter and "national army" in chapter
        assert "Grammar annotations" in chapter


def test_audit_rejects_missing_chapter(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    missing = run_dir / "chapter_002-hsk2"
    missing.rename(tmp_path / "removed")

    with pytest.raises(PublicationError, match="missing 1 chapter-level run"):
        audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)


def test_audit_reports_length_order_without_rejecting_a_natural_shorter_level(tmp_path):
    source_dir, run_dir = _fixture(tmp_path, lengths=(3, 2))
    report = audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)
    assert report["length_order_observation"]["strictly_increasing_per_chapter_and_total"] is False


def test_audit_rejects_source_hash_mismatch(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    manifest_path = run_dir / "chapter_001-hsk1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_sha256"] = "0" * 64
    _write_json(manifest_path, manifest)

    with pytest.raises(PublicationError, match="source provenance hash mismatch"):
        audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)


def test_audit_accepts_real_chapter_harness_source_digest_convention(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    for manifest_path in run_dir.glob("chapter_*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = Path(manifest["source"])
        # This is the exact convention written by ChapterHarness.write_manifest.
        manifest["source_sha256"] = digest(source.read_text(encoding="utf-8"))
        _write_json(manifest_path, manifest)

    audit = audit_runs(
        [run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2
    )
    assert audit["expected_chapters"] == 2


def test_simplified_diagnostic_does_not_treat_identical_glyph_as_traditional():
    # The variant table intentionally contains documentation pairs such as
    # 力力; unchanged characters must never make publication fail.
    assert "力" not in TRADITIONAL


def test_audit_rejects_annotation_reconstruction_failure(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    reader_path = run_dir / "chapter_001-hsk1" / "reader.json"
    reader = json.loads(reader_path.read_text(encoding="utf-8"))
    reader["segments"][0]["text"] = "不是原文"
    _write_json(reader_path, reader)

    with pytest.raises(PublicationError, match="annotations violate segmentation contract"):
        audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)


def test_audit_rejects_clause_sized_lexical_annotation(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    current = run_dir / "chapter_001-hsk1"
    reader_path = current / "reader.json"
    reader = json.loads(reader_path.read_text(encoding="utf-8"))
    text = reader["text"]
    reader["segments"] = [
        {"text": text[:-2], "type": "word", "pinyin": "x", "meaning_en": "a sentence"},
        {"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ]
    _write_json(reader_path, reader)
    report_path = current / "annotation-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["reader_sha256"] = hashlib.sha256(reader_path.read_bytes()).hexdigest()
    _write_json(report_path, report)

    with pytest.raises(PublicationError, match="segmentation contract|clause-sized lexical segment"):
        audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)


def test_audit_rejects_stale_annotation_report_hash(tmp_path):
    source_dir, run_dir = _fixture(tmp_path)
    report_path = run_dir / "chapter_001-hsk1" / "annotation-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["reader_sha256"] = "0" * 64
    _write_json(report_path, report)

    with pytest.raises(PublicationError, match="stale for reader"):
        audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)


def test_audit_normalizes_traditional_output_and_annotations(tmp_path):
    source_dir, run_dir = _fixture(tmp_path, lengths=(10, 20))
    current = run_dir / "chapter_001-hsk1"
    text = "國軍將戰來說時為與後東長張劉關趙馬孫。\n"
    (current / "chapter.txt").write_text(text, encoding="utf-8")
    report = json.loads((current / "report.json").read_text(encoding="utf-8"))
    report["chapter_cjk"] = len(text.strip("。\n"))
    _write_json(current / "report.json", report)
    reader = json.loads((current / "reader.json").read_text(encoding="utf-8"))
    reader["text"] = text
    reader["segments"] = [
        {"text": char, "type": "word", "pinyin": "x", "meaning_en": "word"}
        for char in text[:-2]
    ] + [{"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""}]
    reader["grammar_overlays"] = []
    _write_json(current / "reader.json", reader)
    annotation_report = json.loads(
        (current / "annotation-report.json").read_text(encoding="utf-8")
    )
    annotation_report["chapter_sha256"] = hashlib.sha256(
        (current / "chapter.txt").read_bytes()
    ).hexdigest()
    annotation_report["reader_sha256"] = hashlib.sha256(
        (current / "reader.json").read_bytes()
    ).hexdigest()
    _write_json(current / "annotation-report.json", annotation_report)

    audit = audit_runs([run_dir], source_dir, ["hsk1", "hsk2"], expected_chapters=2)
    chapter = audit["books"]["hsk1"][0]
    assert "國" not in chapter["text"]
    assert "国军将战" in chapter["text"]
    assert "".join(item["text"] for item in chapter["segments"]) == chapter["text"]
