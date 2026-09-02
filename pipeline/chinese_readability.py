"""Mechanical learner-readability checks for Chinese adaptations.

Character coverage is useful, but it cannot distinguish a beginner sentence
from a compressed literary sentence made out of individually familiar hanzi.
This module adds a conservative word-token and sentence-shape gate. Sentence
and clause ceilings widen progressively so higher-level prose can develop
normally without falling back into compressed synopsis style.
"""

from __future__ import annotations

import csv
from functools import lru_cache
import math
from pathlib import Path
import re
from typing import Any, Iterable

import jieba


ROOT = Path(__file__).resolve().parent.parent
_HAN_RUN = re.compile(r"[\u3400-\u9fff]+")
_SENTENCE_BREAK = re.compile(r"[。！？!?]+|\n+")
_CLAUSE_BREAK = re.compile(r"[，,；;：:。！？!?]+|\n+")

# HSK1 prose should read like an explicit beginner narrative, not a synopsis.
# Story words and names are exempted separately, so this budget is for the
# remaining vocabulary.  Sentence limits are guardrails against compression,
# not a claim that every good beginner sentence has one exact length.
HSK1_MAX_ABOVE_LEVEL_WORD_RATIO = 0.15
HSK1_MAX_SENTENCE_CJK = 18
HSK1_MAX_CLAUSE_CJK = 10
LOWER_LEVEL_QUALIFICATION_RATIOS = {
    "hsk1": 0.15,
    "hsk2": 0.12,
    "hsk3": 0.10,
    "hsk4": 0.08,
    "hsk5": 0.06,
}
PROSE_SHAPE_LIMITS = {
    "hsk1": (18, 10),
    "hsk2": (28, 14),
    "hsk3": (42, 22),
    "hsk4": (56, 30),
    "hsk5": (72, 38),
    "hsk6": (90, 48),
}
PARAGRAPH_SHAPE_LIMITS = {
    "hsk1": (5, 12, 90),
    "hsk2": (7, 16, 140),
    "hsk3": (9, 24, 190),
}
HELP_MAX_CJK = {
    "hsk1": {"explanation": 24, "example": 20},
    "hsk2": {"explanation": 28, "example": 24},
    "hsk3": {"explanation": 34, "example": 30},
    "hsk4": {"explanation": 42, "example": 38},
    "hsk5": {"explanation": 52, "example": 46},
    "hsk6": {"explanation": 64, "example": 56},
}


def _level_number(level: str) -> int:
    match = re.fullmatch(r"hsk([1-7])", level.lower())
    if match is None:
        raise ValueError(f"unsupported HSK level: {level}")
    return int(match.group(1))


def lower_level_qualification_ratio(target_level: str) -> float | None:
    """Return the stable lower-band threshold used for distinctiveness."""
    number = _level_number(target_level)
    if number == 1:
        return None
    return LOWER_LEVEL_QUALIFICATION_RATIOS[f"hsk{number - 1}"]


@lru_cache(maxsize=7)
def cumulative_words(level: str) -> frozenset[str]:
    number = _level_number(level)
    words: set[str] = set()
    for current in range(1, number + 1):
        label = f"hsk{current}" if current <= 6 else "hsk7to9"
        path = ROOT / "data" / "chinese" / "words" / f"{label}_words.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            words.update(
                row["word"].strip() for row in csv.DictReader(handle)
                if row.get("word", "").strip()
            )
    return frozenset(words)


@lru_cache(maxsize=7)
def words_at_level(level: str) -> frozenset[str]:
    """Return words introduced by one HSK 3.0 band.

    ``hsk7`` represents the combined advanced 7--9 list distributed by the
    project.  Keeping this separate from ``cumulative_words`` lets reader
    metadata say where a lookup-only word first appears instead of merely
    reporting that it is outside the current target.
    """
    number = _level_number(level)
    label = f"hsk{number}" if number <= 6 else "hsk7to9"
    path = ROOT / "data" / "chinese" / "words" / f"{label}_words.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return frozenset(
            row["word"].strip() for row in csv.DictReader(handle)
            if row.get("word", "").strip()
        )


@lru_cache(maxsize=7)
def cumulative_characters(level: str) -> frozenset[str]:
    number = _level_number(level)
    characters: set[str] = set()
    for current in range(1, number + 1):
        label = f"hsk{current}" if current <= 6 else "hsk7to9"
        path = ROOT / "data" / "chinese" / "characters" / f"{label}_chars.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            characters.update(
                row["character"].strip() for row in csv.DictReader(handle)
                if row.get("character", "").strip()
            )
    return frozenset(characters)


@lru_cache(maxsize=7)
def characters_at_level(level: str) -> frozenset[str]:
    number = _level_number(level)
    label = f"hsk{number}" if number <= 6 else "hsk7to9"
    path = ROOT / "data" / "chinese" / "characters" / f"{label}_chars.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return frozenset(
            row["character"].strip() for row in csv.DictReader(handle)
            if row.get("character", "").strip()
        )


@lru_cache(maxsize=1)
def story_words() -> frozenset[str]:
    """Load explicitly teachable names and essential terms, not their chars."""
    path = ROOT / "output" / "chinese" / "sanguoyanyi" / "glossary.txt"
    if not path.is_file():
        return frozenset()
    return frozenset(
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


@lru_cache(maxsize=7)
def _tokenizer(level: str) -> jieba.Tokenizer:
    # Keep Jieba's natural compound vocabulary: deleting non-HSK compounds
    # would turn words such as 专权 into individually familiar characters and
    # recreate the exact false positive this gate is designed to prevent.
    tokenizer = jieba.Tokenizer()
    for word in cumulative_words(level):
        tokenizer.add_word(word, freq=50_000)
    return tokenizer


def _protected_lexical_tokens(
    han_run: str, level: str, protected_words: frozenset[str]
) -> list[str]:
    """Tokenize one Han run while preserving reviewed names/story phrases.

    Jieba's HMM fallback sometimes invents boundaries such as ``因杀``,
    ``双带``, or ``矛和`` for out-of-dictionary sequences.  Those are not
    lexical units and inflate an adaptation's above-level ratio.  Disabling
    HMM falls back to honest single-character pieces, while Jieba's dictionary
    still preserves established compounds.  Reviewed exemptions are matched
    longest-first so a multi-character name or story term stays one token.
    """
    protected = sorted(
        (word for word in protected_words if word and word in han_run),
        key=lambda word: (-len(word), word),
    )
    if not protected:
        return list(_tokenizer(level).lcut(han_run, HMM=False))

    result: list[str] = []
    cursor = 0
    while cursor < len(han_run):
        match = next(
            (word for word in protected if han_run.startswith(word, cursor)),
            None,
        )
        if match is not None:
            result.append(match)
            cursor += len(match)
            continue
        next_start = min(
            (
                position
                for word in protected
                if (position := han_run.find(word, cursor + 1)) >= 0
            ),
            default=len(han_run),
        )
        result.extend(
            _tokenizer(level).lcut(han_run[cursor:next_start], HMM=False)
        )
        cursor = next_start
    return result


def lexical_tokens(
    text: str,
    level: str,
    *,
    protected_words: set[str] | frozenset[str] | None = None,
) -> list[str]:
    result: list[str] = []
    protected = frozenset(protected_words or ())
    for match in _HAN_RUN.finditer(text):
        result.extend(_protected_lexical_tokens(match.group(), level, protected))
    return result


def expand_allowed_word_surfaces(words: Iterable[str]) -> frozenset[str]:
    """Include exact Han clauses from reviewed punctuated fixed phrases."""
    expanded: set[str] = set()
    for raw in words:
        surface = str(raw).strip()
        if not surface:
            continue
        expanded.add(surface)
        expanded.update(_HAN_RUN.findall(surface))
    return frozenset(expanded)


def _cjk_length(text: str) -> int:
    return sum("\u3400" <= char <= "\u9fff" for char in text)


def _decomposes_into_known_words(token: str, words: frozenset[str]) -> bool:
    """Accept transparent combinations such as 很+多 without hiding 专权.

    Segmentation dictionaries often join ordinary adjacent beginner words.
    Requiring the combined surface itself to be listed would reject prose a
    learner can directly decode.  The decomposition uses word-list entries,
    never merely familiar characters, which keeps literary compounds visible.
    """
    if len(token) < 2:
        return False
    reachable = [False] * (len(token) + 1)
    reachable[0] = True
    for end in range(1, len(token) + 1):
        reachable[end] = any(
            reachable[start] and token[start:end] in words
            for start in range(end)
        )
    return reachable[-1]


def known_word_decomposition(
    token: str, words: frozenset[str]
) -> list[str] | None:
    """Return a deterministic known-word decomposition, if one exists.

    Prefer fewer pieces, then longer leftmost pieces.  The evidence is stable
    across runs and is suitable for displaying or auditing in a sidecar.
    """
    if len(token) < 2:
        return None
    best: list[list[str] | None] = [None] * (len(token) + 1)
    best[0] = []
    for end in range(1, len(token) + 1):
        candidates: list[list[str]] = []
        for start in range(end):
            if best[start] is not None and token[start:end] in words:
                candidates.append([*best[start], token[start:end]])
        if candidates:
            best[end] = min(
                candidates,
                key=lambda parts: (len(parts), tuple(-len(part) for part in parts), parts),
            )
    result = best[-1]
    return result if result and len(result) > 1 else None


def first_hsk_word_level(surface: str) -> int | None:
    for number in range(1, 8):
        if surface in words_at_level(f"hsk{number}"):
            return number
    return None


def first_hsk_character_level(surface: str) -> int | None:
    if len(surface) != 1:
        return None
    for number in range(1, 8):
        if surface in characters_at_level(f"hsk{number}"):
            return number
    return None


def classify_reviewed_segment(
    surface: str,
    segment_type: str,
    target_level: str,
    *,
    is_story_term: bool = False,
) -> dict[str, Any]:
    """Attach mechanical level evidence to one agent-reviewed segment.

    The agent owns boundaries, linguistic type, contextual explanation, names,
    and story-term overlays.  This function owns only HSK-list membership and
    the resulting learning-focus flag.  Names and story terms remain
    lookup-only even when their surface happens to occur in the target list.
    """
    target_number = _level_number(target_level)
    if segment_type == "punctuation":
        return {
            "target_hsk_level": target_number,
            "hsk_status": "not_applicable",
            "matched_hsk_level": None,
            "character_hsk_level": None,
            "hsk_evidence": "punctuation",
            "learning_focus": "not_applicable",
            "lookup_reason": "",
        }

    matched_level = first_hsk_word_level(surface)
    target_decomposition = known_word_decomposition(
        surface, cumulative_words(target_level)
    )
    # Character-writing bands are useful supporting context but are not word
    # levels.  A learner may be expected to recognize a hanzi years before a
    # particular lexical use is introduced, so this value never decides
    # ``hsk_status`` or ``matched_hsk_level``.
    character_level = first_hsk_character_level(surface)

    # Match the established readability gate: a tokenizer or annotation agent
    # may group adjacent beginner words into a surface which is introduced as
    # a lexicalized word only at a later level.  If the target-level pieces are
    # transparent, the learner can still decode the grouped surface.
    if matched_level is not None and matched_level <= target_number:
        effective_level = matched_level
        status = "in_level"
        evidence = f"listed_hsk{matched_level}"
    elif target_decomposition is not None:
        effective_level = target_number
        status = "in_level"
        evidence = (
            f"decomposes_into_hsk{target_number}_words:"
            + "+".join(target_decomposition)
        )
    else:
        decomposition: list[str] | None = None
        decomposition_level: int | None = None
        for number in range(target_number + 1, 8):
            candidate = known_word_decomposition(
                surface, cumulative_words(f"hsk{number}")
            )
            if candidate is not None:
                decomposition = candidate
                decomposition_level = number
                break
        candidates = [
            value for value in (matched_level, decomposition_level)
            if value is not None
        ]
        effective_level = min(candidates) if candidates else None

    if effective_level is None:
        status = "unlisted"
        evidence = "not_in_hsk_3_word_lists"
    elif effective_level > target_number:
        status = "above_level"
        if matched_level == effective_level:
            evidence = f"listed_hsk{matched_level}"
        elif decomposition_level == effective_level and decomposition is not None:
            evidence = (
                f"decomposes_into_hsk{decomposition_level}_words:"
                + "+".join(decomposition)
            )
        else:
            raise AssertionError("effective HSK level has no word evidence")

    if status in {"above_level", "unlisted"}:
        learning_focus = "lookup"
        lookup_reason = (
            "story_term" if is_story_term else
            "proper_name" if segment_type == "name" else
            "above_target_level" if status == "above_level" else
            "unlisted"
        )
    elif segment_type == "name":
        learning_focus, lookup_reason = "lookup", "proper_name"
    else:
        learning_focus, lookup_reason = "target", ""

    return {
        "target_hsk_level": target_number,
        "hsk_status": status,
        "matched_hsk_level": effective_level,
        "character_hsk_level": character_level,
        "hsk_evidence": evidence,
        "learning_focus": learning_focus,
        "lookup_reason": lookup_reason,
    }


def classify_reviewed_compositional_segment(
    surface: str,
    segment_type: str,
    target_level: str,
    components: Iterable[str],
    *,
    is_story_term: bool = False,
) -> dict[str, Any]:
    """Classify a reviewed construction by its lexical components.

    Productive predicates such as ``走进来`` are unlikely to have an exact HSK
    word-list entry.  Once an agent-reviewed decomposition has established the
    actual learner-facing parts, exact full-surface membership is therefore the
    wrong test.  The hardest required component controls the focus while the
    grammar construction remains a separate concern.

    Callers must only use this for a semantically reviewed decomposition, not
    arbitrary character splitting.
    """
    parts = [str(component) for component in components]
    if len(parts) < 2 or any(not part for part in parts) or "".join(parts) != surface:
        raise ValueError("reviewed components must losslessly reconstruct surface")

    target_number = _level_number(target_level)
    component_results = [
        classify_reviewed_segment(part, "word", target_level)
        for part in parts
    ]
    unlisted = [
        (part, result)
        for part, result in zip(parts, component_results)
        if result["hsk_status"] == "unlisted"
    ]
    above = [
        (part, result)
        for part, result in zip(parts, component_results)
        if result["hsk_status"] == "above_level"
    ]

    if unlisted:
        status = "unlisted"
        effective_level = None
        lookup_reason = "unlisted_component"
    elif above:
        status = "above_level"
        effective_level = max(
            int(result["matched_hsk_level"])
            for _, result in above
            if result["matched_hsk_level"] is not None
        )
        lookup_reason = "above_target_component"
    else:
        status = "in_level"
        effective_level = max(
            int(result["matched_hsk_level"])
            for result in component_results
            if result["matched_hsk_level"] is not None
        )
        lookup_reason = ""

    evidence_parts = []
    for part, result in zip(parts, component_results):
        matched = result["matched_hsk_level"]
        if matched is not None:
            evidence = f"hsk{matched}"
        else:
            character_level = result["character_hsk_level"]
            evidence = (
                f"unlisted_word(character_hsk{character_level})"
                if character_level is not None
                else "unlisted_word"
            )
        evidence_parts.append(f"{part}={evidence}")

    if is_story_term:
        learning_focus, lookup_reason = "lookup", "story_term"
    elif segment_type == "name":
        learning_focus, lookup_reason = "lookup", "proper_name"
    elif status in {"above_level", "unlisted"}:
        learning_focus = "lookup"
    else:
        learning_focus = "target"

    return {
        "target_hsk_level": target_number,
        "hsk_status": status,
        "matched_hsk_level": effective_level,
        "character_hsk_level": first_hsk_character_level(surface),
        "hsk_evidence": "reviewed_components:" + "+".join(evidence_parts),
        "learning_focus": learning_focus,
        "lookup_reason": lookup_reason,
    }


def enrich_reviewed_segments(
    segments: Iterable[dict[str, Any]], target_level: str
) -> list[dict[str, Any]]:
    """Return enriched copies after agent role metadata has been attached."""
    enriched: list[dict[str, Any]] = []
    for segment in segments:
        item = dict(segment)
        item.update(classify_reviewed_segment(
            str(item.get("text", "")),
            str(item.get("type", "")),
            target_level,
            is_story_term=bool(item.get("story_term")),
        ))
        enriched.append(item)
    return enriched


def _unit_lengths(text: str, separator: re.Pattern[str]) -> list[int]:
    return [
        length for part in separator.split(text)
        if (length := _cjk_length(part)) > 0
    ]


def validate_paragraph_structure(
    text: str,
    level: str,
    *,
    min_paragraphs: int | None = None,
    max_paragraphs: int | None = None,
    max_paragraph_cjk: int | None = None,
) -> dict[str, Any]:
    """Require readable semantic blocks instead of a generated wall of text."""
    normalized_level = level.lower()
    defaults = PARAGRAPH_SHAPE_LIMITS.get(normalized_level)
    if defaults is None and any(
        value is None
        for value in (min_paragraphs, max_paragraphs, max_paragraph_cjk)
    ):
        return {
            "passes": True,
            "enforced": False,
            "level": normalized_level,
            "paragraph_count": 0,
            "paragraph_cjk_lengths": [],
        }
    if defaults is not None:
        default_minimum, default_maximum, default_length = defaults
    else:
        default_minimum = default_maximum = default_length = 0
    minimum = min_paragraphs if min_paragraphs is not None else default_minimum
    maximum = max_paragraphs if max_paragraphs is not None else default_maximum
    length_limit = (
        max_paragraph_cjk
        if max_paragraph_cjk is not None
        else default_length
    )
    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", text.strip())
        if part.strip()
    ]
    lengths = [_cjk_length(part) for part in paragraphs]
    count_pass = minimum <= len(paragraphs) <= maximum
    length_pass = bool(lengths) and max(lengths) <= length_limit
    return {
        "passes": count_pass and length_pass,
        "enforced": True,
        "level": normalized_level,
        "paragraph_count": len(paragraphs),
        "min_paragraphs": minimum,
        "max_paragraphs": maximum,
        "paragraph_cjk_lengths": lengths,
        "max_paragraph_cjk": length_limit,
        "long_paragraphs": [length for length in lengths if length > length_limit],
        "count_pass": count_pass,
        "length_pass": length_pass,
    }


def validate_beginner_chinese(
    text: str,
    level: str,
    *,
    allowed_words: set[str] | frozenset[str] | None = None,
    max_above_level_word_ratio: float | None = None,
) -> dict[str, Any]:
    """Return word-level evidence plus progressive prose-shape constraints."""
    normalized_level = level.lower()
    known_words = cumulative_words(normalized_level)
    allowed = expand_allowed_word_surfaces(
        story_words() | frozenset(allowed_words or ())
    )
    tokens = lexical_tokens(
        text, normalized_level, protected_words=allowed,
    )
    above_tokens: list[str] = []
    for token in tokens:
        if (
            token in allowed
            or token in known_words
            or _decomposes_into_known_words(token, known_words)
        ):
            continue
        above_tokens.append(token)
    above_unique = list(dict.fromkeys(above_tokens))
    total = len(tokens)
    above_ratio = len(above_tokens) / total if total else 0.0
    sentence_lengths = _unit_lengths(text, _SENTENCE_BREAK)
    clause_lengths = _unit_lengths(text, _CLAUSE_BREAK)

    default_ratio = (
        HSK1_MAX_ABOVE_LEVEL_WORD_RATIO
        if normalized_level == "hsk1" else 0.05
    )
    maximum_ratio = (
        default_ratio
        if max_above_level_word_ratio is None
        else max_above_level_word_ratio
    )
    max_above_tokens = math.ceil(total * maximum_ratio)
    lexical_pass = len(above_tokens) <= max_above_tokens
    max_sentence_cjk, max_clause_cjk = PROSE_SHAPE_LIMITS[normalized_level]
    shape_enforced = True
    sentence_pass = (
        bool(sentence_lengths) and max(sentence_lengths) <= max_sentence_cjk
    )
    clause_pass = bool(clause_lengths) and max(clause_lengths) <= max_clause_cjk
    return {
        "passes": lexical_pass and sentence_pass and clause_pass,
        "enforced": True,
        "shape_enforced": shape_enforced,
        "level": normalized_level,
        "total_word_tokens": total,
        "above_level_word_tokens": len(above_tokens),
        "above_level_word_ratio": above_ratio,
        "above_level_word_percent": round(above_ratio * 100, 2),
        "max_above_level_word_ratio": maximum_ratio,
        "max_above_level_word_tokens": max_above_tokens,
        "above_level_words": above_unique,
        "sentence_cjk_lengths": sentence_lengths,
        "max_sentence_cjk": max_sentence_cjk,
        "long_sentences": [
            value for value in sentence_lengths
            if value > max_sentence_cjk
        ],
        "clause_cjk_lengths": clause_lengths,
        "max_clause_cjk": max_clause_cjk,
        "long_clauses": [
            value for value in clause_lengths
            if value > max_clause_cjk
        ],
        "lexical_pass": lexical_pass,
        "sentence_pass": sentence_pass,
        "clause_pass": clause_pass,
    }


def validate_level_distinctiveness(
    text: str,
    level: str,
    *,
    allowed_words: set[str] | frozenset[str] | None = None,
    lower_level_max_above_ratio: float | None = None,
    min_target_band_unique: int = 0,
) -> dict[str, Any]:
    """Prove that prose uses its target band rather than the lower one."""
    normalized_level = level.lower()
    number = _level_number(normalized_level)
    allowed = expand_allowed_word_surfaces(
        story_words() | frozenset(allowed_words or ())
    )
    if number == 1:
        return {
            "passes": True,
            "enforced": False,
            "level": normalized_level,
            "lower_level": None,
            "lower_level_lexical_pass": None,
            "target_band_tokens": 0,
            "target_band_unique": 0,
            "min_target_band_unique": 0,
            "target_band_words": [],
        }

    target_band = words_at_level(normalized_level)
    tokens = lexical_tokens(text, normalized_level, protected_words=allowed)
    band_tokens = [
        token for token in tokens
        if token not in allowed and token in target_band
    ]
    band_words = list(dict.fromkeys(band_tokens))
    lower_level = f"hsk{number - 1}"
    lower = validate_beginner_chinese(
        text,
        lower_level,
        allowed_words=allowed,
        max_above_level_word_ratio=lower_level_max_above_ratio,
    )
    lower_rejected_lexically = not lower["lexical_pass"]
    unique_pass = len(band_words) >= min_target_band_unique
    return {
        "passes": lower_rejected_lexically and unique_pass,
        "enforced": True,
        "level": normalized_level,
        "lower_level": lower_level,
        "lower_level_lexical_pass": lower["lexical_pass"],
        "lower_level_above_word_ratio": lower["above_level_word_ratio"],
        "lower_level_above_word_percent": lower["above_level_word_percent"],
        "target_band_tokens": len(band_tokens),
        "target_band_unique": len(band_words),
        "min_target_band_unique": min_target_band_unique,
        "target_band_words": band_words,
        "lower_level_evidence": lower,
    }


def validate_chinese_help_text(
    text: str,
    level: str,
    *,
    kind: str,
    taught_surfaces: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Verify that a short immersion hint is comprehensible at target level.

    The headword or exact story phrase may of course occur in its own example;
    everything else must be expressible with cumulative target-level words or
    transparent combinations of those words.  Short cards have no useful
    statistical margin, so unlike chapter prose this is an exact zero-extra-
    vocabulary gate.
    """
    normalized_level = level.lower()
    if normalized_level not in HELP_MAX_CJK:
        raise ValueError(f"unsupported help-card level: {level}")
    if kind not in {"explanation", "example"}:
        raise ValueError(f"unsupported help-card kind: {kind}")
    protected = frozenset(taught_surfaces or ())
    tokens = lexical_tokens(
        text, normalized_level, protected_words=protected,
    )
    known_words = cumulative_words(normalized_level)
    above_tokens = [
        token for token in tokens
        if token not in protected
        and token not in known_words
        and not _decomposes_into_known_words(token, known_words)
    ]
    latin = re.findall(r"[A-Za-z]", text)
    cjk_length = _cjk_length(text)
    maximum = HELP_MAX_CJK[normalized_level][kind]
    return {
        "passes": bool(tokens) and not above_tokens and not latin
        and cjk_length <= maximum,
        "level": normalized_level,
        "kind": kind,
        "cjk_length": cjk_length,
        "max_cjk": maximum,
        "tokens": tokens,
        "above_level_words": list(dict.fromkeys(above_tokens)),
        "contains_latin": bool(latin),
    }


def hsk1_prompt_guidance() -> str:
    words = "、".join(sorted(cumulative_words("hsk1")))
    return f"""This must be decodable beginner prose, not a compact historical
synopsis. Use explicit subjects, modern word order, one action per sentence,
and frequent full stops. Never compress events into literary four-character
phrases, stacked names, semicolon lists, or noun-heavy clauses. Rewrite an idea
with ordinary concrete words even when that takes more characters. Keep only a
small number of indispensable names or story terms; annotations are not
permission to make the surrounding prose advanced. Never replace a necessary
word with vague or unnatural Chinese just to improve the vocabulary score. For
example, write natural phrases such as 招兵的告示, 拜天地, and 关羽是二哥;
reject word-list-driven substitutes such as 招兵的字, 对天地说话, or 关羽第二.
After those taught terms,
at least 85% of naturally segmented word tokens must be HSK1. Keep every
sentence at no more than {HSK1_MAX_SENTENCE_CJK} Chinese characters and every
comma-separated clause at no more than {HSK1_MAX_CLAUSE_CJK}. Prefer this HSK1
vocabulary wherever it can express the meaning:
{words}"""
