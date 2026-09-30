"""Validate and publish only Hong Gildong Level 1, chapter 1."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.korean_readability import ROOT, validate_chapter
from scripts import generate_app_content_json as app_content


PROSE = ROOT / "content/korean/honggildong/l1.md"
ANNOTATIONS = ROOT / "content/korean/honggildong/l1.annotations.json"


def build() -> dict:
    chapters = app_content.chapters_from_markdown(PROSE)
    if len(chapters) != 1:
        raise ValueError("Korean pilot must contain exactly one chapter")
    document = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    if len(document.get("chapters", [])) != 1:
        raise ValueError("Korean pilot annotations must contain exactly one chapter")
    chapter = document["chapters"][0]
    if chapter["text"] != chapters[0]["content"]:
        raise ValueError("Korean pilot prose and annotations differ")
    result = validate_chapter(chapter)
    entries = app_content.build_language("korean", {"l1": 1})
    if len(entries) != 1 or len(entries[0]["chapters"]) != 1:
        raise ValueError("Korean publication scope exceeded first level/chapter")
    destination = app_content.ASSET_ROOT / "content_ko.json"
    destination.write_text(
        json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
