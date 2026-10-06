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
from pipeline.japanese_agent_harness import (
    is_numeric_comma_construction,
    japanese_char_count,
    japanese_form_step_issues,
    japanese_learner_segmentation_issues,
    japanese_overlay_policy_issue,
    japanese_required_overlay_issues,
    japanese_segment_issue,
    verify_japanese_chunk_review,
)
from pipeline.annotation_publication import (
    AnnotationPublicationError, verify_chunk_attempt_receipts,
)
from pipeline.japanese_dictionary_links import dictionary_link_issue
from pipeline.japanese_readability import level_diagnostics


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
_SEGMENT_TYPES = {
    "word",
    "grammar",
    "auxiliary",
    "particle",
    "name",
    "idiom",
    "punctuation",
}


def _canonical_segments(raw: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    """Validate the final Japanese harness segment schema exactly."""
    result: list[dict[str, Any]] = []
    for index, segment in enumerate(raw):
        value = dict(segment)
        has_authored_form_steps = "form_steps" in value
        has_authored_dictionary_link = (
            "dictionary_key" in value
            and "dictionary_definition_en" in value
        )
        expected = {
            "surface", "type", "lemma", "surface_kana", "lemma_kana",
            "part_of_speech", "conjugation_form", "meaning_en",
            "story_role", "story_importance_en",
        }
        optional = {
            "grammar_candidate_key", "dictionary_key",
            "dictionary_definition_en", "form_steps",
        }
        if not set(value).issubset(expected | optional) or not expected.issubset(value):
            raise PublicationError(f"annotation segment {index} violates schema: {path}")
        value.setdefault("grammar_candidate_key", "")
        value.setdefault("dictionary_key", "")
        value.setdefault("dictionary_definition_en", "")
        value.setdefault("form_steps", [])
        string_fields = (expected | optional) - {"form_steps"}
        if any(not isinstance(value[key], str) for key in string_fields):
            raise PublicationError(f"annotation segment {index} violates schema: {path}")
        if not value["surface"] or value["type"] not in _SEGMENT_TYPES:
            raise PublicationError(f"annotation segment {index} violates schema: {path}")
        if value["type"] == "punctuation":
            if any(value[key] for key in string_fields - {"surface", "type", "story_role"}):
                raise PublicationError(f"punctuation annotation must have empty fields: {path}")
            if value["form_steps"] != []:
                raise PublicationError(f"punctuation annotation has form steps: {path}")
            if value["story_role"] != "none":
                raise PublicationError(f"punctuation annotation has a story role: {path}")
        else:
            required = (
                "lemma", "surface_kana", "lemma_kana", "part_of_speech",
                "conjugation_form", "meaning_en",
            )
            if not all(value[key].strip() for key in required):
                raise PublicationError(f"annotation fields must be nonempty: {path}")
            if not _KANA_READING.fullmatch(value["surface_kana"]):
                raise PublicationError(f"annotation surface reading is not kana-only: {path}")
            if not _KANA_READING.fullmatch(value["lemma_kana"]):
                raise PublicationError(f"annotation lemma reading is not kana-only: {path}")
            if value["grammar_candidate_key"] and not re.fullmatch(
                r"[a-z][a-z0-9_.-]*", value["grammar_candidate_key"]
            ):
                raise PublicationError(f"annotation grammar key is invalid: {path}")
            if value["story_role"] not in {"none", "name", "story_term"}:
                raise PublicationError(f"annotation story role is invalid: {path}")
            if value["story_role"] == "story_term" and not value["story_importance_en"].strip():
                raise PublicationError(f"story term lacks importance: {path}")
            if value["story_role"] == "story_term" and value["type"] not in {"word", "idiom"}:
                raise PublicationError(f"functional segment cannot be story vocabulary: {path}")
            if any(mark in value["surface"] for mark in "。！？、\n"):
                raise PublicationError(f"semantic annotation contains punctuation: {path}")
            link_issue = dictionary_link_issue(
                lemma=value["lemma"], lemma_kana=value["lemma_kana"],
                key=value["dictionary_key"],
                definition=value["dictionary_definition_en"],
                functional=value["type"] in {"grammar", "auxiliary", "particle"},
                require_available=(
                    has_authored_dictionary_link
                    and value["type"] in {"word", "idiom"}
                ),
            )
            if link_issue:
                raise PublicationError(
                    f"annotation segment {index} dictionary link: {link_issue}: {path}"
                )
            form_issues = japanese_form_step_issues(value) if has_authored_form_steps else []
            if form_issues:
                raise PublicationError(
                    f"annotation segment {index} form chain: {form_issues}: {path}"
                )
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


def _grammar_overlays(raw: Any, text: str, segment_count: int, path: Path) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise PublicationError(f"grammar_overlays must be an array of objects: {path}")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        overlay = dict(item)
        expected = {
            "start", "end", "surface", "grammar_candidate_key", "pattern",
            "meaning_en", "head_lemma", "head_lemma_kana", "form_label",
            "explanation_en", "components",
        }
        if set(overlay) != expected:
            raise PublicationError(f"grammar overlay {index} violates schema: {path}")
        start, end = overlay["start"], overlay["end"]
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            raise PublicationError(f"grammar overlay {index} character span is invalid: {path}")
        if overlay["surface"] != text[start:end]:
            raise PublicationError(f"grammar overlay {index} surface/span mismatch: {path}")
        required_strings = (
            "surface", "grammar_candidate_key", "pattern", "meaning_en",
            "head_lemma", "head_lemma_kana", "form_label", "explanation_en",
        )
        if not all(isinstance(overlay[key], str) and overlay[key].strip()
                   for key in required_strings):
            raise PublicationError(f"grammar overlay {index} has empty explanation: {path}")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", overlay["grammar_candidate_key"]):
            raise PublicationError(f"grammar overlay {index} has invalid candidate key: {path}")
        if not _KANA_READING.fullmatch(overlay["head_lemma_kana"]):
            raise PublicationError(f"grammar overlay {index} has invalid head reading: {path}")
        components = overlay["components"]
        if not isinstance(components, list) or not components:
            raise PublicationError(f"grammar overlay {index} lacks components: {path}")
        cursor = 0
        numeric_comma = is_numeric_comma_construction(overlay["surface"])
        for component_index, component in enumerate(components):
            required_component = {
                "start", "end", "surface", "lemma", "lemma_kana", "function_en",
            }
            optional_component = {
                "lookup_kind", "dictionary_key", "dictionary_definition_en",
            }
            if (
                not isinstance(component, dict)
                or not required_component.issubset(component)
                or not set(component).issubset(required_component | optional_component)
            ):
                raise PublicationError(
                    f"grammar overlay {index} component {component_index} violates schema: {path}"
                )
            has_authored_dictionary_link = (
                "dictionary_key" in component
                and "dictionary_definition_en" in component
            )
            component.setdefault("lookup_kind", "none")
            component.setdefault("dictionary_key", "")
            component.setdefault("dictionary_definition_en", "")
            component_start, component_end = component["start"], component["end"]
            if (
                not isinstance(component_start, int)
                or not isinstance(component_end, int)
                or (
                    component_start != cursor
                    and not (
                        numeric_comma
                        and component_start > cursor
                        and set(overlay["surface"][cursor:component_start]) == {"、"}
                    )
                )
                or not component_start < component_end <= len(overlay["surface"])
                or overlay["surface"][component_start:component_end] != component["surface"]
                or any(not isinstance(component[field], str) or not component[field].strip()
                       for field in ("surface", "lemma", "lemma_kana", "function_en"))
                or (
                    not _KANA_READING.fullmatch(component["lemma_kana"])
                    and not (
                        numeric_comma
                        and component["surface"] == "、"
                        and component["lemma"] == "、"
                        and component["lemma_kana"] == "、"
                    )
                )
            ):
                raise PublicationError(
                    f"grammar overlay {index} component {component_index} is invalid: {path}"
                )
            if component["lookup_kind"] not in {"lexical", "grammar", "none"}:
                raise PublicationError(
                    f"grammar overlay {index} component {component_index} has invalid lookup kind: {path}"
                )
            if not isinstance(component["dictionary_key"], str) or not isinstance(
                component["dictionary_definition_en"], str
            ):
                raise PublicationError(
                    f"grammar overlay {index} component {component_index} has invalid dictionary metadata: {path}"
                )
            link_issue = dictionary_link_issue(
                lemma=component["lemma"], lemma_kana=component["lemma_kana"],
                key=component["dictionary_key"],
                definition=component["dictionary_definition_en"],
                functional=component["lookup_kind"] != "lexical",
                require_available=(
                    has_authored_dictionary_link
                    and component["lookup_kind"] == "lexical"
                ),
            )
            if link_issue:
                raise PublicationError(
                    f"grammar overlay {index} component {component_index} dictionary link: "
                    f"{link_issue}: {path}"
                )
            cursor = component_end
        if cursor != len(overlay["surface"]) and not (
            numeric_comma
            and overlay["surface"][cursor:]
            and set(overlay["surface"][cursor:]) == {"、"}
        ):
            raise PublicationError(f"grammar overlay {index} components do not reconstruct: {path}")
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
        receipt_version = report.get("chunk_review_receipts_version")
        if (receipt_version not in (None, 1)
                or (receipt_version == 1
                    and annotation_audit.get("chunk_review_receipts_version") != 1)):
            raise PublicationError(f"chapter report/reader review receipt version mismatch: {run_dir}")
        try:
            if (report.get("reader_sha256") and
                    report.get("reader_sha256") != _sha256(reader_path)):
                raise AnnotationPublicationError("chapter report does not bind current reader")
            chunk_items = verify_chunk_attempt_receipts(
                run_dir, reader, text, surface_key="surface")
            if chunk_items is not None:
                receipts = annotation_audit.get("chunk_review_receipts", [])
                if not all(
                    verify_japanese_chunk_review(
                        item, text[receipt["source_start"]:receipt["source_end"]], run_dir)
                    for item, receipt in zip(chunk_items, receipts)
                ):
                    raise AnnotationPublicationError("chunk review or adjudication did not replay")
        except (AnnotationPublicationError, OSError, ValueError, TypeError, KeyError) as exc:
            raise PublicationError(
                f"annotation chunk review evidence failed: {reader_path}: {exc}") from exc
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
    if segments:
        segmentation_issues = japanese_learner_segmentation_issues(segments)
        if segmentation_issues:
            raise PublicationError(
                "learner-facing Japanese segmentation failed: "
                f"{reader_path}: {segmentation_issues[:4]}"
            )
        overlay_issues = [
            {
                "surface": str(overlay.get("surface", "")),
                "message": issue,
            }
            for overlay in overlays
            if (issue := japanese_overlay_policy_issue(overlay))
        ]
        if overlay_issues:
            raise PublicationError(
                "learner-facing Japanese grammar overlay failed: "
                f"{reader_path}: {overlay_issues[:4]}"
            )
        required_overlay_issues = japanese_required_overlay_issues(
            segments, overlays,
        )
        if required_overlay_issues:
            raise PublicationError(
                "required learner-facing Japanese grammar overlay is missing: "
                f"{reader_path}: {required_overlay_issues[:4]}"
            )
    count = _character_count(text)
    diagnostics = (
        level_diagnostics(segments, level, overlays) if segments else None
    )
    if diagnostics is not None and not diagnostics["passes"]:
        raise PublicationError(
            f"{level.upper()} vocabulary coverage exceeds the publish limit: "
            f"{diagnostics['above_level_ratio']:.1%} > "
            f"{diagnostics['maximum_above_level_ratio']:.1%}: {run_dir}"
        )
    return {
        "number": number,
        "title": title,
        "text": text,
        "segments": segments,
        "grammar_overlays": overlays,
        "annotation_audit": annotation_audit,
        "materiality_audit": materiality_audit,
        "level_diagnostics": diagnostics,
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
    return {
        "book": "wagahai",
        "title": "吾輩は猫である",
        "title_en": "I Am a Cat",
        "author": "夏目漱石",
        "source_manifest": str((source_dir / "manifest.json").resolve()),
        "expected_chapters": expected_chapters,
        "levels": levels,
        "books": books,
        "length_order_observation": {
            "strictly_increasing_per_chapter_and_total": not failures,
            "observed_irregularities": failures,
        },
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
            "length_is_not_an_acceptance_gate": True,
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
