#!/usr/bin/env python3
"""Extract character sets for each proficiency level.

Merges two sources:
  1. Official character lists (data/characters/*.csv) — the authoritative set
  2. Characters extracted from word lists (data/words/*.csv) — catches any chars
     that appear in level-appropriate words but aren't in the official char list

The union of both gives the most complete allowed-character set for each level.

For Japanese (JLPT), there are no official character CSVs, so only word-derived
kanji are used.
"""

import csv
import json
from pathlib import Path


def is_cjk(char: str) -> bool:
    """Check if a character is a CJK ideograph (hanzi/kanji)."""
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF       # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF    # CJK Extension A
        or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
        or 0xF900 <= cp <= 0xFAFF    # CJK Compatibility Ideographs
    )


def extract_chars_from_words(words: list[str]) -> set[str]:
    """Extract all unique CJK characters from a list of words."""
    chars = set()
    for word in words:
        for ch in word:
            if is_cjk(ch):
                chars.add(ch)
    return chars


def load_words_from_csv(path: Path) -> list[str]:
    """Load the 'word' column from a CSV file."""
    words = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            w = row.get("word", "").strip()
            if w:
                words.append(w)
    return words


def load_official_chars_from_csv(path: Path) -> set[str]:
    """Load official characters from a character CSV file (column: 'character')."""
    chars = set()
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ch = row.get("character", "").strip()
            if ch and is_cjk(ch):
                chars.add(ch)
    return chars


def build_cumulative_charsets(
    levels: list[tuple[str, Path | None, Path]],
) -> dict[str, dict]:
    """Build per-level and cumulative character sets.

    Args:
        levels: list of (level_key, char_csv_path_or_None, word_csv_path)
                sorted by level.

    Returns:
        dict mapping level_key -> {
            "new_chars": sorted list of chars introduced at this level,
            "cumulative_chars": sorted list of all chars up to this level,
            "new_count": int,
            "cumulative_count": int,
            "from_official": int (chars from official list at this level),
            "from_words": int (extra chars found only in word list),
        }
    """
    result = {}
    cumulative: set[str] = set()

    for level_key, char_csv, word_csv in levels:
        # Official characters for this level
        official = load_official_chars_from_csv(char_csv) if char_csv and char_csv.exists() else set()

        # Characters derived from word list
        words = load_words_from_csv(word_csv)
        from_words = extract_chars_from_words(words)

        # Union of both sources
        all_chars = official | from_words
        new_chars = all_chars - cumulative
        cumulative = cumulative | all_chars

        # Count how many came from each source (for this level's new chars)
        only_in_words = new_chars - official
        only_in_official = new_chars - from_words

        result[level_key] = {
            "new_chars": sorted(new_chars),
            "cumulative_chars": sorted(cumulative),
            "new_count": len(new_chars),
            "cumulative_count": len(cumulative),
            "from_official": len(new_chars - only_in_words),
            "from_words_only": len(only_in_words),
            "from_official_only": len(only_in_official),
        }

    return result


def build_hsk_charsets(data_dir: Path) -> dict[str, dict]:
    """Build character sets from HSK official char lists + word lists."""
    chars_dir = data_dir / "characters"
    words_dir = data_dir / "words"
    levels = [
        ("hsk1", chars_dir / "hsk1_chars.csv", words_dir / "hsk1_words.csv"),
        ("hsk2", chars_dir / "hsk2_chars.csv", words_dir / "hsk2_words.csv"),
        ("hsk3", chars_dir / "hsk3_chars.csv", words_dir / "hsk3_words.csv"),
        ("hsk4", chars_dir / "hsk4_chars.csv", words_dir / "hsk4_words.csv"),
        ("hsk5", chars_dir / "hsk5_chars.csv", words_dir / "hsk5_words.csv"),
        ("hsk6", chars_dir / "hsk6_chars.csv", words_dir / "hsk6_words.csv"),
        ("hsk7to9", chars_dir / "hsk7to9_chars.csv", words_dir / "hsk7to9_words.csv"),
    ]
    return build_cumulative_charsets(levels)


def build_jlpt_charsets(data_dir: Path) -> dict[str, dict]:
    """Build character sets from JLPT word lists (kanji extraction).

    JLPT has no official per-level character CSVs, so only word-derived kanji.
    """
    words_dir = data_dir / "words"
    levels = [
        ("n5", None, words_dir / "n5_words.csv"),
        ("n4", None, words_dir / "n4_words.csv"),
        ("n3", None, words_dir / "n3_words.csv"),
        ("n2", None, words_dir / "n2_words.csv"),
        ("n1", None, words_dir / "n1_words.csv"),
    ]
    return build_cumulative_charsets(levels)


def save_charsets(charsets: dict[str, dict], output_dir: Path):
    """Save character sets as flat text files + summary JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {}
    for level_key, data in charsets.items():
        # Save cumulative char list as flat text (for feeding to AI)
        txt_path = output_dir / f"{level_key}_chars.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("".join(data["cumulative_chars"]))

        summary[level_key] = {
            "new_count": data["new_count"],
            "cumulative_count": data["cumulative_count"],
            "from_official": data.get("from_official", 0),
            "from_words_only": data.get("from_words_only", 0),
            "from_official_only": data.get("from_official_only", 0),
        }

    # Save summary JSON
    with open(output_dir / "charset_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Extract character sets from official lists + word lists")
    parser.add_argument(
        "--language", choices=["chinese", "japanese", "both"], default="both",
        help="Which language to process",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    if args.language in ("chinese", "both"):
        hsk_data = project_root / "data"
        hsk_out = project_root / "pipeline" / "charsets" / "hsk"
        charsets = build_hsk_charsets(hsk_data)
        summary = save_charsets(charsets, hsk_out)
        print("HSK character sets (official chars + word-derived chars):")
        for k, v in summary.items():
            extra = f" (+{v['from_words_only']} from words only)" if v['from_words_only'] else ""
            print(f"  {k}: {v['new_count']} new, {v['cumulative_count']} cumulative{extra}")

    if args.language in ("japanese", "both"):
        jlpt_data = project_root / "jlpt" / "data"
        jlpt_out = project_root / "pipeline" / "charsets" / "jlpt"
        charsets = build_jlpt_charsets(jlpt_data)
        summary = save_charsets(charsets, jlpt_out)
        print("JLPT character sets (kanji from words):")
        for k, v in summary.items():
            print(f"  {k}: {v['new_count']} new, {v['cumulative_count']} cumulative")


if __name__ == "__main__":
    main()
