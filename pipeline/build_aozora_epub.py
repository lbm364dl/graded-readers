"""Build a reproducible EPUB and clean chapter corpus from Aozora Bunko text.

The converter deliberately supports the small, well-defined Aozora notation
used by the repository's source of Natsume Soseki's *Wagahai wa Neko de Aru*:
ruby, editorial/layout notes, and JIS X 0213 gaiji placeholders.  It refuses
ambiguous gaiji instead of silently dropping characters.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


TITLE = "吾輩は猫である"
AUTHOR = "夏目漱石"
BOOK_ID = "urn:sha256:{digest}"
CHAPTER_MARKER = re.compile(
    r"^［＃８字下げ］([一二三四五六七八九十]+)［＃「\1」は中見出し］$",
    re.MULTILINE,
)
AOZORA_NOTE = re.compile(r"［＃(.*?)］")
GAIJI_NOTE = re.compile(r"^.*?(?:第[34]水準)?([12])-(\d+)-(\d+)$")
RUBY = re.compile(
    r"(?:｜(?P<explicit>[^《\n]+)|(?P<implicit>[一-龯々〆ヵヶ]+))《(?P<reading>[^》\n]+)》"
)
FOOTER = re.compile(r"\n底本：")


class AozoraConversionError(ValueError):
    """Raised when the source cannot be converted without silent data loss."""


@dataclass(frozen=True)
class CleanChapter:
    number: int
    label: str
    body: str

    @property
    def text(self) -> str:
        return f"第{self.number}章　{self.label}\n\n{self.body.rstrip()}\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_gaiji(note: str) -> str:
    match = GAIJI_NOTE.fullmatch(note)
    if not match:
        raise AozoraConversionError(f"unresolved gaiji note: {note}")
    plane, row, cell = map(int, match.groups())
    escape = b"\x1b$(Q" if plane == 1 else b"\x1b$(P"
    try:
        return (escape + bytes((row + 32, cell + 32)) + b"\x1b(B").decode(
            "iso2022_jp_2004"
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise AozoraConversionError(f"invalid JIS X 0213 gaiji: {note}") from exc


def clean_aozora_notes(text: str) -> str:
    """Remove Aozora directives, resolving every ``※［＃...gaiji...］``."""
    def gaiji(match: re.Match[str]) -> str:
        return _decode_gaiji(match.group(1))

    text = re.sub(r"※［＃([^］]+)］", gaiji, text)
    # Remaining notes describe indentation, emphasis, source corrections, or
    # ruby corrections. Their target text is already present immediately next
    # to the note, so removing only the directive preserves the reading text.
    return AOZORA_NOTE.sub("", text)


def extract_clean_chapters(source: str) -> list[CleanChapter]:
    """Extract all numbered chapters and exclude Aozora header/footer metadata."""
    markers = list(CHAPTER_MARKER.finditer(source))
    if len(markers) != 11:
        raise AozoraConversionError(f"expected 11 chapter markers, found {len(markers)}")
    footer = FOOTER.search(source, markers[-1].end())
    end_of_book = footer.start() if footer else len(source)
    chapters: list[CleanChapter] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else end_of_book
        body = clean_aozora_notes(source[marker.end():end]).strip()
        if not body:
            raise AozoraConversionError(f"chapter {index + 1} is empty")
        chapters.append(CleanChapter(index + 1, marker.group(1), body))
    return chapters


def ruby_xhtml(text: str) -> str:
    """Escape prose and translate Aozora ruby into semantic XHTML."""
    pieces: list[str] = []
    cursor = 0
    for match in RUBY.finditer(text):
        pieces.append(html.escape(text[cursor:match.start()]))
        base = match.group("explicit") or match.group("implicit")
        pieces.append(
            f"<ruby>{html.escape(base)}<rp>（</rp><rt>{html.escape(match.group('reading'))}</rt>"
            "<rp>）</rp></ruby>"
        )
        cursor = match.end()
    pieces.append(html.escape(text[cursor:]))
    rendered = "".join(pieces)
    if "《" in rendered or "》" in rendered or "｜" in rendered:
        raise AozoraConversionError("unconverted ruby notation remains")
    return rendered


def _chapter_xhtml(chapter: CleanChapter) -> str:
    paragraphs = []
    for paragraph in re.split(r"\n+", chapter.body):
        paragraph = paragraph.strip()
        if paragraph:
            paragraphs.append(f"<p>{ruby_xhtml(paragraph)}</p>")
    return """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja" lang="ja">
<head><meta charset="utf-8"/><title>{title}</title><link rel="stylesheet" href="style.css"/></head>
<body><section epub:type="chapter" xmlns:epub="http://www.idpf.org/2007/ops">
<h1>第{number}章　{label}</h1>
{body}
</section></body></html>
""".format(title=TITLE, number=chapter.number, label=chapter.label, body="\n".join(paragraphs))


def _zip_write(archive: zipfile.ZipFile, name: str, data: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data.encode("utf-8"))


def build_epub(chapters: list[CleanChapter], destination: Path, source_hash: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_items = "\n".join(
        f'<item id="c{i}" href="chapter_{i:02d}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(1, len(chapters) + 1)
    )
    spine = "\n".join(f'<itemref idref="c{i}"/>' for i in range(1, len(chapters) + 1))
    nav = "\n".join(
        f'<li><a href="chapter_{c.number:02d}.xhtml">第{c.number}章　{c.label}</a></li>'
        for c in chapters
    )
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="ja">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{BOOK_ID.format(digest=source_hash)}</dc:identifier>
<dc:title>{TITLE}</dc:title><dc:creator>{AUTHOR}</dc:creator><dc:language>ja</dc:language>
<meta property="dcterms:modified">2018-02-05T00:00:00Z</meta>
</metadata><manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="css" href="style.css" media-type="text/css"/>
{manifest_items}
</manifest><spine>{spine}</spine></package>'''
    nav_doc = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ja">
<head><title>目次</title></head><body><nav epub:type="toc" id="toc"><h1>目次</h1><ol>{nav}</ol></nav></body></html>'''
    container = '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>
<rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
</rootfiles></container>'''
    css = 'html { writing-mode: vertical-rl; } body { line-height: 1.8; } ruby rt { font-size: 0.55em; }\n'
    with zipfile.ZipFile(destination, "w") as archive:
        mime = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        mime.compress_type = zipfile.ZIP_STORED
        archive.writestr(mime, b"application/epub+zip")
        _zip_write(archive, "META-INF/container.xml", container)
        _zip_write(archive, "EPUB/package.opf", opf)
        _zip_write(archive, "EPUB/nav.xhtml", nav_doc)
        _zip_write(archive, "EPUB/style.css", css)
        for chapter in chapters:
            _zip_write(archive, f"EPUB/chapter_{chapter.number:02d}.xhtml", _chapter_xhtml(chapter))


def build(source_path: Path, output_dir: Path, epub_path: Path) -> Path:
    source_bytes = source_path.read_bytes()
    source = source_bytes.decode("utf-8-sig").replace("\r\n", "\n")
    chapters = extract_clean_chapters(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for chapter in chapters:
        path = output_dir / f"chapter_{chapter.number:02d}.txt"
        data = chapter.text.encode("utf-8")
        path.write_bytes(data)
        entries.append({
            "number": chapter.number,
            "label": chapter.label,
            "file": path.name,
            "bytes": len(data),
            "sha256": sha256(data),
        })
    build_epub(chapters, epub_path, sha256(source_bytes))
    literary_body = "\n".join(chapter.body for chapter in chapters).encode("utf-8")
    manifest = {
        "title": TITLE,
        "author": AUTHOR,
        "source": str(source_path),
        "source_bytes": len(source_bytes),
        "source_sha256": sha256(source_bytes),
        "chapter_count": len(chapters),
        "normalized_literary_body_bytes": len(literary_body),
        "normalized_literary_body_sha256": sha256(literary_body),
        "epub": epub_path.name,
        "epub_bytes": epub_path.stat().st_size,
        "epub_sha256": sha256(epub_path.read_bytes()),
        "chapters": entries,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def verify_manifest(manifest_path: Path) -> None:
    """Verify source, clean chapters, reconstructed body, EPUB hash and ZIP shape."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_path = Path(manifest["source"])
    source_data = source_path.read_bytes()
    if (len(source_data) != manifest["source_bytes"]
            or sha256(source_data) != manifest["source_sha256"]):
        raise AozoraConversionError("source checksum mismatch")
    source_text = source_data.decode("utf-8-sig").replace("\r\n", "\n")
    extracted = extract_clean_chapters(source_text)
    entries = manifest.get("chapters", [])
    if manifest.get("chapter_count") != 11 or len(entries) != 11:
        raise AozoraConversionError("manifest must contain all 11 chapters")
    if [entry.get("number") for entry in entries] != list(range(1, 12)):
        raise AozoraConversionError("manifest chapters are not in canonical order")
    expected_files = [f"chapter_{number:02d}.txt" for number in range(1, 12)]
    if [entry.get("file") for entry in entries] != expected_files:
        raise AozoraConversionError("manifest chapter filenames are not canonical")
    actual_files = sorted(path.name for path in manifest_path.parent.glob("chapter_*.txt"))
    if actual_files != expected_files:
        raise AozoraConversionError("chapter directory has missing or unexpected files")
    bodies: list[str] = []
    for chapter, entry in zip(extracted, entries, strict=True):
        path = manifest_path.parent / entry["file"]
        data = path.read_bytes()
        if len(data) != entry["bytes"] or sha256(data) != entry["sha256"]:
            raise AozoraConversionError(f"chapter checksum mismatch: {path}")
        text = data.decode("utf-8")
        _, separator, body = text.partition("\n\n")
        if not separator:
            raise AozoraConversionError(f"chapter has no heading separator: {path}")
        if text != chapter.text or entry.get("label") != chapter.label:
            raise AozoraConversionError(f"chapter differs from Aozora source: {path}")
        bodies.append(body.rstrip())
    reconstructed = "\n".join(bodies).encode("utf-8")
    if sha256(reconstructed) != manifest["normalized_literary_body_sha256"]:
        raise AozoraConversionError("clean chapters do not reconstruct the literary body")
    epub_path = manifest_path.parent.parent / manifest["epub"]
    epub_data = epub_path.read_bytes()
    if len(epub_data) != manifest["epub_bytes"] or sha256(epub_data) != manifest["epub_sha256"]:
        raise AozoraConversionError("EPUB checksum mismatch")
    with zipfile.ZipFile(epub_path) as archive:
        if archive.testzip() is not None or archive.namelist()[0] != "mimetype":
            raise AozoraConversionError("invalid EPUB ZIP structure")
        if archive.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
            raise AozoraConversionError("EPUB mimetype must be stored uncompressed")
        if archive.read("mimetype") != b"application/epub+zip":
            raise AozoraConversionError("invalid EPUB mimetype")
        expected_xhtml = [f"EPUB/chapter_{number:02d}.xhtml" for number in range(1, 12)]
        actual_xhtml = sorted(
            name for name in archive.namelist() if name.startswith("EPUB/chapter_")
        )
        if actual_xhtml != expected_xhtml:
            raise AozoraConversionError("EPUB has missing or unexpected chapter documents")
        for chapter, name in zip(extracted, expected_xhtml, strict=True):
            if archive.read(name) != _chapter_xhtml(chapter).encode("utf-8"):
                raise AozoraConversionError(f"EPUB chapter differs from source: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--chapters-dir", type=Path, required=True)
    parser.add_argument("--epub", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.source, args.chapters_dir, args.epub)
    verify_manifest(manifest)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
