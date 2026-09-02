"""Deterministic JLPT vocabulary diagnostics for Japanese reader editions.

JLPT does not publish an official vocabulary list.  The repository's versioned
lists are therefore an editorial baseline, not a claim about the exam.  Keeping
the lookup and thresholds in one module prevents the publisher and app asset
builder from silently disagreeing about common kana/kanji spellings.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Iterable

from fugashi import Tagger


ROOT = Path(__file__).resolve().parent.parent
LEVEL_NUMBER = {"n5": 1, "n4": 2, "n3": 3, "n2": 4, "n1": 5}

# An annotated literary reader can carry more exceptions than a textbook, but
# unknown content words must remain occasional.  These are token ratios after
# grammar chunks, particles, auxiliaries, names, and a tiny fixed cast
# allowlist are excluded.
MAX_ABOVE_LEVEL_RATIO = {
    "n5": 0.10,
    "n4": 0.15,
    "n3": 0.24,
    "n2": 0.28,
    "n1": 0.32,
}

_UNIDIC_LEMMA_ALIASES = {
    "我が輩": "吾輩", "為る": "する", "成る": "なる",
    "有る": "ある", "居る": "いる", "遣る": "やる",
    "其処": "そこ", "此の": "この", "未だ": "まだ",
    "何処": "どこ", "事": "こと", "側": "そば", "早い": "速い",
}

# Learner-facing agents intentionally keep these conventional lexical units
# whole. Protect them during the cheap preflight so its denominator and level
# decision match the final agent segmentation instead of counting
# transparent-looking tokenizer pieces such as そこ + で.
PREFLIGHT_LEXICAL_UNITS = {"そこで"}


def _katakana_to_hiragana(value: str) -> str:
    return "".join(
        chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char
        for char in value
    )


def preflight_level_diagnostics(
    text: str,
    level: str,
    story_terms: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Estimate lexical level before expensive agent annotation.

    Fugashi/UniDic is used only for lemma lookup and a fail-fast prose gate.
    It never supplies the learner-facing segmentation published in the app.
    """
    allowed = set(STORY_ALLOWLIST)
    for term in story_terms:
        allowed.update((
            str(term.get("surface", "")).strip(),
            str(term.get("lemma", "")).strip(),
        ))
    allowed.discard("")

    # Count conventional multiword vocabulary once rather than penalizing the
    # tokenizer's internal noun/particle boundaries. Grammar primaries are
    # omitted because the final diagnostic omits them too.
    protected: list[tuple[int, int, dict[str, str] | None]] = []
    preflight_units = set(VOCABULARY_ALIASES) | PREFLIGHT_LEXICAL_UNITS
    for expression in sorted(preflight_units, key=len, reverse=True):
        if len(expression) < 2:
            continue
        for match in re.finditer(re.escape(expression), text):
            protected.append((match.start(), match.end(), {
                "surface": expression,
                "lemma": expression,
                "surface_kana": expression,
                "lemma_kana": expression,
            }))
    for match in re.finditer(
        r"ことに(?:する|した|します|しました)|ことがある|ことはない|ことだけ", text
    ):
        protected.append((match.start(), match.end(), None))
    protected.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    nonoverlapping: list[tuple[int, int, dict[str, str] | None]] = []
    for item in protected:
        if any(item[0] < end and start < item[1] for start, end, _ in nonoverlapping):
            continue
        nonoverlapping.append(item)

    tokens: list[dict[str, Any]] = []
    cursor = 0
    tagger = Tagger()
    emitted_protected: set[tuple[int, int]] = set()
    for word in tagger(text):
        # MeCab omits whitespace tokens, so locate each emitted surface from
        # the prior end rather than assuming token lengths cover newlines.
        start = text.find(word.surface, cursor)
        if start < 0:
            raise ValueError("Japanese preflight tokenizer lost source alignment")
        end = start + len(word.surface)
        cursor = end
        covering = next(
            (item for item in nonoverlapping if item[0] <= start and end <= item[1]),
            None,
        )
        if covering is not None:
            key = (covering[0], covering[1])
            if key not in emitted_protected and covering[2] is not None:
                tokens.append(covering[2])
            emitted_protected.add(key)
            continue
        feature = word.feature
        if feature.pos1 in {"助詞", "助動詞", "補助記号", "記号", "空白"}:
            continue
        lemma = _UNIDIC_LEMMA_ALIASES.get(feature.lemma, feature.lemma)
        lemma_kana = _katakana_to_hiragana(feature.kanaBase or "")
        surface_kana = _katakana_to_hiragana(feature.kana or "")
        if word.surface in allowed or lemma in allowed:
            continue
        tokens.append({
            "surface": word.surface,
            "lemma": lemma,
            "surface_kana": surface_kana,
            "lemma_kana": lemma_kana,
        })

    target = LEVEL_NUMBER[level]
    above = [
        token for token in tokens
        if (matched := matched_level(token)) is None or matched > target
    ]
    ratio = len(above) / len(tokens) if tokens else 0.0
    return {
        "policy": "pre_annotation_publish_gate",
        "tokenizer": "fugashi-unidic-lite-lemma-preflight-only",
        "tokens_considered": len(tokens),
        "above_level_tokens": len(above),
        "above_level_ratio": round(ratio, 4),
        "maximum_above_level_ratio": MAX_ABOVE_LEVEL_RATIO[level],
        "passes": ratio <= MAX_ABOVE_LEVEL_RATIO[level],
        "sample": list(dict.fromkeys(str(item["lemma"]) for item in above))[:30],
        "excluded": "grammar spans, particles, auxiliaries, punctuation, fixed cast, and reviewed story terms",
    }

STORY_TERM_BUDGET = {"n5": 2, "n4": 4, "n3": 8, "n2": 16, "n1": 22}
STORY_ALLOWLIST = {"吾輩", "猫", "主人", "迷亭", "寒月", "苦沙弥"}

# Inflectional grammar must never become yellow "special vocabulary" merely
# because an unofficial vocabulary CSV omits it or an annotator labels it as a
# word rather than an auxiliary.  These are a grammatical baseline at every
# reader level, not editorial exceptions.  Matching them as N5 also makes the
# publisher and app builder agree about forms such as ない.
GRAMMAR_BASELINE_LEMMAS = {
    "ない", "ぬ", "ん", "た", "だ", "です", "ます", "う", "よう",
    "れる", "られる", "せる", "させる", "たい", "そうだ", "ようだ",
    "て", "で", "ば", "なら", "ある", "いる", "する", "なる", "来る",
    "くる", "行く", "いく",
}

# The source lists occasionally omit transparent spelling or phrase variants
# of vocabulary they already contain.  These aliases are lexical equivalents,
# not permission to promote arbitrary compounds: each maps to the listed head
# that determines the expression's learner level.
VOCABULARY_ALIASES = {
    "良い": "いい",
    "よい": "いい",
    "女の人": "女",
    "男の人": "男",
    "何回": "何",
    "何回も": "何",
    "何も": "何",
    "誰も": "誰",
    "気持ちよい": "気持ち",
    "気持ちのよい": "気持ち",
    "悪くなる": "悪い",
    "何度": "何",
    "何度も": "何",
    "その後": "後",
    "その時": "時",
    "後で": "後",
    "ある日": "日",
    "音がする": "音",
    "においがする": "におい",
    "匂いがする": "匂い",
}

# The imported unofficial lists have a few conspicuous holes.  Keep these
# small, versioned editorial corrections explicit rather than allowing the
# pass/fail gate to classify a basic word from kanji difficulty or agent
# intuition.  JLPT itself publishes no official vocabulary list.
EDITORIAL_BASELINE_LEVELS = {
    "時": 1, "とき": 1, "匹": 1,
    # Transparent beginner language missing from the imported unofficial list.
    "ゆっくり": 1, "ニャーニャー": 1, "にゃあにゃあ": 1,
    # Basic N5 narrative coordination; the imported list places it much later.
    "そして": 1,
    # A basic N4 discourse connector in ordinary graded prose.
    "そこで": 2,
}

_TRANSPARENT_NUMERIC_EXPRESSION = re.compile(
    r"^[一二三四五六七八九十百千〇零\d]+"
    r"(?:円(?:[一二三四五六七八九十百千〇零\d]+銭)?|銭)?$"
)

_TRANSPARENT_COUNTER_EXPRESSION = re.compile(
    r"^[一二三四五六七八九十百千〇零\d]+"
    r"(?:人|匹|枚|本|冊|台|個|回|歳|時|分|日|月|年)$"
)

_PRODUCTIVE_OVERLAY_MARKERS = (
    "compound", "mimetic-suru", "adjective-ku", "te-iku", "te-kuru",
    "benefactive",
)


@lru_cache(maxsize=1)
def vocabulary_levels() -> dict[str, int]:
    """Return the earliest local JLPT level keyed by spelling and reading."""
    result: dict[str, int] = {}
    directory = ROOT / "data" / "japanese" / "words"
    for index, label in enumerate(("n5", "n4", "n3", "n2", "n1"), 1):
        path = directory / f"{label}_words.csv"
        if not path.is_file():
            raise ValueError(f"JLPT vocabulary data missing: {path}")
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for field in ("word", "reading"):
                    value = str(row.get(field, "")).strip()
                    if value:
                        result.setdefault(value, index)
    for value, level in EDITORIAL_BASELINE_LEVELS.items():
        result[value] = min(result.get(value, level), level)
    for alias, canonical in VOCABULARY_ALIASES.items():
        if canonical in result:
            result[alias] = min(result.get(alias, result[canonical]), result[canonical])
    return result


def matched_level(segment: dict[str, Any]) -> int | None:
    """Match a segment through both surface/dictionary spelling and reading."""
    vocabulary = vocabulary_levels()
    values = [
        segment.get("lemma"),
        segment.get("lemma_kana", segment.get("lemma_reading")),
        segment.get("surface", segment.get("text")),
        segment.get("surface_kana", segment.get("reading")),
    ]
    # Agents correctly use dictionary forms such as 静かだ for na-adjectives,
    # while common vocabulary lists normally store the bare head 静か.
    for value in tuple(values):
        if isinstance(value, str) and len(value) > 1 and value.endswith("だ"):
            values.append(value[:-1])
    if any(value in GRAMMAR_BASELINE_LEMMAS for value in values):
        return 1
    if any(
        isinstance(value, str) and (
            _TRANSPARENT_NUMERIC_EXPRESSION.fullmatch(value)
            or _TRANSPARENT_COUNTER_EXPRESSION.fullmatch(value)
        )
        for value in values
    ):
        # The imported lists enumerate individual numerals but naturally omit
        # most productive combinations such as 四十, 一円, or 五十銭. Their
        # composition is beginner number grammar, not unknown literary vocab.
        return 1
    matches = [vocabulary[value] for value in values if value in vocabulary]
    return min(matches) if matches else None


def matched_level_with_overlays(
    segment: dict[str, Any], start: int, end: int,
    overlays: Iterable[dict[str, Any]] = (),
) -> int | None:
    """Classify reviewed productive constructions from their lexical roots.

    A compound such as 動き出す may be absent from a finite vocabulary CSV even
    when both 動く and the productive 出す element are already in level.  Only an
    exact, reviewed overlay explicitly marked as productive can supply this
    fallback; fixed idioms and arbitrary phrases still need their own lexical
    classification.
    """
    direct = matched_level(segment)
    composed: list[int] = []
    for overlay in overlays:
        if overlay.get("start") != start or overlay.get("end") != end:
            continue
        description = " ".join((
            str(overlay.get("grammar_candidate_key", "")),
            str(overlay.get("pattern", "")),
            str(overlay.get("form_label", "")),
        )).casefold()
        if not any(marker in description for marker in _PRODUCTIVE_OVERLAY_MARKERS):
            continue
        lexical_levels: list[int] = []
        unresolved = False
        for component in overlay.get("components", []):
            if component.get("lookup_kind") != "lexical":
                continue
            level = matched_level({
                "surface": component.get("surface", ""),
                "surface_kana": component.get("lemma_kana", ""),
                "lemma": component.get("lemma", ""),
                "lemma_kana": component.get("lemma_kana", ""),
                "type": "word",
            })
            if level is None:
                unresolved = True
                break
            lexical_levels.append(level)
        if not unresolved and len(lexical_levels) >= 2:
            composed.append(max(lexical_levels))
    candidates = ([direct] if direct is not None else []) + composed
    return min(candidates) if candidates else None


def level_diagnostics(
    segments: Iterable[dict[str, Any]], level: str,
    overlays: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Measure exceptional content-word tokens against the local baseline."""
    target = LEVEL_NUMBER[level]
    considered: list[str] = []
    above: list[str] = []
    overlay_list = list(overlays)
    offset = 0
    for segment in segments:
        surface = str(segment.get("surface", segment.get("text", "")))
        start, end = offset, offset + len(surface)
        offset = end
        if segment.get("type") in {
            "punctuation", "grammar", "particle", "auxiliary", "name",
        }:
            continue
        lemma = str(segment.get("lemma", "")).strip()
        if not lemma or lemma in STORY_ALLOWLIST:
            continue
        considered.append(lemma)
        match = matched_level_with_overlays(
            segment, start, end, overlay_list,
        )
        if match is None or match > target:
            above.append(lemma)
    ratio = len(above) / len(considered) if considered else 0.0
    return {
        "policy": "publish_gate",
        "tokens_considered": len(considered),
        "above_level_tokens": len(above),
        "above_level_ratio": round(ratio, 4),
        "maximum_above_level_ratio": MAX_ABOVE_LEVEL_RATIO[level],
        "passes": ratio <= MAX_ABOVE_LEVEL_RATIO[level],
        "sample": list(dict.fromkeys(above))[:30],
        "excluded": "grammar chunks, particles, auxiliaries, names, and the fixed cast allowlist",
    }


def vocabulary_prompt_reference(level: str) -> str:
    """Compact cumulative baseline for adaptation and independent review."""
    target = LEVEL_NUMBER[level]
    values = sorted(
        value for value, matched in vocabulary_levels().items()
        if matched <= target
    )
    return "、".join(values)
