"""Extract ordered chapter text files from a Project Gutenberg-style EPUB.

The EPUB navigation document is not necessarily a chapter table of contents
(Gutenberg 23950's nav contains only its licence).  Reading order is therefore
derived from the package spine, and chapter boundaries from headings in that
ordered content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


CONTAINER = "META-INF/container.xml"
CHAPTER_HEADING = re.compile(
    r"^第([〇○零一二三四五六七八九十百兩两]+)回\s*[：:]\s*(.+)$"
)
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6"}
_DIGITS = {"〇": 0, "○": 0, "零": 0, "一": 1, "二": 2, "兩": 2,
           "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9}


class ExtractionError(ValueError):
    """Raised when an EPUB cannot prove a complete, ordered chapter set."""


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    paragraphs: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n\n".join((self.title, *self.paragraphs)).rstrip() + "\n"


def chinese_chapter_number(value: str) -> int:
    """Parse both normal numerals (九十九) and Gutenberg's digit style (一○○)."""
    if "百" in value or "十" in value:
        total = 0
        current = 0
        for char in value:
            if char in _DIGITS:
                current = _DIGITS[char]
            elif char == "十":
                total += (current or 1) * 10
                current = 0
            elif char == "百":
                total += (current or 1) * 100
                current = 0
            else:  # guarded by CHAPTER_HEADING, retained for direct callers
                raise ExtractionError(f"unsupported Chinese numeral: {value}")
        return total + current
    try:
        # Gutenberg writes 100–120 as 一○○, 一○一, ... 一二○.
        return int("".join(str(_DIGITS[char]) for char in value))
    except (KeyError, ValueError) as exc:
        raise ExtractionError(f"unsupported Chinese numeral: {value}") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _block_parts(element: ET.Element) -> list[str]:
    """Return logical blocks, recovering headings embedded in malformed ``p`` tags.

    Gutenberg 23950 has chapter 90's heading after the preceding paragraph's
    text but before that paragraph's closing tag.  Source newlines let us
    recover that boundary without treating mentions of ``第…回`` in prose as
    headings.  Other source line wrapping is removed (Chinese EPUB text should
    not gain artificial spaces at those wraps).
    """
    lines = [line.strip() for line in "".join(element.itertext()).splitlines()]
    parts: list[str] = []
    buffered: list[str] = []
    for line in lines:
        if CHAPTER_HEADING.fullmatch(line):
            before = "".join(buffered).strip()
            if before:
                parts.append(before)
            parts.append(line)
            buffered = []
        elif line:
            buffered.append(line)
    after = "".join(buffered).strip()
    if after:
        parts.append(after)
    return parts


def _package_path(epub: zipfile.ZipFile) -> PurePosixPath:
    try:
        root = ET.fromstring(epub.read(CONTAINER))
        full_path = next(
            node.attrib["full-path"] for node in root.iter()
            if _local_name(node.tag) == "rootfile"
        )
    except (KeyError, StopIteration, ET.ParseError) as exc:
        raise ExtractionError("EPUB container does not identify a package document") from exc
    path = PurePosixPath(full_path)
    if path.is_absolute() or ".." in path.parts:
        raise ExtractionError(f"unsafe package path: {full_path}")
    return path


def _spine_documents(epub: zipfile.ZipFile) -> list[PurePosixPath]:
    package_path = _package_path(epub)
    try:
        package = ET.fromstring(epub.read(str(package_path)))
    except (KeyError, ET.ParseError) as exc:
        raise ExtractionError(f"cannot read package document: {package_path}") from exc

    manifest = {
        node.attrib["id"]: node.attrib["href"]
        for node in package.iter()
        if _local_name(node.tag) == "item" and "id" in node.attrib
        and "href" in node.attrib
    }
    paths: list[PurePosixPath] = []
    for node in package.iter():
        if _local_name(node.tag) != "itemref":
            continue
        idref = node.attrib.get("idref", "")
        if idref not in manifest:
            raise ExtractionError(f"spine references missing manifest item: {idref}")
        href = manifest[idref].split("#", 1)[0]
        path = package_path.parent / href
        if path.is_absolute() or ".." in path.parts:
            raise ExtractionError(f"unsafe spine path: {href}")
        paths.append(path)
    if not paths:
        raise ExtractionError("EPUB package has no spine documents")
    return paths


def extract_chapters(epub_path: Path | str) -> list[Chapter]:
    """Return chapters in proven spine order, rejecting gaps and duplicates."""
    chapters: list[Chapter] = []
    title: str | None = None
    number: int | None = None
    paragraphs: list[str] = []
    reached_end = False

    with zipfile.ZipFile(epub_path) as epub:
        for document_path in _spine_documents(epub):
            if reached_end:
                break
            try:
                document = ET.fromstring(epub.read(str(document_path)))
            except KeyError as exc:
                raise ExtractionError(f"spine document is missing: {document_path}") from exc
            except ET.ParseError as exc:
                raise ExtractionError(f"invalid XHTML in {document_path}") from exc
            for block in document.iter():
                if _local_name(block.tag) not in BLOCK_TAGS:
                    continue
                for text in _block_parts(block):
                    match = CHAPTER_HEADING.fullmatch(text)
                    if match:
                        if title is not None and number is not None:
                            chapters.append(Chapter(number, title, tuple(paragraphs)))
                        number = chinese_chapter_number(match.group(1))
                        title = text
                        paragraphs = []
                    elif title is not None and text:
                        # Gutenberg's end marker follows the final chapter and is not
                        # part of the novel.
                        if text.startswith("End of Project Gutenberg"):
                            reached_end = True
                            break
                        paragraphs.append(text)
                if reached_end:
                    break

    if title is not None and number is not None:
        chapters.append(Chapter(number, title, tuple(paragraphs)))
    if not chapters:
        raise ExtractionError("no chapter headings found in EPUB spine")
    actual = [chapter.number for chapter in chapters]
    expected = list(range(1, len(chapters) + 1))
    if actual != expected:
        raise ExtractionError(
            f"chapter numbering is not contiguous: expected {expected}, got {actual}"
        )
    if any(not chapter.paragraphs for chapter in chapters):
        empty = [chapter.number for chapter in chapters if not chapter.paragraphs]
        raise ExtractionError(f"chapters without body text: {empty}")
    return chapters


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_chapters(
    chapters: list[Chapter],
    output_dir: Path | str,
    *,
    source_epub: Path | str | None = None,
) -> Path:
    """Write stable numbered files and a checksum-bearing manifest."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    width = max(3, len(str(len(chapters))))
    entries = []
    for chapter in chapters:
        filename = f"chapter_{chapter.number:0{width}d}.txt"
        text = chapter.text
        (destination / filename).write_text(text, encoding="utf-8")
        entries.append({
            "number": chapter.number,
            "title": chapter.title,
            "file": filename,
            "characters": len(text),
            "sha256": _sha256(text.encode("utf-8")),
        })
    manifest: dict[str, object] = {
        "chapter_count": len(chapters),
        "chapters": entries,
    }
    if source_epub is not None:
        source_path = Path(source_epub)
        source_data = source_path.read_bytes()
        manifest = {
            "source_epub": source_path.name,
            "source_epub_bytes": len(source_data),
            "source_epub_sha256": _sha256(source_data),
            **manifest,
        }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def verify_manifest(manifest_path: Path | str, source_epub: Path | str | None = None) -> None:
    """Prove that a manifest is an exact, ordered extraction of its source EPUB."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = manifest.get("chapters")
    if not isinstance(entries, list) or manifest.get("chapter_count") != len(entries):
        raise ExtractionError("manifest chapter count mismatch")
    expected_numbers = list(range(1, len(entries) + 1))
    if [entry.get("number") for entry in entries] != expected_numbers:
        raise ExtractionError("manifest chapters are not in contiguous canonical order")
    expected_files = [f"chapter_{number:03d}.txt" for number in expected_numbers]
    if [entry.get("file") for entry in entries] != expected_files:
        raise ExtractionError("manifest chapter filenames are not canonical")
    actual_files = sorted(item.name for item in path.parent.glob("chapter_*.txt"))
    if actual_files != expected_files:
        raise ExtractionError("chapter directory has missing or unexpected chapter files")

    if source_epub is None:
        source_name = manifest.get("source_epub")
        if not isinstance(source_name, str):
            raise ExtractionError("manifest does not bind a source EPUB")
        source_epub = path.parent.parent / source_name
    epub_path = Path(source_epub)
    epub_data = epub_path.read_bytes()
    if (manifest.get("source_epub_bytes") != len(epub_data)
            or manifest.get("source_epub_sha256") != _sha256(epub_data)):
        raise ExtractionError("source EPUB checksum mismatch")

    extracted = extract_chapters(epub_path)
    if len(extracted) != len(entries):
        raise ExtractionError("source EPUB chapter count differs from manifest")
    for chapter, entry in zip(extracted, entries, strict=True):
        chapter_path = path.parent / entry["file"]
        data = chapter_path.read_bytes()
        if (entry.get("title") != chapter.title
                or entry.get("characters") != len(chapter.text)
                or entry.get("sha256") != _sha256(data)
                or data != chapter.text.encode("utf-8")):
            raise ExtractionError(f"chapter differs from source EPUB: {chapter.number}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    chapters = extract_chapters(args.epub)
    manifest = write_chapters(chapters, args.output_dir, source_epub=args.epub)
    verify_manifest(manifest, args.epub)
    print(f"Extracted {len(chapters)} chapters; manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
