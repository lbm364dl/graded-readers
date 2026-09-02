#!/usr/bin/env python3
"""Generate dictionary_ja.json from JLPT vocabulary CSV files."""

import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORDS_DIR = BASE / "data" / "japanese" / "words"
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

    def merge_canonical(existing: dict, entry: dict) -> dict:
        """Preserve every supplied sense for one exact written headword."""
        if existing.get("w") != entry["w"] or existing.get("a") is True:
            return entry
        merged = dict(existing)
        merged["l"] = min(int(existing["l"]), int(entry["l"]))
        merged["d"] = list(dict.fromkeys([*existing["d"], *entry["d"]]))
        merged["pos"] = list(dict.fromkeys([
            *existing.get("pos", []), *entry.get("pos", []),
        ]))
        return merged

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
                part_of_speech = row.get("pos", "").strip()

                if not word:
                    continue

                definitions = split_definitions(english)
                if not definitions:
                    continue

                entry = {
                    "w": word,
                    "p": reading,
                    "l": level,
                    "d": definitions,
                    "pos": [part_of_speech] if part_of_speech else [],
                }

                # A real written headword always wins over a reading alias at
                # the same key. Merge duplicate rows instead of silently
                # discarding later senses.
                if word in dictionary:
                    dictionary[word] = merge_canonical(dictionary[word], entry)
                else:
                    dictionary[word] = entry

                # Reading aliases remain useful for ad-hoc lookup, but carry
                # their canonical headword and never replace an exact written
                # entry. Agent-authored links may target canonical entries only.
                if reading and reading != word and reading not in dictionary:
                    dictionary[reading] = {**entry, "a": True}

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
