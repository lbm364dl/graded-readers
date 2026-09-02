#!/usr/bin/env python3
"""Add level-controlled Chinese-first help cards to reviewed annotations."""

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
from pipeline.chinese_readability import (
    cumulative_words,
    validate_chinese_help_text,
)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _context(text: str, start: int, surface: str) -> str:
    left = max(
        text.rfind("。", 0, start),
        text.rfind("！", 0, start),
        text.rfind("？", 0, start),
        text.rfind("\n", 0, start),
    ) + 1
    candidates = [
        position for marker in "。！？\n"
        if (position := text.find(marker, start + len(surface))) >= 0
    ]
    right = min(candidates) + 1 if candidates else len(text)
    return text[left:right].strip()


def build_help_targets(
    text: str, segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Collapse equivalent difficult occurrences into stable help targets."""
    targets: list[dict[str, Any]] = []
    by_signature: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    text_offset = 0
    for index, segment in enumerate(segments):
        surface = str(segment.get("text", ""))
        segment_start = text_offset
        text_offset += len(surface)
        if (
            segment.get("type") == "punctuation"
            or segment.get("learning_focus") != "lookup"
            or segment.get("lookup_reason") == "proper_name"
            or segment.get("type") == "name"
        ):
            continue
        signature = (
            surface,
            str(segment.get("meaning_en", "")),
            str(segment.get("story_term", "")),
            str(segment.get("lookup_reason", "")),
        )
        target = by_signature.get(signature)
        if target is None:
            target = {
                "target_id": f"help_{len(targets):03d}",
                "surface": surface,
                "meaning_en": signature[1],
                "story_term": signature[2],
                "story_term_meaning_en": str(
                    segment.get("story_term_meaning_en", "")
                ),
                "lookup_reason": signature[3],
                "segment_indices": [],
                "contexts": [],
            }
            by_signature[signature] = target
            targets.append(target)
        target["segment_indices"].append(index)
        context = _context(text, segment_start, surface)
        if context and context not in target["contexts"]:
            target["contexts"].append(context)
    if text_offset != len(text):
        raise ValueError("segments do not reconstruct text while building help targets")
    sentence_by_segment: dict[int, int] = {}
    sentence_number = 0
    for index, segment in enumerate(segments):
        sentence_by_segment[index] = sentence_number
        if any(mark in str(segment.get("text", "")) for mark in "。！？\n"):
            sentence_number += 1
    target_sentences = {
        str(target["target_id"]): {
            sentence_by_segment[index] for index in target["segment_indices"]
        }
        for target in targets
    }
    for target in targets:
        target["support_surfaces"] = sorted(
            {
                str(other["surface"])
                for other in targets
                if other["target_id"] != target["target_id"]
                and target_sentences[str(target["target_id"])]
                & target_sentences[str(other["target_id"])]
            },
            key=lambda surface: (-len(surface), surface),
        )
    return targets


def target_listing(targets: list[dict[str, Any]]) -> str:
    return json.dumps(targets, ensure_ascii=False, separators=(",", ":"))


def _taught_surfaces(target: dict[str, Any]) -> set[str]:
    return {
        value for value in (
            str(target.get("surface", "")).strip(),
            str(target.get("story_term", "")).strip(),
            *(str(item).strip() for item in target.get("support_surfaces", [])),
        )
        if value
    }


def validate_card_set(
    result: dict[str, Any], targets: list[dict[str, Any]], level: str
) -> list[dict[str, Any]]:
    cards = result.get("cards")
    if not isinstance(cards, list):
        return [{"problem": "cards is not a list"}]
    expected = {target["target_id"]: target for target in targets}
    actual: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            issues.append({"problem": "card is not an object"})
            continue
        target_id = str(card.get("target_id", ""))
        if target_id in actual:
            issues.append({"target_id": target_id, "problem": "duplicate card"})
            continue
        actual[target_id] = card
    if set(actual) != set(expected):
        issues.append({
            "problem": "target ids do not exactly match",
            "missing": sorted(set(expected) - set(actual)),
            "unexpected": sorted(set(actual) - set(expected)),
        })
    for target_id in sorted(set(actual) & set(expected)):
        card = actual[target_id]
        target = expected[target_id]
        if card.get("surface") != target["surface"]:
            issues.append({
                "target_id": target_id,
                "problem": "surface mismatch",
                "expected": target["surface"],
            })
            continue
        explanation = str(card.get("explanation_zh", "")).strip()
        example = str(card.get("example_zh", "")).strip()
        if not explanation and not example:
            issues.append({
                "target_id": target_id,
                "problem": "a difficult item needs a Chinese hint or example",
            })
            continue
        taught = _taught_surfaces(target)
        for kind, value in (("explanation", explanation), ("example", example)):
            if not value:
                continue
            report = validate_chinese_help_text(
                value, level, kind=kind, taught_surfaces=taught,
            )
            if not report["passes"]:
                issues.append({
                    "target_id": target_id,
                    "surface": target["surface"],
                    "field": f"{kind}_zh",
                    "problem": "Chinese help fails deterministic level/length gate",
                    "above_level_words": report["above_level_words"],
                    "contains_latin": report["contains_latin"],
                    "cjk_length": report["cjk_length"],
                    "max_cjk": report["max_cjk"],
                })
        if explanation:
            report = validate_chinese_help_text(
                explanation, level, kind="explanation", taught_surfaces=taught,
            )
            other_tokens = [
                token for token in report["tokens"] if token not in taught
            ]
            if len(other_tokens) < 2:
                issues.append({
                    "target_id": target_id,
                    "field": "explanation_zh",
                    "problem": "explanation is too thin or circular",
                })
            if target["surface"] in explanation:
                issues.append({
                    "target_id": target_id,
                    "field": "explanation_zh",
                    "problem": "explanation repeats the headword and is circular",
                })
        primary_surfaces = {
            value for key in ("surface", "story_term")
            if (value := str(target.get(key, "")).strip())
        }
        if example and not any(surface in example for surface in primary_surfaces):
            issues.append({
                "target_id": target_id,
                "problem": "example does not contain the taught word or story phrase",
            })
        if explanation and example and explanation == example:
            issues.append({
                "target_id": target_id,
                "problem": "explanation and example are duplicates",
            })
    return issues


def validate_and_prune_optional_explanations(
    result: dict[str, Any], targets: list[dict[str, Any]], level: str
) -> list[dict[str, Any]]:
    """Drop a bad optional explanation when a valid example already suffices."""
    issues = validate_card_set(result, targets, level)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        if issue.get("target_id"):
            by_id.setdefault(str(issue["target_id"]), []).append(issue)
    cards = {
        str(card.get("target_id", "")): card for card in result.get("cards", [])
        if isinstance(card, dict)
    }
    changed = False
    for target_id, target_issues in by_id.items():
        card = cards.get(target_id)
        if (
            card is not None
            and str(card.get("example_zh", "")).strip()
            and target_issues
            and all(
                issue.get("field") == "explanation_zh"
                for issue in target_issues
            )
        ):
            card["explanation_zh"] = ""
            changed = True
    return validate_card_set(result, targets, level) if changed else issues


def merge_targeted_repairs(
    previous: dict[str, Any],
    candidate: dict[str, Any],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep passing cards immutable so iterative repair is monotonic."""
    mutable_ids = {
        str(issue["target_id"]) for issue in issues if issue.get("target_id")
    }
    if not mutable_ids or any(not issue.get("target_id") for issue in issues):
        return candidate
    replacements = {
        str(card.get("target_id", "")): card for card in candidate.get("cards", [])
    }
    return {
        "cards": [
            replacements.get(str(card.get("target_id", "")), card)
            if str(card.get("target_id", "")) in mutable_ids else card
            for card in previous.get("cards", [])
        ]
    }


def generation_prompt(
    text: str, targets: list[dict[str, Any]], level: str
) -> str:
    number = int(level.removeprefix("hsk"))
    vocabulary_reference = ""
    if number <= 2:
        vocabulary_reference = (
            "\nCUMULATIVE APPROVED WORD REFERENCE:\n"
            + "、".join(sorted(cumulative_words(level)))
        )
    return f"""You create Chinese-first help cards for a {level.upper()}
graded reader. The learner should build intuition in Chinese and see English
only if they explicitly ask for it.

CHAPTER:
{text}

EXACT DIFFICULT TARGETS:
{target_listing(targets)}

Return exactly one card for every target_id and no others. Keep `surface`
exact. Choose whichever is genuinely most helpful:
- `explanation_zh`: a short, concrete Chinese paraphrase or contextual clue;
- `example_zh`: one short, natural Chinese example containing the target word
  or its exact `story_term`;
- or both when both add real value.

Do not write a dictionary-style definition just to fill a field. Do not merely
repeat the chapter sentence. Do not explain grammar with advanced labels. Do
not translate into English. Use only words an {level.upper()} learner should
already understand, apart from the exact target surface/story term being
taught. A target's `support_surfaces` are the only other difficult words you
may use, and only when necessary for a natural concrete example (for example,
a measure word needs a noun). A word like 他, 我, or 有 is intentionally absent: never add cards for
ordinary known vocabulary or names. Empty one of the two fields when the other
does the job better. Keep each card concrete, natural, non-circular, and
faithful to the word's meaning in its supplied chapter contexts.
{vocabulary_reference}
"""


def repair_prompt(
    text: str,
    targets: list[dict[str, Any]],
    proposal: dict[str, Any],
    issues: list[dict[str, Any]],
    level: str,
) -> str:
    return f"""Repair a Chinese-first help-card set for a {level.upper()}
reader. Return the complete card set, not a patch.

CHAPTER:
{text}

TARGETS:
{target_listing(targets)}

CURRENT CARDS:
{json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))}

DETERMINISTIC FAILURES:
{json.dumps(issues, ensure_ascii=False, separators=(",", ":"))}

Fix every listed failure. Every non-target word in a hint/example must be
understandable at {level.upper()}. Prefer a short concrete example over an
abstract definition when that is easier. Preserve every target_id and exact
surface, include no extra cards, and leave one field empty when unnecessary.
Change only cards whose target_id occurs in DETERMINISTIC FAILURES; copy every
other card byte-for-byte. If one field fails but the other already gives a
useful passing clue, make the failing field empty. Never solve one card by
using a different difficult target word unless it appears in that target's
explicit `support_surfaces` and makes the example substantially more natural.
"""


def review_prompt(
    text: str,
    targets: list[dict[str, Any]],
    proposal: dict[str, Any],
    level: str,
    *,
    failures: list[dict[str, Any]] | None = None,
) -> str:
    failure_section = "" if failures is None else (
        "\nDETERMINISTIC FAILURES TO REPAIR:\n"
        + json.dumps(failures, ensure_ascii=False, separators=(",", ":"))
        + "\nChange only cards whose target_id appears in those failures; copy "
        "every other card exactly. If only an explanation fails and its "
        "example is already helpful, make the explanation empty. Never use "
        "another difficult target word unless it is an explicit support_surface."
    )
    return f"""You are the independent final reviewer of Chinese-first help
cards for a {level.upper()} graded reader.

CHAPTER:
{text}

TARGETS:
{target_listing(targets)}

PROPOSED COMPLETE CARD SET:
{json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))}
{failure_section}

Return the complete final card set. Use verdict `pass` only if every card is
unchanged; otherwise use `revised`. Check contextual accuracy, natural Chinese,
actual usefulness, circularity, and whether the explanation/example itself is
understandable at {level.upper()}. Reject cards that merely restate the chapter
sentence or use abstract teaching terminology. It is fine for one field to be
empty. Do not add ordinary known words or names. Preserve every target_id and
exact surface and include no extra cards. Put only concise audit comments in
notes; those notes are not learner-facing.
"""


def pedagogy_prompt(
    text: str,
    targets: list[dict[str, Any]],
    proposal: dict[str, Any],
    level: str,
    *,
    failures: list[dict[str, Any]] | None = None,
) -> str:
    targeted = "" if failures is None else (
        "\nDETERMINISTIC FAILURES:\n"
        + json.dumps(failures, ensure_ascii=False, separators=(",", ":"))
        + "\nChange only the target_ids listed in those failures and preserve "
        "all other cards exactly."
    )
    return f"""You are the final pedagogy editor for Chinese-first help cards
in a {level.upper()} graded reader. This is a final common-sense audit after a
separate generator and verifier.

CHAPTER:
{text}

TARGETS (including the only permitted linked support words):
{target_listing(targets)}

CURRENT COMPLETE CARDS:
{json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))}
{targeted}

Return the complete final card set. Use `pass` only if no card changes and
`revised` otherwise. In addition to contextual correctness and {level.upper()}
language, reject these pedagogical failures:
- a clue that is factually misleading (for example, 酒 is not 水);
- an unnatural or incomplete classifier fragment such as 我有一把 or 这里有三匹;
- a circular clue that restates its headword;
- an abstract dictionary label where a concrete example would work;
- an example that merely copies the chapter sentence and adds no intuition.

For a classifier, use a complete natural example with a noun from that
target's `support_surfaces`, such as 一匹马 or 一把剑. Otherwise avoid linked
difficult words. It is better to leave `explanation_zh` empty and give one
excellent example than to force a weak definition. Keep exact ids/surfaces,
include every card and no extras, and put concise non-learner-facing audit
comments in notes.
"""


def apply_cards(
    segments: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    enriched = [
        dict(segment, explanation_zh="", example_zh="")
        for segment in segments
    ]
    cards = {item["target_id"]: item for item in result["cards"]}
    for target in targets:
        card = cards[target["target_id"]]
        for index in target["segment_indices"]:
            enriched[index]["explanation_zh"] = card["explanation_zh"].strip()
            enriched[index]["example_zh"] = card["example_zh"].strip()
    return enriched


def cards_from_segments(
    segments: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "cards": [
            {
                "target_id": target["target_id"],
                "surface": target["surface"],
                "explanation_zh": str(
                    segments[target["segment_indices"][0]].get(
                        "explanation_zh", ""
                    )
                ),
                "example_zh": str(
                    segments[target["segment_indices"][0]].get(
                        "example_zh", ""
                    )
                ),
            }
            for target in targets
        ]
    }


async def enrich(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    reader_path = run_dir / "reader.json"
    report_path = run_dir / "annotation-report.json"
    reader = load_object(reader_path)
    report = load_object(report_path)
    text = str(reader.get("text", ""))
    segments = reader.get("segments")
    if (
        not text or not isinstance(segments, list)
        or "learning_focus_audit" not in reader
        or reader["learning_focus_audit"].get("semantic_focus_reviewed") is not True
        or (
            "immersion_help_audit" in reader
            and not args.refresh
            and not args.pedagogy_only
            and not args.audit_target
        )
    ):
        if (
            "immersion_help_audit" in reader
            and not args.refresh
            and not args.pedagogy_only
            and not args.audit_target
        ):
            print(json.dumps(reader["immersion_help_audit"], ensure_ascii=False, indent=2))
            return
        raise ValueError("reader needs complete semantic learning-focus review")
    if "".join(str(item.get("text", "")) for item in segments) != text:
        raise ValueError("reader segments do not reconstruct reader text")

    targets = build_help_targets(text, segments)
    runner = CodexRunner(
        run_dir=run_dir,
        model=args.model,
        semaphore=asyncio.Semaphore(1),
        timeout=args.timeout,
    )
    if args.pedagogy_only:
        previous_audit = reader.get("immersion_help_audit", {})
        verifier_record_path = (
            run_dir / "agents/immersion_help/verification/result.json"
        )
        verifier_record = (
            load_object(verifier_record_path)
            if verifier_record_path.is_file() else {}
        )
        verification = {
            **cards_from_segments(segments, targets),
            "verdict": str(
                verifier_record.get(
                    "verdict", previous_audit.get("verifier_verdict", "pass")
                )
            ),
            "notes": list(
                verifier_record.get(
                    "notes", previous_audit.get("verifier_notes", [])
                )
            ),
        }
        issues = validate_and_prune_optional_explanations(
            verification, targets, args.level
        )
        if issues:
            raise ValueError(
                "stored immersion cards fail deterministic checks: "
                + json.dumps(issues, ensure_ascii=False)
            )
        generation_repairs = int(previous_audit.get("generation_repair_count", 0))
        verification_repairs = int(
            previous_audit.get("verification_repair_count", 0)
        )
    else:
        proposal = await runner.call(
            "immersion_help/generation",
            generation_prompt(text, targets, args.level),
            SCHEMAS / "immersion-help.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        generation_repairs = 0
        issues = validate_and_prune_optional_explanations(
            proposal, targets, args.level
        )
        while issues and generation_repairs < args.max_repairs:
            generation_repairs += 1
            candidate = await runner.call(
                f"immersion_help/generation_repair_{generation_repairs:02d}",
                repair_prompt(text, targets, proposal, issues, args.level),
                SCHEMAS / "immersion-help.schema.json",
                args.effort,
                refresh=args.refresh,
            )
            proposal = merge_targeted_repairs(proposal, candidate, issues)
            issues = validate_and_prune_optional_explanations(
                proposal, targets, args.level
            )
        if issues:
            raise ValueError(
                "immersion generation still fails deterministic checks: "
                + json.dumps(issues, ensure_ascii=False)
            )

        verification = await runner.call(
            "immersion_help/verification",
            review_prompt(text, targets, proposal, args.level),
            SCHEMAS / "immersion-help-review.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        if (
            verification["verdict"] == "pass"
            and verification["cards"] != proposal["cards"]
        ):
            raise ValueError("immersion verifier changed cards while claiming pass")
        verification_repairs = 0
        issues = validate_and_prune_optional_explanations(
            verification, targets, args.level
        )
        while issues and verification_repairs < args.max_repairs:
            verification_repairs += 1
            candidate = await runner.call(
                f"immersion_help/verification_repair_{verification_repairs:02d}",
                review_prompt(
                    text,
                    targets,
                    {"cards": verification["cards"]},
                    args.level,
                    failures=issues,
                ),
                SCHEMAS / "immersion-help-review.schema.json",
                args.effort,
                refresh=args.refresh,
            )
            merged = merge_targeted_repairs(verification, candidate, issues)
            verification = {
                **candidate,
                "verdict": "revised",
                "cards": merged["cards"],
            }
            issues = validate_and_prune_optional_explanations(
                verification, targets, args.level
            )
        if issues:
            raise ValueError(
                "immersion verification still fails deterministic checks: "
                + json.dumps(issues, ensure_ascii=False)
            )

    operator_issues: list[dict[str, Any]] = []
    valid_target_ids = {str(target["target_id"]) for target in targets}
    for value in args.audit_target:
        target_id, separator, reason = value.partition("=")
        if not separator or target_id not in valid_target_ids or not reason.strip():
            raise ValueError(f"invalid --audit-target: {value}")
        operator_issues.append({
            "target_id": target_id,
            "problem": reason.strip(),
        })
    if operator_issues:
        candidate = await runner.call(
            "immersion_help/operator_pedagogy_audit",
            pedagogy_prompt(
                text,
                targets,
                {"cards": verification["cards"]},
                args.level,
                failures=operator_issues,
            ),
            SCHEMAS / "immersion-help-review.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        merged = merge_targeted_repairs(verification, candidate, operator_issues)
        pedagogy = {
            **candidate,
            "verdict": "revised",
            "cards": merged["cards"],
        }
    else:
        pedagogy = await runner.call(
            "immersion_help/pedagogy_audit",
            pedagogy_prompt(
                text, targets, {"cards": verification["cards"]}, args.level,
            ),
            SCHEMAS / "immersion-help-review.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        if (
            pedagogy["verdict"] == "pass"
            and pedagogy["cards"] != verification["cards"]
        ):
            raise ValueError("pedagogy auditor changed cards while claiming pass")
    pedagogy_repairs = 0
    issues = validate_and_prune_optional_explanations(
        pedagogy, targets, args.level
    )
    while issues and pedagogy_repairs < args.max_repairs:
        pedagogy_repairs += 1
        candidate = await runner.call(
            f"immersion_help/pedagogy_repair_{pedagogy_repairs:02d}",
            pedagogy_prompt(
                text,
                targets,
                {"cards": pedagogy["cards"]},
                args.level,
                failures=issues,
            ),
            SCHEMAS / "immersion-help-review.schema.json",
            args.effort,
            refresh=args.refresh,
        )
        merged = merge_targeted_repairs(pedagogy, candidate, issues)
        pedagogy = {
            **candidate,
            "verdict": "revised",
            "cards": merged["cards"],
        }
        issues = validate_and_prune_optional_explanations(
            pedagogy, targets, args.level
        )
    if issues:
        raise ValueError(
            "pedagogy audit still fails deterministic checks: "
            + json.dumps(issues, ensure_ascii=False)
        )

    final_segments = apply_cards(segments, targets, pedagogy)
    if "".join(str(item["text"]) for item in final_segments) != text:
        raise AssertionError("immersion enrichment changed source text")
    reader["segments"] = final_segments
    reader["immersion_help_audit"] = {
        "schema_version": 1,
        "target": args.level,
        "selection": "lookup-non-name-v1",
        "model": args.model,
        "effort": args.effort,
        "generation_reviewed": True,
        "verifier_verdict": verification["verdict"],
        "pedagogy_reviewed": True,
        "pedagogy_verdict": pedagogy["verdict"],
        "deterministic_level_check": "exact-target-level-words-v1",
        "unique_card_count": len(targets),
        "card_occurrence_count": sum(
            len(target["segment_indices"]) for target in targets
        ),
        "explanation_count": sum(
            bool(item["explanation_zh"]) for item in pedagogy["cards"]
        ),
        "example_count": sum(
            bool(item["example_zh"]) for item in pedagogy["cards"]
        ),
        "generation_repair_count": generation_repairs,
        "verification_repair_count": verification_repairs,
        "pedagogy_repair_count": pedagogy_repairs,
        "operator_pedagogy_flag_count": len(operator_issues),
        "verifier_notes": verification["notes"],
        "pedagogy_notes": pedagogy["notes"],
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
    print(json.dumps(reader["immersion_help_audit"], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--level", default="hsk1")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-repairs", type=int, default=3)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--pedagogy-only", action="store_true",
        help="reuse stored valid cards and rerun only the final pedagogy audit",
    )
    parser.add_argument(
        "--audit-target", action="append", default=[], metavar="ID=REASON",
        help="during a pedagogy rerun, lock other cards and repair this flagged id",
    )
    args = parser.parse_args()
    asyncio.run(enrich(args))


if __name__ == "__main__":
    main()
