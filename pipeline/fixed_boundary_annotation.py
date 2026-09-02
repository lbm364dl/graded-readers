#!/usr/bin/env python3
"""One-call Chinese annotation with deterministic, immutable Jieba boundaries."""

from __future__ import annotations

import argparse
import asyncio
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

import jieba
import jieba.posseg as pseg
from pypinyin import Style, lazy_pinyin

from pipeline.agent_harness import ChapterHarness, CodexRunner, ROOT, split_annotation_chunks
from pipeline.annotate_chinese import atomic_json, sha256_bytes, utc_now
from src.config import ALL_PUNCTUATION
from src.segmentation.segmenter import ChineseSegmenter

SCHEMA = ROOT / "pipeline" / "schemas" / "fixed-annotation.schema.json"
CORRECTION_SCHEMA = ROOT / "pipeline" / "schemas" / "fixed-annotation-correction.schema.json"
PARTICLES = {"u", "ul", "uz", "ug", "uj", "uv", "ud", "y", "e", "zg"}
# Conservative exceptions for compounds that the HSK lists do not contain but
# that should remain individually tappable lexical units in literary prose.
LITERARY_COMPOUNDS = {
    "笑谈", "相逢", "宽和", "后宫", "盗贼", "秋月", "春风",
    # Common lexical verbs/temporal words that the HSK-driven Jieba mutation
    # otherwise breaks into characters.
    "火攻", "斩杀", "改姓", "少时",
    # Common learner-facing compounds that HSK-weighted Jieba can split or
    # attach to a neighboring word in literary narrative.
    "一天", "第二天", "心里", "一直", "本事", "祭礼", "兄弟",
    "百姓", "刀剑", "东西", "乱事",
}
CHAPTER_NAMES = {"封谞", "张让", "十常侍", "刘胜", "灵帝", "靖王", "长江"}
CLASSICAL_PREDICATES = {"亡", "贫"}


def _punctuation_char(char: str) -> bool:
    return char.isspace() or char in ALL_PUNCTUATION or unicodedata.category(char).startswith("P")


def fixed_segments(text: str) -> list[str]:
    """Return POS-aware Jieba surfaces, splitting mixed punctuation losslessly."""
    ChineseSegmenter()  # install the project's HSK dictionary first
    result: list[str] = []
    for pair in pseg.cut(text):
        token = pair.word
        start = 0
        for index in range(1, len(token) + 1):
            if index == len(token) or _punctuation_char(token[index]) != _punctuation_char(token[start]):
                result.append(token[start:index])
                start = index
    if "".join(result) != text or any(not item for item in result):
        raise ValueError("fixed segmentation did not reconstruct input")
    return result


def refined_fixed_segments(
    text: str,
    *,
    protected_names: set[str] | frozenset[str] | None = None,
    protected_compounds: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Experimental conservative refinement of POS/Jieba surfaces.

    This intentionally covers common compositional shapes and a very small
    literary/name exception set; it is a boundary experiment, not a claim of
    general Chinese morphological perfection.
    """
    base = fixed_segments(text)
    # First restore known compounds/names that the HSK-dictionary mutation can
    # cause Jieba to split. Longest candidates win at each position.
    joined: list[str] = []
    index = 0
    names = CHAPTER_NAMES | {
        word for word in (protected_names or ()) if 1 < len(word) <= 6
    }
    compounds = LITERARY_COMPOUNDS | {
        word for word in (protected_compounds or ()) if 1 < len(word) <= 4
    }
    protected = sorted(compounds | names, key=len, reverse=True)
    # Establish exact protected-span boundaries before joining. A tokenizer
    # can return ``有乱`` + ``事`` even though the reviewed word is ``乱事``;
    # splitting at the raw-text start lets the ordinary joining loop restore
    # ``有`` + ``乱事`` losslessly.
    protected_boundaries = {
        boundary
        for word in protected
        for match in re.finditer(re.escape(word), text)
        for boundary in (match.start(), match.end())
    }
    split_base: list[str] = []
    cursor = 0
    for token in base:
        end = cursor + len(token)
        cuts = [cursor, *sorted(
            boundary for boundary in protected_boundaries
            if cursor < boundary < end
        ), end]
        split_base.extend(text[left:right] for left, right in zip(cuts, cuts[1:]))
        cursor = end
    base = split_base
    while index < len(base):
        match = None
        consumed = 0
        for word in protected:
            surface = ""
            for end in range(index, len(base)):
                surface += base[end]
                if surface == word:
                    match, consumed = word, end - index + 1
                    break
                if not word.startswith(surface):
                    break
            if match:
                break
        if match:
            joined.append(match)
            index += consumed
        else:
            joined.append(base[index])
            index += 1

    result: list[str] = []
    hsk = ChineseSegmenter._hsk_words
    for token in joined:
        if (len(token) < 2 or token in hsk or token in compounds
                or token in names or all(_punctuation_char(c) for c in token)):
            result.append(token)
            continue
        embedded = next((word for word in protected if token.endswith(word) and token != word), None)
        if embedded:
            result.extend((token[:-len(embedded)], embedded))
            continue
        name_prefix = next(
            (word for word in names if token.startswith(word) and token != word),
            None,
        )
        if name_prefix:
            result.extend((name_prefix, token[len(name_prefix):]))
            continue
        if token.startswith("由") and len(token) > 1:
            result.extend(("由", token[1:]))
            continue
        if token[0] in {"之", "因"} and len(token) == 2:
            result.extend(token)
            continue
        chars = [(part.word, part.flag) for char in token for part in pseg.cut(char)]
        flags = [flag for _, flag in chars]
        quantity = re.fullmatch(r"([一二三四五六七八九十百千万两]+)(.+)", token)
        if quantity:
            result.extend(quantity.groups())
            continue
        # Productive learner-facing phrases: numeral+unit/noun, V+object,
        # subject+classical predicate, or an accidental name/noun+predicate.
        split_all = (
            flags[0].startswith("m")
            or (len(token) == 2 and flags[0].startswith("v") and flags[1].startswith("n"))
            or (len(token) == 2 and flags[0].startswith("v") and flags[1].startswith("a"))
            or (len(token) == 2 and token[-1] in CLASSICAL_PREDICATES)
            or (len(token) == 2 and token[0] in {"必", "共", "孝", "当"})
            or (len(token) == 2 and token[0] in {"推"})
            or token in {"鸡化雄", "马骑"}
        )
        if split_all:
            result.extend(token)
        elif len(token) >= 3 and (flags[-1][0] in {"a", "v"} or token[-1] in "东西南北"):
            result.extend((token[:-1], token[-1]))
        elif len(token) == 2 and pseg.lcut(token)[0].flag in {"d", "b"}:
            result.extend(token)
        else:
            result.append(token)
    # Context disambiguates 耳垂 here as subject+predicate, not 'earlobe'.
    contextual: list[str] = []
    index = 0
    while index < len(result):
        token = result[index]
        if token == "耳垂" and contextual and contextual[-1] == "两":
            contextual.extend(("耳", "垂"))
        elif index + 1 < len(result) and token + result[index + 1] in {"后宫", "靖王"}:
            contextual.append(token + result[index + 1])
            index += 1
        else:
            contextual.append(token)
        index += 1
    result = contextual
    if "".join(result) != text:
        raise ValueError("refined segmentation did not reconstruct input")
    return result


@lru_cache(maxsize=4)
def _load_dictionary(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dictionary_hints(surfaces: list[str], dictionary_path: Path | None = None) -> list[dict[str, Any]]:
    """Build deterministic metadata hints without weakening exact boundaries.

    The app dictionary is generated from the project's Chinese dictionary data.
    Unknown items still receive deterministic pinyin; Luna only has to supply or
    contextualize their meanings and types.
    """
    path = dictionary_path or ROOT / "app" / "assets" / "dictionary.json"
    dictionary = _load_dictionary(str(path.resolve()))
    hints: list[dict[str, Any]] = []
    for index, surface in enumerate(surfaces):
        item: dict[str, Any] = {"index": index, "text": surface}
        if all(_punctuation_char(char) for char in surface):
            item.update({"pinyin_hint": "", "meaning_hints": []})
        else:
            entry = dictionary.get(surface, {})
            item["pinyin_hint"] = entry.get("p") or " ".join(
                lazy_pinyin(surface, style=Style.TONE, neutral_tone_with_five=False)
            )
            item["meaning_hints"] = entry.get("d", [])[:2]
        hints.append(item)
    return hints


def prompt_for(chunk: str, surfaces: list[str], level: str) -> str:
    indexed = dictionary_hints(surfaces)
    return f"""Annotate this immutable {level.upper()} Chinese reader chunk.

The indexed segmentation below is fixed. Return exactly one segment for every item,
in the same order, with the identical index and text. Never merge, split, omit, add,
normalize, or alter boundaries. Supply tone-mark pinyin and a short contextual English
meaning for lexical items. Punctuation/whitespace must have type punctuation and blank
pinyin/meaning. Use particle only for grammatical particles, name for proper names, and
idiom only when the entire fixed surface is a real lexicalized idiom; otherwise word.

Add genuinely useful grammar overlays (constructions, not ordinary sentence
translations). Directional and resultative complements that materially shape a
predicate are high priority, even when fixed boundaries keep the verb and complement
as separate tokens. Explain the main verb, the complement's contribution, and their
combined contextual meaning; never give the complement the whole predicate's gloss.
Give every overlay a concise lowercase grammar_candidate_key. It is a provisional
clustering hint for a later unification pass, not a canonical grammar lesson ID, so
choose a descriptive slug without assuming other agents will use the same wording.
Overlay start/end are Python character offsets within CHUNK, end
exclusive; text must equal CHUNK[start:end]. Keep explanations concise. Self-check the
fixed list, punctuation fields, meanings, and every offset before returning JSON.

CHUNK:
{chunk}

FIXED SEGMENTS WITH DETERMINISTIC DICTIONARY HINTS (contextualize rather than
blindly copying a hint; an empty meaning list means you must supply the meaning):
{json.dumps(indexed, ensure_ascii=False)}"""


def boundary_correction_prompt(chunk: str, metadata: Any = None) -> str:
    """Expose neutral proposals to a future constrained correction call."""
    # Lazy import avoids a module cycle: the proposal helper uses the refined
    # segmenter defined above.
    from pipeline.chinese_boundary_proposals import (
        boundary_proposals, constrained_correction_prompt,
    )
    return constrained_correction_prompt(boundary_proposals(chunk, metadata))


def validate_result(chunk: str, surfaces: list[str], value: dict[str, Any]) -> dict[str, Any]:
    segments = value.get("segments")
    if not isinstance(segments, list) or len(segments) != len(surfaces):
        raise ValueError("model changed fixed segment count")
    canonical = []
    for index, (surface, item) in enumerate(zip(surfaces, segments)):
        if item.get("index") != index or item.get("text") != surface:
            raise ValueError(f"model changed fixed segment {index}")
        punctuation = all(_punctuation_char(char) for char in surface)
        if punctuation:
            if item.get("type") != "punctuation" or item.get("pinyin") or item.get("meaning_en"):
                raise ValueError(f"invalid punctuation annotation at {index}")
        elif not str(item.get("pinyin", "")).strip() or not str(item.get("meaning_en", "")).strip():
            raise ValueError(f"missing lexical annotation at {index}")
        canonical.append({key: item[key] for key in ("text", "type", "pinyin", "meaning_en")})
    candidate = ChapterHarness.canonicalize_annotation_candidate(
        chunk, {"segments": canonical, "grammar_overlays": value.get("grammar_overlays")}
    )
    overlays = candidate.get("grammar_overlays")
    if not isinstance(overlays, list):
        raise ValueError("grammar_overlays must be a list")
    for overlay in overlays:
        start, end = overlay.get("start"), overlay.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(chunk)):
            raise ValueError("invalid grammar overlay range")
        if chunk[start:end] != overlay.get("text"):
            raise ValueError("grammar overlay text does not match offsets")
    result = {"segments": canonical, "grammar_overlays": overlays}
    issues = ChapterHarness.annotation_contract_issues(chunk, result)
    if issues:
        raise ValueError(f"publisher annotation contract failed: {issues}")
    return result


def correction_prompt(
    chunk: str, annotation: dict[str, Any], findings: dict[str, Any] | None = None
) -> str:
    # One lossless row per current token replaces the former duplicated JSON
    # annotation plus TSV table.  Positional columns make the table compact but
    # keep both token indices and character spans explicit.
    char_offset = 0
    table: list[list[Any]] = []
    for index, segment in enumerate(annotation["segments"]):
        end = char_offset + len(segment["text"])
        table.append([
            index, char_offset, end, segment["text"], segment["type"],
            segment["pinyin"], segment["meaning_en"],
        ])
        char_offset = end
    from pipeline.chinese_boundary_proposals import boundary_proposals
    proposals = boundary_proposals(chunk, {"current_annotation": annotation})
    # CHUNK already supplies every character. Proposal segment objects formerly
    # repeated it twice, then repeated their ends in boundary arrays and again
    # in disagreement regions. Boundaries plus the exact CHUNK are equivalent.
    compact_evidence = {
        "contract": proposals["contract"],
        "text_length": proposals["text_length"],
        "plain_jieba_boundaries": proposals["proposals"]["plain_jieba"]["boundaries"],
        "refined_boundaries": proposals["proposals"]["refined"]["boundaries"],
        "gazetteer_spans": [
            [item["start"], item["end"], item["text"], item["kind"],
             item["metadata_sources"]]
            for item in proposals["gazetteer_spans"]
        ],
        "disagreement_regions": [
            [item["start"], item["end"], item["plain_boundaries"],
             item["refined_boundaries"]]
            for item in proposals["disagreement_regions"]
        ],
        "selected_boundaries": None,
    }
    compact_json = lambda value: json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    )
    review = "" if findings is None else (
        f"\nINDEPENDENT REVIEW FINDINGS:\n{compact_json(findings)}\n"
    )
    return f"""Return only JSON matching the supplied schema. Correct this Chinese
tap-to-explain annotation using the smallest set of boundary/metadata patches.
Each patch replaces the adjacent existing SEGMENT TOKEN range [start_index,end_index).
PATCH start_index/end_index MUST be TOKEN_INDEX numbers from CURRENT SEGMENT TABLE.
They are NEVER character offsets. For example, to replace table tokens 12 and 13,
return start_index=12,end_index=14 even if their text begins at character 19.
Replacement text concatenated must exactly equal the replaced source text. Patches
must be sorted and non-overlapping. Split compositional phrases into dictionary
words and particles; merge genuine compounds, idioms, complete names, and compact
learner-facing verb-complement predicates. Meanings
explain only the tapped segment in context. Return the complete corrected set of
genuine localized grammar overlays; treat meaning-changing directional/resultative
complements as high-value grammar rather than optional detail. Omit clause summaries
and lexical paraphrases. Every overlay needs a concise lowercase
grammar_candidate_key. It is only a provisional clustering hint, not a canonical
lesson ID; preserve existing keys unless an explicit grammar finding requires
replacing the overlay.
Patch only exact surfaces named by INDEPENDENT REVIEW FINDINGS. Do not improve
unmentioned tokens. Change grammar overlays only for explicit grammar findings;
otherwise return the existing overlays unchanged.
Only grammar_overlays.start/end and the neutral boundary evidence use zero-based Python
CHARACTER offsets into CHUNK. Never copy those character offsets into patch start_index
or end_index. If an existing segment is already right, do not patch it. Never change,
omit, or normalize source characters.

CHUNK:\n{chunk}

CURRENT SEGMENT TABLE (one JSON row per token; patch indices must come from
TOKEN_INDEX, never CHAR_START/CHAR_END):
COLUMNS=[TOKEN_INDEX,CHAR_START,CHAR_END,TEXT,TYPE,PINYIN,MEANING_EN]
{compact_json(table)}

CURRENT GRAMMAR OVERLAYS:\n{compact_json(annotation['grammar_overlays'])}

NEUTRAL BOUNDARY EVIDENCE:
All start/end/boundaries values inside this evidence are CHARACTER OFFSETS for
locating disagreements only. Convert the desired span to TOKEN_INDEX values using
CURRENT SEGMENT TABLE before writing a patch.
EVIDENCE FORMAT: plain_jieba_boundaries/refined_boundaries are neutral candidate
CHARACTER boundaries in exact CHUNK. gazetteer_spans rows are
[START,END,TEXT,KIND,METADATA_SOURCES]. disagreement_regions rows are
[START,END,PLAIN_BOUNDARIES,REFINED_BOUNDARIES]. Neither tokenizer is authoritative.
COMPACT BOUNDARY EVIDENCE JSON:
{compact_json(compact_evidence)}
{review}"""


def _validate_segment_metadata(segment: dict[str, Any]) -> None:
    expected = {"text", "type", "pinyin", "meaning_en"}
    if set(segment) != expected or not isinstance(segment.get("text"), str) or not segment["text"]:
        raise ValueError("invalid replacement segment fields")
    if segment.get("type") not in {"word", "particle", "name", "idiom", "punctuation"}:
        raise ValueError("invalid replacement segment type")
    punctuation = all(_punctuation_char(char) for char in segment["text"])
    if punctuation:
        if segment["type"] != "punctuation" or segment.get("pinyin") or segment.get("meaning_en"):
            raise ValueError("invalid replacement punctuation metadata")
    elif (segment["type"] == "punctuation" or not str(segment.get("pinyin", "")).strip()
          or not str(segment.get("meaning_en", "")).strip()):
        raise ValueError("invalid replacement lexical metadata")


def _review_surface(value: Any) -> str:
    """Normalize reviewer display separators without changing Chinese text."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s*(?:/|／|\|)\s*", "", value).strip()


def _review_scopes(
    chunk: str, segments: list[dict[str, Any]], findings: dict[str, Any], *, grammar: bool
) -> tuple[set[tuple[int, int]], list[tuple[int, int]], list[dict[str, Any]]]:
    """Resolve unambiguous review surfaces to minimal token/character spans."""
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for segment in segments:
        end = cursor + len(segment["text"])
        offsets.append((cursor, end))
        cursor = end
    token_ranges: set[tuple[int, int]] = set()
    char_ranges: list[tuple[int, int]] = []
    discarded: list[dict[str, Any]] = []
    issues = findings.get("issues", []) if isinstance(findings, dict) else []
    for issue in issues if isinstance(issues, list) else []:
        if not isinstance(issue, dict):
            continue
        problem = str(issue.get("problem", "")).lower()
        is_grammar = "grammar" in problem
        if is_grammar != grammar:
            continue
        raw_surface = issue.get("segment_text")
        surface = _review_surface(raw_surface)
        if not surface:
            discarded.append({"kind": "issue", "reason": "empty_surface"})
            continue
        has_offset = "start" in issue or "end" in issue
        if has_offset:
            start, end = issue.get("start"), issue.get("end")
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or not 0 <= start < end <= len(chunk)
                    or raw_surface != chunk[start:end]):
                discarded.append({"kind": "issue", "surface": surface,
                                  "reason": "invalid_offset"})
                continue
        else:
            # Legacy cached reviews had no offsets. They remain usable only
            # when their surface resolves to exactly one occurrence.
            occurrences = [match.span() for match in re.finditer(re.escape(surface), chunk)]
            if len(occurrences) != 1:
                discarded.append({
                    "kind": "issue", "surface": surface,
                    "reason": "ambiguous_surface" if occurrences else "surface_not_found",
                    "occurrences": len(occurrences),
                })
                continue
            start, end = occurrences[0]
        if raw_surface != chunk[start:end] and has_offset:
            discarded.append({
                "kind": "issue", "surface": surface, "reason": "invalid_offset",
            })
            continue
        owners = [index for index, (left, right) in enumerate(offsets)
                  if left < end and start < right]
        if not owners:
            discarded.append({"kind": "issue", "surface": surface,
                              "reason": "no_token_range"})
            continue
        token_ranges.add((owners[0], owners[-1] + 1))
        char_ranges.append((start, end))
    return token_ranges, char_ranges, discarded


def review_scoped_correction(
    chunk: str,
    annotation: dict[str, Any],
    correction: dict[str, Any],
    findings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Discard model mutations not authorized by concrete review findings."""
    base = annotation.get("segments")
    if not isinstance(base, list):
        raise ValueError("segments must be an array")
    lexical_ranges, lexical_chars, lexical_evidence = _review_scopes(
        chunk, base, findings, grammar=False
    )
    # An under-grouping finding necessarily authorizes incorporating one
    # immediately adjacent token; the review surface often names only the
    # truncated token (for example 南华 -> 南华老仙). Keep this expansion small
    # and deterministic rather than accepting arbitrary neighboring edits.
    lexical_allowed = set(lexical_ranges)
    for issue in findings.get("issues", []) if isinstance(findings, dict) else []:
        if not isinstance(issue, dict):
            continue
        suggestion = str(issue.get("suggested_fix", "")).lower()
        requests_boundary_change = (
            issue.get("problem") == "under_grouped"
            or any(marker in suggestion for marker in (
                "segment as", "resegment", "split", "merge",
                "分成", "分为", "拆分", "合并",
            ))
        )
        if not requests_boundary_change:
            continue
        issue_ranges, _, _ = _review_scopes(
            chunk, base, {"issues": [issue]}, grammar=False,
        )
        for start, end in issue_ranges:
            if start > 0:
                lexical_allowed.add((start - 1, end))
            if end < len(base):
                lexical_allowed.add((start, end + 1))
            if requests_boundary_change:
                # A malformed baseline can split the requested expression
                # across three tiny tokens (for example 退 / 入长 / 社 while
                # the repair is 退入 / 长社). Permit no more than two adjacent
                # tokens, and only for an explicit boundary-change request.
                if start > 1:
                    lexical_allowed.add((start - 2, end))
                if start > 0 and end < len(base):
                    lexical_allowed.add((start - 1, end + 1))
                if end + 1 < len(base):
                    lexical_allowed.add((start, end + 2))
    grammar_ranges, grammar_chars, grammar_evidence = _review_scopes(
        chunk, base, findings, grammar=True
    )
    accepted_patches: list[dict[str, Any]] = []
    discarded_patches: list[dict[str, Any]] = []
    raw_patches = correction.get("patches", [])
    if not isinstance(raw_patches, list):
        raise ValueError("patches must be an array")
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for segment in base:
        end = cursor + len(segment["text"])
        offsets.append((cursor, end))
        cursor = end
    for patch in raw_patches:
        key = (patch.get("start_index"), patch.get("end_index")) if isinstance(patch, dict) else None
        contained = False
        if (key and all(isinstance(item, int) for item in key)
                and 0 <= key[0] < key[1] <= len(offsets)):
            patch_chars = (offsets[key[0]][0], offsets[key[1] - 1][1])
            contained = any(left <= patch_chars[0] < patch_chars[1] <= right
                            for left, right in lexical_chars)
        if key in lexical_allowed or contained:
            accepted_patches.append(patch)
        else:
            discarded_patches.append({"range": list(key) if key else None,
                                      "reason": "outside_unambiguous_review_scope"})

    base_overlays = annotation.get("grammar_overlays", [])
    if not isinstance(base_overlays, list):
        raise ValueError("grammar_overlays must be an array")
    raw_overlays = correction.get("grammar_overlays", [])
    if not isinstance(raw_overlays, list):
        raise ValueError("grammar_overlays must be an array")
    if not grammar_ranges:
        overlays = list(base_overlays)
        discarded_overlays = len(raw_overlays)
    else:
        def overlaps_scope(item: dict[str, Any]) -> bool:
            start, end = item.get("start"), item.get("end")
            return (isinstance(start, int) and isinstance(end, int)
                    and any(start < right and left < end for left, right in grammar_chars))

        overlays = [item for item in base_overlays
                    if not isinstance(item, dict) or not overlaps_scope(item)]
        accepted_overlays = [item for item in raw_overlays
                             if isinstance(item, dict) and overlaps_scope(item)]
        overlays.extend(accepted_overlays)
        discarded_overlays = len(raw_overlays) - len(accepted_overlays)
    scoped = {"patches": accepted_patches, "grammar_overlays": overlays}
    evidence = {
        "accepted_patch_count": len(accepted_patches),
        "discarded_patches": discarded_patches,
        "discarded_overlay_count": discarded_overlays,
        "discarded_issue_scopes": lexical_evidence + grammar_evidence,
    }
    return scoped, evidence


def apply_correction_patches(
    chunk: str, annotation: dict[str, Any], correction: dict[str, Any]
) -> dict[str, Any]:
    """Apply strictly adjacent, lossless annotation patches."""
    base = annotation.get("segments")
    patches = correction.get("patches")
    if not isinstance(base, list) or not isinstance(patches, list):
        raise ValueError("segments and patches must be arrays")
    output: list[dict[str, Any]] = []
    cursor = 0
    for patch in patches:
        if set(patch) != {"start_index", "end_index", "segments"}:
            raise ValueError("invalid correction patch fields")
        start, end, replacements = patch["start_index"], patch["end_index"], patch["segments"]
        if (not isinstance(start, int) or not isinstance(end, int)
                or not cursor <= start < end <= len(base)):
            raise ValueError("patch ranges must be sorted, adjacent ranges and non-overlapping")
        if not isinstance(replacements, list) or not replacements:
            raise ValueError("patch replacement must be nonempty")
        expected = "".join(item["text"] for item in base[start:end])
        actual = "".join(item.get("text", "") for item in replacements)
        if actual != expected:
            raise ValueError("patch changed source text")
        for item in replacements:
            _validate_segment_metadata(item)
        output.extend(base[cursor:start])
        output.extend(replacements)
        cursor = end
    output.extend(base[cursor:])
    candidate = {"segments": output, "grammar_overlays": correction.get("grammar_overlays")}
    candidate = ChapterHarness.canonicalize_annotation_candidate(chunk, candidate)
    if "".join(item["text"] for item in output) != chunk:
        raise ValueError("corrected segments do not reconstruct chunk")
    issues = ChapterHarness.annotation_contract_issues(chunk, candidate)
    if issues:
        raise ValueError(f"corrected annotation contract failed: {issues}")
    return candidate


async def run(args: argparse.Namespace) -> dict[str, Any]:
    source_path, output = Path(args.input).resolve(), Path(args.output_dir).resolve()
    chapter_bytes = source_path.read_bytes()
    chapter = chapter_bytes.decode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    (output / "chapter.txt").write_bytes(chapter_bytes)
    maximum = args.chunk_maximum if args.chunk_maximum is not None else 260
    if maximum < args.chunk_size:
        raise ValueError("chunk maximum must be at least chunk size")
    chunks = split_annotation_chunks(chapter, args.chunk_size, maximum)
    runner = CodexRunner(output, args.model, asyncio.Semaphore(args.concurrency), args.timeout)

    async def one(index: int, chunk: str) -> dict[str, Any]:
        surfaces = refined_fixed_segments(chunk) if args.refined_boundaries else fixed_segments(chunk)
        raw = await runner.call(
            f"fixed-{index:03d}", prompt_for(chunk, surfaces, args.level), SCHEMA,
            args.effort, refresh=args.refresh,
        )
        return validate_result(chunk, surfaces, raw)

    started = utc_now()
    results = await asyncio.gather(*(one(i, chunk) for i, chunk in enumerate(chunks)))
    segments = [segment for result in results for segment in result["segments"]]
    if "".join(item["text"] for item in segments) != chapter:
        raise ValueError("assembled fixed annotations do not reconstruct chapter")
    overlays, offset = [], 0
    for chunk, result in zip(chunks, results):
        overlays.extend({**item, "start": item["start"] + offset, "end": item["end"] + offset}
                        for item in result["grammar_overlays"])
        offset += len(chunk)
    reader = {
        "title": source_path.stem, "level": args.level.upper(), "text": chapter,
        "segments": segments, "grammar_overlays": overlays,
        "annotation_audit": {
            "mode": "fixed_boundaries_one_call", "chapter_sha256": sha256_bytes(chapter_bytes),
            "chunks": len(chunks), "model_calls": len(chunks),
            "deterministically_validated": True,
            "all_reviewed": False,
            "review_note": "Experimental one-call output; no independent semantic review.",
        },
    }
    atomic_json(output / "reader.json", reader)
    report = {
        "status": "complete", "started_at": started, "completed_at": utc_now(),
        "chunks": len(chunks), "model_calls": len(chunks), "segments": len(segments),
        "max_segment_characters": max(map(lambda item: len(item["text"]), segments)),
        "reconstructs": True, "reader": str(output / "reader.json"),
    }
    atomic_json(output / "report.json", report)
    return report


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--input", required=True)
    value.add_argument("--output-dir", required=True)
    value.add_argument("--level", default="hsk4")
    value.add_argument("--model", default="gpt-5.6-luna")
    value.add_argument("--effort", default="low")
    value.add_argument("--chunk-size", type=int, default=200)
    value.add_argument("--chunk-maximum", type=int)
    value.add_argument("--refined-boundaries", action="store_true")
    value.add_argument("--concurrency", type=int, default=3)
    value.add_argument("--timeout", type=int, default=900)
    value.add_argument("--refresh", action="store_true")
    return value


def main() -> int:
    result = asyncio.run(run(parser().parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
