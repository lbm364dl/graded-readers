#!/usr/bin/env python3
"""Generate content_ja.json from JLPT graded reader markdown files."""

import json
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE / "output" / "japanese"
READERS_DIR = BASE / "output" / "japanese" / "readers"
ASSETS_DIR = BASE / "app" / "assets"

# N5 filename -> internal level 1 (easiest), N1 -> internal level 5 (hardest)
JLPT_TO_INTERNAL = {"n5": 1, "n4": 2, "n3": 3, "n2": 4, "n1": 5}


def parse_title_line(line: str):
    """Parse '# Japanese Title (English Title)' -> (ja_title, en_title)."""
    m = re.match(r"^#\s+(.+?)\s*\((.+?)\)\s*$", line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Fallback: no parenthetical
    m2 = re.match(r"^#\s+(.+)$", line)
    if m2:
        return m2.group(1).strip(), m2.group(1).strip()
    return line.strip("# ").strip(), line.strip("# ").strip()


def is_appendix_title(title: str) -> bool:
    """Return True if a chapter title looks like an appendix/reference section.

    Only matches titles that are primarily appendix content, not story chapters
    that happen to contain a keyword (e.g., "— 原文と解説" in chapter titles).
    """
    # Strip leading number prefix like "15. " for matching
    stripped = re.sub(r"^\d+\.\s*", "", title)

    # Exact-start patterns: the section title itself starts with these
    appendix_starts = [
        "ことば", "文学的考察", "重要な和歌", "古語総まとめ",
        "古語文法まとめ", "主要語注", "参考文献", "付記",
        "補論", "総論", "結語", "作品論", "跋",
    ]
    for kw in appendix_starts:
        if stripped.startswith(kw):
            return True

    # Patterns for non-numbered sections that are clearly appendices
    appendix_contains = [
        "文学的地位", "vocabulary", "classical grammar",
        "読み方——一つの提案",
    ]
    lower = title.lower()
    for kw in appendix_contains:
        if kw.lower() in lower:
            return True

    # Special: "解説 —" at the start (standalone analysis section, not "原文と解説")
    if stripped.startswith("解説"):
        return True

    return False


def parse_chapters(text: str):
    """Split markdown text into chapters based on ## headers.

    Returns list of {"title": ..., "content": ...} dicts.
    """
    lines = text.split("\n")

    # Find all ## headers (both numbered like "## 1. Title" and unnumbered like "## Title")
    header_pattern = re.compile(r"^##\s+(.+)$")

    all_headers = []
    for i, line in enumerate(lines):
        m = header_pattern.match(line)
        if m:
            all_headers.append((i, m.group(1).strip()))

    # Separate numbered chapters from non-numbered sections
    numbered_pattern = re.compile(r"^(\d+)\.\s+(.+)$")

    chapter_starts = []
    for line_no, title in all_headers:
        m = numbered_pattern.match(title)
        if m:
            # Numbered chapter - include unless it's an appendix
            full_title = title  # e.g., "1. 光る竹"
            if not is_appendix_title(full_title):
                chapter_starts.append((line_no, full_title))
        else:
            # Non-numbered ## header — only include if NOT an appendix
            # These are sections like "震旦（中国）の話" in konjaku
            if not is_appendix_title(title):
                chapter_starts.append((line_no, title))

    chapters = []
    for idx, (start_line, title) in enumerate(chapter_starts):
        # Content goes from start_line+1 to next header (any ##, not just chapters)
        # Find the next ## header after this one
        next_header_line = len(lines)
        for line_no, _ in all_headers:
            if line_no > start_line:
                next_header_line = line_no
                break

        content_lines = lines[start_line + 1 : next_header_line]
        content = extract_content(content_lines)
        if content.strip():
            chapters.append({"title": title, "content": content})

    return chapters


def extract_content(lines: list[str]) -> str:
    """Clean up content lines: strip --- separators, join paragraphs."""
    # Remove leading/trailing blank lines and ---
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            continue
        if stripped.startswith("**~ ") and stripped.endswith(" ~**"):
            continue
        result.append(line)

    # Join and clean up
    text = "\n".join(result).strip()

    # Collapse multiple blank lines into double newlines (paragraph breaks)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text


def load_metadata(book_dir: Path) -> dict | None:
    """Load metadata.json if it exists."""
    meta_path = book_dir / "metadata.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text("utf-8"))
    return None


def parse_book_file(filepath: Path, book_key: str, metadata: dict | None):
    """Parse a single graded reader markdown file."""
    text = filepath.read_text(encoding="utf-8")

    # Extract level from filename (n1_book.md -> n1)
    fname = filepath.stem  # e.g., "n5_taketori"
    level_key = fname.split("_")[0]  # "n5"
    internal_level = JLPT_TO_INTERNAL.get(level_key, 1)

    # Get titles from metadata, fallback to parsing first line
    if metadata:
        ja_title = metadata.get("title_zh", book_key)
        en_title = metadata.get("title_en", book_key)
    else:
        lines = text.split("\n")
        ja_title, en_title = parse_title_line(lines[0])

    # Parse chapters
    chapters = parse_chapters(text)

    return {
        "id": f"{book_key}_{level_key}",
        "book": book_key,
        "bookTitle": ja_title,
        "bookTitleEn": en_title,
        "level": internal_level,
        "chapters": chapters,
    }


def parse_reader_file(filepath: Path):
    """Parse a standalone reader markdown file into a single-chapter entry."""
    text = filepath.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Extract title
    ja_title, en_title = parse_title_line(lines[0])

    # Extract level from filename (n5_01_xxx.md -> n5)
    fname = filepath.stem  # e.g., "n5_01_watashi_no_ichinichi"
    level_key = fname.split("_")[0]  # "n5"
    internal_level = JLPT_TO_INTERNAL.get(level_key, 1)

    # Build reader ID from filename without level prefix
    # e.g., "n5_01_watashi_no_ichinichi" -> "readers_n5_01"
    parts = fname.split("_")
    reader_id = f"readers_{parts[0]}_{parts[1]}"

    # Content: everything after the title line, JLPT level, and first ---
    content_lines = []
    past_header = False
    for line in lines[1:]:
        stripped = line.strip()
        if not past_header:
            if stripped == "---":
                past_header = True
            continue
        if stripped == "---":
            continue
        content_lines.append(line)

    content = "\n".join(content_lines).strip()
    content = re.sub(r"\n{3,}", "\n\n", content)
    # Strip JLPT level line if still present
    content = re.sub(r"\*\*JLPT Level N\d\*\*\s*", "", content).strip()

    chapter = {"title": ja_title, "content": content}

    return {
        "id": reader_id,
        "book": "readers",
        "bookTitle": "短編読物",
        "bookTitleEn": "Short Readers",
        "level": internal_level,
        "chapters": [chapter],
    }


def main():
    entries = []

    # 1. Process graded reader books (15 books × 5 levels)
    for book_dir in sorted(OUTPUT_DIR.iterdir()):
        if not book_dir.is_dir():
            continue
        book_key = book_dir.name
        metadata = load_metadata(book_dir)
        for md_file in sorted(book_dir.glob("n[1-5]_*.md")):
            entry = parse_book_file(md_file, book_key, metadata)
            if entry["chapters"]:
                entries.append(entry)

    # 2. Process standalone readers
    for md_file in sorted(READERS_DIR.glob("n[1-5]_*.md")):
        entry = parse_reader_file(md_file)
        if entry["chapters"]:
            entries.append(entry)

    # Sort by level then by id
    entries.sort(key=lambda e: (e["level"], e["id"]))

    out_path = ASSETS_DIR / "content_ja.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"Wrote {out_path}")
    print(f"  Total entries: {len(entries)}")
    total_chapters = sum(len(e["chapters"]) for e in entries)
    print(f"  Total chapters: {total_chapters}")
    print(f"  File size: {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
