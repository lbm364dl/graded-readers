import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from pipeline.build_aozora_epub import (
    AozoraConversionError,
    build,
    clean_aozora_notes,
    ruby_xhtml,
    verify_manifest,
)


def test_ruby_conversion_handles_implicit_and_explicit_bases():
    rendered = ruby_xhtml("吾輩《わがはい》と一番｜獰悪《どうあく》")
    assert "<ruby>吾輩" in rendered
    assert "<ruby>獰悪" in rendered
    assert "｜" not in rendered and "《" not in rendered


def test_notes_resolve_gaiji_and_remove_layout_directives():
    assert clean_aozora_notes("大気※［＃「陷のつくり＋炎」、第3水準1-87-64］［＃２字下げ］") == "大気燄"
    with pytest.raises(AozoraConversionError, match="unresolved gaiji"):
        clean_aozora_notes("※［＃unknown glyph］")


def test_builds_complete_valid_epub_from_actual_source(tmp_path):
    source = Path("books/japanese/wagahai_wa_neko_de_aru.txt")
    chapters_dir = tmp_path / "chapters"
    epub = tmp_path / "book.epub"
    manifest_path = build(source, chapters_dir, epub)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verify_manifest(manifest_path)
    assert manifest["chapter_count"] == 11
    assert len(manifest["chapters"]) == 11
    assert manifest["normalized_literary_body_bytes"] > 900_000
    assert all((chapters_dir / item["file"]).is_file() for item in manifest["chapters"])

    with zipfile.ZipFile(epub) as archive:
        assert archive.namelist()[0] == "mimetype"
        info = archive.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert len([n for n in archive.namelist() if n.startswith("EPUB/chapter_")]) == 11
        for name in ("META-INF/container.xml", "EPUB/package.opf", "EPUB/nav.xhtml"):
            ET.fromstring(archive.read(name))
        for i in range(1, 12):
            ET.fromstring(archive.read(f"EPUB/chapter_{i:02d}.xhtml"))
        joined = b"".join(archive.read(f"EPUB/chapter_{i:02d}.xhtml") for i in range(1, 12))
        assert b"<ruby>" in joined
        assert "［＃" not in joined.decode("utf-8")


def test_committed_wagahai_corpus_exactly_matches_source_and_epub():
    verify_manifest(Path("books/japanese/wagahai_wa_neko_de_aru/manifest.json"))
