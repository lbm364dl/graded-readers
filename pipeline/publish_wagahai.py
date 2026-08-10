#!/usr/bin/env python3
"""Deterministically audit and publish full JLPT editions of 吾輩は猫である.

This command performs no model calls. It validates all requested levels before
replacing any reader artifact.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from pipeline.epub3 import build_epub3
from pipeline.build_aozora_epub import (
    AozoraConversionError,
    verify_manifest as verify_source_manifest,
)
from pipeline.japanese_agent_harness import japanese_char_count, japanese_segment_issue


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = ROOT / "books" / "japanese" / "wagahai_wa_neko_de_aru"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "japanese" / "wagahai"
LEVELS = ("n5", "n4", "n3", "n2", "n1")
RUN_NAME = re.compile(r"^chapter_(\d{2,3})-(n[1-5])$")
# Count readable Japanese script, rather than bytes or punctuation.  The CJK
# ranges include iteration mark 々 separately and exclude Latin glosses.


class PublicationError(ValueError):
    """An input failed a publication gate."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"cannot read valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _character_count(text: str) -> int:
    """Use the generation harness's single canonical Japanese metric."""
    return japanese_char_count(text)


def _harness_source_digest(path: Path) -> str:
    """Fingerprint format used by ChapterHarness (UTF-8 text plus NUL)."""
    hasher = hashlib.sha256()
    hasher.update(path.read_text(encoding="utf-8").encode("utf-8"))
    hasher.update(b"\0")
    return hasher.hexdigest()


def _discover_runs(run_dirs: list[Path]) -> dict[tuple[int, str], Path]:
    found: dict[tuple[int, str], Path] = {}
    for parent in run_dirs:
        if not parent.is_dir():
            raise PublicationError(f"run directory does not exist: {parent}")
        for candidate in sorted(parent.iterdir()):
            match = RUN_NAME.fullmatch(candidate.name)
            if not match or not candidate.is_dir():
                continue
            key = (int(match.group(1)), match.group(2))
            if key in found:
                raise PublicationError(
                    f"duplicate run for chapter {key[0]:02d} {key[1]}: "
                    f"{found[key]} and {candidate}"
                )
            found[key] = candidate
    return found


def _source_index(source_dir: Path, expected_chapters: int) -> dict[int, dict[str, Any]]:
    manifest = _load_json(source_dir / "manifest.json")
    chapters = manifest.get("chapters")
    if manifest.get("chapter_count") != expected_chapters or not isinstance(chapters, list):
        raise PublicationError(
            f"source manifest must declare exactly {expected_chapters} chapters"
        )
    result: dict[int, dict[str, Any]] = {}
    for expected, item in enumerate(chapters, 1):
        if not isinstance(item, dict) or item.get("number") != expected:
            raise PublicationError(f"source chapter order breaks at {expected:02d}")
        path = (source_dir / str(item.get("file", ""))).resolve()
        if not path.is_file():
            raise PublicationError(f"source chapter missing: {path}")
        digest = _sha256(path)
        if item.get("sha256") != digest:
            raise PublicationError(f"source hash mismatch: {path}")
        result[expected] = {**item, "path": path, "sha256": digest}
    return result


def _review_verdicts(report: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    # Both names are accepted so the deterministic publisher remains compatible
    # with scene-based and whole-chapter Japanese generation harnesses.
    verdicts = report.get("scene_verdicts", report.get("unit_verdicts"))
    if not isinstance(verdicts, dict) or not verdicts:
        raise PublicationError(f"source-grounded review verdicts missing: {run_dir}")
    if any(value != "pass" for value in verdicts.values()):
        raise PublicationError(f"not every source-grounded review passed: {run_dir}")
    return verdicts


_KANA_READING = re.compile(r"^[\u3040-\u30ffー・\s]+$")
_SEGMENT_TYPES = {"word", "auxiliary", "particle", "name", "idiom", "punctuation"}


def _canonical_segments(raw: list[dict[str, Any]], path: Path) -> list[dict[str, str]]:
    """Validate the final Japanese harness segment schema exactly."""
    result: list[dict[str, str]] = []
    for index, segment in enumerate(raw):
        value = dict(segment)
        expected = {"surface", "type", "lemma", "kana", "meaning_en"}
        if set(value) != expected or any(not isinstance(value[key], str) for key in expected):
            raise PublicationError(f"annotation segment {index} violates schema: {path}")
        if not value["surface"] or value["type"] not in _SEGMENT_TYPES:
            raise PublicationError(f"annotation segment {index} violates schema: {path}")
        if value["type"] == "punctuation":
            if any(value[key] for key in ("lemma", "kana", "meaning_en")):
                raise PublicationError(f"punctuation annotation must have empty fields: {path}")
        else:
            if not all(value[key].strip() for key in ("lemma", "kana", "meaning_en")):
                raise PublicationError(f"annotation fields must be nonempty: {path}")
            if not _KANA_READING.fullmatch(value["kana"]):
                raise PublicationError(f"annotation reading is not kana-only: {path}")
            if any(mark in value["surface"] for mark in "。！？、\n"):
                raise PublicationError(f"semantic annotation contains punctuation: {path}")
            if value["type"] in {"word", "auxiliary", "particle"} and len(value["surface"]) > 12:
                raise PublicationError(f"ordinary annotation is clause-sized: {path}")
            issue = japanese_segment_issue(value["surface"], value["type"])
            if issue:
                raise PublicationError(f"annotation segment {index}: {issue}: {path}")
        result.append(value)
    return result


_REVIEW_FINDING_FIELDS = ("unsupported_additions", "distortions", "language_problems")


def _materiality_evidence(report: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Require auditable evidence that a passing review has no material issue.

    Above-level vocabulary alone is intentionally not a failure: literary and
    story vocabulary is expected and annotated. Actual additions, factual or
    causal distortions, and unresolved language defects are fail-closed.
    """
    review = report.get("chapter_review")
    assert isinstance(review, dict)  # checked immediately before this call
    unresolved = []
    for field in _REVIEW_FINDING_FIELDS:
        values = review.get(field, [])
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise PublicationError(f"invalid {field} review evidence: {run_dir}")
        for finding in values:
            normalized = finding.lower()
            if field == "language_problems" and (
                "above-level" in normalized or "above level" in normalized
            ) and not any(term in normalized for term in ("unnatural", "incorrect", "ungrammatical")):
                continue
            unresolved.append({"category": field, "finding": finding,
                               "classification": "material"})
    supplied = report.get("materiality_audit")
    if supplied is not None:
        if not isinstance(supplied, dict) or supplied.get("reviewed") is not True:
            raise PublicationError(f"invalid materiality audit evidence: {run_dir}")
        supplied_findings = supplied.get("unresolved_material_findings")
        if not isinstance(supplied_findings, list):
            raise PublicationError(f"invalid materiality audit findings: {run_dir}")
        unresolved.extend(x for x in supplied_findings if x not in unresolved)
    if unresolved:
        raise PublicationError(
            f"pass verdict has unresolved material findings: {run_dir}: {unresolved[:4]}"
        )
    return {"reviewed": True, "unresolved_material_findings": [],
            "policy": "literary/story vocabulary is diagnostic, not a hard failure"}


def _level_diagnostics(segments: list[dict[str, str]], level: str) -> dict[str, Any]:
    """Informational vocabulary diagnostics, excluding names and story terms."""
    level_number = {"n5": 1, "n4": 2, "n3": 3, "n2": 4, "n1": 5}[level]
    allowed: set[str] = {"吾輩", "猫", "主人", "迷亭", "寒月", "苦沙弥"}
    vocabulary: dict[str, int] = {}
    data = ROOT / "data" / "japanese" / "words"
    for index, label in enumerate(("n5", "n4", "n3", "n2", "n1"), 1):
        path = data / f"{label}_words.csv"
        if not path.is_file():
            raise PublicationError(f"JLPT vocabulary data missing: {path}")
        import csv
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                vocabulary.setdefault(row["word"], index)
    considered = []
    above = []
    for segment in segments:
        if segment["type"] in {"punctuation", "particle", "auxiliary", "name"}:
            continue
        lemma = segment["lemma"]
        if lemma in allowed:
            continue
        considered.append(lemma)
        if vocabulary.get(lemma, 99) > level_number:
            above.append(lemma)
    return {
        "policy": "diagnostic_only",
        "tokens_considered": len(considered),
        "above_level_tokens": len(above),
        "above_level_ratio": round(len(above) / len(considered), 4) if considered else 0,
        "sample": list(dict.fromkeys(above))[:30],
        "excluded": "particles, auxiliaries, names, and story allowlist",
    }


def _grammar_overlays(raw: Any, text: str, segment_count: int, path: Path) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise PublicationError(f"grammar_overlays must be an array of objects: {path}")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        overlay = dict(item)
        expected = {"start", "end", "surface", "grammar", "meaning_en"}
        if set(overlay) != expected:
            raise PublicationError(f"grammar overlay {index} violates schema: {path}")
        start, end = overlay["start"], overlay["end"]
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            raise PublicationError(f"grammar overlay {index} character span is invalid: {path}")
        if overlay["surface"] != text[start:end]:
            raise PublicationError(f"grammar overlay {index} surface/span mismatch: {path}")
        if not all(isinstance(overlay[key], str) and overlay[key].strip()
                   for key in ("surface", "grammar", "meaning_en")):
            raise PublicationError(f"grammar overlay {index} has empty explanation: {path}")
        result.append(overlay)
    return result


def _duplicate_heading_prefix(text: str, title: str, number: int) -> int:
    """Return the safe leading heading length to remove, including blank lines."""
    first, separator, _ = text.partition("\n")
    plain = re.sub(r"^#{1,6}\s*", "", first).strip()
    numbered = re.sub(rf"^(?:第?{number}[章.]|第[一二三四五六七八九十]+章)[：:\s]*", "", plain)
    duplicate = first.lstrip().startswith("#") or plain == title or numbered == title
    if not duplicate:
        return 0
    consumed = len(first) + len(separator)
    while consumed < len(text) and text[consumed] == "\n":
        consumed += 1
    return consumed


def _trim_segment_prefix(
    segments: list[dict[str, str]], count: int
) -> tuple[list[dict[str, str]], int]:
    remaining = count
    result: list[dict[str, str]] = []
    removed = 0
    for segment in segments:
        text = segment["surface"]
        if remaining >= len(text):
            remaining -= len(text)
            removed += 1
            continue
        if remaining:
            # A heading boundary cutting through a semantic segment indicates
            # malformed segmentation; do not publish a misleading partial word.
            raise PublicationError("duplicate chapter heading is not independently segmented")
        result.append(segment)
    if remaining:
        raise PublicationError("duplicate chapter heading exceeds annotation text")
    return result, removed


def _audit_chapter(
    run_dir: Path,
    source: dict[str, Any],
    level: str,
    number: int,
    require_annotations: bool,
) -> dict[str, Any]:
    manifest = _load_json(run_dir / "manifest.json")
    report = _load_json(run_dir / "report.json")
    if manifest.get("status") != "complete" or report.get("status") != "complete":
        raise PublicationError(f"run is not complete: {run_dir}")
    if manifest.get("level") != level:
        raise PublicationError(f"level mismatch: {run_dir}")
    if Path(str(manifest.get("source", ""))).resolve() != source["path"]:
        raise PublicationError(f"source path mismatch: {run_dir}")
    # Source manifests use the conventional raw-file SHA-256. Agent runs use
    # ChapterHarness.digest(), which hashes the exact decoded text plus a NUL
    # separator. Accept either only after recomputing it from the already
    # manifest-verified source file.
    valid_provenance = {source["sha256"], _harness_source_digest(source["path"])}
    if manifest.get("source_sha256") not in valid_provenance:
        raise PublicationError(f"source provenance hash mismatch: {run_dir}")

    chapter_path = run_dir / "chapter.txt"
    try:
        text = chapter_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PublicationError(f"accepted chapter missing: {chapter_path}") from exc
    count = _character_count(text)
    if not text.strip() or count == 0:
        raise PublicationError(f"accepted chapter is empty: {chapter_path}")
    reported_count = report.get(
        "japanese_chars", report.get("japanese_characters", report.get("chapter_cjk"))
    )
    if reported_count != count:
        raise PublicationError(f"chapter character count does not match report: {run_dir}")
    _review_verdicts(report, run_dir)
    if report.get("chapter_review_verdict") != "pass":
        raise PublicationError(f"whole-chapter review did not pass: {run_dir}")
    chapter_review = report.get("chapter_review")
    if not isinstance(chapter_review, dict) or chapter_review.get("verdict") != "pass":
        raise PublicationError(f"whole-chapter review evidence missing: {run_dir}")
    materiality_audit = _materiality_evidence(report, run_dir)

    outline = _load_json(run_dir / "outline.json")
    title = str(outline.get("chapter_title", "")).strip() or f"第{number}章"
    segments: list[dict[str, Any]] = []
    annotation_audit: dict[str, Any] | None = None
    reader_path = run_dir / "reader.json"
    if reader_path.exists():
        reader = _load_json(reader_path)
        if reader.get("text") != text:
            raise PublicationError(f"reader text differs from accepted chapter: {run_dir}")
        raw_segments = reader.get("segments")
        if not isinstance(raw_segments, list) or any(not isinstance(x, dict) for x in raw_segments):
            raise PublicationError(f"invalid annotation segments: {reader_path}")
        segments = _canonical_segments(raw_segments, reader_path)
        if "".join(segment["surface"] for segment in segments) != text:
            raise PublicationError(f"annotations do not reconstruct chapter: {reader_path}")
        annotation_audit = reader.get("annotation_audit")
        if not isinstance(annotation_audit, dict) or annotation_audit.get("all_reviewed") is not True:
            raise PublicationError(f"annotations were not all reviewed: {reader_path}")
        title = str(reader.get("title", title)).strip() or title
    elif require_annotations:
        raise PublicationError(f"reviewed annotations missing: {reader_path}")

    heading_prefix = _duplicate_heading_prefix(text, title, number)
    overlays = reader.get("grammar_overlays", []) if reader_path.exists() else []
    if heading_prefix:
        text = text[heading_prefix:]
        removed_segments = 0
        if segments:
            segments, removed_segments = _trim_segment_prefix(segments, heading_prefix)
        # Character-index overlays cannot safely survive a removed heading.
        # Shift those wholly in the story and reject overlays crossing it.
        for overlay_index, overlay in enumerate(overlays):
            if isinstance(overlay, dict) and isinstance(overlay.get("start"), int):
                if overlay["start"] < heading_prefix:
                    raise PublicationError(f"grammar overlay intersects duplicate heading: {reader_path}")
                overlay = dict(overlay)
                overlay["start"] -= heading_prefix
                overlay["end"] -= heading_prefix
                overlays[overlay_index] = overlay
    overlays = _grammar_overlays(overlays, text, len(segments), reader_path)
    count = _character_count(text)
    return {
        "number": number,
        "title": title,
        "text": text,
        "segments": segments,
        "grammar_overlays": overlays,
        "annotation_audit": annotation_audit,
        "materiality_audit": materiality_audit,
        "level_diagnostics": _level_diagnostics(segments, level) if segments else None,
        "characters": count,
        "source": {
            "file": source["file"],
            "sha256": source["sha256"],
            "run": str(run_dir.resolve()),
        },
    }


def audit_runs(
    run_dirs: list[Path],
    source_dir: Path,
    levels: list[str],
    expected_chapters: int = 11,
    require_annotations: bool = True,
) -> dict[str, Any]:
    """Return a complete publishable collection, or raise without writing."""
    if levels != sorted(set(levels), key=LEVELS.index):
        raise PublicationError("levels must be unique and in N5-to-N1 order")
    if expected_chapters == 11:
        try:
            verify_source_manifest(source_dir / "manifest.json")
        except (AozoraConversionError, OSError, KeyError, TypeError) as exc:
            raise PublicationError(f"source corpus completeness verification failed: {exc}") from exc
    sources = _source_index(source_dir.resolve(), expected_chapters)
    runs = _discover_runs(run_dirs)
    required = {
        (chapter, level)
        for chapter in range(1, expected_chapters + 1)
        for level in levels
    }
    missing = sorted(required - set(runs))
    if missing:
        preview = ", ".join(f"{number:02d}-{level}" for number, level in missing[:12])
        raise PublicationError(f"missing {len(missing)} chapter-level runs: {preview}")

    books = {
        level: [
            _audit_chapter(runs[(number, level)], sources[number], level, number,
                           require_annotations)
            for number in range(1, expected_chapters + 1)
        ]
        for level in levels
    }
    failures: list[dict[str, Any]] = []
    for lower, upper in zip(levels, levels[1:]):
        low_total = sum(item["characters"] for item in books[lower])
        high_total = sum(item["characters"] for item in books[upper])
        if low_total >= high_total:
            failures.append({"scope": "book", "lower": lower, "upper": upper,
                             "lower_characters": low_total, "upper_characters": high_total})
        for low, high in zip(books[lower], books[upper]):
            if low["characters"] >= high["characters"]:
                failures.append({"scope": "chapter", "chapter": low["number"],
                                 "lower": lower, "lower_characters": low["characters"],
                                 "upper": upper, "upper_characters": high["characters"]})
    if failures:
        raise PublicationError(f"strictly increasing length audit failed: {failures[:12]}")
    return {
        "book": "wagahai",
        "title": "吾輩は猫である",
        "title_en": "I Am a Cat",
        "author": "夏目漱石",
        "source_manifest": str((source_dir / "manifest.json").resolve()),
        "expected_chapters": expected_chapters,
        "levels": levels,
        "books": books,
    }


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def publish(audit: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Write Markdown, reviewed annotations, EPUBs, and bound audit evidence."""
    if audit.get("expected_chapters") != 11 or audit.get("levels") != list(LEVELS):
        raise PublicationError("publication requires the complete 11-chapter x 5-level matrix")
    if any(
        not isinstance(item.get("annotation_audit"), dict)
        or item["annotation_audit"].get("all_reviewed") is not True
        for level in LEVELS for item in audit.get("books", {}).get(level, [])
    ):
        raise PublicationError("publication requires reviewed annotations for the full matrix")
    # Prepare every payload first, so an exception cannot leave a half-built set.
    payloads: dict[Path, str] = {}
    level_stats: dict[str, Any] = {}
    for level in audit["levels"]:
        chapters = audit["books"][level]
        preamble = (
            f"# 吾輩は猫である (I Am a Cat)\n\n"
            f"**JLPT Level {level.upper()}**\n\n"
            "夏目漱石の全十一章を、原作に基づいて段階別に書き直した読物です。\n\n"
        )
        body = "\n\n".join(
            f"## {item['number']}. {item['title']}\n\n{item['text'].strip()}"
            for item in chapters
        ) + "\n"
        annotation_document = {
            "schema_version": 1,
            "book": audit["book"],
            "title": audit["title"],
            "author": audit["author"],
            "level": level.upper(),
            "chapter_count": len(chapters),
            "chapters": [{
                "number": item["number"], "title": item["title"],
                "text": item["text"], "segments": item["segments"],
                "grammar_overlays": item["grammar_overlays"],
                "annotation_audit": item["annotation_audit"],
                "materiality_audit": item["materiality_audit"],
                "level_diagnostics": item["level_diagnostics"],
                "source": item["source"],
            } for item in chapters],
        }
        markdown_path = output_dir / f"{level}_wagahai.md"
        annotations_path = output_dir / f"{level}_wagahai_annotations.json"
        epub_path = output_dir / f"{level}_wagahai.epub"
        payloads[markdown_path] = preamble + body
        payloads[annotations_path] = json.dumps(
            annotation_document, ensure_ascii=False, indent=2
        ) + "\n"
        level_stats[level] = {
            "markdown": str(markdown_path.resolve()),
            "annotations": str(annotations_path.resolve()),
            "epub": str(epub_path.resolve()),
            "chapters": len(chapters),
            "japanese_characters": sum(item["characters"] for item in chapters),
        }
    # Validate all EPUB payloads before replacing any published artifact.
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_epubs: dict[str, Path] = {}
    try:
        for level in audit["levels"]:
            with tempfile.NamedTemporaryFile(dir=output_dir.parent, delete=False) as handle:
                temporary = Path(handle.name)
            temporary_epubs[level] = temporary
            build_epub3(
                temporary,
                identifier=f"wagahai-{level}",
                title=f"吾輩は猫である（{level.upper()}分級読物）",
                language="ja",
                author=audit["author"],
                chapters=audit["books"][level],
                chapter_label=lambda item: f"{item['number']}. {item['title']}",
            )
        for path, content in payloads.items():
            _atomic_text(path, content)
        output_dir.mkdir(parents=True, exist_ok=True)
        for level, temporary in temporary_epubs.items():
            temporary.replace(Path(level_stats[level]["epub"]))
        for level, stats in level_stats.items():
            stats["markdown_sha256"] = _sha256(Path(stats["markdown"]))
            stats["annotations_sha256"] = _sha256(Path(stats["annotations"]))
            stats["epub_sha256"] = _sha256(Path(stats["epub"]))
    finally:
        for temporary in temporary_epubs.values():
            temporary.unlink(missing_ok=True)

    report = {
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "book": audit["book"],
        "source_manifest": audit["source_manifest"],
        "invariants": {
            "exact_ordered_nonempty_chapters": True,
            "source_provenance_verified": True,
            "all_generation_reviews_passed": True,
            "materiality_evidence_verified": True,
            "complete_11_by_5_matrix": True,
            "annotations_reconstruct_text": True,
            "annotations_reviewed": True,
            "strictly_increasing_per_chapter_and_total_length": True,
            "complete_accessible_epub_per_level": True,
        },
        "levels": level_stats,
    }
    _atomic_text(output_dir / "publication-audit.json",
                 json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-dir", action="append", required=True)
    result.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    result.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    result.add_argument("--levels", nargs="+", default=list(LEVELS), choices=LEVELS)
    result.add_argument("--expected-chapters", type=int, default=11)
    result.add_argument("--allow-missing-annotations", action="store_true")
    result.add_argument("--audit-only", action="store_true")
    result.add_argument("--build-app-content", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.allow_missing_annotations and not args.audit_only:
            raise PublicationError(
                "--allow-missing-annotations is only valid with --audit-only"
            )
        audit = audit_runs([Path(path) for path in args.run_dir], Path(args.source_dir),
                           args.levels, args.expected_chapters,
                           not args.allow_missing_annotations)
        if args.audit_only:
            print(json.dumps({"status": "complete", "levels": {
                level: {"chapters": len(audit["books"][level]),
                        "japanese_characters": sum(
                            item["characters"] for item in audit["books"][level])}
                for level in args.levels
            }}, ensure_ascii=False, indent=2))
            return 0
        report = publish(audit, Path(args.output_dir))
        if args.build_app_content:
            subprocess.run([sys.executable,
                            str(ROOT / "scripts" / "generate_jlpt_content_json.py")],
                           cwd=ROOT, check=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (PublicationError, subprocess.CalledProcessError) as exc:
        print(f"publication failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
