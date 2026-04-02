#!/usr/bin/env python3
"""Generate dictionary_ja.json from JLPT vocabulary CSV files."""

import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORDS_DIR = BASE / "jlpt" / "data" / "words"
ASSETS_DIR = BASE / "app" / "assets"

# N5 CSV -> internal level 1 (easiest), N1 CSV -> internal level 5 (hardest)
LEVEL_MAP = {
    "n5_words.csv": 1,
    "n4_words.csv": 2,
    "n3_words.csv": 3,
    "n2_words.csv": 4,
    "n1_words.csv": 5,
}


def split_definitions(english: str) -> list[str]:
    """Split English definitions on semicolons."""
    parts = [p.strip() for p in english.split(";")]
    return [p for p in parts if p]


def main():
    dictionary: dict[str, dict] = {}

    # Process from easiest (N5=level 1) to hardest (N1=level 5)
    # so that the LOWEST level is kept for duplicates
    for csv_name in ["n5_words.csv", "n4_words.csv", "n3_words.csv", "n2_words.csv", "n1_words.csv"]:
        level = LEVEL_MAP[csv_name]
        csv_path = WORDS_DIR / csv_name

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                word = row["word"].strip()
                reading = row["reading"].strip()
                english = row["english"].strip()

                if not word:
                    continue

                definitions = split_definitions(english)
                if not definitions:
                    continue

                entry = {
                    "p": reading,
                    "l": level,
                    "d": definitions,
                }

                # Keep the lowest level (first occurrence)
                if word not in dictionary:
                    dictionary[word] = entry

                # Also add reading as alternate key (e.g. きれい for 綺麗)
                if reading and reading != word and reading not in dictionary:
                    dictionary[reading] = entry

    # Sort by key for consistent output
    sorted_dict = dict(sorted(dictionary.items()))

    out_path = ASSETS_DIR / "dictionary_ja.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sorted_dict, f, ensure_ascii=False, indent=2)

    print(f"Wrote {out_path}")
    print(f"  Total entries: {len(sorted_dict)}")
    print(f"  File size: {out_path.stat().st_size:,} bytes")

    # Level breakdown
    level_counts = {}
    for entry in sorted_dict.values():
        l = entry["l"]
        level_counts[l] = level_counts.get(l, 0) + 1
    for level in sorted(level_counts):
        jlpt = {1: "N5", 2: "N4", 3: "N3", 4: "N2", 5: "N1"}[level]
        print(f"  Level {level} ({jlpt}): {level_counts[level]} words")


if __name__ == "__main__":
    main()
