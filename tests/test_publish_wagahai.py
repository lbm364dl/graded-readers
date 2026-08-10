import hashlib
import json
from pathlib import Path
import zipfile

import pytest
import pipeline.publish_wagahai as publisher_module

from pipeline.japanese_agent_harness import japanese_char_count
from pipeline.publish_wagahai import (
    PublicationError, _character_count, audit_runs, publish,
)


def test_publisher_uses_harness_character_metric_at_boundaries():
    text = "ひらがな カタカナー 時々 々・。ABC"
    assert _character_count(text) == japanese_char_count(text)
    assert _character_count("ー") == 1
    assert _character_count("々") == 1
    # The canonical harness range deliberately treats the Katakana middle dot
    # as a Japanese-script character while excluding Japanese full stop.
    assert _character_count("・。ABC") == 1


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path, lengths=(2, 3), annotated=True):
    sources = tmp_path / "sources"
    sources.mkdir()
    chapter_items = []
    for number in range(1, 3):
        path = sources / f"chapter_{number:02d}.txt"
        path.write_text(f"第{number}章の原文です。", encoding="utf-8")
        chapter_items.append({"number": number, "label": str(number),
                              "file": path.name,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    _json(sources / "manifest.json", {"chapter_count": 2, "chapters": chapter_items})

    runs = tmp_path / "runs"
    for number in range(1, 3):
        source = sources / f"chapter_{number:02d}.txt"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        for level, count in zip(("n5", "n4"), lengths):
            run = runs / f"chapter_{number:02d}-{level}"
            run.mkdir(parents=True)
            text = "猫です" * count + "。\n"
            (run / "chapter.txt").write_text(text, encoding="utf-8")
            _json(run / "manifest.json", {"status": "complete", "level": level,
                                           "source": str(source.resolve()),
                                           "source_sha256": digest})
            _json(run / "report.json", {"status": "complete",
                                         "japanese_characters": 3 * count,
                                         "unit_verdicts": {"chapter": "pass"},
                                         "chapter_review_verdict": "pass",
                                         "chapter_review": {"verdict": "pass"}})
            _json(run / "outline.json", {"chapter_title": f"猫の話{number}"})
            if annotated:
                segments = []
                for _ in range(count):
                    segments.extend([
                        {"surface": "猫", "kana": "ねこ", "lemma": "猫",
                         "type": "word", "meaning_en": "cat"},
                        {"surface": "です", "kana": "です", "lemma": "です",
                         "type": "auxiliary", "meaning_en": "is"},
                    ])
                segments.append({"surface": "。\n", "kana": "", "lemma": "",
                                 "type": "punctuation", "meaning_en": ""})
                _json(run / "reader.json", {
                    "title": f"猫の話{number}", "text": text,
                    "segments": segments,
                    "grammar_overlays": [{"start": 1, "end": 3, "surface": "です",
                                          "grammar": "Nです", "meaning_en": "copula"}],
                    "annotation_audit": {"all_reviewed": True},
                })
    return sources, runs


def test_audit_and_publish_full_ordered_collection(tmp_path):
    sources, runs = _fixture(tmp_path)
    audit = audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)
    output = tmp_path / "output"
    with pytest.raises(PublicationError, match="complete 11-chapter x 5-level matrix"):
        publish(audit, output)
    assert not output.exists()


def test_rejects_missing_run(tmp_path):
    sources, runs = _fixture(tmp_path)
    (runs / "chapter_02-n4").rename(tmp_path / "removed")
    with pytest.raises(PublicationError, match="missing 1 chapter-level run"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_rejects_non_increasing_chapter(tmp_path):
    sources, runs = _fixture(tmp_path, lengths=(3, 2))
    with pytest.raises(PublicationError, match="strictly increasing length"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_rejects_provenance_hash_mismatch(tmp_path):
    sources, runs = _fixture(tmp_path)
    path = runs / "chapter_01-n5" / "manifest.json"
    value = json.loads(path.read_text())
    value["source_sha256"] = "0" * 64
    _json(path, value)
    with pytest.raises(PublicationError, match="source provenance hash mismatch"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_rejects_scene_only_run_without_passed_chapter_review(tmp_path):
    sources, runs = _fixture(tmp_path)
    report_path = runs / "chapter_01-n5" / "report.json"
    report = json.loads(report_path.read_text())
    report["chapter_review_verdict"] = "not_run"
    report["chapter_review"] = None
    _json(report_path, report)
    with pytest.raises(PublicationError, match="whole-chapter review did not pass"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


@pytest.mark.parametrize("field", ["unsupported_additions", "distortions", "language_problems"])
def test_rejects_pass_with_unresolved_material_finding(tmp_path, field):
    sources, runs = _fixture(tmp_path)
    path = runs / "chapter_01-n5" / "report.json"
    report = json.loads(path.read_text())
    report["chapter_review"][field] = ["material unresolved defect"]
    _json(path, report)
    with pytest.raises(PublicationError, match="unresolved material findings"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_above_level_story_vocabulary_is_diagnostic_not_failure(tmp_path):
    sources, runs = _fixture(tmp_path)
    path = runs / "chapter_01-n5" / "report.json"
    report = json.loads(path.read_text())
    report["chapter_review"]["language_problems"] = ["above-level literary vocabulary"]
    _json(path, report)
    audit = audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)
    assert audit["books"]["n5"][0]["materiality_audit"]["unresolved_material_findings"] == []


def test_rejects_noncanonical_text_reading_segment_fields(tmp_path):
    sources, runs = _fixture(tmp_path)
    reader_path = runs / "chapter_01-n5" / "reader.json"
    reader = json.loads(reader_path.read_text())
    segment = reader["segments"][0]
    segment["text"] = segment.pop("surface")
    segment["reading"] = segment.pop("kana")
    _json(reader_path, reader)
    with pytest.raises(PublicationError, match="violates schema"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_rejects_unreviewed_or_nonreconstructing_annotations(tmp_path):
    sources, runs = _fixture(tmp_path)
    path = runs / "chapter_01-n5" / "reader.json"
    value = json.loads(path.read_text())
    value["segments"][0]["surface"] = "別の文"
    _json(path, value)
    with pytest.raises(PublicationError, match="annotations do not reconstruct"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_rejects_changed_source_file(tmp_path):
    sources, runs = _fixture(tmp_path)
    (sources / "chapter_01.txt").write_text("改変された原文", encoding="utf-8")
    with pytest.raises(PublicationError, match="source hash mismatch"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_accepts_agent_harness_text_fingerprint(tmp_path):
    sources, runs = _fixture(tmp_path)
    source = sources / "chapter_01.txt"
    digest = hashlib.sha256(source.read_text(encoding="utf-8").encode() + b"\0").hexdigest()
    manifest_path = runs / "chapter_01-n5" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_sha256"] = digest
    _json(manifest_path, manifest)

    audit = audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)
    assert audit["books"]["n5"][0]["source"]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda segment: segment.update(kana="neko"), "kana-only"),
        (lambda segment: segment.update(surface="猫" * 19), "clause-sized"),
        (lambda segment: segment.update(meaning_en=""), "must be nonempty"),
    ],
)
def test_rejects_semantically_invalid_segment_schema(tmp_path, mutation, message):
    sources, runs = _fixture(tmp_path)
    path = runs / "chapter_01-n5" / "reader.json"
    value = json.loads(path.read_text())
    mutation(value["segments"][0])
    if message == "clause-sized":
        value["text"] = "".join(item["surface"] for item in value["segments"])
        (runs / "chapter_01-n5" / "chapter.txt").write_text(value["text"], encoding="utf-8")
        report_path = runs / "chapter_01-n5" / "report.json"
        report = json.loads(report_path.read_text())
        report["japanese_characters"] = None
        report["japanese_chars"] = sum(
            "\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff"
            for char in value["text"]
        )
        _json(report_path, report)
    _json(path, value)
    with pytest.raises(PublicationError, match=message):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_rejects_invalid_grammar_overlay_span(tmp_path):
    sources, runs = _fixture(tmp_path)
    path = runs / "chapter_01-n5" / "reader.json"
    value = json.loads(path.read_text())
    value["grammar_overlays"][0]["end"] = len(value["text"]) + 1
    _json(path, value)
    with pytest.raises(PublicationError, match="character span is invalid"):
        audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)


def test_strips_independently_segmented_duplicate_heading(tmp_path):
    sources, runs = _fixture(tmp_path)
    run = runs / "chapter_01-n5"
    reader_path = run / "reader.json"
    reader = json.loads(reader_path.read_text())
    prefix = "# 猫の話1\n\n"
    reader["text"] = prefix + reader["text"]
    reader["segments"] = [
        {"surface": "# ", "type": "punctuation", "lemma": "", "kana": "",
         "meaning_en": ""},
        {"surface": "猫の話", "type": "name", "lemma": "猫の話", "kana": "ねこのはなし",
         "meaning_en": "the cat's story"},
        {"surface": "1\n\n", "type": "punctuation", "lemma": "", "kana": "",
         "meaning_en": ""},
    ] + reader["segments"]
    reader["grammar_overlays"] = []
    _json(reader_path, reader)
    (run / "chapter.txt").write_text(reader["text"], encoding="utf-8")
    report_path = run / "report.json"
    report = json.loads(report_path.read_text())
    report["japanese_characters"] += 3
    _json(report_path, report)

    audit = audit_runs([runs], sources, ["n5", "n4"], expected_chapters=2)
    assert audit["books"]["n5"][0]["text"].startswith("猫です")
    assert not audit["books"]["n5"][0]["text"].startswith("#")


def test_full_eleven_by_five_matrix_and_provenance(tmp_path, monkeypatch):
    verifier_calls = []
    monkeypatch.setattr(
        publisher_module, "verify_source_manifest",
        lambda path: verifier_calls.append(path),
    )
    sources = tmp_path / "sources"
    sources.mkdir()
    manifest = []
    runs = tmp_path / "runs"
    levels = ["n5", "n4", "n3", "n2", "n1"]
    for chapter in range(1, 12):
        source = sources / f"chapter_{chapter:02d}.txt"
        source.write_text(f"原作第{chapter}章", encoding="utf-8")
        raw_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest.append({"number": chapter, "file": source.name, "sha256": raw_digest})
        harness_digest = hashlib.sha256(source.read_text().encode() + b"\0").hexdigest()
        for level_index, level in enumerate(levels, 1):
            run = runs / f"chapter_{chapter:02d}-{level}"
            run.mkdir(parents=True)
            text = "猫" * level_index + "。\n"
            (run / "chapter.txt").write_text(text, encoding="utf-8")
            _json(run / "manifest.json", {"status": "complete", "level": level,
                                           "source": str(source.resolve()),
                                           "source_sha256": harness_digest})
            _json(run / "report.json", {"status": "complete",
                                         "japanese_chars": level_index,
                                         "scene_verdicts": {"scene_01": "pass"},
                                         "chapter_review_verdict": "pass",
                                         "chapter_review": {"verdict": "pass"}})
            _json(run / "outline.json", {"chapter_title": f"第{chapter}章"})
    _json(sources / "manifest.json", {"chapter_count": 11, "chapters": manifest})

    audit = audit_runs([runs], sources, levels, require_annotations=False)
    assert set(audit["books"]) == set(levels)
    assert all(len(audit["books"][level]) == 11 for level in levels)
    assert verifier_calls == [sources / "manifest.json"]


def test_full_canonical_audit_fails_closed_through_source_verifier(tmp_path, monkeypatch):
    def reject(_path):
        raise publisher_module.AozoraConversionError("deliberate source drift")

    monkeypatch.setattr(publisher_module, "verify_source_manifest", reject)
    with pytest.raises(PublicationError, match="source corpus completeness.*source drift"):
        audit_runs([], tmp_path, ["n5"], expected_chapters=11)


def test_publish_full_matrix_includes_valid_self_contained_epubs(tmp_path):
    levels = ["n5", "n4", "n3", "n2", "n1"]
    books = {}
    for level_index, level in enumerate(levels, 1):
        books[level] = []
        for number in range(1, 12):
            text = "猫です。\n"
            books[level].append({
                "number": number, "title": f"猫の話{number}", "text": text,
                "segments": [
                    {"surface": "猫", "type": "word", "lemma": "猫", "kana": "ねこ",
                     "meaning_en": "cat"},
                    {"surface": "です", "type": "auxiliary", "lemma": "です", "kana": "です",
                     "meaning_en": "is"},
                    {"surface": "。\n", "type": "punctuation", "lemma": "", "kana": "",
                     "meaning_en": ""},
                ],
                "grammar_overlays": [{"start": 1, "end": 3, "surface": "です",
                                       "grammar": "Nです", "meaning_en": "copula"}],
                "annotation_audit": {"all_reviewed": True},
                "materiality_audit": {"reviewed": True,
                                       "unresolved_material_findings": []},
                "level_diagnostics": {}, "characters": level_index,
                "source": {"file": f"chapter_{number:02d}.txt",
                           "sha256": f"{number:064x}", "run": "fixture"},
            })
    audit = {
        "book": "wagahai", "title": "吾輩は猫である", "author": "夏目漱石",
        "source_manifest": "/fixture/manifest.json", "expected_chapters": 11,
        "levels": levels, "books": books,
    }
    output = tmp_path / "published"
    report = publish(audit, output)
    assert report["invariants"]["complete_accessible_epub_per_level"] is True
    for level in levels:
        path = output / f"{level}_wagahai.epub"
        assert report["levels"][level]["epub_sha256"] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        with zipfile.ZipFile(path) as archive:
            assert archive.namelist()[0] == "mimetype"
            assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
            assert len([name for name in archive.namelist()
                        if name.startswith("EPUB/chapter_")]) == 11
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")
            assert "Vocabulary annotations" in chapter and "ねこ" in chapter
