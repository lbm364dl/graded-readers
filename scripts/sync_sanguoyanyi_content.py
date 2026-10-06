#!/usr/bin/env python3
"""Snapshot accepted Sanguoyanyi runs into the canonical app content library."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from opencc import OpenCC

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.chinese_readability import (
    lower_level_qualification_ratio,
    validate_beginner_chinese,
    validate_level_distinctiveness,
    validate_paragraph_structure,
)
from pipeline.adaptation_policy import (
    policy_for,
    target_chars_for_source_length,
    target_length_bounds,
)


LEVELS = tuple(f"hsk{i}" for i in range(1, 7))
DEFAULT_RUN_ROOTS = {
    "hsk1": ROOT / "runs/graded-readers/sanguoyanyi-full-hsk123",
    "hsk2": ROOT / "runs/graded-readers/sanguoyanyi-full-hsk123",
    "hsk3": ROOT / "runs/graded-readers/sanguoyanyi-full-hsk123",
    "hsk4": ROOT / "runs/graded-readers/sanguoyanyi-full-hsk456",
    "hsk5": ROOT / "runs/graded-readers/sanguoyanyi-full-hsk456",
    "hsk6": ROOT / "runs/graded-readers/sanguoyanyi-full-hsk456",
}
DEFAULT_OUTPUT = ROOT / "content/chinese/sanguoyanyi"
TO_SIMPLIFIED = OpenCC("t2s")


def load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def chapter_run_dir(root: Path, number: int, level: str) -> Path:
    """Resolve both production and source-stem book-harness run names."""
    direct = root / f"chapter_{number:03d}-{level}"
    if direct.is_dir():
        return direct
    candidates = sorted(root.glob(f"chapter_{number:03d}_*-{level}"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return direct
    raise ValueError(
        f"ambiguous chapter {number} {level} runs under {root}: {candidates}"
    )


def reviewed_focus_words(run_dir: Path) -> set[str]:
    path = run_dir / "focus-vocabulary-plan.json"
    if not path.is_file():
        return set()
    plan = load_object(path)
    words: set[str] = set()
    for key in ("names", "story_terms"):
        items = plan.get(key)
        if not isinstance(items, list):
            raise ValueError(f"invalid focus vocabulary plan: {path}")
        for item in items:
            if not isinstance(item, dict) or not str(item.get("surface", "")).strip():
                raise ValueError(f"invalid focus vocabulary item: {path}")
            words.add(str(item["surface"]).strip())
    return words


def accepted_chapter(run_dir: Path) -> tuple[str, str]:
    manifest = load_object(run_dir / "manifest.json")
    report = load_object(run_dir / "report.json")
    if manifest.get("status") != "complete" or report.get("status") != "complete":
        raise ValueError(f"run is not accepted: {run_dir}")
    verdicts = report.get("scene_verdicts")
    if not isinstance(verdicts, dict) or not verdicts or set(verdicts.values()) != {"pass"}:
        raise ValueError(f"not every scene passed review: {run_dir}")
    text = (run_dir / "chapter.txt").read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"accepted chapter is empty: {run_dir}")
    outline = load_object(run_dir / "outline.json")
    title = str(outline.get("chapter_title", "")).strip()
    if not title:
        raise ValueError(f"chapter title is empty: {run_dir}")
    normalized_text = TO_SIMPLIFIED.convert(text)
    level = str(manifest.get("level", ""))
    readability = validate_beginner_chinese(
        normalized_text,
        level,
        allowed_words=reviewed_focus_words(run_dir),
        max_above_level_word_ratio=policy_for(level).max_above_level_ratio,
    )
    if not readability["passes"]:
        raise ValueError(
            f"accepted prose fails the level word/sentence readability gate: {run_dir}"
        )
    policy = policy_for(level)
    distinctiveness = validate_level_distinctiveness(
        normalized_text,
        level,
        allowed_words=reviewed_focus_words(run_dir),
        lower_level_max_above_ratio=(
            lower_level_qualification_ratio(level)
        ),
        min_target_band_unique=policy.min_target_band_unique,
    )
    if not distinctiveness["passes"]:
        raise ValueError(
            f"accepted prose fails the target-level distinctiveness gate: {run_dir}"
        )
    paragraph_structure = validate_paragraph_structure(normalized_text, level)
    if not paragraph_structure["passes"]:
        raise ValueError(
            f"accepted prose fails the paragraph structure gate: {run_dir}"
        )
    source_path = Path(str(manifest.get("source", "")))
    if not source_path.is_file():
        raise ValueError(f"accepted run source is missing: {run_dir}")
    source_text = source_path.read_text(encoding="utf-8")
    source_cjk = sum("\u4e00" <= char <= "\u9fff" for char in source_text)
    target = target_chars_for_source_length(source_cjk, level)
    minimum, maximum = target_length_bounds(target, level)
    chapter_cjk = sum("\u4e00" <= char <= "\u9fff" for char in normalized_text)
    if not minimum <= chapter_cjk <= maximum:
        raise ValueError(
            "accepted prose fails the source-relative length gate: "
            f"{chapter_cjk} CJK, required {minimum}-{maximum} for {run_dir}"
        )
    return TO_SIMPLIFIED.convert(title), normalized_text


def reviewed_annotation(run_dir: Path, expected_text: str) -> dict:
    reader_path = run_dir / "reader.json"
    report_path = run_dir / "annotation-report.json"
    if not reader_path.is_file() or not report_path.is_file():
        raise ValueError(f"reviewed annotations missing: {run_dir}")
    reader = load_object(reader_path)
    report = load_object(report_path)
    raw_text = (run_dir / "chapter.txt").read_text(encoding="utf-8")
    if reader.get("text") != raw_text:
        raise ValueError(f"annotation text is stale: {run_dir}")
    if report.get("status") != "complete":
        raise ValueError(f"annotation report is incomplete: {run_dir}")
    if report.get("chapter_sha256") != hashlib.sha256(
        (run_dir / "chapter.txt").read_bytes()
    ).hexdigest():
        raise ValueError(f"annotation chapter hash is stale: {run_dir}")
    if report.get("reader_sha256") != hashlib.sha256(reader_path.read_bytes()).hexdigest():
        raise ValueError(f"annotation reader hash is stale: {run_dir}")
    segments = reader.get("segments")
    overlays = reader.get("grammar_overlays")
    audit = reader.get("annotation_audit")
    if (not isinstance(segments, list) or not segments
            or not isinstance(overlays, list)
            or not isinstance(audit, dict) or audit.get("all_reviewed") is not True):
        raise ValueError(f"annotations are not fully reviewed: {run_dir}")
    if "".join(str(item.get("text", "")) for item in segments) != raw_text:
        raise ValueError(f"annotations do not reconstruct prose: {run_dir}")
    normalized_segments = []
    for item in segments:
        published = {
            key: value for key, value in item.items()
            if key not in {"explanation_zh", "example_zh"}
        }
        published["text"] = TO_SIMPLIFIED.convert(str(item.get("text", "")))
        normalized_segments.append(published)
    # chapter.txt is a line-oriented artifact and ends in one newline; the
    # Markdown content body intentionally does not. Annotation preserves that
    # boundary as its own punctuation segment, so remove only trailing
    # whitespace-only segments from the exported snapshot.
    while (
        normalized_segments
        and not str(normalized_segments[-1].get("text", "")).strip()
    ):
        normalized_segments.pop()
    normalized_overlays = [
        {**item, "text": TO_SIMPLIFIED.convert(str(item.get("text", "")))}
        for item in overlays
    ]
    if "".join(str(item["text"]) for item in normalized_segments) != expected_text:
        raise ValueError(f"normalized annotations do not reconstruct prose: {run_dir}")
    for overlay in normalized_overlays:
        start, end = overlay.get("start"), overlay.get("end")
        if (not isinstance(start, int) or not isinstance(end, int)
                or expected_text[start:end] != overlay.get("text")):
            raise ValueError(f"grammar overlay does not reconstruct prose: {run_dir}")
    result = {
        "text": expected_text,
        "segments": normalized_segments,
        "grammar_overlays": normalized_overlays,
        "annotation_audit": audit,
    }
    focus_audit = reader.get("learning_focus_audit")
    if focus_audit is not None:
        if (not isinstance(focus_audit, dict)
                or focus_audit.get("role_assignment_reviewed") is not True):
            raise ValueError(f"learning-focus metadata is not reviewed: {run_dir}")
        result["learning_focus_audit"] = focus_audit
    subsegment_audit = reader.get("subsegment_audit")
    if subsegment_audit is not None:
        if (
            not isinstance(subsegment_audit, dict)
            or subsegment_audit.get("all_reviewed") is not True
        ):
            raise ValueError(f"subsegment metadata is not reviewed: {run_dir}")
        for segment in normalized_segments:
            parts = segment.get("subsegments", [])
            composition = segment.get("composition_en", "")
            if not isinstance(parts, list) or not isinstance(composition, str):
                raise ValueError(f"invalid subsegment metadata: {run_dir}")
            if not parts:
                continue
            surface = str(segment["text"])
            cursor = 0
            if len(parts) < 2 or not composition.strip():
                raise ValueError(f"incomplete subsegment metadata: {run_dir}")
            for part in parts:
                if not isinstance(part, dict) or set(part) != {
                    "start", "end", "text", "meaning_en"
                }:
                    raise ValueError(f"invalid subsegment item: {run_dir}")
                start, end, text = (
                    part.get("start"), part.get("end"), part.get("text")
                )
                if (
                    not isinstance(start, int)
                    or not isinstance(end, int)
                    or start != cursor
                    or surface[start:end] != text
                    or not isinstance(part.get("meaning_en"), str)
                    or not part["meaning_en"].strip()
                ):
                    raise ValueError(f"subsegments do not reconstruct: {run_dir}")
                cursor = end
            if cursor != len(surface):
                raise ValueError(f"subsegments do not cover surface: {run_dir}")
        result["subsegment_audit"] = subsegment_audit
    return result


def sync(
    output_dir: Path,
    expected_chapters: int,
    *,
    allow_partial_annotations: bool = False,
    run_roots: dict[str, Path] | None = None,
    level_chapter_counts: dict[str, int] | None = None,
    levels: tuple[str, ...] = LEVELS,
) -> None:
    roots = {**DEFAULT_RUN_ROOTS, **(run_roots or {})}
    counts = level_chapter_counts or {}
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "id": "sanguoyanyi",
        "title_native": "三国演义",
        "title_en": "Romance of the Three Kingdoms",
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for level in levels:
        level_count = counts.get(level, expected_chapters)
        chapters = []
        annotations = []
        for number in range(1, level_count + 1):
            run_dir = chapter_run_dir(roots[level], number, level)
            title, text = accepted_chapter(run_dir)
            chapters.append(f"## {title}\n\n{text}")
            annotation_ready = (
                (run_dir / "reader.json").is_file()
                and (run_dir / "annotation-report.json").is_file()
            )
            if annotation_ready or not allow_partial_annotations:
                annotation = reviewed_annotation(run_dir, text)
                annotations.append({
                    "number": number,
                    "title": title,
                    **annotation,
                })
        destination = output_dir / f"{level}.md"
        destination.write_text("\n\n---\n\n".join(chapters) + "\n", encoding="utf-8")
        annotation_destination = output_dir / f"{level}.annotations.json"
        annotation_schema = 1
        if annotations and all("learning_focus_audit" in item for item in annotations):
            annotation_schema = 2
            if all(
                item["learning_focus_audit"].get("semantic_focus_reviewed") is True
                for item in annotations
            ):
                annotation_schema = 3
            if all(
                item.get("subsegment_audit", {}).get("all_reviewed") is True
                for item in annotations
            ):
                annotation_schema = 5
        annotation_destination.write_text(
            json.dumps({
                "schema_version": annotation_schema,
                "book": "sanguoyanyi",
                "level": level,
                "chapter_count": len(annotations),
                "expected_chapter_count": level_count,
                "chapters": annotations,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {destination} ({len(chapters)} chapters)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-chapters", type=int, default=120)
    parser.add_argument(
        "--allow-partial-annotations", action="store_true",
        help="snapshot reviewed sidecars that are ready and leave other prose non-interactive",
    )
    parser.add_argument(
        "--run-root", action="append", default=[], metavar="LEVEL=PATH",
        help="override one accepted run root (repeatable)",
    )
    parser.add_argument(
        "--level-chapters", action="append", default=[], metavar="LEVEL=COUNT",
        help="override the chapter count for one preview level (repeatable)",
    )
    parser.add_argument(
        "--level", action="append", choices=LEVELS,
        help="sync only this level (repeatable; defaults to every level)",
    )
    args = parser.parse_args()
    run_roots: dict[str, Path] = {}
    for value in args.run_root:
        level, separator, path = value.partition("=")
        if not separator or level not in LEVELS or level in run_roots:
            parser.error(f"invalid or duplicate --run-root: {value}")
        run_roots[level] = Path(path).resolve()
    level_counts: dict[str, int] = {}
    for value in args.level_chapters:
        level, separator, raw_count = value.partition("=")
        if not separator or level not in LEVELS or level in level_counts:
            parser.error(f"invalid or duplicate --level-chapters: {value}")
        try:
            count = int(raw_count)
        except ValueError:
            parser.error(f"invalid --level-chapters count: {value}")
        if count < 1:
            parser.error(f"invalid --level-chapters count: {value}")
        level_counts[level] = count
    sync(
        args.output_dir, args.expected_chapters,
        allow_partial_annotations=args.allow_partial_annotations,
        run_roots=run_roots,
        level_chapter_counts=level_counts,
        levels=tuple(args.level or LEVELS),
    )


if __name__ == "__main__":
    main()
