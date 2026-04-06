#!/usr/bin/env python3
"""Validate all existing readers against their level's character constraints.

Produces a summary table and detailed reports.
"""

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


def validate_all():
    project_root = Path(__file__).resolve().parent.parent
    pipeline_dir = Path(__file__).resolve().parent

    readers = []
    # HSK readers
    hsk_readers = sorted((project_root / "readers").glob("hsk*.md"))
    # JLPT readers
    jlpt_readers = sorted((project_root / "jlpt" / "readers").glob("n*.md"))
    readers = hsk_readers + jlpt_readers

    results = []
    for reader_path in readers:
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
        text = reader_path.read_text(encoding="utf-8")
        text = strip_markdown_headers(text)

        result = validate_characters(text, allowed_chars)
        result["file"] = reader_path.name
        result["level"] = level_key
        result["language"] = language
        results.append(result)

    # Print table
    print(f"{'File':<40} {'Level':<8} {'In-lvl%':>8} {'Above%':>8} {'Status':>8}")
    print("-" * 76)
    for r in results:
        status = "PASS" if r["passes"] else "FAIL"
        print(f"{r['file']:<40} {r['level']:<8} {r['in_level_percent']:>7.1f}% "
              f"{r['above_level_percent']:>7.2f}% {status:>8}")

    # Summary
    passing = sum(1 for r in results if r["passes"])
    total = len(results)
    print(f"\n{passing}/{total} readers pass the 95/5 character constraint.")

    return results


if __name__ == "__main__":
    validate_all()
