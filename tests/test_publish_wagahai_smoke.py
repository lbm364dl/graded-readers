import json

import pipeline.publish_wagahai_smoke as publisher


def test_inline_readings_are_removed_and_offsets_are_remapped():
    chapter = {
        "text": "吾輩（わがはい）は凶悪（きょうあく）な猫。",
        "segments": [
            {"surface": "吾輩", "type": "word"},
            {"surface": "（", "type": "punctuation"},
            {"surface": "わがはい", "type": "word"},
            {"surface": "）", "type": "punctuation"},
            {"surface": "は", "type": "particle"},
            {"surface": "凶悪（きょうあく）", "type": "word"},
            {"surface": "な", "type": "auxiliary"},
            {"surface": "猫", "type": "word"},
            {"surface": "。", "type": "punctuation"},
        ],
        "grammar_overlays": [{
            "start": 9,
            "end": 19,
            "surface": "凶悪（きょうあく）な",
            "components": [
                {"start": 0, "end": 2, "surface": "凶悪"},
                {"start": 2, "end": 9, "surface": "（きょうあく）"},
                {"start": 9, "end": 10, "surface": "な"},
            ],
        }],
    }

    cleaned = publisher._without_inline_readings(chapter)

    assert cleaned["text"] == "吾輩は凶悪な猫。"
    assert "".join(item["surface"] for item in cleaned["segments"]) == cleaned["text"]
    assert [item["surface"] for item in cleaned["segments"]] == [
        "吾輩", "は", "凶悪", "な", "猫", "。",
    ]
    overlay = cleaned["grammar_overlays"][0]
    assert cleaned["text"][overlay["start"]:overlay["end"]] == "凶悪な"
    assert [item["surface"] for item in overlay["components"]] == ["凶悪", "な"]


def test_chapter_one_publisher_requires_and_writes_all_jlpt_levels(
    tmp_path, monkeypatch,
):
    source_dir = tmp_path / "source"
    content_dir = tmp_path / "content"
    run_parent = tmp_path / "runs"
    source_dir.mkdir()
    (source_dir / "manifest.json").write_text("{}", encoding="utf-8")
    source = source_dir / "chapter_01.txt"
    source.write_text("原文", encoding="utf-8")

    monkeypatch.setattr(publisher, "verify_source_manifest", lambda _path: None)
    monkeypatch.setattr(
        publisher,
        "_source_index",
        lambda _directory, expected_chapters: {1: source},
    )

    lengths = {level: index for index, level in enumerate(publisher.LEVELS, 1)}

    def audited(_run, _source, level, _number, _annotations):
        return {
            "characters": lengths[level],
            "title": "第1章",
            "text": "猫" * lengths[level] + "。",
            "segments": [],
            "grammar_overlays": [],
            "annotation_audit": {"all_reviewed": True},
            "materiality_audit": {"reviewed": True},
            "level_diagnostics": {"passes": True},
            "source": {"file": "chapter_01.txt"},
        }

    monkeypatch.setattr(publisher, "_audit_chapter", audited)

    report = publisher.publish_smoke(run_parent, source_dir, content_dir)

    assert tuple(report["levels"]) == ("n5", "n4", "n3", "n2", "n1")
    assert report["length_order_observation"]["lengths_increase_n5_to_n1"] is True
    for level in publisher.LEVELS:
        assert (content_dir / f"{level}.md").is_file()
        assert (content_dir / f"{level}.annotations.json").is_file()
    metadata = json.loads((content_dir / "metadata.json").read_text())
    assert metadata["smoke_scope"] == "chapter_01_all_jlpt_levels"
    assert metadata["enabled_levels"] == list(publisher.LEVELS)


def test_chapter_one_publisher_can_expose_only_a_reviewed_level(
    tmp_path, monkeypatch,
):
    source_dir = tmp_path / "source"
    content_dir = tmp_path / "content"
    run_parent = tmp_path / "runs"
    source_dir.mkdir()
    (source_dir / "manifest.json").write_text("{}", encoding="utf-8")
    source = source_dir / "chapter_01.txt"
    source.write_text("原文", encoding="utf-8")
    monkeypatch.setattr(publisher, "verify_source_manifest", lambda _path: None)
    monkeypatch.setattr(
        publisher, "_source_index",
        lambda _directory, expected_chapters: {1: source},
    )
    monkeypatch.setattr(publisher, "_audit_chapter", lambda *_args: {
        "characters": 10,
        "title": "第1章",
        "text": "猫。",
        "segments": [],
        "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True},
        "materiality_audit": {"reviewed": True},
        "level_diagnostics": {"passes": True},
        "source": {"file": "chapter_01.txt"},
    })

    report = publisher.publish_smoke(
        run_parent, source_dir, content_dir, levels=("n5",),
    )

    assert tuple(report["levels"]) == ("n5",)
    assert (content_dir / "n5.annotations.json").is_file()
    assert not (content_dir / "n4.annotations.json").exists()
    metadata = json.loads((content_dir / "metadata.json").read_text())
    assert metadata["enabled_levels"] == ["n5"]
    assert metadata["smoke_scope"] == "chapter_01_selected_jlpt_levels"
