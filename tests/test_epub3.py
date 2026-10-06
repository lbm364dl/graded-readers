from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from pipeline.epub3 import EpubError, build_epub3, validate_epub3


def _chapters(language: str) -> list[dict]:
    if language == "zh-CN":
        return [{
            "number": 1, "title": "桃园结义", "text": "刘备来了。\n",
            "source": {"sha256": "a" * 64},
            "segments": [
                {"text": "刘备", "type": "name", "pinyin": "Liú Bèi",
                 "meaning_en": "Liu Bei"},
                {"text": "来了", "type": "word", "pinyin": "lái le",
                 "meaning_en": "arrived"},
                {"text": "。\n", "type": "punctuation", "pinyin": "",
                 "meaning_en": ""},
            ],
            "grammar_overlays": [{"text": "来了", "pattern": "V了",
                                   "meaning_en": "completed action"}],
        }]
    return [{
        "number": 1, "title": "猫の話", "text": "猫です。\n",
        "source": {"sha256": "b" * 64},
        "segments": [
            {"surface": "猫", "type": "word", "kana": "ねこ", "lemma": "猫",
             "meaning_en": "cat"},
            {"surface": "です", "type": "auxiliary", "kana": "です", "lemma": "です",
             "meaning_en": "is"},
            {"surface": "。\n", "type": "punctuation", "kana": "", "lemma": "",
             "meaning_en": ""},
        ],
        "grammar_overlays": [{"surface": "です", "grammar": "Nです",
                               "meaning_en": "copula"}],
    }]


@pytest.mark.parametrize("language", ["zh-CN", "ja"])
def test_builds_self_contained_valid_epub_with_accessible_annotations(tmp_path, language):
    path = tmp_path / f"reader-{language}.epub"
    chapters = _chapters(language)
    build_epub3(
        path, identifier=f"fixture-{language}", title="Fixture", language=language,
        author="Author" if language == "ja" else None, chapters=chapters,
        chapter_label=lambda chapter: chapter["title"],
    )
    validate_epub3(path, expected_chapters=1, require_annotations=True)
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.testzip() is None
        for member in ("META-INF/container.xml", "EPUB/package.opf",
                       "EPUB/nav.xhtml", "EPUB/chapter_001.xhtml"):
            ET.fromstring(archive.read(member))
        opf = archive.read("EPUB/package.opf").decode("utf-8")
        chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")
        assert 'properties="nav"' in opf
        assert 'data-source-sha256=' in chapter
        assert "Vocabulary annotations" in chapter
        assert "Grammar annotations" in chapter
        assert ("Liu Bei" if language == "zh-CN" else "cat") in chapter


def test_validator_rejects_compressed_or_late_mimetype(tmp_path):
    path = tmp_path / "broken.epub"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("mimetype", "application/epub+zip")
    with pytest.raises(EpubError, match="mimetype must be the first"):
        validate_epub3(path, expected_chapters=1, require_annotations=True)
