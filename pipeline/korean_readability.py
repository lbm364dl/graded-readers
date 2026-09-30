"""Reviewed occurrence and vocabulary gate for Korean graded chapters.

Korean endings and particles cannot be graded by counting Hangul syllables.
The publisher uses explicitly reviewed lexical identities on existing tap units.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import re


ROOT = Path(__file__).resolve().parent.parent
VOCAB_SOURCE = ROOT / "data/korean/words/nikl_2003.tsv"
VOCAB_SHA256 = "00249cf0509427fc2e434f318b1a37c599c694b807dc37f8b17b4601e2482c6e"
MAX_NON_BEGINNER_RATIO = 0.10
MAX_STORY_TERMS = 1
MAX_SENTENCE_EOJEOL = 12
EXCEPTIONS = ROOT / "content/lexicon/korean/l1.exceptions.json"
GRAMMAR = ROOT / "content/lexicon/korean/l1.grammar.json"


@lru_cache(maxsize=1)
def vocabulary() -> dict[str, str]:
    data = VOCAB_SOURCE.read_bytes()
    if hashlib.sha256(data).hexdigest() != VOCAB_SHA256:
        raise ValueError("Korean vocabulary source hash changed; review the new edition")
    lines = [line for line in data.decode("utf-8").splitlines() if line]
    if lines[0] != "순위\t단어\t품사\t풀이\t등급":
        raise ValueError("unexpected Korean vocabulary header")
    result: dict[str, str] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 5 or fields[4] not in {"A", "B", "C"}:
            raise ValueError("invalid Korean vocabulary row")
        identity = f"{fields[1]}/{fields[2]}"
        if identity in result:
            raise ValueError(f"duplicate Korean vocabulary identity: {identity}")
        result[identity] = fields[4]
    if Counter(result.values()) != {"A": 982, "B": 2111, "C": 2872}:
        raise ValueError("Korean vocabulary grade counts changed")
    return result


def diagnostics(chapter: dict) -> dict:
    """Check a manually segmented chapter; never infer lemma from a suffix."""
    text = chapter["text"]
    segments = chapter["segments"]
    if not isinstance(segments, list) or not segments:
        raise ValueError("Korean chapter has no segments")
    if "".join(segment.get("text", "") for segment in segments) != text:
        raise ValueError("Korean segments do not reconstruct chapter")
    if chapter.get("annotation_audit", {}).get("all_reviewed") is not True:
        raise ValueError("unreviewed Korean chapter")
    grades = vocabulary()
    exception_doc = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
    if exception_doc.get("reviewed") is not True:
        raise ValueError("Korean lexical exceptions are unreviewed")
    exceptions = {(item["kind"], item["id"]) for item in exception_doc["entries"]}
    grammar_doc = json.loads(GRAMMAR.read_text(encoding="utf-8"))
    if grammar_doc.get("reviewed") is not True:
        raise ValueError("Korean grammar registry is unreviewed")
    grammar_ids = {item["id"] for item in grammar_doc["entries"]}
    counts = Counter()
    above: list[str] = []
    story_terms: set[str] = set()
    for segment in segments:
        kind = segment.get("type")
        if kind == "punctuation":
            if segment.get("lexical") is not None:
                raise ValueError("punctuation cannot have Korean lexical identity")
            continue
        if kind != "word" or not str(segment.get("meaning_en", "")).strip():
            raise ValueError("Korean word lacks a reviewed gloss")
        lexical = segment.get("lexical")
        if not isinstance(lexical, dict) or set(lexical) != {"kind", "id"}:
            raise ValueError(f"Korean word lacks explicit lexical identity: {segment.get('text')}")
        identity = lexical["id"]
        if lexical["kind"] == "vocabulary":
            if identity not in grades:
                raise ValueError(f"unresolved Korean vocabulary identity: {identity}")
            counts["vocabulary"] += 1
            if grades[identity] != "A":
                above.append(identity)
        elif lexical["kind"] == "proper_name":
            if ("proper_name", identity) not in exceptions:
                raise ValueError(f"unreviewed Korean proper name: {identity}")
            counts["proper_name"] += 1
        elif lexical["kind"] == "story_term":
            if ("story_term", identity) not in exceptions:
                raise ValueError(f"unreviewed Korean story term: {identity}")
            story_terms.add(identity)
            counts["story_term"] += 1
        elif lexical["kind"] == "grammar":
            if identity not in grammar_ids:
                raise ValueError(f"unreviewed Korean grammar identity: {identity}")
            counts["grammar"] += 1
        else:
            raise ValueError(f"unknown Korean lexical kind: {lexical['kind']}")
    if len(story_terms) > MAX_STORY_TERMS:
        raise ValueError("Korean story-term budget exceeded")
    if not counts["vocabulary"]:
        raise ValueError("Korean chapter has no graded vocabulary")
    ratio = len(above) / counts["vocabulary"]
    sentences = [part.strip() for part in re.split(r"[.!?。！？]", text) if part.strip()]
    longest = max((len(sentence.split()) for sentence in sentences), default=0)
    return {
        "beginner_vocabulary": counts["vocabulary"] - len(above),
        "vocabulary_occurrences": counts["vocabulary"],
        "above_beginner": above,
        "above_beginner_ratio": round(ratio, 4),
        "story_terms": sorted(story_terms),
        "longest_sentence_eojeol": longest,
        "passes": ratio <= MAX_NON_BEGINNER_RATIO and longest <= MAX_SENTENCE_EOJEOL,
    }


def validate_chapter(chapter: dict) -> dict:
    result = diagnostics(chapter)
    if not result["passes"]:
        raise ValueError(f"Korean Level 1 readability gate failed: {result}")
    return result
