#!/usr/bin/env python3
"""One-call annotation experiment: deterministic baseline plus Luna deltas."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import jieba.posseg as pseg

from pipeline.agent_harness import CHINESE_PINYIN_POLICY, ChapterHarness, CodexRunner, ROOT
from pipeline.annotate_chinese import atomic_json, sha256_bytes, utc_now
from pipeline.chinese_translation_policy import CHINESE_TRANSLATION_POLICY
from pipeline.fixed_boundary_annotation import (
    _punctuation_char, _validate_segment_metadata, dictionary_hints,
    refined_fixed_segments,
)
from pipeline.chinese_boundary_proposals import boundary_proposals

SCHEMA = ROOT / "pipeline" / "schemas" / "delta-annotation.schema.json"
PARTICLE_FLAGS = {"u", "ul", "uz", "ug", "uj", "uv", "ud", "y", "e", "zg"}
PARTICLE_SURFACES = frozenset({
    "的", "地", "得", "了", "着", "过", "吗", "呢", "吧", "啊", "呀",
    "嘛", "么", "之", "所", "者", "也", "而", "乎", "矣", "焉",
})


def concise_dictionary_meaning(value: str) -> str:
    """Reduce dictionary glosses to a tap-friendly contract-safe first sense."""
    value = value.strip()
    # Biographical dictionary entries and parenthetical etymology are useful in
    # a dictionary screen, but too long for an inline tap annotation.
    head = value.split(" (", 1)[0].split(";", 1)[0].strip()
    if len(head) <= 150:
        return head
    return head[:147].rstrip() + "..."


def deterministic_baseline(
    text: str,
    *,
    protected_names: set[str] | frozenset[str] | None = None,
    protected_compounds: set[str] | frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], set[int]]:
    names = frozenset(protected_names or ())
    surfaces = refined_fixed_segments(
        text,
        protected_names=names,
        protected_compounds=protected_compounds,
    )
    hints = dictionary_hints(surfaces)
    segments: list[dict[str, Any]] = []
    unknown: set[int] = set()
    for hint in hints:
        surface = hint["text"]
        if all(_punctuation_char(char) for char in surface):
            segments.append({"text": surface, "type": "punctuation", "pinyin": "", "meaning_en": ""})
            continue
        meanings = hint["meaning_hints"]
        if not meanings:
            unknown.add(hint["index"])
        flag = next(iter(pseg.cut(surface))).flag
        pinyin_hint = hint["pinyin_hint"]
        segments.append({
            "text": surface,
            "type": ("name" if surface in names else
                     "particle" if flag in PARTICLE_FLAGS else
                     "name" if pinyin_hint[:1].isupper() else "word"),
            "pinyin": pinyin_hint,
            "meaning_en": concise_dictionary_meaning(meanings[0]) if meanings else "[LUNA REQUIRED]",
        })
    assert "".join(item["text"] for item in segments) == text

    # A dictionary hit is not sufficient evidence that its context-free
    # metadata is suitable for a tap-to-explain reader.  Independent review of
    # the chapter-one smoke attributed 132/141 valid findings to deterministic
    # metadata; 104 were single Han characters (polysemy, readings, particles,
    # and false surname/name senses).  Likewise, ``name`` above is inferred
    # solely from an uppercase dictionary pinyin hint, which produces false
    # positives for ordinary words.  Send these ambiguity-prone entries through
    # the same single compact chapter-level delta call as true unknowns.
    return segments, unknown


def contextual_delta_targets(
    baseline: list[dict[str, Any]], required: set[int]
) -> set[int]:
    """Return required plus context-sensitive metadata review targets."""
    contextual = {
        index
        for index, item in enumerate(baseline)
        if item["type"] != "punctuation"
    }
    return required | contextual


def compact_delta_targets(baseline: list[dict[str, Any]], unknown: set[int]) -> list[dict[str, Any]]:
    """Serialize only items Luna must enrich, with tiny local context windows."""
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for item in baseline:
        end = cursor + len(item["text"])
        offsets.append((cursor, end))
        cursor = end
    targets: list[dict[str, Any]] = []
    for index in sorted(unknown):
        start, end = offsets[index]
        left = [item["text"] for item in baseline[max(0, index - 2):index]]
        right = [item["text"] for item in baseline[index + 1:index + 3]]
        targets.append({"index": index, "start": start, "end": end,
                        "text": baseline[index]["text"], "left": left, "right": right,
                        "pinyin_hint": baseline[index]["pinyin"]})
    return targets


def compact_boundary_targets(text: str, baseline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only neutral disagreement regions plus nearby current tokens."""
    evidence = boundary_proposals(text)
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for item in baseline:
        end = cursor + len(item["text"])
        offsets.append((cursor, end))
        cursor = end
    targets = []
    for region in evidence["disagreement_regions"]:
        start, end = region["start"], region["end"]
        owners = [index for index, (left, right) in enumerate(offsets)
                  if left < end and start < right]
        if not owners:
            continue
        targets.append({
            "start": start, "end": end, "text": text[start:end],
            "plain_boundaries": region["plain_boundaries"],
            "refined_boundaries": region["refined_boundaries"],
        })
    return targets


def contextual_delta_batches(
    text: str, baseline: list[dict[str, Any]], required: set[int],
    chunks: list[str], maximum_targets: int = 150,
) -> list[dict[str, Any]]:
    """Partition mandatory contextual targets along sentence-safe chunks."""
    if maximum_targets < 1 or "".join(chunks) != text:
        raise ValueError("invalid contextual delta batch policy")
    targets = contextual_delta_targets(baseline, required)
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for item in baseline:
        end = cursor + len(item["text"])
        offsets.append((cursor, end))
        cursor = end
    batches: list[dict[str, Any]] = []
    chunk_start = 0
    assigned: set[int] = set()
    for chunk in chunks:
        chunk_end = chunk_start + len(chunk)
        local = [index for index in sorted(targets)
                 if chunk_start <= offsets[index][0]
                 and offsets[index][1] <= chunk_end]
        for position in range(0, len(local), maximum_targets):
            indices = local[position:position + maximum_targets]
            batches.append({
                "indices": indices, "start": chunk_start, "end": chunk_end,
            })
            assigned.update(indices)
        chunk_start = chunk_end
    if assigned != targets:
        raise ValueError("contextual targets do not fit sentence-safe chunks")
    return batches


def delta_batch_prompt(
    text: str, baseline: list[dict[str, Any]], indices: set[int], level: str,
    boundary_start: int, boundary_end: int, include_boundary: bool = True,
) -> str:
    """Require contextual metadata for every target in one bounded batch."""
    targets = compact_delta_targets(baseline, indices)
    rows = [[
        item["index"], item["start"], item["end"], item["text"],
        item["left"][-1] if item["left"] else "",
        item["right"][0] if item["right"] else "",
        baseline[item["index"]]["type"], item["pinyin_hint"],
        ("" if baseline[item["index"]]["meaning_en"] == "[LUNA REQUIRED]"
         else baseline[item["index"]]["meaning_en"]),
    ] for item in targets]
    boundary_targets = [
        item for item in compact_boundary_targets(text, baseline)
        if boundary_start <= item["start"] and item["end"] <= boundary_end
    ] if include_boundary else []
    return f"""Return only JSON matching the schema. Annotate one bounded metadata
batch for this complete {level.upper()} Chinese reader. Every TARGET ROW is mandatory:
return exactly one override for each row, except a baseline index replaced by one of your
boundary_patches must not also have an override. Never repeat or add an index.

BOUNDARY TARGETS are neutral tokenizer disagreements within this batch's sentence-safe
region. Patch only genuine learner-facing boundary errors. Each patch must use one exact
listed character span, be sorted/non-overlapping/lossless, and supply complete metadata.
Keep ordinary words and particles tappable. A compact verb plus directional or
resultative complement may be one learner-facing predicate (for example 泛上来 or
拿出来), but never absorb its object or clause. Names may have at most 6 Han characters
and other segments at most 4. Return boundary_patches=[] when no correction is needed.

For overrides, judge CURRENT_TYPE, CURRENT_PINYIN, and CURRENT_MEANING in the full TEXT
context and return concise contextual metadata for the tapped token alone. Grammar
overlays are exact-offset constructions, never clause paraphrases. A meaning-changing
directional/resultative complement is high priority: explain the main verb, what the
complement contributes, and their combined meaning. Give every overlay a concise
lowercase grammar_candidate_key. It is a provisional clustering hint for later agent
unification, not a canonical grammar lesson ID, and equivalent constructions need not
already use identical keys. Do not use
tools or external sources. Self-check all mandatory rows, indices, and offsets.

{CHINESE_PINYIN_POLICY}
{CHINESE_TRANSLATION_POLICY}

TEXT:\n{text}

BOUNDARY TARGETS:\n{json.dumps(boundary_targets, ensure_ascii=False, separators=(',', ':'))}

TARGET ROW COLUMNS=[INDEX,START,END,TEXT,LEFT_TOKEN,RIGHT_TOKEN,CURRENT_TYPE,CURRENT_PINYIN,CURRENT_MEANING]
MANDATORY TARGET ROWS:\n{json.dumps(rows, ensure_ascii=False, separators=(',', ':'))}"""


def delta_prompt(text: str, baseline: list[dict[str, Any]], unknown: set[int], level: str) -> str:
    target_indices = contextual_delta_targets(baseline, unknown)
    targets = compact_delta_targets(baseline, target_indices)
    # Repeating object keys and two context arrays for hundreds of deliberately
    # targeted one-character entries more than triples prompt size.  Serialize
    # the same evidence as fixed-column rows while retaining exact offsets and
    # both neighboring-token windows.
    target_rows = [
        [
            item["index"], item["text"],
            baseline[item["index"]]["type"], item["pinyin_hint"],
            ("" if item["index"] in unknown
             else baseline[item["index"]]["meaning_en"]),
            item["index"] in unknown,
        ]
        for item in targets
    ]
    boundary_targets = compact_boundary_targets(text, baseline)
    return f"""Return only JSON matching the schema. Review this complete {level.upper()}
Chinese reader annotation. Most boundaries are fixed. BOUNDARY TARGETS are neutral
tokenizer disagreements, not instructions to change anything. Return a boundary patch
only for a genuine learner-facing word-boundary error. Patch start/end must exactly equal
one listed target. Replacement text must concatenate to TEXT[start:end] exactly. Patches
must be sorted, non-overlapping, and minimal. Keep ordinary words, particles, titles,
real idioms, complete names, and compact verb-complement predicates tappable; do not
group objects or clauses. Non-name Han segments
may contain at most 4 characters; names at most 6. Supply complete contextual metadata
for every replacement segment. Otherwise return boundary_patches=[].

CURRENT_TYPE, CURRENT_PINYIN, and
CURRENT_MEANING are the existing deterministic metadata you must judge in the supplied
context. Every row with REQUIRED=true must
have exactly one override. A REQUIRED=false row already has deterministic metadata: return
an override for it only when its existing type, pinyin hint, or dictionary meaning is wrong
in this context. Never repeat an index. Do not return any other index. Use the exact surface and neighboring-token
context to supply type, pinyin, and a concise English meaning. Meanings explain only the
tapped token, never its sentence. Add only genuine grammar constructions as overlays,
not sentence translations. Meaning-changing directional/resultative complements are
high priority and should explain both the base verb and the complement's contribution.
Give every overlay a concise lowercase grammar_candidate_key. Treat it only as a
provisional clustering hint for later agent unification, not as a canonical lesson ID;
equivalent constructions need not already use identical keys.
Overlay offsets are zero-based Python character offsets into TEXT and the
overlay text must match exactly. Self-check indices and offsets. Do not use tools or
search for another source; everything required is below.

{CHINESE_PINYIN_POLICY}
{CHINESE_TRANSLATION_POLICY}

TEXT:\n{text}

BOUNDARY TARGETS contain exact character spans plus each tokenizer's candidate
character boundaries. TEXT provides all surrounding context:
{json.dumps(boundary_targets, ensure_ascii=False, separators=(',', ':'))}

TARGET ROW COLUMNS=[INDEX,TEXT,CURRENT_TYPE,CURRENT_PINYIN,CURRENT_MEANING,REQUIRED]
CONTEXTUAL TARGETS:\n{json.dumps(target_rows, ensure_ascii=False, separators=(',', ':'))}"""


def missing_required_prompt(
    text: str, baseline: list[dict[str, Any]], missing: set[int], level: str
) -> str:
    """Request one bounded completion for mandatory rows omitted by the seed."""
    rows = []
    for item in compact_delta_targets(baseline, missing):
        index = item["index"]
        rows.append([
            index, item["start"], item["end"], item["text"],
            item["left"][-1] if item["left"] else "",
            item["right"][0] if item["right"] else "",
            baseline[index]["type"], baseline[index]["pinyin"],
            baseline[index]["meaning_en"],
        ])
    return f"""Return only JSON matching the schema. This is one bounded completion
for a {level.upper()} Chinese reader annotation seed. Return exactly one override for
EVERY ROW below and no other index. Boundaries and source characters are immutable.
Use the local context to provide contextual type, pinyin, and a concise English meaning
for only the tapped token. grammar_overlays MUST be an empty array; the initial seed
already owns chapter-level grammar overlays. boundary_patches MUST be an empty array;
the initial seed already owns boundary decisions. Self-check every required index.

{CHINESE_PINYIN_POLICY}
{CHINESE_TRANSLATION_POLICY}

TEXT:\n{text}

ROW COLUMNS=[INDEX,START,END,TEXT,LEFT_TOKEN,RIGHT_TOKEN,CURRENT_TYPE,CURRENT_PINYIN,CURRENT_MEANING]
MISSING REQUIRED ROWS:\n{json.dumps(rows, ensure_ascii=False, separators=(',', ':'))}"""


def collapse_identical_overrides(value: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Collapse byte-equivalent same-index rows; reject semantic conflicts."""
    result = copy.deepcopy(value)
    kept: list[dict[str, Any]] = []
    by_index: dict[Any, dict[str, Any]] = {}
    collapsed = 0
    for item in result.get("overrides", []):
        index = item.get("index") if isinstance(item, dict) else None
        if index not in by_index:
            by_index[index] = item
            kept.append(item)
        elif item == by_index[index]:
            collapsed += 1
        else:
            raise ValueError(f"conflicting duplicate delta override index: {index}")
    result["overrides"] = kept
    return result, collapsed


def normalize_batch_overrides(
    value: dict[str, Any], allowed: set[int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Discard unsafe/out-of-scope seed rows so bounded completion can repair them."""
    discarded: list[dict[str, str]] = []

    def record(item: Any, reason: str) -> None:
        encoded = json.dumps(item, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        discarded.append({"reason": reason, "sha256": hashlib.sha256(encoded).hexdigest()})

    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in value.get("overrides", []):
        index = item.get("index") if isinstance(item, dict) else None
        if not isinstance(index, int) or isinstance(index, bool) or index not in allowed:
            record(item, "non_batch_index")
            continue
        grouped.setdefault(index, []).append(item)
    kept: list[dict[str, Any]] = []
    duplicate_collapses = 0
    for index in sorted(grouped):
        rows = grouped[index]
        unique: list[dict[str, Any]] = []
        for row in rows:
            if row not in unique:
                unique.append(row)
        duplicate_collapses += len(rows) - len(unique)
        if len(unique) == 1:
            kept.append(unique[0])
        else:
            for row in rows:
                record(row, "conflicting_same_index")
    result = copy.deepcopy(value)
    result["overrides"] = kept
    return result, {
        "discarded_count": len(discarded),
        "duplicate_collapses": duplicate_collapses,
        "discarded": discarded,
    }


def normalize_optional_overlays(
    text: str, value: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep only exact, non-conflicting optional grammar overlays."""
    expected = {
        "start", "end", "text", "grammar_candidate_key", "pattern", "meaning_en"
    }
    valid: list[dict[str, Any]] = []
    discarded: list[dict[str, str]] = []

    def record(item: Any, reason: str) -> None:
        encoded = json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        discarded.append({"reason": reason, "sha256": hashlib.sha256(encoded).hexdigest()})

    for item in value.get("grammar_overlays", []):
        if not isinstance(item, dict) or set(item) != expected:
            record(item, "invalid_fields")
            continue
        start, end = item.get("start"), item.get("end")
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)
                or not 0 <= start < end <= len(text)):
            record(item, "invalid_offsets")
            continue
        if item.get("text") != text[start:end]:
            record(item, "surface_mismatch")
            continue
        if (not isinstance(item.get("grammar_candidate_key"), str)
                or not re.fullmatch(
                    r"[a-z][a-z0-9_.-]*",
                    item["grammar_candidate_key"],
                )
                or not isinstance(item.get("pattern"), str) or not item["pattern"].strip()
                or not isinstance(item.get("meaning_en"), str)
                or not item["meaning_en"].strip()):
            record(item, "empty_explanation")
            continue
        valid.append(item)

    by_span: dict[tuple[int, int], list[dict[str, Any]]] = {}
    span_order: list[tuple[int, int]] = []
    for item in valid:
        span = (item["start"], item["end"])
        if span not in by_span:
            span_order.append(span)
            by_span[span] = []
        if item not in by_span[span]:
            by_span[span].append(item)

    kept: list[dict[str, Any]] = []
    duplicate_collapses = 0
    for span in span_order:
        originals = [item for item in valid if (item["start"], item["end"]) == span]
        unique = by_span[span]
        duplicate_collapses += len(originals) - len(unique)
        if len(unique) == 1:
            kept.append(unique[0])
        else:
            for item in originals:
                record(item, "conflicting_same_span")

    result = copy.deepcopy(value)
    result["grammar_overlays"] = kept
    evidence = {
        "discarded_count": len(discarded),
        "duplicate_collapses": duplicate_collapses,
        "discarded": discarded,
    }
    return result, evidence


def _apply_boundary_patches(
    text: str, baseline: list[dict[str, Any]], patches: Any,
    allowed_spans: set[tuple[int, int]] | None = None,
) -> tuple[list[dict[str, Any]], set[int], dict[int, int]]:
    """Apply exact seed patches and map each untouched baseline token."""
    if not isinstance(patches, list):
        raise ValueError("boundary_patches must be an array")
    allowed = allowed_spans if allowed_spans is not None else {
        (item["start"], item["end"])
        for item in compact_boundary_targets(text, baseline)
    }
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for item in baseline:
        end = cursor + len(item["text"])
        offsets.append((cursor, end))
        cursor = end
    token_boundaries = {0, len(text)} | {end for _, end in offsets}
    output: list[dict[str, Any]] = []
    touched: set[int] = set()
    mapping: dict[int, int] = {}
    base_cursor = 0
    prior_end = 0
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {"start", "end", "segments"}:
            raise ValueError("invalid boundary patch fields")
        start, end, replacements = patch["start"], patch["end"], patch["segments"]
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)
                or start < prior_end or not 0 <= start < end <= len(text)):
            raise ValueError("boundary patches must be sorted and non-overlapping")
        if (start, end) not in allowed:
            raise ValueError("boundary patch targets a non-disagreement span")
        if start not in token_boundaries or end not in token_boundaries:
            raise ValueError("boundary patch must align to baseline token boundaries")
        while base_cursor < len(baseline) and offsets[base_cursor][1] <= start:
            mapping[base_cursor] = len(output)
            output.append(dict(baseline[base_cursor]))
            base_cursor += 1
        first = base_cursor
        while base_cursor < len(baseline) and offsets[base_cursor][0] < end:
            touched.add(base_cursor)
            base_cursor += 1
        if first == base_cursor:
            raise ValueError("boundary patch did not replace baseline tokens")
        if not isinstance(replacements, list) or not replacements:
            raise ValueError("boundary patch replacement must be nonempty")
        actual = "".join(item.get("text", "") for item in replacements
                         if isinstance(item, dict))
        if actual != text[start:end]:
            raise ValueError("boundary patch changed source text")
        for item in replacements:
            _validate_segment_metadata(item)
            han_length = sum("\u3400" <= char <= "\u9fff" for char in item["text"])
            if han_length > (6 if item["type"] == "name" else 4):
                raise ValueError("boundary patch segment exceeds tap-size limit")
            output.append(dict(item))
        prior_end = end
    while base_cursor < len(baseline):
        mapping[base_cursor] = len(output)
        output.append(dict(baseline[base_cursor]))
        base_cursor += 1
    if "".join(item["text"] for item in output) != text:
        raise ValueError("boundary patches do not reconstruct source")
    return output, touched, mapping


def boundary_covered_indices(
    text: str, baseline: list[dict[str, Any]], value: dict[str, Any]
) -> set[int]:
    """Validate boundary patches and return baseline indices they replace."""
    return _apply_boundary_patches(text, baseline, value.get("boundary_patches", []))[1]


def normalize_optional_boundary_patches(
    text: str, baseline: list[dict[str, Any]], value: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Salvage valid optional boundary proposals while keeping apply_delta strict."""
    discarded: list[dict[str, str]] = []

    def record(item: Any, reason: str) -> None:
        encoded = json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        discarded.append({"reason": reason, "sha256": hashlib.sha256(encoded).hexdigest()})

    unique: list[dict[str, Any]] = []
    duplicate_collapses = 0
    allowed = {(item["start"], item["end"])
               for item in compact_boundary_targets(text, baseline)}

    def hides_transparent_units(patch: dict[str, Any]) -> bool:
        quantity = re.compile(
            r"^[一二三四五六七八九十百千万两]+(?:多)?"
            r"(?:个|匹|两|斤|群|片|把|名|位|辆|座|件|只|本|张|杯)$"
        )
        for segment in patch.get("segments", []):
            surface = str(segment.get("text", ""))
            if quantity.fullmatch(surface):
                return True
            if surface.startswith("十分") and len(surface) > 2:
                return True
            if surface.startswith("很") and len(surface) > 1:
                return True
            if surface.startswith("往") and len(surface) > 1:
                return True
        return False

    for patch in value.get("boundary_patches", []):
        if patch in unique:
            duplicate_collapses += 1
            continue
        try:
            _apply_boundary_patches(text, baseline, [patch], allowed)
        except (TypeError, ValueError) as exc:
            record(patch, str(exc))
            continue
        if hides_transparent_units(patch):
            record(patch, "groups transparent quantity, degree, or direction units")
            continue
        unique.append(patch)

    # Competing analyses of the same or overlapping source span are optional
    # and unsafe to choose between deterministically, so discard the cluster.
    conflicted: set[int] = set()
    for left, first in enumerate(unique):
        for right in range(left + 1, len(unique)):
            second = unique[right]
            if first["start"] < second["end"] and second["start"] < first["end"]:
                conflicted.update((left, right))
    kept = []
    for index, patch in enumerate(unique):
        if index in conflicted:
            record(patch, "conflicting_or_overlapping_patch")
        else:
            kept.append(patch)
    kept.sort(key=lambda patch: (patch["start"], patch["end"]))
    # This is an assertion on the normalizer; final apply_delta validates again.
    _apply_boundary_patches(text, baseline, kept, allowed)
    result = copy.deepcopy(value)
    result["boundary_patches"] = kept
    return result, {
        "discarded_count": len(discarded),
        "duplicate_collapses": duplicate_collapses,
        "discarded": discarded,
    }


def apply_delta(text: str, baseline: list[dict[str, Any]], unknown: set[int],
                value: dict[str, Any]) -> dict[str, Any]:
    segments, touched, mapping = _apply_boundary_patches(
        text, baseline, value.get("boundary_patches", []),
    )
    allowed = contextual_delta_targets(baseline, unknown)
    seen: set[int] = set()
    for override in value.get("overrides", []):
        index = override.get("index")
        if not isinstance(index, int) or not 0 <= index < len(baseline) or index in seen:
            raise ValueError("invalid or duplicate delta index")
        if index not in allowed:
            raise ValueError("delta returned a non-target index")
        if index in touched:
            raise ValueError("delta override overlaps a boundary patch")
        output_index = mapping[index]
        if segments[output_index]["type"] == "punctuation":
            raise ValueError("delta cannot override punctuation")
        seen.add(index)
        kind = override["type"]
        if kind == "particle" and baseline[index]["text"] not in PARTICLE_SURFACES:
            kind = "word"
        segments[output_index].update({
            "type": kind,
            "pinyin": override["pinyin"],
            "meaning_en": override["meaning_en"],
        })
    missing = unknown - seen - touched
    if missing:
        raise ValueError(f"delta omitted required meanings: {sorted(missing)[:20]}")
    candidate = ChapterHarness.canonicalize_annotation_candidate(
        text, {"segments": segments, "grammar_overlays": value.get("grammar_overlays")}
    )
    issues = ChapterHarness.annotation_contract_issues(text, candidate)
    if issues:
        raise ValueError(f"delta annotation contract failed: {issues}")
    return candidate


async def run(args: argparse.Namespace) -> dict[str, Any]:
    source, output = Path(args.input).resolve(), Path(args.output_dir).resolve()
    text = source.read_text(encoding="utf-8")
    output.mkdir(parents=True, exist_ok=True)
    (output / "chapter.txt").write_text(text, encoding="utf-8")
    baseline, unknown = deterministic_baseline(text)
    runner = CodexRunner(output, args.model, asyncio.Semaphore(1), args.timeout)
    started = utc_now()
    raw = await runner.call("delta", delta_prompt(text, baseline, unknown, args.level),
                            SCHEMA, args.effort, refresh=args.refresh)
    annotation = apply_delta(text, baseline, unknown, raw)
    reader = {
        "title": source.stem, "level": args.level.upper(), "text": text, **annotation,
        "annotation_audit": {
            "mode": "deterministic_baseline_one_delta_call",
            "chapter_sha256": sha256_bytes(text.encode()), "chunks": 1, "model_calls": 1,
            "dictionary_or_deterministic_segments": len(baseline) - len(unknown),
            "required_luna_meanings": len(unknown), "deterministically_validated": True,
            "all_reviewed": False,
        },
    }
    atomic_json(output / "reader.json", reader)
    report = {"status": "complete", "started_at": started, "completed_at": utc_now(),
              "model_calls": 1, "segments": len(baseline), "unknown": len(unknown),
              "overrides": len(raw["overrides"]), "overlays": len(raw["grammar_overlays"]),
              "reconstructs": True, "reader": str(output / "reader.json")}
    atomic_json(output / "report.json", report)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", required=True)
    result.add_argument("--output-dir", required=True)
    result.add_argument("--level", default="hsk4")
    result.add_argument("--model", default="gpt-6-luna")
    result.add_argument("--effort", default="low")
    result.add_argument("--timeout", type=int, default=1200)
    result.add_argument("--refresh", action="store_true")
    return result


def main() -> int:
    print(json.dumps(asyncio.run(run(parser().parse_args())), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
