#!/usr/bin/env python3
"""Audit and publish the clean N5-through-N1 chapter-one Wagahai editions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from pipeline.build_aozora_epub import (
    AozoraConversionError,
    verify_manifest as verify_source_manifest,
)
from pipeline.publish_wagahai import (
    PublicationError,
    _audit_chapter,
    _source_index,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = ROOT / "books" / "japanese" / "wagahai_wa_neko_de_aru"
DEFAULT_CONTENT_DIR = ROOT / "content" / "japanese" / "wagahai"
LEVELS = ("n5", "n4", "n3", "n2", "n1")
INLINE_READING = re.compile(r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\u3005])（[\u3040-\u309fー]+）")


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    temporary.replace(path)


def _without_inline_readings(chapter: dict[str, Any]) -> dict[str, Any]:
    """Remove Aozora-style parenthetical readings from the published surface.

    Readings remain available from each annotated segment.  This keeps a
    source-close N1 adaptation readable in the app instead of rendering ruby
    source notation inline (for example, ``吾輩（わがはい）``).  Segment and
    grammar-overlay offsets are remapped onto the cleaned text.
    """
    result = dict(chapter)
    old_text = str(chapter["text"])
    removed = [(match.start(), match.end()) for match in INLINE_READING.finditer(old_text)]
    if not removed:
        return result

    removed_positions = {
        position for start, end in removed for position in range(start, end)
    }

    def clean_span(start: int, end: int) -> str:
        return "".join(
            char
            for position, char in enumerate(old_text[start:end], start)
            if position not in removed_positions
        )

    def new_offset(position: int) -> int:
        return position - sum(
            max(0, min(position, end) - start) for start, end in removed
        )

    clean_segments: list[dict[str, Any]] = []
    cursor = 0
    for segment in chapter["segments"]:
        old_surface = str(segment["surface"])
        end = cursor + len(old_surface)
        new_surface = clean_span(cursor, end)
        if new_surface:
            cleaned = dict(segment)
            cleaned["surface"] = new_surface
            clean_segments.append(cleaned)
        cursor = end
    if cursor != len(old_text):
        raise PublicationError("Japanese segments do not reconstruct text before reading cleanup")

    clean_overlays: list[dict[str, Any]] = []
    for overlay in chapter["grammar_overlays"]:
        old_start = int(overlay["start"])
        old_end = int(overlay["end"])
        cleaned_overlay = dict(overlay)
        cleaned_overlay["start"] = new_offset(old_start)
        cleaned_overlay["end"] = new_offset(old_end)
        cleaned_overlay["surface"] = clean_span(old_start, old_end)
        clean_components: list[dict[str, Any]] = []
        for component in overlay.get("components", []):
            component_start = old_start + int(component["start"])
            component_end = old_start + int(component["end"])
            component_surface = clean_span(component_start, component_end)
            if not component_surface:
                continue
            cleaned_component = dict(component)
            cleaned_component["start"] = (
                new_offset(component_start) - cleaned_overlay["start"]
            )
            cleaned_component["end"] = (
                new_offset(component_end) - cleaned_overlay["start"]
            )
            cleaned_component["surface"] = component_surface
            clean_components.append(cleaned_component)
        cleaned_overlay["components"] = clean_components
        clean_overlays.append(cleaned_overlay)

    clean_text = clean_span(0, len(old_text))
    if "".join(segment["surface"] for segment in clean_segments) != clean_text:
        raise PublicationError("Japanese segments do not reconstruct text after reading cleanup")
    for overlay in clean_overlays:
        if clean_text[overlay["start"]:overlay["end"]] != overlay["surface"]:
            raise PublicationError("Japanese grammar overlay drifted during reading cleanup")

    result["text"] = clean_text
    result["segments"] = clean_segments
    result["grammar_overlays"] = clean_overlays
    return result


def publish_smoke(
    run_parent: Path,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    content_dir: Path = DEFAULT_CONTENT_DIR,
    levels: tuple[str, ...] = LEVELS,
) -> dict[str, Any]:
    """Fail closed unless every selected chapter-one level is complete."""
    if not levels or any(level not in LEVELS for level in levels):
        raise PublicationError(f"invalid selected JLPT levels: {levels}")
    levels = tuple(level for level in LEVELS if level in set(levels))
    try:
        verify_source_manifest(source_dir / "manifest.json")
    except AozoraConversionError as exc:
        raise PublicationError(f"source corpus verification failed: {exc}") from exc
    sources = _source_index(source_dir, expected_chapters=11)
    chapter_source = sources[1]
    chapters = {
        level: _audit_chapter(
            run_parent / f"chapter_01-{level}",
            chapter_source,
            level,
            1,
            True,
        )
        for level in levels
    }
    lengths = [chapters[level]["characters"] for level in levels]
    if lengths != sorted(lengths) or len(lengths) != len(set(lengths)):
        raise PublicationError("N5-through-N1 lengths must increase strictly")

    metadata = {
        "id": "wagahai",
        "title_native": "吾輩は猫である",
        "title_en": "I Am a Cat",
        "author_native": "夏目漱石",
        "smoke_scope": (
            "chapter_01_all_jlpt_levels"
            if levels == LEVELS else "chapter_01_selected_jlpt_levels"
        ),
        "enabled_levels": list(levels),
    }
    payloads: dict[Path, str] = {
        content_dir / "metadata.json": json.dumps(
            metadata, ensure_ascii=False, indent=2
        ) + "\n"
    }
    for level, audited_chapter in chapters.items():
        chapter = _without_inline_readings(audited_chapter)
        payloads[content_dir / f"{level}.md"] = (
            f"## {chapter['title']}\n\n{chapter['text'].strip()}\n"
        )
        annotation_document = {
            "schema_version": 1,
            "language": "japanese",
            "book": "wagahai",
            "level": level,
            "chapter_count": 1,
            "expected_chapter_count": 1,
            "chapters": [{
                "number": 1,
                "title": chapter["title"],
                "text": chapter["text"].strip(),
                "segments": chapter["segments"],
                "grammar_overlays": chapter["grammar_overlays"],
                "annotation_audit": chapter["annotation_audit"],
                "materiality_audit": chapter["materiality_audit"],
                "level_diagnostics": chapter["level_diagnostics"],
                "source": chapter["source"],
            }],
        }
        # Markdown chapter extraction strips the harness's trailing newline.
        # Bind the annotation text and its final punctuation segment to that
        # exact clean representation before the generic asset builder runs.
        annotation_chapter = annotation_document["chapters"][0]
        while (
            annotation_chapter["segments"]
            and annotation_chapter["segments"][-1]["type"] == "punctuation"
            and annotation_chapter["segments"][-1]["surface"].endswith("\n")
        ):
            final = dict(annotation_chapter["segments"][-1])
            final["surface"] = final["surface"].rstrip("\n")
            if final["surface"]:
                annotation_chapter["segments"][-1] = final
                break
            annotation_chapter["segments"].pop()
        payloads[content_dir / f"{level}.annotations.json"] = json.dumps(
            annotation_document, ensure_ascii=False, indent=2
        ) + "\n"

    for path, value in payloads.items():
        _atomic_text(path, value)
    report = {
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "book": "wagahai",
        "scope": metadata["smoke_scope"],
        "source_manifest": str((source_dir / "manifest.json").resolve()),
        "levels": {
            level: {
                "characters": chapters[level]["characters"],
                "run": str((run_parent / f"chapter_01-{level}").resolve()),
            }
            for level in levels
        },
        "invariants": {
            "source_provenance_verified": True,
            "source_grounded_reviews_passed": True,
            "annotations_reconstruct_text": True,
            "annotations_reviewed": True,
            "lengths_increase_n5_to_n1": True,
            "legacy_japanese_content_excluded": True,
        },
    }
    _atomic_text(
        content_dir / "smoke-publication-audit.json",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-parent", required=True)
    result.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    result.add_argument("--content-dir", default=str(DEFAULT_CONTENT_DIR))
    result.add_argument(
        "--levels", nargs="+", choices=LEVELS, default=list(LEVELS),
        help="publish only these fully reviewed levels and hide unselected levels",
    )
    result.add_argument("--build-app-content", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        report = publish_smoke(
            Path(args.run_parent), Path(args.source_dir), Path(args.content_dir),
            tuple(args.levels),
        )
        if args.build_app_content:
            subprocess.run(
                [sys.executable, "-m", "scripts.generate_app_content_json"],
                cwd=ROOT,
                check=True,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (PublicationError, subprocess.CalledProcessError) as exc:
        print(f"smoke publication failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
