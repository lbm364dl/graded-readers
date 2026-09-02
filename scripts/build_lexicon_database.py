#!/usr/bin/env python3
"""Build the app's indexed, read-only lookup database from source JSON assets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "app" / "assets"
DEFAULT_OUTPUT = ASSETS / "lexicon.sqlite3"
DEFAULT_MANIFEST = ASSETS / "lexicon_manifest.json"
CHARACTER_DATA = ROOT / "data" / "chinese" / "characters"


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def load_character_levels() -> dict[str, int]:
    """Load the official New HSK 3.0 character-introduction bands."""
    levels: dict[str, int] = {}
    for level in range(1, 8):
        label = f"hsk{level}" if level <= 6 else "hsk7to9"
        path = CHARACTER_DATA / f"{label}_chars.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                character = (row.get("character") or "").strip()
                if character:
                    levels.setdefault(character, level)
    return levels


def build_database(output: Path) -> dict[str, int | str]:
    dictionaries = {
        "zh": load_object(ASSETS / "dictionary.json"),
        "ja": load_object(ASSETS / "dictionary_ja.json"),
    }
    etymology = load_object(ASSETS / "etymology.json")
    glyphs = load_object(ASSETS / "glyphs.json")
    character_levels = load_character_levels()

    temporary = output.with_suffix(f"{output.suffix}.building")
    temporary.unlink(missing_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA page_size = 4096;
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            PRAGMA temp_store = MEMORY;

            CREATE TABLE metadata (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE dictionary (
              language TEXT NOT NULL,
              lookup_key TEXT NOT NULL,
              word TEXT NOT NULL,
              reading TEXT NOT NULL,
              definitions TEXT NOT NULL,
              part_of_speech TEXT NOT NULL,
              level INTEGER,
              is_alias INTEGER NOT NULL,
              PRIMARY KEY (language, lookup_key)
            ) WITHOUT ROWID;

            CREATE TABLE etymology (
              character TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE glyph (
              character TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE character_level (
              character TEXT PRIMARY KEY,
              level INTEGER NOT NULL
            ) WITHOUT ROWID;
            """
        )

        for language, entries in dictionaries.items():
            rows = []
            for word, raw_value in entries.items():
                if not isinstance(raw_value, dict):
                    continue
                rows.append(
                    (
                        language,
                        word,
                        str(raw_value.get("w") or word),
                        str(raw_value.get("p") or ""),
                        compact_json(raw_value.get("d") or []),
                        compact_json(raw_value.get("pos") or []),
                        raw_value.get("l"),
                        1 if raw_value.get("a") is True else 0,
                    )
                )
            connection.executemany(
                "INSERT INTO dictionary VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
            )

        connection.executemany(
            "INSERT INTO etymology VALUES (?, ?)",
            ((character, compact_json(payload)) for character, payload in etymology.items()),
        )
        connection.executemany(
            "INSERT INTO glyph VALUES (?, ?)",
            ((character, compact_json(payload)) for character, payload in glyphs.items()),
        )
        connection.executemany(
            "INSERT INTO character_level VALUES (?, ?)",
            character_levels.items(),
        )

        counts: dict[str, int | str] = {
            "schema_version": 3,
            "dictionary_zh_entries": len(dictionaries["zh"]),
            "dictionary_ja_entries": len(dictionaries["ja"]),
            "etymology_entries": len(etymology),
            "glyph_entries": len(glyphs),
            "character_level_entries": len(character_levels),
        }
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            ((key, str(value)) for key, value in counts.items()),
        )
        connection.commit()
        connection.execute("VACUUM")
        connection.execute("PRAGMA optimize")
    finally:
        connection.close()

    temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    counts["sha256"] = digest
    counts["bytes"] = output.stat().st_size
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    metadata = build_database(args.output)
    args.manifest.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Built {args.output} ({metadata['bytes']} bytes, "
        f"sha256 {str(metadata['sha256'])[:16]}...)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
