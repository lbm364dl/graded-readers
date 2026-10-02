"""Rebuild published editions while retaining the reviewed Level 1 smoke check."""
from __future__ import annotations
import json
from pipeline.korean_readability import ROOT, validate_chapter
from scripts import generate_app_content_json as app_content

PROSE = ROOT / "content/korean/honggildong/l1.md"
ANNOTATIONS = ROOT / "content/korean/honggildong/l1.annotations.json"


def build() -> dict:
    document = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    results = [validate_chapter(chapter) for chapter in document["chapters"]]
    entries = app_content.build_language("korean", app_content.LANGUAGES['korean'][0])
    destination = app_content.ASSET_ROOT / "content_ko.json"
    destination.write_text(json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"passes": all(result["passes"] for result in results), "chapters": len(results)}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
