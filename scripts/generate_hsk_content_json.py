#!/usr/bin/env python3
"""Generate content.json from HSK graded reader markdown files."""

import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE / "output" / "chinese"
ASSETS_DIR = BASE / "app" / "assets"

HSK_TO_INTERNAL = {"hsk1": 1, "hsk2": 2, "hsk3": 3, "hsk4": 4, "hsk5": 5, "hsk6": 6}


def parse_chapters(text: str):
    """Split markdown text into chapters based on ## headers.

    Content between ## headers becomes the chapter body.
    ### headers and all other markdown are kept as-is within the chapter content.
    """
    lines = text.split("\n")
    header_pattern = re.compile(r"^##\s+(.+)$")

    all_headers = []
    for i, line in enumerate(lines):
        m = header_pattern.match(line)
        if m:
            all_headers.append((i, m.group(1).strip()))

    if not all_headers:
        # No ## headers — treat entire text as a single chapter
        content = "\n".join(lines).strip()
        content = re.sub(r"\n{3,}", "\n\n", content)
        if content:
            return [{"title": "", "content": content}]
        return []

    chapters = []
    for idx, (start_line, title) in enumerate(all_headers):
        # Content goes from after this header to the next ## header
        next_header_line = len(lines)
        for line_no, _ in all_headers:
            if line_no > start_line:
                next_header_line = line_no
                break

        content_lines = lines[start_line + 1 : next_header_line]
        content = "\n".join(content_lines).strip()
        content = re.sub(r"\n{3,}", "\n\n", content)

        if content:
            chapters.append({"title": title, "content": content})

    return chapters


def load_metadata(book_dir: Path) -> dict | None:
    """Load metadata.json if it exists."""
    meta_path = book_dir / "metadata.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text("utf-8"))
    return None


def parse_book_file(filepath: Path, book_key: str, metadata: dict | None):
    """Parse a single graded reader markdown file."""
    text = filepath.read_text(encoding="utf-8")

    # Extract level from filename (hsk4_book.md -> hsk4)
    fname = filepath.stem
    level_key = fname.split("_")[0]
    internal_level = HSK_TO_INTERNAL.get(level_key, 1)

    # Get titles from metadata or filename
    if metadata:
        book_title = metadata.get("title_zh", book_key)
        book_title_en = metadata.get("title_en", book_key)
    else:
        book_title = book_key
        book_title_en = book_key

    chapters = parse_chapters(text)

    return {
        "id": f"{book_key}_{level_key}",
        "book": book_key,
        "bookTitle": book_title,
        "bookTitleEn": book_title_en,
        "level": internal_level,
        "chapters": chapters,
    }



def main():
    entries = []

    # 1. Process graded reader books from output/
    for book_dir in sorted(OUTPUT_DIR.iterdir()):
        if not book_dir.is_dir():
            continue
        if book_dir.name.startswith("_"):
            continue  # Skip test directories

        book_key = book_dir.name
        metadata = load_metadata(book_dir)

        for md_file in sorted(book_dir.glob("hsk[1-6]_*.md")):
            entry = parse_book_file(md_file, book_key, metadata)
            if entry["chapters"]:
                entries.append(entry)

    # Sort by level then by id
    entries.sort(key=lambda e: (e["level"], e["id"]))

    out_path = ASSETS_DIR / "content.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"Wrote {out_path}")
    print(f"  Total entries: {len(entries)}")
    total_chapters = sum(len(e["chapters"]) for e in entries)
    print(f"  Total chapters: {total_chapters}")
    print(f"  File size: {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
