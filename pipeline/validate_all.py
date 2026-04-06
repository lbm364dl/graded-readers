#!/usr/bin/env python3
"""Validate all existing readers against their level's character constraints.

Produces a summary table and detailed reports.
"""

import argparse
import re
from pathlib import Path

from pipeline.validate_text import load_charset, validate_characters, strip_markdown_headers


def detect_level_from_filename(filename: str) -> tuple[str, str] | None:
    """Extract level key and language from a reader filename.

    Returns (level_key, language) or None.
    """
    m = re.match(r"(hsk\d+)", filename)
    if m:
        return m.group(1), "chinese"
    m = re.match(r"(n\d+)", filename)
    if m:
        return m.group(1), "japanese"
    return None


def load_glossary_chars(glossary_path: Path) -> set[str]:
    """Load glossary characters from a glossary.txt file."""
    chars: set[str] = set()
    for line in glossary_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for ch in line:
            cp = ord(ch)
            if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
                    or 0x20000 <= cp <= 0x2A6DF or 0xF900 <= cp <= 0xFAFF):
                chars.add(ch)
    return chars


def validate_all(include_output: bool = False):
    project_root = Path(__file__).resolve().parent.parent
    pipeline_dir = Path(__file__).resolve().parent

    readers = []
    # HSK readers
    hsk_readers = sorted((project_root / "readers").glob("hsk*.md"))
    # JLPT readers
    jlpt_readers = sorted((project_root / "jlpt" / "readers").glob("n*.md"))
    readers = [(p, None) for p in hsk_readers + jlpt_readers]

    # Output directory readers (with glossary support)
    if include_output:
        output_dir = project_root / "output"
        if output_dir.exists():
            for book_dir in sorted(output_dir.iterdir()):
                if not book_dir.is_dir():
                    continue
                glossary_path = book_dir / "glossary.txt"
                for md in sorted(book_dir.glob("hsk*_*.md")):
                    readers.append((md, glossary_path if glossary_path.exists() else None))
                for md in sorted(book_dir.glob("n*_*.md")):
                    readers.append((md, glossary_path if glossary_path.exists() else None))

    results = []
    for reader_path, glossary_path in readers:
        info = detect_level_from_filename(reader_path.name)
        if not info:
            continue
        level_key, language = info

        if language == "chinese":
            charset_path = pipeline_dir / "charsets" / "hsk" / f"{level_key}_chars.txt"
        else:
            charset_path = pipeline_dir / "charsets" / "jlpt" / f"{level_key}_chars.txt"

        if not charset_path.exists():
            continue

        allowed_chars = load_charset(charset_path)

        # Add glossary characters if available
        if glossary_path and glossary_path.exists():
            allowed_chars = allowed_chars | load_glossary_chars(glossary_path)

        text = reader_path.read_text(encoding="utf-8")
        text = strip_markdown_headers(text)

        result = validate_characters(text, allowed_chars)
        # Show path relative to project root for output/ files
        if include_output and "output" in str(reader_path):
            result["file"] = str(reader_path.relative_to(project_root))
        else:
            result["file"] = reader_path.name
        result["level"] = level_key
        result["language"] = language
        results.append(result)

    # Print table
    print(f"{'File':<55} {'Level':<8} {'In-lvl%':>8} {'Above%':>8} {'Status':>8}")
    print("-" * 91)
    for r in results:
        status = "PASS" if r["passes"] else "FAIL"
        print(f"{r['file']:<55} {r['level']:<8} {r['in_level_percent']:>7.1f}% "
              f"{r['above_level_percent']:>7.02f}% {status:>8}")

    # Summary
    passing = sum(1 for r in results if r["passes"])
    total = len(results)
    print(f"\n{passing}/{total} readers pass the 95/5 character constraint.")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", action="store_true",
                        help="Also validate files in the output/ directory (with glossary support)")
    args = parser.parse_args()
    validate_all(include_output=args.output)
