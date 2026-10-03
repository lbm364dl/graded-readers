from pipeline.prepare_retelling_sources import balanced_ranges, build_bundles


def test_balanced_ranges_cover_sources_once():
    ranges = balanced_ranges(120, 24)
    assert ranges[0] == (1, 5)
    assert ranges[-1] == (116, 120)
    assert [n for start, end in ranges for n in range(start, end + 1)] == list(range(1, 121))


def test_builds_level_specific_story_bundles(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for number in range(1, 7):
        (source / f"chapter_{number:03d}.txt").write_text(f"原文{number}", encoding="utf-8")
    manifest = build_bundles(source, tmp_path / "out", "hsk1", bundle_count=2)
    assert manifest["story_chapters"] == 2
    assert manifest["bundles"][0]["source_files"] == [
        "chapter_001.txt", "chapter_002.txt", "chapter_003.txt"
    ]
    assert "ORIGINAL CHAPTER 3" in (tmp_path / "out/hsk1/chapter_001.txt").read_text()
