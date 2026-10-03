import json
import zipfile
from pathlib import Path

import pytest

from pipeline.extract_epub_chapters import (
    ExtractionError,
    chinese_chapter_number,
    extract_chapters,
    write_chapters,
    verify_manifest,
)


def _epub(path: Path, documents: list[str]) -> Path:
    container = """<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>"""
    items = "".join(
        f'<item id="d{i}" href="part{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(documents))
    )
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(documents)))
    package = f'<package xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><manifest>{items}</manifest><spine>{spine}</spine></package>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/book.opf", package)
        # Deliberately reverse ZIP insertion order: only the spine is authoritative.
        for i in reversed(range(len(documents))):
            archive.writestr(f"OPS/part{i}.xhtml", documents[i])
    return path


def test_chinese_chapter_number_supports_both_styles():
    assert chinese_chapter_number("九十九") == 99
    assert chinese_chapter_number("一○○") == 100
    assert chinese_chapter_number("一二○") == 120


def test_extracts_spine_order_and_excludes_boilerplate(tmp_path):
    epub = _epub(tmp_path / "book.epub", [
        "<html><body><p>preface</p><p>第一回：甲</p><p> one\n line </p></body></html>",
        "<html><body><p>continued</p><p>第二回：乙</p><p>body two</p><p>End of Project Gutenberg book</p></body></html>",
    ])
    chapters = extract_chapters(epub)
    assert [chapter.number for chapter in chapters] == [1, 2]
    assert chapters[0].paragraphs == ("oneline", "continued")
    assert chapters[1].paragraphs == ("body two",)

    manifest_path = write_chapters(chapters, tmp_path / "out")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["chapter_count"] == 2
    assert [entry["title"] for entry in manifest["chapters"]] == ["第一回：甲", "第二回：乙"]
    assert (tmp_path / "out/chapter_001.txt").read_text(encoding="utf-8") == "第一回：甲\n\noneline\n\ncontinued\n"


def test_recovers_heading_embedded_in_previous_paragraph(tmp_path):
    epub = _epub(tmp_path / "book.epub", [
        "<html><body><p>第一回：甲</p><p>end of one\n第二回：乙</p><p>body two</p></body></html>"
    ])
    chapters = extract_chapters(epub)
    assert [chapter.title for chapter in chapters] == ["第一回：甲", "第二回：乙"]
    assert chapters[0].paragraphs == ("end of one",)


def test_rejects_numbering_gap(tmp_path):
    epub = _epub(tmp_path / "book.epub", [
        "<html><body><p>第一回：甲</p><p>a</p><p>第三回：丙</p><p>c</p></body></html>"
    ])
    with pytest.raises(ExtractionError, match="not contiguous"):
        extract_chapters(epub)


def test_actual_sanguoyanyi_is_exact_complete_source_extraction():
    verify_manifest(Path("books/chinese/sanguoyanyi/manifest.json"))
