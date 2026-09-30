#!/usr/bin/env python3
"""Build app reader assets exclusively from the canonical content library."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pipeline.japanese_agent_harness import (
    is_numeric_comma_construction,
    japanese_form_step_issues,
)
from pipeline.japanese_dictionary_links import dictionary_link_issue
from pipeline.japanese_readability import (
    matched_level_with_overlays,
    vocabulary_levels,
)


ROOT = Path(__file__).resolve().parent.parent
CONTENT_ROOT = ROOT / "content"
ASSET_ROOT = ROOT / "app/assets"
LANGUAGES = {
    "chinese": ({f"hsk{i}": i for i in range(1, 7)}, "content.json"),
    "japanese": ({f"n{i}": 6 - i for i in range(1, 6)}, "content_ja.json"),
    "korean": ({f"l{i}": i for i in range(1, 7)}, "content_ko.json"),
}
HEADER = re.compile(r"^##\s+(.+)$")


def japanese_vocabulary_levels() -> dict[str, int]:
    """Return the versioned local JLPT baseline keyed by spelling/reading.

    JLPT does not publish a canonical vocabulary list, so this lookup is only
    used for stable reader highlighting. Agent judgments remain responsible
    for names, indispensable story vocabulary, and contextual explanations.
    """
    return dict(vocabulary_levels())


def load_japanese_annotations(
    path: Path, chapters: list[dict[str, str]], *, book_id: str,
    level_key: str, require_complete: bool,
) -> list[str]:
    """Validate and publish the clean agent-first Japanese sidecars."""
    if not path.is_file():
        if not require_complete:
            return ["" for _ in chapters]
        raise ValueError(f"reviewed Japanese agent annotations missing: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError(f"invalid Japanese annotation document: {path}")
    annotated = document.get("chapters")
    if not isinstance(annotated, list):
        raise ValueError(f"invalid Japanese annotation chapters: {path}")
    by_number = {
        item.get("number"): item for item in annotated if isinstance(item, dict)
    }
    if len(by_number) != len(annotated) or any(
        not isinstance(number, int) or not 1 <= number <= len(chapters)
        for number in by_number
    ):
        raise ValueError(f"invalid Japanese annotation chapter numbering: {path}")
    if require_complete and set(by_number) != set(range(1, len(chapters) + 1)):
        raise ValueError(f"Japanese annotation chapter count does not match prose: {path}")

    target_level = {"n5": 1, "n4": 2, "n3": 3, "n2": 4, "n1": 5}[level_key]
    segment_fields = {
        "surface", "type", "lemma", "surface_kana", "lemma_kana",
        "part_of_speech", "conjugation_form", "meaning_en", "story_role",
        "story_importance_en",
    }
    optional_segment_fields = {
        "grammar_candidate_key", "dictionary_key",
        "dictionary_definition_en", "form_steps",
    }
    overlay_fields = {
        "start", "end", "surface", "grammar_candidate_key", "pattern",
        "meaning_en", "head_lemma", "head_lemma_kana", "form_label",
        "explanation_en", "components",
    }
    assets = ["" for _ in chapters]
    for number, item in sorted(by_number.items()):
        chapter = chapters[number - 1]
        if item.get("text") != chapter["content"]:
            raise ValueError(f"Japanese annotation text differs at chapter {number}: {path}")
        segments = item.get("segments")
        overlays = item.get("grammar_overlays")
        audit = item.get("annotation_audit")
        if (
            not isinstance(segments, list) or not segments
            or not isinstance(overlays, list)
            or not isinstance(audit, dict) or audit.get("all_reviewed") is not True
        ):
            raise ValueError(f"unreviewed Japanese annotation chapter {number}: {path}")
        published_segments = []
        offset = 0
        for segment in segments:
            if (
                not isinstance(segment, dict)
                or not segment_fields.issubset(segment)
                or not set(segment).issubset(segment_fields | optional_segment_fields)
            ):
                raise ValueError(f"invalid Japanese segment in chapter {number}: {path}")
            segment = dict(segment)
            has_authored_form_steps = "form_steps" in segment
            has_authored_dictionary_link = (
                "dictionary_key" in segment
                and "dictionary_definition_en" in segment
            )
            segment.setdefault("grammar_candidate_key", "")
            segment.setdefault("dictionary_key", "")
            segment.setdefault("dictionary_definition_en", "")
            segment.setdefault("form_steps", [])
            surface = segment["surface"]
            if not isinstance(surface, str) or not surface:
                raise ValueError(f"empty Japanese segment in chapter {number}: {path}")
            end = offset + len(surface)
            direct_grammar_key = segment.get("grammar_candidate_key", "")
            if not isinstance(direct_grammar_key, str) or (
                direct_grammar_key
                and not re.fullmatch(r"[a-z][a-z0-9_.-]*", direct_grammar_key)
            ):
                raise ValueError(f"invalid Japanese primary grammar key: {path}")
            grammar_keys = sorted({
                overlay["grammar_candidate_key"] for overlay in overlays
                if isinstance(overlay, dict)
                and isinstance(overlay.get("start"), int)
                and isinstance(overlay.get("end"), int)
                and overlay["start"] < end and offset < overlay["end"]
            } | ({direct_grammar_key} if direct_grammar_key else set()))
            kind = segment["type"]
            story_role = segment["story_role"]
            story_importance = segment["story_importance_en"]
            if story_role == "story_term" and kind not in {"word", "idiom"}:
                raise ValueError(
                    f"functional Japanese segment cannot be story vocabulary "
                    f"in chapter {number}: {path}"
                )
            if kind == "punctuation":
                punctuation_metadata = (
                    segment_fields - {"surface", "type", "story_role"}
                ) | optional_segment_fields
                if (
                    any(segment[field] for field in punctuation_metadata)
                    or direct_grammar_key
                    or story_role != "none"
                ):
                    raise ValueError(f"punctuation has Japanese metadata in chapter {number}: {path}")
                matched = None
                status = "not_applicable"
                focus = "not_applicable"
                reason = ""
            else:
                required = (
                    "lemma", "surface_kana", "lemma_kana", "part_of_speech",
                    "conjugation_form", "meaning_en",
                )
                if any(not str(segment[field]).strip() for field in required):
                    raise ValueError(f"empty Japanese explanation in chapter {number}: {path}")
                link_issue = dictionary_link_issue(
                    lemma=segment["lemma"], lemma_kana=segment["lemma_kana"],
                    key=segment["dictionary_key"],
                    definition=segment["dictionary_definition_en"],
                    functional=kind in {"grammar", "auxiliary", "particle"},
                    require_available=(
                        has_authored_dictionary_link
                        and kind in {"word", "idiom"}
                    ),
                )
                if link_issue:
                    raise ValueError(f"invalid Japanese dictionary link ({link_issue}): {path}")
                form_issues = (
                    japanese_form_step_issues(segment)
                    if has_authored_form_steps else []
                )
                if form_issues:
                    raise ValueError(f"invalid Japanese form chain {form_issues}: {path}")
                matched = matched_level_with_overlays(
                    segment, offset, end, overlays,
                )
                if kind in {"particle", "auxiliary"}:
                    status, focus, reason = "not_applicable", "target", ""
                elif matched is None:
                    status, focus, reason = "unlisted", "lookup", "unlisted"
                elif matched <= target_level:
                    status, focus, reason = "in_level", "target", ""
                else:
                    status, focus, reason = "above_level", "lookup", "above_level"
                if story_role == "name":
                    focus, reason = "lookup", "proper_name"
                elif story_role == "story_term" and (
                    matched is None or matched > target_level
                ):
                    if not str(segment["story_importance_en"]).strip():
                        raise ValueError(f"Japanese story term lacks importance: {path}")
                    focus, reason = "lookup", "story_term"
                elif story_role == "story_term":
                    # Story vocabulary is an exception mechanism, not a badge
                    # for every noun that participates in the plot.  A known
                    # level word such as 本 remains ordinary target vocabulary.
                    story_role = "none"
                    story_importance = ""
            published_segments.append({
                "text": surface,
                "type": kind,
                "reading": segment["surface_kana"],
                "lemma": segment["lemma"],
                "lemma_reading": segment["lemma_kana"],
                "part_of_speech": segment["part_of_speech"],
                "conjugation_form": segment["conjugation_form"],
                "meaning_en": segment["meaning_en"],
                "story_role": story_role,
                "story_importance_en": story_importance,
                "target_curriculum_level": target_level,
                "curriculum_status": status,
                "matched_curriculum_level": matched,
                "learning_focus": focus,
                "lookup_reason": reason,
                "grammar_candidate_keys": grammar_keys,
                "dictionary_key": segment["dictionary_key"],
                "dictionary_definition_en": segment["dictionary_definition_en"],
                "form_steps": segment["form_steps"],
            })
            offset = end
        if "".join(segment["text"] for segment in published_segments) != chapter["content"]:
            raise ValueError(f"Japanese segments do not reconstruct chapter {number}: {path}")

        published_overlays = []
        for overlay in overlays:
            if not isinstance(overlay, dict) or set(overlay) != overlay_fields:
                raise ValueError(f"invalid Japanese grammar overlay in chapter {number}: {path}")
            start, end = overlay["start"], overlay["end"]
            if (
                not isinstance(start, int) or not isinstance(end, int)
                or not 0 <= start < end <= len(chapter["content"])
                or chapter["content"][start:end] != overlay["surface"]
                or not re.fullmatch(r"[a-z][a-z0-9_.-]*", overlay["grammar_candidate_key"])
            ):
                raise ValueError(f"Japanese grammar overlay does not reconstruct: {path}")
            cursor = 0
            numeric_comma = is_numeric_comma_construction(overlay["surface"])
            for component in overlay["components"]:
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
                    raise ValueError(f"invalid Japanese grammar component: {path}")
                has_authored_dictionary_link = (
                    "dictionary_key" in component
                    and "dictionary_definition_en" in component
                )
                component.setdefault("lookup_kind", "none")
                component.setdefault("dictionary_key", "")
                component.setdefault("dictionary_definition_en", "")
                if component["lookup_kind"] not in {"lexical", "grammar", "none"}:
                    raise ValueError(f"invalid Japanese component lookup kind: {path}")
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
                    raise ValueError(
                        f"invalid Japanese component dictionary link ({link_issue}): {path}"
                    )
                component_start, component_end = component["start"], component["end"]
                if (
                    (
                        component_start != cursor
                        and not (
                            numeric_comma
                            and component_start > cursor
                            and set(overlay["surface"][cursor:component_start]) == {"、"}
                        )
                    )
                    or overlay["surface"][component_start:component_end] != component["surface"]
                ):
                    raise ValueError(f"Japanese grammar components do not reconstruct: {path}")
                cursor = component_end
            if cursor != len(overlay["surface"]) and not (
                numeric_comma
                and overlay["surface"][cursor:]
                and set(overlay["surface"][cursor:]) == {"、"}
            ):
                raise ValueError(f"Japanese grammar components do not cover form: {path}")
            published_overlays.append({**overlay, "text": overlay["surface"]})

        relative = Path("annotations") / f"japanese_{book_id}_{level_key}_{number:03d}.json"
        destination = ASSET_ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "language": "japanese", "text": chapter["content"],
            "segments": published_segments, "grammar_overlays": published_overlays,
            "annotation_audit": audit,
        }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        assets[number - 1] = f"assets/{relative.as_posix()}"
    return assets


def chapters_from_markdown(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    headers = [(index, match.group(1).strip()) for index, line in enumerate(lines)
               if (match := HEADER.match(line))]
    chapters = []
    for position, (start, title) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        body_lines = lines[start + 1:end]
        while body_lines and (not body_lines[0].strip() or body_lines[0].strip() == "---"):
            body_lines.pop(0)
        while body_lines and (not body_lines[-1].strip() or body_lines[-1].strip() == "---"):
            body_lines.pop()
        content = "\n".join(body_lines).strip()
        if not content:
            raise ValueError(f"empty chapter {title!r} in {path}")
        chapters.append({"title": title, "content": content})
    if not chapters:
        raise ValueError(f"no chapters found in {path}")
    return chapters


def load_annotations(
    path: Path, chapters: list[dict[str, str]], *, language: str,
    book_id: str, level_key: str, require_complete: bool = True,
) -> list[str]:
    if language == "japanese":
        return load_japanese_annotations(
            path, chapters, book_id=book_id, level_key=level_key,
            require_complete=require_complete,
        )
    if language == "korean":
        return load_korean_annotations(path, chapters, book_id=book_id,
                                       level_key=level_key,
                                       require_complete=require_complete)
    if not path.is_file():
        if not require_complete:
            return ["" for _ in chapters]
        raise ValueError(f"reviewed agent annotations missing: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(document, dict)
            or document.get("schema_version") not in {1, 2, 3, 4, 5, 6}):
        raise ValueError(f"invalid annotation document: {path}")
    schema_version = document["schema_version"]
    if language == "chinese" and schema_version < 3:
        raise ValueError(
            f"Chinese annotations lack reviewed learning-focus metadata: {path}"
        )
    annotated = document.get("chapters")
    if not isinstance(annotated, list):
        raise ValueError(f"annotation chapter count does not match prose: {path}")
    items_by_number: dict[int, dict[str, Any]] = {}
    for item in annotated:
        if not isinstance(item, dict) or not isinstance(item.get("number"), int):
            raise ValueError(f"invalid annotation chapter entry: {path}")
        number = item["number"]
        if number in items_by_number or not 1 <= number <= len(chapters):
            raise ValueError(f"invalid or duplicate annotation chapter {number}: {path}")
        items_by_number[number] = item
    if require_complete and set(items_by_number) != set(range(1, len(chapters) + 1)):
        raise ValueError(f"annotation chapter count does not match prose: {path}")

    assets = ["" for _ in chapters]
    for number, item in sorted(items_by_number.items()):
        chapter = chapters[number - 1]
        if item.get("text") != chapter["content"]:
            raise ValueError(f"annotation text differs at chapter {number}: {path}")
        segments = item.get("segments")
        overlays = item.get("grammar_overlays")
        audit = item.get("annotation_audit")
        if (not isinstance(segments, list) or not segments
                or not isinstance(overlays, list)
                or not isinstance(audit, dict) or audit.get("all_reviewed") is not True):
            raise ValueError(f"unreviewed or malformed chapter {number}: {path}")
        expected_segment = {"text", "type", "pinyin", "meaning_en"}
        if schema_version >= 2:
            expected_segment |= {
                "story_term", "story_term_meaning_en",
                "story_term_importance_en", "target_hsk_level",
                "hsk_status", "matched_hsk_level", "hsk_evidence",
                "learning_focus", "lookup_reason",
            }
        if schema_version >= 3:
            expected_segment |= {
                "character_hsk_level", "focus_review_note_en",
            }
        if schema_version == 4:
            expected_segment |= {"explanation_zh", "example_zh"}
        if schema_version >= 5:
            expected_segment |= {"composition_en", "subsegments"}
        segment_types = {"word", "particle", "name", "idiom", "punctuation"}
        for segment in segments:
            if (not isinstance(segment, dict) or set(segment) != expected_segment
                    or segment.get("type") not in segment_types):
                raise ValueError(f"invalid segment in chapter {number}: {path}")
            lexical = segment["type"] != "punctuation"
            if lexical and (not str(segment["pinyin"]).strip()
                            or not str(segment["meaning_en"]).strip()):
                raise ValueError(f"empty agent explanation in chapter {number}: {path}")
            if not lexical and (segment["pinyin"] or segment["meaning_en"]):
                raise ValueError(f"punctuation has metadata in chapter {number}: {path}")
            if schema_version >= 2:
                if (segment.get("hsk_status") not in {
                        "in_level", "above_level", "unlisted", "not_applicable"
                    } or segment.get("learning_focus") not in {
                        "target", "lookup", "not_applicable"
                    } or not isinstance(segment.get("target_hsk_level"), int)
                    or (segment.get("matched_hsk_level") is not None
                        and not isinstance(segment.get("matched_hsk_level"), int))):
                    raise ValueError(
                        f"invalid deterministic focus metadata in chapter {number}: {path}"
                    )
                if lexical and not str(segment.get("hsk_evidence", "")).strip():
                    raise ValueError(f"missing HSK evidence in chapter {number}: {path}")
                if not lexical and (
                    segment.get("hsk_status") != "not_applicable"
                    or segment.get("learning_focus") != "not_applicable"
                ):
                    raise ValueError(
                        f"punctuation has learning focus in chapter {number}: {path}"
                    )
                if schema_version >= 3 and (
                    segment.get("character_hsk_level") is not None
                    and not isinstance(segment.get("character_hsk_level"), int)
                ):
                    raise ValueError(
                        f"invalid character evidence in chapter {number}: {path}"
                    )
                if schema_version == 4:
                    has_help = bool(
                        str(segment.get("explanation_zh", "")).strip()
                        or str(segment.get("example_zh", "")).strip()
                    )
                    help_required = (
                        lexical
                        and segment.get("learning_focus") == "lookup"
                        and segment.get("lookup_reason") != "proper_name"
                        and segment.get("type") != "name"
                    )
                    if help_required != has_help:
                        raise ValueError(
                            f"invalid Chinese-first help selection in chapter "
                            f"{number}: {path}"
                        )
                if schema_version >= 5:
                    composition = segment.get("composition_en")
                    parts = segment.get("subsegments")
                    if not isinstance(composition, str) or not isinstance(parts, list):
                        raise ValueError(
                            f"invalid subsegment metadata in chapter {number}: {path}"
                        )
                    if parts:
                        cursor = 0
                        surface = str(segment["text"])
                        if len(parts) < 2 or not composition.strip():
                            raise ValueError(
                                f"incomplete subsegment metadata in chapter {number}: {path}"
                            )
                        for part in parts:
                            if not isinstance(part, dict) or set(part) != {
                                "start", "end", "text", "meaning_en"
                            }:
                                raise ValueError(
                                    f"invalid subsegment in chapter {number}: {path}"
                                )
                            start, end = part["start"], part["end"]
                            if (
                                not isinstance(start, int)
                                or not isinstance(end, int)
                                or start != cursor
                                or surface[start:end] != part["text"]
                                or not isinstance(part["meaning_en"], str)
                                or not part["meaning_en"].strip()
                            ):
                                raise ValueError(
                                    f"subsegments do not reconstruct in chapter {number}: {path}"
                                )
                            cursor = end
                        if cursor != len(surface):
                            raise ValueError(
                                f"subsegments do not cover surface in chapter {number}: {path}"
                            )
        if "".join(str(segment["text"]) for segment in segments) != chapter["content"]:
            raise ValueError(f"segments do not reconstruct chapter {number}: {path}")
        expected_overlay = {
            "start", "end", "text", "grammar_candidate_key", "pattern", "meaning_en"
        }
        for overlay in overlays:
            if not isinstance(overlay, dict) or set(overlay) != expected_overlay:
                raise ValueError(f"invalid grammar overlay in chapter {number}: {path}")
            start, end = overlay["start"], overlay["end"]
            if (not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start < end <= len(chapter["content"])
                    or chapter["content"][start:end] != overlay["text"]
                    or not isinstance(overlay["grammar_candidate_key"], str)
                    or not re.fullmatch(
                        r"[a-z][a-z0-9_.-]*", overlay["grammar_candidate_key"]
                    )):
                raise ValueError(f"grammar overlay does not reconstruct chapter {number}: {path}")

        relative = Path("annotations") / (
            f"{language}_{book_id}_{level_key}_{number:03d}.json"
        )
        destination = ASSET_ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        published_segments = []
        segment_start = 0
        for segment in segments:
            segment_end = segment_start + len(str(segment["text"]))
            grammar_candidate_keys = sorted({
                str(overlay["grammar_candidate_key"])
                for overlay in overlays
                if overlay["start"] < segment_end and segment_start < overlay["end"]
            })
            published = {
                key: value for key, value in segment.items()
                if key not in {"explanation_zh", "example_zh"}
            }
            published["grammar_candidate_keys"] = grammar_candidate_keys
            published_segments.append(published)
            segment_start = segment_end
        payload: dict[str, Any] = {
            "text": chapter["content"],
            "segments": published_segments,
            "grammar_overlays": overlays,
        }
        if schema_version >= 2:
            focus_audit = item.get("learning_focus_audit")
            if (not isinstance(focus_audit, dict)
                    or focus_audit.get("role_assignment_reviewed") is not True):
                raise ValueError(
                    f"unreviewed learning-focus metadata in chapter {number}: {path}"
                )
            if (schema_version >= 3
                    and focus_audit.get("semantic_focus_reviewed") is not True):
                raise ValueError(
                    f"unreviewed semantic focus in chapter {number}: {path}"
                )
            payload["learning_focus_audit"] = focus_audit
        if schema_version >= 5:
            subsegment_audit = item.get("subsegment_audit")
            if (
                not isinstance(subsegment_audit, dict)
                or subsegment_audit.get("all_reviewed") is not True
            ):
                raise ValueError(
                    f"unreviewed subsegment metadata in chapter {number}: {path}"
                )
            payload["subsegment_audit"] = subsegment_audit
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        assets[number - 1] = f"assets/{relative.as_posix()}"
    return assets


def load_korean_annotations(
    path: Path, chapters: list[dict[str, str]], *, book_id: str,
    level_key: str, require_complete: bool,
) -> list[str]:
    """Publish reviewed Korean tap units without Chinese/Japanese lookup guesses."""
    if not path.is_file():
        if not require_complete:
            return ["" for _ in chapters]
        raise ValueError(f"reviewed Korean annotations missing: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or document.get("language") != "korean":
        raise ValueError(f"invalid Korean annotation document: {path}")
    annotated = document.get("chapters")
    if not isinstance(annotated, list):
        raise ValueError(f"invalid Korean annotation chapters: {path}")
    by_number = {item.get("number"): item for item in annotated if isinstance(item, dict)}
    if len(by_number) != len(annotated) or any(
        not isinstance(n, int) or not 1 <= n <= len(chapters) for n in by_number
    ):
        raise ValueError(f"invalid Korean annotation numbering: {path}")
    if require_complete and set(by_number) != set(range(1, len(chapters) + 1)):
        raise ValueError(f"Korean annotation chapter count does not match prose: {path}")
    assets = ["" for _ in chapters]
    for number, item in sorted(by_number.items()):
        chapter = chapters[number - 1]
        segments = item.get("segments")
        if (item.get("text") != chapter["content"] or not isinstance(segments, list)
                or not segments or item.get("annotation_audit", {}).get("all_reviewed") is not True):
            raise ValueError(f"unreviewed Korean chapter {number}: {path}")
        if "".join(segment.get("text", "") for segment in segments) != chapter["content"]:
            raise ValueError(f"Korean segments do not reconstruct chapter {number}: {path}")
        published = []
        for segment in segments:
            if set(segment) != {"text", "type", "meaning_en"}:
                raise ValueError(f"invalid Korean segment: {path}")
            kind = segment["type"]
            if kind not in {"word", "punctuation"} or (kind == "word") != bool(segment["meaning_en"].strip()):
                raise ValueError(f"invalid Korean segment meaning: {path}")
            published.append({**segment, "pinyin": "", "learning_focus":
                              "not_applicable" if kind == "punctuation" else "target"})
        relative = Path("annotations") / f"korean_{book_id}_{level_key}_{number:03d}.json"
        destination = ASSET_ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "language": "korean", "text": chapter["content"],
            "segments": published, "grammar_overlays": [],
            "annotation_audit": item["annotation_audit"],
        }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        assets[number - 1] = f"assets/{relative.as_posix()}"
    return assets


def build_language(
    language: str, levels: dict[str, int], *, require_complete_annotations: bool = True
) -> list[dict]:
    entries = []
    language_root = CONTENT_ROOT / language
    if not language_root.exists():
        return entries
    for book_dir in sorted(path for path in language_root.iterdir() if path.is_dir()):
        metadata_path = book_dir / "metadata.json"
        if not metadata_path.is_file():
            raise ValueError(f"missing metadata: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        book_id = str(metadata["id"])
        enabled_levels = metadata.get("enabled_levels")
        if enabled_levels is not None and (
            not isinstance(enabled_levels, list)
            or not enabled_levels
            or any(level not in levels for level in enabled_levels)
        ):
            raise ValueError(f"invalid enabled_levels: {metadata_path}")
        for level_key, internal_level in levels.items():
            if enabled_levels is not None and level_key not in enabled_levels:
                continue
            source = book_dir / f"{level_key}.md"
            if not source.exists():
                continue
            chapters = chapters_from_markdown(source)
            annotation_assets = load_annotations(
                book_dir / f"{level_key}.annotations.json", chapters,
                language=language, book_id=book_id, level_key=level_key,
                require_complete=require_complete_annotations,
            )
            if not require_complete_annotations:
                ready = [
                    (chapter, annotation_asset)
                    for chapter, annotation_asset in zip(
                        chapters, annotation_assets
                    )
                    if annotation_asset
                ]
                if not ready:
                    continue
                chapters = [chapter for chapter, _ in ready]
                annotation_assets = [asset for _, asset in ready]
            for chapter, annotation_asset in zip(chapters, annotation_assets):
                chapter["annotationAsset"] = annotation_asset
            entries.append({
                "id": f"{book_id}_{level_key}",
                "book": book_id,
                "bookTitle": str(metadata["title_native"]),
                "bookTitleEn": str(metadata["title_en"]),
                "level": internal_level,
                "chapters": chapters,
            })
    return sorted(entries, key=lambda item: (item["level"], item["id"]))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-partial-annotations", action="store_true",
        help="emit only chapters with ready reviewed sidecars",
    )
    args = parser.parse_args()
    # Validate the pilot's independent tap boundaries before replacing assets.
    # Source edits require an agent update, never silent lexical fragmentation.
    from pipeline.chinese_reading_units import (
        DECISIONS as unit_decisions, OUTPUT as unit_output, build as build_units,
    )
    from pipeline.annotate_chinese import atomic_json
    unit_payload = build_units(json.loads(unit_decisions.read_text()))
    from pipeline.usage_dictionary import (
        DECISIONS as dictionary_decisions, OUTPUT as dictionary_output, build_published,
    )
    dictionary_payload = build_published(json.loads(dictionary_decisions.read_text()))
    from pipeline import dictionary_corpus
    if dictionary_corpus.MANIFEST.exists():
        dictionary_payload = dictionary_corpus.build(publish=False)
        _, unit_payload = dictionary_corpus.editorial_input()
    from pipeline import japanese_usage_dictionary
    japanese_payload = (japanese_usage_dictionary.build()
                        if japanese_usage_dictionary.SENSES.exists() else None)
    from pipeline import japanese_grammar_dictionary
    grammar_payload = (japanese_grammar_dictionary.build()
                       if japanese_grammar_dictionary.REGISTRY.exists() else None)
    from pipeline import japanese_sentence_breakdowns
    sentence_breakdown_payload = japanese_sentence_breakdowns.build()
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    annotation_root = ASSET_ROOT / "annotations"
    if annotation_root.exists():
        for generated in annotation_root.rglob("*.json"):
            generated.unlink()
    for language, (levels, filename) in LANGUAGES.items():
        entries = build_language(
            language, levels,
            require_complete_annotations=not args.allow_partial_annotations,
        )
        destination = ASSET_ROOT / filename
        destination.write_text(
            json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        chapters = sum(len(entry["chapters"]) for entry in entries)
        print(f"Wrote {destination}: {len(entries)} readers, {chapters} chapters")
    atomic_json(unit_output, unit_payload)
    atomic_json(dictionary_output, dictionary_payload)
    if japanese_payload is not None:
        atomic_json(japanese_usage_dictionary.OUTPUT, japanese_payload)
    if grammar_payload is not None:
        atomic_json(japanese_grammar_dictionary.OUTPUT, grammar_payload)
    atomic_json(japanese_sentence_breakdowns.OUTPUT, sentence_breakdown_payload)


if __name__ == "__main__":
    main()
