"""Small deterministic EPUB 3 builder and structural validator.

The generated books are deliberately self-contained: every chapter contains
the reading text followed by accessible lexical and grammar annotation lists.
The JSON sidecars remain the canonical machine-readable representation.
"""

from __future__ import annotations

import html
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Callable
import xml.etree.ElementTree as ET
import zipfile


MIMETYPE = b"application/epub+zip"
CONTAINER = "META-INF/container.xml"
OPF_NS = "http://www.idpf.org/2007/opf"
XHTML_NS = "http://www.w3.org/1999/xhtml"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"


class EpubError(ValueError):
    """An EPUB could not be built or did not pass structural validation."""


def _paragraphs(text: str) -> str:
    parts = [part for part in text.strip().split("\n") if part]
    return "".join(f"<p>{html.escape(part)}</p>" for part in parts)


def _annotations(chapter: dict[str, Any]) -> str:
    lexical = []
    for segment in chapter.get("segments", []):
        kind = str(segment.get("type", ""))
        if kind == "punctuation":
            continue
        surface = str(segment.get("text", segment.get("surface", "")))
        reading = str(segment.get("pinyin", segment.get("kana", "")))
        meaning = str(segment.get("meaning_en", ""))
        if not surface or not meaning:
            continue
        qualifier = f" <span class=\"reading\">{html.escape(reading)}</span>" if reading else ""
        lexical.append(
            f'<dt><span>{html.escape(surface)}</span>{qualifier}</dt>'
            f'<dd>{html.escape(meaning)}</dd>'
        )
    grammar = []
    for overlay in chapter.get("grammar_overlays", []):
        surface = str(overlay.get("text", overlay.get("surface", "")))
        pattern = str(overlay.get("pattern", overlay.get("grammar", "")))
        meaning = str(overlay.get("meaning_en", ""))
        if surface and pattern and meaning:
            grammar.append(
                f'<dt>{html.escape(surface)} — {html.escape(pattern)}</dt>'
                f'<dd>{html.escape(meaning)}</dd>'
            )
    if not lexical and not grammar:
        return ""
    sections = []
    if lexical:
        sections.append(
            '<section class="annotations" aria-labelledby="lexical-heading">'
            '<h2 id="lexical-heading">Vocabulary annotations</h2><dl>'
            + "".join(lexical) + "</dl></section>"
        )
    if grammar:
        sections.append(
            '<section class="annotations" aria-labelledby="grammar-heading">'
            '<h2 id="grammar-heading">Grammar annotations</h2><dl>'
            + "".join(grammar) + "</dl></section>"
        )
    return "".join(sections)


def _zip_write(archive: zipfile.ZipFile, name: str, value: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, value.encode("utf-8"))


def build_epub3(
    destination: Path,
    *,
    identifier: str,
    title: str,
    language: str,
    chapters: list[dict[str, Any]],
    chapter_label: Callable[[dict[str, Any]], str],
    author: str | None = None,
) -> None:
    """Atomically build and validate a complete EPUB 3 book."""
    if not chapters:
        raise EpubError("EPUB requires at least one chapter")
    destination.parent.mkdir(parents=True, exist_ok=True)
    docs: list[tuple[str, str]] = []
    for index, chapter in enumerate(chapters, 1):
        filename = f"chapter_{index:03d}.xhtml"
        label = chapter_label(chapter)
        source_sha = html.escape(str(chapter.get("source", {}).get("sha256", "")))
        document = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{html.escape(language)}" '
            f'xml:lang="{html.escape(language)}"><head><meta charset="utf-8"/>'
            f'<title>{html.escape(label)}</title><link rel="stylesheet" type="text/css" '
            'href="style.css"/></head><body><section epub:type="chapter" '
            f'data-source-sha256="{source_sha}"><h1>{html.escape(label)}</h1>'
            f'{_paragraphs(str(chapter["text"]))}{_annotations(chapter)}'
            '</section></body></html>'
        )
        docs.append((filename, document))
    manifest = "".join(
        f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, (name, _) in enumerate(docs, 1)
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(1, len(docs) + 1))
    navigation = "".join(
        f'<li><a href="{name}">{html.escape(chapter_label(chapter))}</a></li>'
        for (name, _), chapter in zip(docs, chapters)
    )
    creator = f'<dc:creator>{html.escape(author)}</dc:creator>' if author else ""
    package = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="uid">{html.escape(identifier)}</dc:identifier>'
        f'<dc:title>{html.escape(title)}</dc:title><dc:language>{html.escape(language)}</dc:language>'
        f'{creator}<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>'
        '</metadata><manifest><item id="nav" href="nav.xhtml" '
        'media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="css" href="style.css" media-type="text/css"/>'
        f'{manifest}</manifest><spine>{spine}</spine></package>'
    )
    nav_doc = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{html.escape(language)}" '
        f'xml:lang="{html.escape(language)}"><head><meta charset="utf-8"/>'
        f'<title>{html.escape(title)}</title></head><body><nav epub:type="toc" id="toc">'
        f'<h1>{html.escape(title)}</h1><ol>{navigation}</ol></nav></body></html>'
    )
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="EPUB/package.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    css = (
        'body{font-family:serif;line-height:1.65;margin:5%;}'
        '.annotations{border-top:1px solid #999;margin-top:2em;}'
        'dt{font-weight:bold;margin-top:.6em}.reading{font-weight:normal;color:#555}'
    )
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            mime = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
            mime.compress_type = zipfile.ZIP_STORED
            mime.external_attr = 0o644 << 16
            archive.writestr(mime, MIMETYPE)
            _zip_write(archive, CONTAINER, container)
            _zip_write(archive, "EPUB/package.opf", package)
            _zip_write(archive, "EPUB/nav.xhtml", nav_doc)
            _zip_write(archive, "EPUB/style.css", css)
            for filename, document in docs:
                _zip_write(archive, f"EPUB/{filename}", document)
        validate_epub3(temporary, expected_chapters=len(chapters), require_annotations=True)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_epub3(path: Path, *, expected_chapters: int, require_annotations: bool) -> None:
    """Fail closed on the EPUB container, package graph, order, and XHTML."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or names[0] != "mimetype":
                raise EpubError("mimetype must be the first ZIP member")
            info = archive.getinfo("mimetype")
            if info.compress_type != zipfile.ZIP_STORED or archive.read("mimetype") != MIMETYPE:
                raise EpubError("mimetype must be exact and uncompressed")
            if archive.testzip() is not None:
                raise EpubError("EPUB ZIP contains a corrupt member")
            container = ET.fromstring(archive.read(CONTAINER))
            rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise EpubError("container.xml does not identify a package")
            opf_path = PurePosixPath(rootfile.get("full-path", ""))
            package = ET.fromstring(archive.read(str(opf_path)))
            manifest = {
                item.get("id"): item for item in package.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item")
            }
            nav_items = [item for item in manifest.values() if "nav" in item.get("properties", "").split()]
            if len(nav_items) != 1:
                raise EpubError("OPF must declare exactly one navigation document")
            spine_ids = [item.get("idref") for item in package.findall(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref")]
            if len(spine_ids) != expected_chapters or any(ref not in manifest for ref in spine_ids):
                raise EpubError("OPF spine does not contain the expected ordered chapters")
            base = opf_path.parent
            chapter_paths = [base / str(manifest[ref].get("href")) for ref in spine_ids]
            if len(set(chapter_paths)) != expected_chapters:
                raise EpubError("OPF spine contains duplicate chapters")
            for chapter_path in chapter_paths:
                root = ET.fromstring(archive.read(str(chapter_path)))
                if root.tag != f"{{{XHTML_NS}}}html":
                    raise EpubError(f"chapter is not XHTML: {chapter_path}")
                if require_annotations and not root.findall(f".//{{{XHTML_NS}}}section[@class='annotations']"):
                    raise EpubError(f"chapter annotations missing: {chapter_path}")
            nav_path = base / str(nav_items[0].get("href"))
            nav = ET.fromstring(archive.read(str(nav_path)))
            links = [node.get("href") for node in nav.findall(f".//{{{XHTML_NS}}}a")]
            if links != [str(path.relative_to(base)) for path in chapter_paths]:
                raise EpubError("navigation order does not exactly match the spine")
    except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise EpubError(f"invalid EPUB: {exc}") from exc
