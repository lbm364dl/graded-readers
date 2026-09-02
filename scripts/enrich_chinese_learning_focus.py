#!/usr/bin/env python3
"""Add agent-audited story roles and deterministic HSK focus to a reader."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.agent_harness import CodexRunner, SCHEMAS
from pipeline.chinese_readability import enrich_reviewed_segments


FOCUS_FIELDS = {
    "story_term",
    "story_term_meaning_en",
    "story_term_importance_en",
    "target_hsk_level",
    "hsk_status",
    "matched_hsk_level",
    "character_hsk_level",
    "hsk_evidence",
    "learning_focus",
    "lookup_reason",
    "focus_review_note_en",
    "explanation_zh",
    "example_zh",
}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def contains_han(value: str) -> bool:
    return any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        for character in value
    )


def clean_segments(raw_segments: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("reader has no segments")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, dict):
            raise ValueError(f"segment {index} is not an object")
        result.append({key: value for key, value in raw.items() if key not in FOCUS_FIELDS})
    return result


def segment_listing(segments: list[dict[str, Any]], *, include_focus: bool) -> str:
    rows = []
    for index, segment in enumerate(segments):
        row: dict[str, Any] = {
            "index": index,
            "text": segment["text"],
            "type": segment["type"],
            "meaning_en": segment["meaning_en"],
        }
        if include_focus:
            row.update({
                "hsk_status": segment["hsk_status"],
                "matched_hsk_level": segment["matched_hsk_level"],
                "character_hsk_level": segment["character_hsk_level"],
                "hsk_evidence": segment["hsk_evidence"],
                "learning_focus": segment["learning_focus"],
                "lookup_reason": segment["lookup_reason"],
                "story_term": segment["story_term"],
            })
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def normalize_role_spans(
    result: dict[str, Any], segments: list[dict[str, Any]]
) -> int:
    """Repair index-only slips when the agent supplied exact unambiguous text.

    The model does not get to change boundaries here.  We map its exact phrase
    back onto the already reviewed segments, preferring a match which begins at
    the supplied start and otherwise the nearest exact match.
    """
    corrections = 0
    for item in result.get("story_terms", []):
        start, end, expected = (
            item["start_segment"], item["end_segment"], item["text"]
        )
        if (0 <= start < end <= len(segments)
                and "".join(str(segments[index]["text"])
                            for index in range(start, end)) == expected):
            continue
        matches: list[tuple[int, int]] = []
        for candidate_start in range(len(segments)):
            surface = ""
            for candidate_end in range(candidate_start + 1, len(segments) + 1):
                if segments[candidate_end - 1]["type"] == "punctuation":
                    break
                surface += str(segments[candidate_end - 1]["text"])
                if surface == expected:
                    matches.append((candidate_start, candidate_end))
                    break
                if len(surface) >= len(expected) or not expected.startswith(surface):
                    break
        if not matches:
            raise ValueError(
                f"story-term text is not an exact segment span: {expected!r}"
            )
        normalized_start, normalized_end = min(
            matches,
            key=lambda match: (
                match[0] != start,
                abs(match[0] - start),
                abs(match[1] - end),
                match,
            ),
        )
        item["start_segment"] = normalized_start
        item["end_segment"] = normalized_end
        corrections += 1
    result.get("story_terms", []).sort(
        key=lambda item: (
            item["start_segment"], item["end_segment"], item["text"]
        )
    )
    return corrections


def normalize_nested_story_terms(result: dict[str, Any]) -> int:
    """Prefer the minimal lexical term when agent-selected surfaces nest.

    Story terms are later expanded to every exact occurrence. A phrase such as
    ``一起去投军`` therefore cannot coexist with its lexical core ``投军`` even
    when the proposal happened to mark different occurrences. Keeping the
    shortest surface preserves the reusable lookup unit and prevents
    canonical overlays from colliding.
    """
    ordered = sorted(
        result.get("story_terms", []),
        key=lambda item: (len(item["text"]), item["start_segment"], item["text"]),
    )
    kept: list[dict[str, Any]] = []
    for item in ordered:
        if any(existing["text"] in item["text"] for existing in kept):
            continue
        kept.append(item)
    kept.sort(
        key=lambda item: (
            item["start_segment"], item["end_segment"], item["text"]
        )
    )
    dropped = len(result.get("story_terms", [])) - len(kept)
    result["story_terms"] = kept
    return dropped


def validate_roles(
    result: dict[str, Any], segments: list[dict[str, Any]], *, verifier: bool
) -> None:
    if verifier and result.get("verdict") not in {"pass", "revised"}:
        raise ValueError("focus verifier returned an invalid verdict")
    occupied: set[int] = set()
    previous_start = -1
    for item in result.get("story_terms", []):
        start, end = item["start_segment"], item["end_segment"]
        if not 0 <= start < end <= len(segments):
            raise ValueError(f"story-term range is invalid: {start}:{end}")
        if start < previous_start:
            raise ValueError("story terms must be ordered by segment index")
        previous_start = start
        indices = set(range(start, end))
        if occupied & indices:
            raise ValueError(f"overlapping story-term range: {start}:{end}")
        if any(segments[index]["type"] == "punctuation" for index in indices):
            raise ValueError(f"story term contains punctuation: {start}:{end}")
        surface = "".join(str(segments[index]["text"]) for index in range(start, end))
        if surface != item["text"]:
            raise ValueError(
                f"story term does not reconstruct at {start}:{end}: "
                f"{item['text']!r} != {surface!r}"
            )
        if end - start > 1 and any(
            str(segments[index]["text"]) in {
                "我", "我们", "你", "你们", "他", "他们", "她", "她们",
            }
            for index in indices
        ):
            raise ValueError(
                f"story term contains clause-level personal pronoun: {surface!r}"
            )
        if contains_han(str(item.get("meaning_en", ""))):
            raise ValueError(
                f"story-term English meaning contains Han text: {item['text']!r}"
            )
        if contains_han(str(item.get("importance_en", ""))):
            raise ValueError(
                f"story-term English importance contains Han text: {item['text']!r}"
            )
        occupied.update(indices)

    corrected: set[int] = set()
    for item in result.get("name_corrections", []):
        index = item["segment_index"]
        if not 0 <= index < len(segments) or index in corrected:
            raise ValueError(f"invalid or duplicate name correction: {index}")
        if segments[index]["type"] == "punctuation":
            raise ValueError(f"cannot retype punctuation segment {index}")
        corrected.add(index)


def normalize_and_validate_roles(
    result: dict[str, Any], segments: list[dict[str, Any]], *, verifier: bool
) -> tuple[int, int]:
    span_normalizations = normalize_role_spans(result, segments)
    nested_term_drops = normalize_nested_story_terms(result)
    validate_roles(result, segments, verifier=verifier)
    return span_normalizations, nested_term_drops


def apply_roles(
    base_segments: list[dict[str, Any]], roles: dict[str, Any], level: str
) -> list[dict[str, Any]]:
    segments = [dict(segment) for segment in base_segments]
    for correction in roles["name_corrections"]:
        segments[correction["segment_index"]]["type"] = correction["correct_type"]
    for segment in segments:
        segment["story_term"] = ""
        segment["story_term_meaning_en"] = ""
        segment["story_term_importance_en"] = ""
    canonical_terms: dict[str, dict[str, Any]] = {}
    for term in roles["story_terms"]:
        canonical_terms.setdefault(term["text"], term)
    occupied: dict[int, str] = {}
    for surface, term in canonical_terms.items():
        for start in range(len(segments)):
            combined = ""
            for end in range(start + 1, len(segments) + 1):
                if segments[end - 1]["type"] == "punctuation":
                    break
                combined += str(segments[end - 1]["text"])
                if combined == surface:
                    for index in range(start, end):
                        existing = occupied.get(index)
                        if existing is not None and existing != surface:
                            raise ValueError(
                                f"overlapping canonical story terms: "
                                f"{existing!r} and {surface!r}"
                            )
                        occupied[index] = surface
                        segments[index]["story_term"] = surface
                        segments[index]["story_term_meaning_en"] = term["meaning_en"]
                        segments[index]["story_term_importance_en"] = term["importance_en"]
                    break
                if len(combined) >= len(surface) or not surface.startswith(combined):
                    break
    return enrich_reviewed_segments(segments, level)


def apply_planned_story_term_semantics(
    roles: dict[str, Any], focus_plan: dict[str, Any]
) -> int:
    """Keep selected story-term meanings aligned with the reviewed plan."""
    planned = {
        str(item.get("surface", "")): str(item.get("meaning_en", ""))
        for item in focus_plan.get("story_terms", [])
        if str(item.get("surface", "")) and str(item.get("meaning_en", ""))
    }
    corrections = 0
    for item in roles.get("story_terms", []):
        meaning = planned.get(str(item.get("text", "")))
        if meaning is not None and item.get("meaning_en") != meaning:
            item["meaning_en"] = meaning
            corrections += 1
    return corrections


def role_prompt(text: str, segments: list[dict[str, Any]], level: str) -> str:
    return f"""You are the narrative-role editor for a Chinese graded reader.

TEXT:
{text}

TARGET: {level.upper()}
EXACT REVIEWED SEGMENTS (zero-based):
{segment_listing(segments, include_focus=False)}

Do not rewrite, merge, split, or reorder any segment. Identify only the small
set of chapter-specific words or contiguous phrases that a learner needs in
order to follow this episode: named factions/groups, ritual or historical
concepts, and recurring plot vocabulary. Do not mark ordinary actions,
relations, numbers, generic objects, or every unfamiliar word merely because
it is above {level.upper()}. A story term may span adjacent lexical segments (for example
two separately annotated characters); its text must be their exact
concatenation. `start_segment` is included and `end_segment` is excluded, so a
one-segment term at index 12 uses start 12 and end 13. Give a concise contextual
English meaning and one concise reason it matters in this chapter.
Choose the smallest reusable lexical or fixed phrase, normally no more than
eight Han characters. Never select a sentence, a whole oath clause, or any span
containing punctuation. If an important sentence contains several terms, return
the useful punctuation-free core term(s) separately.

The existing `type` values were assigned by the annotation agent. Return a
name correction only when a segment is clearly a proper name but is not typed
`name`, or is typed `name` but is actually a common word. Return corrections
relative to the supplied list, not a full type inventory. HSK membership is
not your task and must not influence your story-term selection.
"""


def verifier_prompt(
    text: str,
    enriched: list[dict[str, Any]],
    proposal: dict[str, Any],
    level: str,
) -> str:
    return f"""You are the final independent verifier for learning-focus
metadata in a Chinese graded reader.

TEXT:
{text}

TARGET: {level.upper()}
SEGMENTS WITH DETERMINISTIC HSK EVIDENCE:
{segment_listing(enriched, include_focus=True)}

FIRST AGENT'S PROPOSAL:
{json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))}

Audit only (a) proper-name typing and (b) which small set of exact contiguous
phrases are genuinely important chapter-specific story vocabulary. Do not
change segment boundaries, pinyin, meanings, or any `hsk_*` decision. HSK
membership is mechanical evidence and cannot be overridden by this review.
Above-level or unlisted does not automatically make something a story term.

Return the complete final story-term list and the complete correction list,
both relative to the original supplied segment types. Use verdict `pass` if
they are unchanged and `revised` if you alter them. Segment ranges use an
included start and excluded end. Story terms must be reusable lexical units,
not quotations or clause fragments: exclude personal pronouns and grammatical
scaffolding such as 已经 when narrowing a meaningful sentence to its core term.
Keep notes concise.
"""


def semantic_focus_prompt(
    text: str,
    segments: list[dict[str, Any]],
    level: str,
    focus_plan: dict[str, Any] | None = None,
    operator_issues: list[dict[str, Any]] | None = None,
    operator_focus_issues: list[dict[str, Any]] | None = None,
) -> str:
    planned_guidance = json.dumps(
        (focus_plan or {}).get("story_terms", []),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    operator_guidance = json.dumps(
        operator_issues or [], ensure_ascii=False, separators=(",", ":")
    )
    operator_focus_guidance = json.dumps(
        operator_focus_issues or [],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""You are the final semantic auditor for Chinese HSK learning
focus. The HSK word-list membership below was computed mechanically and cannot
be changed by you.

TEXT:
{text}

TARGET: {level.upper()}
REVIEWED SEGMENTS WITH WORD-LEVEL EVIDENCE:
{segment_listing(segments, include_focus=True)}

REVIEWED CHAPTER-SPECIFIC SEMANTIC GUIDANCE:
{planned_guidance}

OPERATOR-IDENTIFIED OBJECTIVE METADATA FAILURES:
{operator_guidance}

For every operator-identified failure, include a `metadata_corrections` item
for that exact segment index and fix the stated factual problem. These are
scoped audit findings, not permission to change unrelated metadata.

OPERATOR-IDENTIFIED OBJECTIVE FOCUS FAILURES:
{operator_focus_guidance}

For every operator-identified focus failure, include a `focus_exceptions` item
for that exact segment index. Use this only when treating the contextual sense
as ordinary target-level vocabulary would be pedagogically misleading.

Review segments currently marked `learning_focus: target`. Return an
exception only when studying that segment as {level.upper()} vocabulary would
be pedagogically misleading because its contextual sense is more advanced
than the listed word, its accepted decomposition is not semantically
transparent here, or its boundary hides an advanced lexical unit. Put those in
`focus_exceptions`.

Separately, audit metadata for every target plus every segment that belongs to
a `story_term` or proper name. Put an item in `metadata_corrections` when its
type, pinyin, or contextual English meaning is objectively malformed or wrong.
This includes a component whose dictionary gloss becomes false inside a
historical name or weapon phrase; give its meaning in this exact context.
Supply the complete corrected metadata. A metadata problem does not by itself
make an in-level word lookup-only. Do not audit ordinary lookup-only words,
stylistic preferences, or a word merely because the surrounding story is
historical. Do not promote any lookup item to target and do not claim that an
HSK list entry is absent when the evidence says it is present. Conservative
exceptions only; an empty list is preferred when the mechanical target label
and metadata are genuinely useful.
"""


def validate_semantic_review(
    review: dict[str, Any], segments: list[dict[str, Any]]
) -> None:
    exceptions = review.get("focus_exceptions")
    corrections = review.get("metadata_corrections")
    if review.get("verdict") == "pass" and (exceptions or corrections):
        raise ValueError("semantic focus pass cannot contain revisions")
    if review.get("verdict") == "revisions" and not (exceptions or corrections):
        raise ValueError("semantic focus revisions verdict is empty")
    seen_exceptions: set[int] = set()
    for item in exceptions or []:
        index = item["segment_index"]
        if not 0 <= index < len(segments) or index in seen_exceptions:
            raise ValueError(f"invalid or duplicate semantic exception: {index}")
        segment = segments[index]
        if item["surface"] != segment["text"]:
            raise ValueError(f"semantic exception surface mismatch at {index}")
        if segment["learning_focus"] != "target":
            raise ValueError(f"semantic exception is not target-level at {index}")
        seen_exceptions.add(index)
    seen_corrections: set[int] = set()
    for item in corrections or []:
        index = item["segment_index"]
        if not 0 <= index < len(segments) or index in seen_corrections:
            raise ValueError(f"invalid or duplicate metadata correction: {index}")
        segment = segments[index]
        if (item["surface"] != segment["text"]
                or segment["type"] == "punctuation"):
            raise ValueError(f"metadata correction surface mismatch at {index}")
        seen_corrections.add(index)


def apply_semantic_review(
    segments: list[dict[str, Any]], review: dict[str, Any], level: str
) -> list[dict[str, Any]]:
    corrected = [dict(segment) for segment in segments]
    for item in review["metadata_corrections"]:
        segment = corrected[item["segment_index"]]
        segment["type"] = item["correct_type"]
        segment["pinyin"] = item["correct_pinyin"]
        segment["meaning_en"] = item["correct_meaning_en"]
    # A type repair can affect name handling; always rerun the mechanical layer
    # before applying the conservative semantic demotions.
    result = [
        dict(segment, focus_review_note_en="")
        for segment in enrich_reviewed_segments(corrected, level)
    ]
    for item in review["focus_exceptions"]:
        segment = result[item["segment_index"]]
        segment["learning_focus"] = "lookup"
        segment["lookup_reason"] = "semantic_exception"
        segment["focus_review_note_en"] = item["explanation_en"]
    return result


async def enrich(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    reader_path = run_dir / "reader.json"
    report_path = run_dir / "annotation-report.json"
    reader = load_object(reader_path)
    report = load_object(report_path)
    text = str(reader.get("text", ""))
    level = args.level.lower()
    if not text or "".join(str(item.get("text", "")) for item in reader.get("segments", [])) != text:
        raise ValueError("reader segments do not reconstruct reader text")
    base_segments = clean_segments(reader["segments"])
    focus_plan_path = run_dir / "focus-vocabulary-plan.json"
    focus_plan = (
        load_object(focus_plan_path)
        if focus_plan_path.is_file() else {"story_terms": []}
    )
    operator_issues: list[dict[str, Any]] = []
    for value in args.audit_segment:
        raw_index, separator, problem = value.partition("=")
        if not separator or not raw_index.isdigit() or not problem.strip():
            raise ValueError(f"invalid --audit-segment: {value}")
        index = int(raw_index)
        if not 0 <= index < len(base_segments):
            raise ValueError(f"invalid --audit-segment index: {index}")
        operator_issues.append({
            "segment_index": index,
            "surface": str(base_segments[index]["text"]),
            "problem": problem.strip(),
        })
    operator_focus_issues: list[dict[str, Any]] = []
    for value in args.audit_focus:
        raw_index, separator, problem = value.partition("=")
        if not separator or not raw_index.isdigit() or not problem.strip():
            raise ValueError(f"invalid --audit-focus: {value}")
        index = int(raw_index)
        if not 0 <= index < len(base_segments):
            raise ValueError(f"invalid --audit-focus index: {index}")
        operator_focus_issues.append({
            "segment_index": index,
            "surface": str(base_segments[index]["text"]),
            "problem": problem.strip(),
        })

    runner = CodexRunner(
        run_dir=run_dir,
        model=args.model,
        semaphore=asyncio.Semaphore(1),
        timeout=args.timeout,
    )
    proposal = await runner.call(
        "learning_focus/role_assignment",
        role_prompt(text, base_segments, level),
        SCHEMAS / "learning-focus-roles.schema.json",
        args.effort,
        refresh=args.refresh,
    )
    try:
        proposal_span_normalizations, proposal_nested_term_drops = (
            normalize_and_validate_roles(proposal, base_segments, verifier=False)
        )
    except ValueError as exc:
        proposal = await runner.call(
            "learning_focus/role_assignment_repair",
            role_prompt(text, base_segments, level)
            + "\n\nThe prior proposal failed deterministic validation: "
            + str(exc)
            + "\nReturn a complete corrected proposal. Remove or narrow the "
              "invalid span; never include punctuation in a story term.\n"
            + json.dumps(proposal, ensure_ascii=False),
            SCHEMAS / "learning-focus-roles.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        proposal_span_normalizations, proposal_nested_term_drops = (
            normalize_and_validate_roles(proposal, base_segments, verifier=False)
        )

    provisional = apply_roles(base_segments, proposal, level)
    verification = await runner.call(
        "learning_focus/verification",
        verifier_prompt(text, provisional, proposal, level),
        SCHEMAS / "learning-focus-verification.schema.json",
        args.effort,
        refresh=args.refresh,
    )
    try:
        verification_span_normalizations, verification_nested_term_drops = (
            normalize_and_validate_roles(verification, base_segments, verifier=True)
        )
    except ValueError as exc:
        verification = await runner.call(
            "learning_focus/verification_repair",
            verifier_prompt(text, provisional, proposal, level)
            + "\n\nThe prior verification failed deterministic validation: "
            + str(exc)
            + "\nReturn a complete corrected verification. Remove or narrow "
              "invalid story-term spans and include no punctuation.\n"
            + json.dumps(verification, ensure_ascii=False),
            SCHEMAS / "learning-focus-verification.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        verification_span_normalizations, verification_nested_term_drops = (
            normalize_and_validate_roles(verification, base_segments, verifier=True)
        )
    planned_semantic_corrections = apply_planned_story_term_semantics(
        verification, focus_plan
    )
    final_segments = apply_roles(base_segments, verification, level)
    semantic_review = await runner.call(
        "learning_focus/semantic_review",
        semantic_focus_prompt(
            text,
            final_segments,
            level,
            focus_plan,
            operator_issues,
            operator_focus_issues,
        ),
        SCHEMAS / "learning-focus-semantic-review.schema.json",
        args.effort,
        refresh=args.refresh,
    )
    validate_semantic_review(semantic_review, final_segments)
    corrected_indices = {
        int(item["segment_index"])
        for item in semantic_review["metadata_corrections"]
    }
    missing_operator_corrections = [
        item for item in operator_issues
        if item["segment_index"] not in corrected_indices
    ]
    if missing_operator_corrections:
        raise ValueError(
            "semantic review omitted operator metadata corrections: "
            + json.dumps(missing_operator_corrections, ensure_ascii=False)
        )
    focus_exception_indices = {
        int(item["segment_index"])
        for item in semantic_review["focus_exceptions"]
    }
    missing_operator_focus = [
        item for item in operator_focus_issues
        if item["segment_index"] not in focus_exception_indices
    ]
    if missing_operator_focus:
        raise ValueError(
            "semantic review omitted operator focus exceptions: "
            + json.dumps(missing_operator_focus, ensure_ascii=False)
        )
    final_segments = apply_semantic_review(final_segments, semantic_review, level)
    if "".join(str(item["text"]) for item in final_segments) != text:
        raise AssertionError("learning-focus enrichment changed source text")

    lexical = [item for item in final_segments if item["type"] != "punctuation"]
    counts = {
        key: sum(item["learning_focus"] == key for item in lexical)
        for key in ("target", "lookup")
    }
    status_counts = {
        key: sum(item["hsk_status"] == key for item in lexical)
        for key in ("in_level", "above_level", "unlisted")
    }
    unique_story_terms = {
        item["text"] for item in verification["story_terms"]
    }
    story_term_occurrences = sum(
        bool(item["story_term"])
        and (index == 0
             or final_segments[index - 1]["story_term"] != item["story_term"])
        for index, item in enumerate(final_segments)
    )
    reader["segments"] = final_segments
    reader["learning_focus_audit"] = {
        "schema_version": 2,
        "target": level,
        "classifier": "hsk3-word-segment-focus-v2",
        "model": args.model,
        "effort": args.effort,
        "role_assignment_reviewed": True,
        "verifier_verdict": verification["verdict"],
        "semantic_focus_reviewed": True,
        "semantic_review_verdict": semantic_review["verdict"],
        "semantic_exception_count": len(semantic_review["focus_exceptions"]),
        "semantic_metadata_correction_count": len(
            semantic_review["metadata_corrections"]
        ),
        "planned_story_semantic_correction_count": planned_semantic_corrections,
        "operator_semantic_flag_count": len(operator_issues),
        "operator_focus_flag_count": len(operator_focus_issues),
        "exact_span_normalizations": (
            proposal_span_normalizations + verification_span_normalizations
        ),
        "canonical_nested_term_drops": (
            proposal_nested_term_drops + verification_nested_term_drops
        ),
        "story_term_count": len(unique_story_terms),
        "story_term_occurrence_count": story_term_occurrences,
        "name_correction_count": len(verification["name_corrections"]),
        "learning_focus_counts": counts,
        "hsk_status_counts": status_counts,
        "verifier_notes": verification["notes"],
    }
    reader_path.write_text(
        json.dumps(reader, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report["reader_sha256"] = hashlib.sha256(reader_path.read_bytes()).hexdigest()
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(reader["learning_focus_audit"], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--level", default="hsk1")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--audit-segment", action="append", default=[], metavar="INDEX=PROBLEM",
        help="require a scoped semantic metadata correction for one segment",
    )
    parser.add_argument(
        "--audit-focus", action="append", default=[], metavar="INDEX=PROBLEM",
        help="require a scoped semantic lookup-only exception for one segment",
    )
    args = parser.parse_args()
    asyncio.run(enrich(args))


if __name__ == "__main__":
    main()
