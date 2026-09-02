#!/usr/bin/env python3
"""Add independently reviewed semantic subsegments to Chinese annotations."""

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
from pipeline.chinese_readability import classify_reviewed_compositional_segment


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def contains_han(text: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in text
    )


def apply_component_hsk_focus(
    segments: list[dict[str, Any]],
    grammar_overlays: list[dict[str, Any]],
    level: str,
) -> int:
    """Reclassify only reviewed constructions occupying one complete segment."""
    exact_grammar_spans = {
        (overlay.get("start"), overlay.get("end"))
        for overlay in grammar_overlays
        if isinstance(overlay, dict)
    }
    changed = 0
    offset = 0
    for segment in segments:
        surface = str(segment.get("text", ""))
        end = offset + len(surface)
        parts = segment.get("subsegments")
        if (
            segment.get("type") == "word"
            and isinstance(parts, list)
            and len(parts) >= 2
            and (offset, end) in exact_grammar_spans
            and all(isinstance(part, dict) for part in parts)
        ):
            component_surfaces = [str(part.get("text", "")) for part in parts]
            classification = classify_reviewed_compositional_segment(
                surface,
                str(segment["type"]),
                level,
                component_surfaces,
                is_story_term=bool(segment.get("story_term")),
            )
            if segment.get("lookup_reason") != "semantic_exception":
                segment.update(classification)
                changed += 1
        offset = end
    return changed


def lookup_inventory() -> tuple[set[str], set[str]]:
    dictionary = load_object(ROOT / "app/assets/dictionary.json")
    etymology = load_object(ROOT / "app/assets/etymology.json")
    return set(dictionary), set(etymology)


def candidate_listing(
    segments: list[dict[str, Any]], dictionary: set[str], etymology: set[str]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    by_surface: dict[str, dict[str, Any]] = {}
    for index, segment in enumerate(segments):
        surface = str(segment.get("text", ""))
        if (
            segment.get("type") in {"punctuation", "name"}
            or len(surface) < 3
            or not all(contains_han(char) for char in surface)
        ):
            continue
        if surface in by_surface:
            by_surface[surface]["occurrence_indices"].append(index)
            continue
        available = []
        for start in range(len(surface)):
            for end in range(start + 1, len(surface) + 1):
                part = surface[start:end]
                source = (
                    "dictionary" if part in dictionary else
                    "etymology" if len(part) == 1 and part in etymology else
                    None
                )
                if source is not None:
                    available.append({
                        "start": start, "end": end, "text": part,
                        "source": source,
                    })
        candidate = {
            "segment_index": index,
            "occurrence_indices": [index],
            "surface": surface,
            "type": segment.get("type", "word"),
            "meaning_en": segment.get("meaning_en", ""),
            "story_term": segment.get("story_term", ""),
            "available_lookups": available,
        }
        candidates.append(candidate)
        by_surface[surface] = candidate
    return candidates


def proposal_prompt(
    text: str,
    level: str,
    candidates: list[dict[str, Any]],
    required_surfaces: list[dict[str, str]] | None = None,
) -> str:
    return f"""You are adding pedagogically useful internal structure to
reviewed Chinese graded-reader annotations.

TARGET: {level.upper()}
FULL TEXT (context only; never rewrite it):
{text}

CANDIDATE SEGMENTS AND EXACT AVAILABLE LOOKUPS:
{json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))}

OPERATOR-IDENTIFIED REQUIRED DECOMPOSITIONS:
{json.dumps(required_surfaces or [], ensure_ascii=False, separators=(",", ":"))}

Every operator-identified surface must be returned with the most useful exact
available partition. The operator has already judged these worth exposing;
use the supplied reason to choose a sound explanation.

Return only genuinely useful decompositions. Each returned item must refer to
one listed candidate and split its complete surface into two or more adjacent,
nonempty subsegments. `start` is included and `end` is excluded. Every returned
subsegment must exactly match one of that candidate's available lookups.
Give every subsegment a concise `meaning_en` for its sense inside this complete
expression. This is the preview shown before dictionary lookup, so do not copy
an unrelated first dictionary sense. For example, 路 in 三路夹攻 means a
direction or attacking force here, not a physical road.

Choose semantic or grammatical units that help a learner understand the whole:
for example 同心协力 -> 同心 + 协力, 黄巾军 -> 黄巾 + 军, or a transparent
construction such as 不得不 when its internal pattern can be explained safely.
Compact verb-complement constructions are especially valuable: for example,
泛上来 -> 泛 + 上来 should distinguish the main verb (“water rises/overflows”)
from the directional complement (“up toward the viewpoint”), instead of giving
the complement the combined predicate's meaning. Likewise consider transparent
forms such as 追上来, 走进去, 拿出来, and 做好 when exact lookups are available.
Do not split merely because dictionary keys exist. Avoid misleading literal
analyses, accidental substrings, person-name fragments, and all-single-character
breakdowns when they do not clarify the expression. Omit a candidate when the
whole is best learned as a single lexical item.

`composition_en` is a concise contextual explanation of how the selected parts
produce the whole meaning. The UI opens definitions and etymology from the
local dictionary after a tap; keep each `meaning_en` specific to this expression
rather than reproducing a list of dictionary definitions.
"""


def verification_prompt(
    text: str,
    level: str,
    candidates: list[dict[str, Any]],
    proposal: dict[str, Any],
    required_surfaces: list[dict[str, str]] | None = None,
) -> str:
    return f"""You are the independent final verifier of semantic subword
decompositions for a Chinese graded reader.

TARGET: {level.upper()}
FULL TEXT:
{text}

CANDIDATES AND EXACT AVAILABLE LOOKUPS:
{json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))}

OPERATOR-IDENTIFIED REQUIRED DECOMPOSITIONS:
{json.dumps(required_surfaces or [], ensure_ascii=False, separators=(",", ":"))}

FIRST AGENT PROPOSAL:
{json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))}

Return the complete final list. Keep only decompositions whose boundaries and
composition explanation are linguistically sound in this exact context and
useful to a learner. Add an omitted decomposition only when it is clearly
valuable. Do not force every long segment to split. Subsegments must cover the
entire surface in order, and every part must be one of its listed lookups.
Audit every subsegment's `meaning_en` for its exact contextual sense; dictionary
ordering is irrelevant and a generic or unrelated first sense must be revised.
Use verdict `pass` only when the final list is unchanged; otherwise `revised`.
Every operator-identified surface must remain in the complete final list.
"""


def validate_result(
    result: dict[str, Any],
    segments: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    verifier: bool,
) -> None:
    if verifier and result.get("verdict") not in {"pass", "revised"}:
        raise ValueError("invalid verifier verdict")
    candidate_map = {item["segment_index"]: item for item in candidates}
    seen: set[int] = set()
    for item in result.get("items", []):
        index = item["segment_index"]
        if index in seen or index not in candidate_map:
            raise ValueError(f"invalid or duplicate segment index: {index}")
        seen.add(index)
        surface = str(segments[index]["text"])
        if item["surface"] != surface:
            raise ValueError(f"surface mismatch at segment {index}")
        available = {
            (part["start"], part["end"], part["text"])
            for part in candidate_map[index]["available_lookups"]
        }
        cursor = 0
        parts = item["subsegments"]
        if len(parts) < 2:
            raise ValueError(f"decomposition has fewer than two parts: {surface}")
        for part in parts:
            key = (part["start"], part["end"], part["text"])
            if key not in available:
                raise ValueError(f"part is not an available lookup: {key}")
            meaning = part.get("meaning_en")
            if (
                not isinstance(meaning, str)
                or not meaning.strip()
                or contains_han(meaning)
            ):
                raise ValueError(
                    f"part lacks a contextual English meaning: {key}"
                )
            if part["start"] != cursor or surface[part["start"]:part["end"]] != part["text"]:
                raise ValueError(f"parts are not exact and contiguous: {surface}")
            cursor = part["end"]
        if cursor != len(surface):
            raise ValueError(f"parts do not cover complete surface: {surface}")


def discard_invalid_optional_items(
    result: dict[str, Any],
    segments: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    required_surfaces: set[str],
    *,
    verifier: bool,
) -> dict[str, Any]:
    """Drop malformed agent suggestions unless the operator required them.

    The agent decides which decompositions and contextual meanings are useful.
    Exact offsets and coverage remain deterministic constraints: an optional
    suggestion that cannot satisfy them is safer to omit than to publish.
    """
    kept: list[dict[str, Any]] = []
    discarded: list[str] = []
    for item in result.get("items", []):
        try:
            validate_result(
                {
                    "verdict": result.get("verdict", "revised"),
                    "items": [item],
                },
                segments,
                candidates,
                verifier=verifier,
            )
        except (KeyError, TypeError, ValueError):
            surface = item.get("surface") if isinstance(item, dict) else None
            if surface in required_surfaces:
                raise
            discarded.append(str(surface or "<malformed item>"))
        else:
            kept.append(item)
    if not discarded:
        return result
    cleaned = {**result, "items": kept}
    cleaned["notes"] = [
        *result.get("notes", []),
        "Deterministic validation omitted malformed optional decompositions: "
        + ", ".join(discarded)
        + ".",
    ]
    if verifier:
        cleaned["verdict"] = "revised"
    return cleaned


async def enrich(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    reader_path = run_dir / "reader.json"
    report_path = run_dir / "annotation-report.json"
    reader = load_object(reader_path)
    report = load_object(report_path)
    text = str(reader.get("text", ""))
    segments = reader.get("segments")
    if (
        not text
        or not isinstance(segments, list)
        or "".join(str(item.get("text", "")) for item in segments) != text
    ):
        raise ValueError("reader segments do not reconstruct reader text")

    dictionary, etymology = lookup_inventory()
    all_candidates = candidate_listing(segments, dictionary, etymology)
    all_candidate_surfaces = {item["surface"] for item in all_candidates}
    excluded_surfaces = set(args.exclude_surface)
    unknown_exclusions = excluded_surfaces - all_candidate_surfaces
    if unknown_exclusions:
        raise ValueError(
            f"excluded surfaces are not candidates: {sorted(unknown_exclusions)}"
        )
    candidates = [
        item for item in all_candidates
        if item["surface"] not in excluded_surfaces
    ]
    candidate_surfaces = {item["surface"] for item in candidates}
    required_surfaces: list[dict[str, str]] = []
    for raw in args.audit_surface:
        surface, separator, reason = raw.partition("=")
        if not separator or not surface or not reason.strip():
            raise ValueError(f"invalid --audit-surface: {raw}")
        if surface not in candidate_surfaces:
            raise ValueError(f"audit surface is not a candidate: {surface}")
        required_surfaces.append({"surface": surface, "reason": reason.strip()})
    required_names = {item["surface"] for item in required_surfaces}
    runner = CodexRunner(
        run_dir=run_dir,
        model=args.model,
        semaphore=asyncio.Semaphore(1),
        timeout=args.timeout,
    )
    proposal = await runner.call(
        "subsegments/proposal",
        proposal_prompt(text, args.level, candidates, required_surfaces),
        SCHEMAS / "subsegment-proposal.schema.json",
        args.effort,
        refresh=args.refresh,
    )
    proposal = discard_invalid_optional_items(
        proposal,
        segments,
        candidates,
        required_names,
        verifier=False,
    )
    try:
        validate_result(proposal, segments, candidates, verifier=False)
        missing = required_names - {item["surface"] for item in proposal["items"]}
        if missing:
            raise ValueError(f"proposal omitted required surfaces: {sorted(missing)}")
    except ValueError as exc:
        proposal = await runner.call(
            "subsegments/proposal_repair",
            proposal_prompt(text, args.level, candidates, required_surfaces)
            + "\n\nThe prior proposal failed exact deterministic validation: "
            + str(exc)
            + "\nReturn a complete corrected proposal using only the supplied "
              "available lookups.\nPRIOR PROPOSAL:\n"
            + json.dumps(proposal, ensure_ascii=False),
            SCHEMAS / "subsegment-proposal.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        proposal = discard_invalid_optional_items(
            proposal,
            segments,
            candidates,
            required_names,
            verifier=False,
        )
        validate_result(proposal, segments, candidates, verifier=False)
        missing = required_names - {item["surface"] for item in proposal["items"]}
        if missing:
            raise ValueError(f"repaired proposal omitted required surfaces: {sorted(missing)}")
    verification = await runner.call(
        "subsegments/verification",
        verification_prompt(
            text, args.level, candidates, proposal, required_surfaces
        ),
        SCHEMAS / "subsegment-verification.schema.json",
        args.effort,
        refresh=args.refresh,
    )
    verification = discard_invalid_optional_items(
        verification,
        segments,
        candidates,
        required_names,
        verifier=True,
    )
    try:
        validate_result(verification, segments, candidates, verifier=True)
        missing = required_names - {
            item["surface"] for item in verification["items"]
        }
        if missing:
            raise ValueError(f"verification omitted required surfaces: {sorted(missing)}")
    except ValueError as exc:
        verification = await runner.call(
            "subsegments/verification_repair",
            verification_prompt(
                text, args.level, candidates, proposal, required_surfaces
            )
            + "\n\nThe prior verification failed exact deterministic validation: "
            + str(exc)
            + "\nReturn a complete corrected verification using only the "
              "supplied available lookups.\nPRIOR VERIFICATION:\n"
            + json.dumps(verification, ensure_ascii=False),
            SCHEMAS / "subsegment-verification.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        verification = discard_invalid_optional_items(
            verification,
            segments,
            candidates,
            required_names,
            verifier=True,
        )
        validate_result(verification, segments, candidates, verifier=True)
        missing = required_names - {
            item["surface"] for item in verification["items"]
        }
        if missing:
            raise ValueError(
                f"repaired verification omitted required surfaces: {sorted(missing)}"
            )

    final_by_surface = {
        item["surface"]: item for item in verification["items"]
    }
    for index, segment in enumerate(segments):
        selected = final_by_surface.get(str(segment.get("text", "")))
        segment["composition_en"] = (
            selected["composition_en"] if selected is not None else ""
        )
        segment["subsegments"] = (
            selected["subsegments"] if selected is not None else []
        )

    component_level_segment_count = apply_component_hsk_focus(
        segments,
        list(reader.get("grammar_overlays", [])),
        args.level,
    )
    focus_audit = reader.get("learning_focus_audit")
    if component_level_segment_count and isinstance(focus_audit, dict):
        lexical = [
            segment for segment in segments
            if segment.get("type") != "punctuation"
        ]
        focus_audit["classifier"] = f"{args.level}-word-segment-focus-v3-components"
        focus_audit["component_level_segment_count"] = component_level_segment_count
        focus_audit["learning_focus_counts"] = {
            key: sum(segment.get("learning_focus") == key for segment in lexical)
            for key in ("target", "lookup")
        }
        focus_audit["hsk_status_counts"] = {
            key: sum(segment.get("hsk_status") == key for segment in lexical)
            for key in ("in_level", "above_level", "unlisted")
        }

    reader["subsegment_audit"] = {
        "schema_version": 1,
        "model": args.model,
        "effort": args.effort,
        "candidate_count": len(candidates),
        "decomposed_surface_count": len(final_by_surface),
        "decomposed_segment_count": sum(
            str(segment.get("text", "")) in final_by_surface
            for segment in segments
        ),
        "proposal_count": len(proposal["items"]),
        "verifier_verdict": verification["verdict"],
        "all_reviewed": True,
        "operator_required_surface_count": len(required_surfaces),
        "operator_excluded_surface_count": len(excluded_surfaces),
        "component_level_segment_count": component_level_segment_count,
        "notes": verification["notes"],
    }
    reader_path.write_text(
        json.dumps(reader, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["reader_sha256"] = hashlib.sha256(reader_path.read_bytes()).hexdigest()
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(reader["subsegment_audit"], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--level", required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--audit-surface",
        action="append",
        default=[],
        metavar="SURFACE=REASON",
        help="require an agent-reviewed decomposition for one candidate surface",
    )
    parser.add_argument(
        "--exclude-surface",
        action="append",
        default=[],
        metavar="SURFACE",
        help="exclude a known misleading candidate from semantic decomposition",
    )
    args = parser.parse_args()
    asyncio.run(enrich(args))


if __name__ == "__main__":
    main()
