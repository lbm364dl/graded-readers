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
EXCEPTIONS = ROOT / "content/lexicon/korean/l1.exceptions.json"
GRAMMAR = ROOT / "content/lexicon/korean/l1.grammar.json"


@lru_cache(maxsize=1)
def vocabulary() -> dict[str, str | None]:
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
    from pipeline.korean_curriculum import additional_lexical_candidates
    heads = {re.sub(r'\d+$', '', identity.rsplit('/', 1)[0]) for identity in result}
    for entry in additional_lexical_candidates(heads):
        result[entry['id']] = None  # Never manufacture an old A/B/C grade.
    return result


def diagnostics(chapter: dict, *, exception_entries: list | None = None,
                grammar_ids: set[str] | None = None) -> dict:
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
    exceptions = {(item["kind"], item["id"]) for item in
                  (exception_doc["entries"] if exception_entries is None else exception_entries)}
    grammar_doc = json.loads(GRAMMAR.read_text(encoding="utf-8"))
    if grammar_doc.get("reviewed") is not True:
        raise ValueError("Korean grammar registry is unreviewed")
    grammar_ids = {item["id"] for item in grammar_doc["entries"]} if grammar_ids is None else grammar_ids
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
                from pipeline.korean_dictionary import _registry, WORDS
                known = _registry(WORDS).get(identity)
                from pipeline.korean_lexical_research import candidates
                attested = {e['id'] for e in candidates()}
                if (known is None or known['kind'] not in ('word', 'story_term')) and identity not in attested:
                    raise ValueError(f"unresolved Korean vocabulary identity: {identity}")
            counts["vocabulary"] += 1
            if grades.get(identity) != "A":
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
        # Sentence length is evidence for independent linguistic review and
        # selective help, not a proxy for grammatical difficulty.
        "passes": ratio <= MAX_NON_BEGINNER_RATIO,
    }


def validate_chapter(chapter: dict, **kwargs) -> dict:
    result = diagnostics(chapter, **kwargs)
    if 'curriculum' in chapter:
        from pipeline.korean_curriculum import evaluate_bindings
        evidence = chapter['curriculum']
        from pipeline.korean_levels import target_level
        grade = evaluate_bindings(chapter, evidence['bindings'], level=target_level(chapter))
        if grade != evidence['evaluation']:
            raise ValueError('Korean curriculum evaluation is stale')
        if not grade['passes']:
            raise ValueError(f'Korean curriculum Level {grade["target_level"]} readability gate failed: {grade}')
        extra = [identity for identity, count in grade['above_level_vocabulary'].items()
                 for _ in range(count)]
        return {**result, 'curriculum': grade, 'passes': grade['passes'],
                'beginner_vocabulary': result['vocabulary_occurrences'] - len(extra),
                'above_beginner': extra,
                'above_beginner_ratio': round(grade['extra_vocabulary_ratio'], 4)}
    if not result["passes"]:
        raise ValueError(f"Korean Level 1 readability gate failed: {result}")
    return result


def classify_segment(segment: dict, curriculum: dict | None = None) -> dict:
    """Carry the same explicit target/lookup distinction as other readers."""
    if segment["type"] == "punctuation":
        return {"curriculum_status": "not_applicable",
                "matched_curriculum_level": None,
                "learning_focus": "not_applicable", "lookup_reason": "",
                "story_role": "none"}
    lexical = segment["lexical"]
    kind, identity = lexical["kind"], lexical["id"]
    if kind == "grammar":
        if curriculum is not None:
            grade = curriculum['evaluation']['grammar_levels'][identity]
            target = curriculum['evaluation']['target_level']
            return {'curriculum_status': 'unlisted' if grade is None else 'above_level' if grade > target else 'in_level',
                    'matched_curriculum_level': grade,
                    'learning_focus': 'lookup' if grade is None or grade > target else 'target',
                    'lookup_reason': 'unlisted_grammar' if grade is None else 'above_level_grammar' if grade > target else '',
                    'story_role': 'none'}
        return {"curriculum_status": "not_applicable",
                "matched_curriculum_level": None,
                "learning_focus": "target", "lookup_reason": "",
                "story_role": "none"}
    if kind == "proper_name":
        return {"curriculum_status": "unlisted",
                "matched_curriculum_level": None,
                "learning_focus": "lookup", "lookup_reason": "proper_name",
                "story_role": "name"}
    if kind == "story_term":
        return {"curriculum_status": "unlisted",
                "matched_curriculum_level": None,
                "learning_focus": "lookup", "lookup_reason": "story_term",
                "story_role": "story_term"}
    level = (curriculum['evaluation']['lexical_levels'][identity] if curriculum is not None
             else {"A": 1, "B": 2, "C": 3}[vocabulary()[identity]])
    target = curriculum["evaluation"].get("target_level", 1) if curriculum is not None else 1
    within = level is not None and level <= target
    return {"curriculum_status": "unlisted" if level is None else "in_level" if within else "above_level",
            "matched_curriculum_level": level,
            "learning_focus": "target" if within else "lookup",
            "lookup_reason": "unlisted_vocabulary" if level is None else "" if within else "above_level",
            "story_role": "none"}
