#!/usr/bin/env python3
"""Bundle original chapters into level-appropriate retelling units."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


DEFAULT_STORY_CHAPTERS = {
    "hsk1": 24,
    "hsk2": 30,
    "hsk3": 40,
    "hsk4": 60,
    "hsk5": 120,
    "hsk6": 120,
}


def balanced_ranges(source_count: int, bundle_count: int) -> list[tuple[int, int]]:
    """Return inclusive, consecutive 1-based ranges covering every source."""
    if source_count < 1 or bundle_count < 1 or bundle_count > source_count:
        raise ValueError("require 1 <= bundle_count <= source_count")
    return [
        (math.floor(i * source_count / bundle_count) + 1,
         math.floor((i + 1) * source_count / bundle_count))
        for i in range(bundle_count)
    ]


def build_bundles(source_dir: Path, output_root: Path, level: str,
                  bundle_count: int | None = None) -> dict:
    sources = sorted(source_dir.glob("chapter_*.txt"))
    count = bundle_count or DEFAULT_STORY_CHAPTERS[level]
    ranges = balanced_ranges(len(sources), count)
    output_dir = output_root / level
    output_dir.mkdir(parents=True, exist_ok=True)
    bundles = []
    for number, (start, end) in enumerate(ranges, 1):
        selected = sources[start - 1:end]
        sections = []
        for source_number, path in enumerate(selected, start):
            sections.append(
                f"\n\n=== ORIGINAL CHAPTER {source_number} ===\n\n"
                + path.read_text(encoding="utf-8").strip()
            )
        text = "".join(sections).lstrip() + "\n"
        path = output_dir / f"chapter_{number:03d}.txt"
        path.write_text(text, encoding="utf-8")
        bundles.append({
            "number": number,
            "file": path.name,
            "source_start": start,
            "source_end": end,
            "source_files": [item.name for item in selected],
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    manifest = {
        "level": level,
        "source_chapters": len(sources),
        "story_chapters": count,
        "bundles": bundles,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--levels", nargs="+", default=list(DEFAULT_STORY_CHAPTERS))
    args = parser.parse_args()
    for level in args.levels:
        manifest = build_bundles(args.source_dir, args.output_dir, level)
        print(f"{level}: {manifest['source_chapters']} source chapters -> "
              f"{manifest['story_chapters']} story chapters")


if __name__ == "__main__":
    main()
