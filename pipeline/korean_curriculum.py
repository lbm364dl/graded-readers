"""Pinned six-level curriculum evidence, separate from dictionary identities.

Source homonym numbers differ between editions. A spelling match is a candidate,
not an approved crosswalk. Productive formations and grammatical uses require
explicit bindings and independent review in the generation pipeline.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/korean/words/nikl_2017_curriculum_20201117.json"
SOURCE_SHA256 = "881d9aa0e2a262f81b7a0be4b301bf607134ce071b123db0e15638869793bf33"


@lru_cache(maxsize=1)
def catalog() -> dict:
    raw = SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Korean curriculum source changed; review the new edition")
    return json.loads(raw)


def entries(kind: str, level: int | None = None) -> list[dict]:
    if kind not in ("vocabulary", "grammar"):
        raise ValueError("Unknown curriculum entry kind")
    if level is not None and level not in range(1, 7):
        raise ValueError("Korean curriculum level must be 1–6")
    return [entry for entry in catalog()[kind] if level is None or entry["level"] <= level]


def prompt_entries(kind: str, level: int | None = None) -> list[dict]:
    """Keep semantic source evidence, omitting duplicate grading/bookkeeping.

    The full unmodified rows remain in the pinned catalog. Agents need the exact
    ID, actual grade, headword/POS or patterns and meanings, not row rankings.
    """
    bookkeeping = {'전체 번호', '등급별 번호', '등급', '국제통용 (2단계)',
                   '문법.표현 교육내용개발(1-4단계)', '어휘교육내용개발(1-4단계)'}
    return [{'id': e['id'], 'level': e['level'],
             'source_fields': {key: value for key, value in e['source_fields'].items()
                               if key not in bookkeeping and value is not None}}
            for e in entries(kind, level)]


def chapter_view(chapter: dict) -> dict:
    """Review grading identities/roles without repeating approved form audits."""
    return {'title': chapter.get('title', ''), 'text': chapter['text'],
            'word_occurrences': [{'segment_index': index, 'text': segment['text'],
                                 'meaning_en': segment['meaning_en'], 'lexical': segment['lexical']}
                                for index, segment in enumerate(chapter['segments'])
                                if segment['type'] == 'word'],
            'grammar_links': chapter['grammar_links']}


def vocabulary_candidates(headword: str) -> list[dict]:
    """Return candidates without conflating homonyms or grading a derived word."""
    return [entry for entry in entries("vocabulary")
            if headword in [re.sub(r"\d+$", "", part.strip())
                            for part in re.split(r'[/·∙‧]', entry["source_fields"]["어휘"])]]


def vocabulary_context_candidates(headword: str) -> list[dict]:
    """Retrieve possible compound prerequisites, never assign their grades.

    Exact candidates preserve all identities. Longer source headwords contained
    in the requested lemma are additional search hits only. An independent
    reviewer must establish formation, sense and POS; spelling is no proof.
    Level 1 rows are already supplied in full, including one-character roots.
    """
    return [entry for entry in entries("vocabulary")
            if any(part == headword or (len(part) >= 2 and part in headword)
                   for raw in re.split(r'[/·∙‧]', entry["source_fields"]["어휘"])
                   for part in [re.sub(r"\d+$", "", raw.strip())])]


def additional_lexical_candidates(existing_headwords: set[str]) -> list[dict]:
    """Offer explicit source-backed identities absent from the legacy catalog.

    Split listed POS categories into separate destinations, keeping the full
    original row as evidence. No old identity is merged or renumbered. A source
    row can aggregate homonyms; the independent binding review still determines
    whether its lexical identity and role match the row. Guide phrases illustrate
    usage; they are not exhaustive lists of a lexeme's senses.
    """
    candidates = []
    for entry in entries('vocabulary'):
        fields = entry['source_fields']
        headwords = {re.sub(r'\d+$', '', p.strip())
                     for p in re.split(r'[/·∙‧]', fields['어휘'])}
        if len(headwords) != 1:
            raise ValueError(f'Curriculum row needs reviewed headword aliases: {entry["id"]}')
        headword = next(iter(headwords))
        if headword in existing_headwords:
            continue
        for pos in sorted({p.strip() for p in re.split(r'[/·∙‧.・]', fields['품사'])}):
            candidates.append({'id': f'{entry["id"]}/{pos}', 'headword': headword,
                'pos': pos, 'meaning': fields['길잡이말'] or '', 'grade': None,
                'curriculum_source_id': entry['id'], 'curriculum_level': entry['level']})
    return candidates


def grammar_occurrence_counts(chapter: dict) -> dict[str, int]:
    """Count linked source positions, not repeated annotation layers."""
    positions = {(link['segment_index'], link['entry_id'])
                 for link in chapter['grammar_links']}
    positions |= {(index, segment['lexical']['id'])
                  for index, segment in enumerate(chapter['segments'])
                  if segment.get('lexical', {}).get('kind') == 'grammar'}
    return dict(Counter(identity for _, identity in positions))


def evaluate_bindings(chapter: dict, bindings: dict, *, level: int = 1,
                      max_extra_ratio: float = 0.10) -> dict:
    """Fail closed on missing evidence; never compute a grade from a suffix.

    A binding's source IDs and explanation are independently reviewed. Local
    validation checks exact identity coverage and computes grades itself.
    Optional above-level grammar keeps its actual grade and an explicit rationale.
    An independent reviewer judges the complete chapter's grammatical load;
    local validation cannot substitute a numerical quota for that judgment.
    """
    entries("vocabulary", level)  # Validate the requested level.
    if not bindings.get('level_reason_en', '').strip():
        raise ValueError('Curriculum bindings need a whole-chapter level justification')
    if bindings.get('prose_revision_reason_en', '').strip():
        raise ValueError('Curriculum assessment requires prose revision')
    optional = {}
    source = {e["id"]: e for kind in ("vocabulary", "grammar") for e in entries(kind)}
    source_kinds = {e['id']: kind for kind in ('vocabulary', 'grammar') for e in entries(kind)}
    required = {("vocabulary", s["lexical"]["id"]) for s in chapter["segments"]
                if s.get("lexical", {}).get("kind") == "vocabulary"}
    required |= {("grammar", link["entry_id"]) for link in chapter["grammar_links"]}
    required |= {('grammar', s['lexical']['id']) for s in chapter['segments']
                 if s.get('lexical', {}).get('kind') == 'grammar'}
    graded = {}
    for binding in bindings["bindings"]:
        key = binding["kind"], binding["entry_id"]
        if key in graded or key not in required:
            raise ValueError(f"Duplicate or unused curriculum binding: {key}")
        ids = binding["source_ids"]
        if len(set(ids)) != len(ids) or any(identity not in source for identity in ids):
            raise ValueError(f"Missing or unknown curriculum evidence: {key}")
        if not binding["analysis_en"].strip():
            raise ValueError(f"Curriculum binding needs a semantic explanation: {key}")
        if binding["equivalence"] == "unlisted":
            if ids:
                raise ValueError(f'Unlisted binding must not imply a source grade: {key}')
            graded[key] = None
            reason = binding.get('optional_reason_en', '').strip()
            if key[0] == 'grammar':
                if not reason:
                    raise ValueError(f'Unlisted grammar needs an optional learning rationale: {key}')
                optional[key[1]] = reason
            elif reason:
                raise ValueError(f'Optional grammar rationale belongs only to grammar: {key}')
            continue
        if not ids:
            raise ValueError(f"Missing or unknown curriculum evidence: {key}")
        if binding["equivalence"] == "listed":
            if len(ids) != 1 or source_kinds[ids[0]] != binding['kind']:
                raise ValueError(f"Listed curriculum binding has the wrong source kind: {key}")
        elif binding["equivalence"] not in ("productive", "grammatical"):
            raise ValueError(f"Unknown curriculum equivalence: {key}")
        if binding['equivalence'] == 'grammatical' and any(source_kinds[i] != 'grammar' for i in ids):
            raise ValueError(f'Grammatical curriculum binding needs grammar evidence: {key}')
        if binding['equivalence'] == 'productive' and len(ids) < 2:
            raise ValueError(f'Productive curriculum binding needs explicit prerequisites: {key}')
        if key[0] == 'grammar' and not any(source_kinds[i] == 'grammar' for i in ids):
            raise ValueError(f'Grammar binding needs grammar evidence: {key}')
        graded[key] = max(source[identity]["level"] for identity in ids)
        reason = binding.get('optional_reason_en', '').strip()
        if key[0] == 'grammar' and graded[key] > level:
            if not reason:
                raise ValueError(f'Above-level grammar needs an optional learning rationale: {key}')
            optional[key[1]] = reason
        elif reason:
            raise ValueError(f'Optional grammar rationale belongs only to above-level grammar: {key}')
    if required != graded.keys():
        raise ValueError(f"Incomplete curriculum bindings: {sorted(required - graded.keys())}")
    occurrences = Counter(s["lexical"]["id"] for s in chapter["segments"]
                          if s.get("lexical", {}).get("kind") == "vocabulary")
    extra = {identity: count for identity, count in occurrences.items()
             if graded["vocabulary", identity] is None or graded["vocabulary", identity] > level}
    grammar = {identity: grade for (kind, identity), grade in graded.items()
               if kind == "grammar" and grade is not None and grade > level}
    # Count distinct linked occurrences, preserving separate source positions.
    grammar_counts = Counter(identity for _, identity in {
        (link['segment_index'], link['entry_id']) for link in chapter['grammar_links']
        if link['entry_id'] in optional})
    ratio = sum(extra.values()) / sum(occurrences.values()) if occurrences else 0
    return {"level_system": catalog()["level_system"], "source_sha256": SOURCE_SHA256,
            "target_level": level, "lexical_levels": {identity: grade for (kind, identity), grade in graded.items() if kind == "vocabulary"},
            "grammar_levels": {identity: grade for (kind, identity), grade in graded.items() if kind == "grammar"},
            "above_level_vocabulary": extra, "above_level_grammar": grammar,
            "optional_grammar_reasons": optional,
            "unlisted_grammar": [identity for (kind, identity), grade in graded.items() if kind == "grammar" and grade is None],
            "above_level_grammar_occurrences": {identity: count for identity, count in grammar_counts.items() if identity in grammar},
            "optional_grammar_occurrences": dict(grammar_counts),
            "extra_vocabulary_ratio": ratio, "passes": ratio <= max_extra_ratio}
