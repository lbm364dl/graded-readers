"""Validation for explicit Japanese reader-to-dictionary links.

Agent annotations explain grammar and context; the local dictionary supplies
optional lexical lookups.  These are deliberately separate.  A missing link is
better than sending a learner to a homographic particle, auxiliary, or reading
alias whose dictionary entry means something unrelated (for example quotative
``と`` opening the noun ``戸`` "door").
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DICTIONARY_PATH = ROOT / "app" / "assets" / "dictionary_ja.json"

# Same-reading kanji are usually homophones, not spelling variants. Keep the
# small set of source spellings whose equivalence is known rather than letting
# reading equality turn 方 into 法 or quotative と into 戸.
ORTHOGRAPHIC_VARIANT_KEYS = {
    "棄てる": {"捨てる"},
    "載せる": {"乗せる"},
}

@lru_cache(maxsize=1)
def dictionary_entries() -> dict[str, dict[str, Any]]:
    raw = json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Japanese dictionary is not an object: {DICTIONARY_PATH}")
    return {
        str(key): value for key, value in raw.items()
        if isinstance(value, dict)
    }


def canonical_entry(key: str) -> dict[str, Any] | None:
    """Return an exact canonical entry, never a reading-only alias."""
    entry = dictionary_entries().get(key)
    if entry is None:
        return None
    canonical = str(entry.get("w") or key)
    if entry.get("a") is True or canonical != key:
        return None
    return entry


def _canonical_candidates(
    lemma: str, lemma_kana: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Find safe written candidates for one linguistic lemma.

    An exact written lemma wins. When that spelling is absent, the same lexeme
    may still be stored under another conventional spelling (棄てる -> 捨てる),
    while a kana lemma may point to a kanji headword (ある -> 在る). In either
    case all canonical entries with the same reading are offered to the agent
    for semantic choice; no candidate is selected by list order.
    """
    exact = canonical_entry(lemma)
    if exact is not None and str(exact.get("p") or "") == lemma_kana:
        return [(lemma, exact)]
    same_reading = [
        (key, entry)
        for key, entry in dictionary_entries().items()
        if entry.get("a") is not True
        and str(entry.get("w") or key) == key
        and str(entry.get("p") or "") == lemma_kana
    ]
    is_kana_lemma = bool(lemma) and all(
        "\u3040" <= char <= "\u30ff" or char in "ー・"
        for char in lemma
    )
    if is_kana_lemma:
        return same_reading
    allowed = ORTHOGRAPHIC_VARIANT_KEYS.get(lemma, set())
    return [item for item in same_reading if item[0] in allowed]


def dictionary_link_issue(
    *, lemma: str, lemma_kana: str, key: str, definition: str,
    functional: bool = False, require_available: bool = False,
) -> str | None:
    """Validate one explicit canonical link and its agent-authored label."""
    if not key:
        if definition:
            return "dictionary_definition_en must be empty when dictionary_key is empty"
        if require_available and not functional:
            if _canonical_candidates(lemma, lemma_kana):
                return "an exact canonical local dictionary entry is available and must be linked"
        return None
    if functional:
        return "functional grammar must not link to a lexical homograph"
    entry = canonical_entry(key)
    if entry is None:
        return "dictionary_key is not an exact canonical local dictionary headword"
    reading = str(entry.get("p") or "")
    if reading != lemma_kana:
        return (
            f"dictionary reading {reading!r} does not match lemma reading "
            f"{lemma_kana!r}"
        )
    allowed_keys = {
        candidate_key
        for candidate_key, _candidate_entry in _canonical_candidates(
            lemma, lemma_kana
        )
    }
    if key not in allowed_keys:
        return (
            "dictionary_key is a same-reading homograph but not a canonical "
            "candidate for this written lemma"
        )
    if not definition.strip():
        return "dictionary_definition_en must give an agent-authored contextual sense"
    return None


def candidate_for_lemma(lemma: str, lemma_kana: str) -> dict[str, Any] | None:
    """Return canonical candidate evidence for an agent's semantic choice."""
    candidates = _canonical_candidates(lemma, lemma_kana)
    if not candidates:
        return None
    rendered = [
        {
            "dictionary_key": key,
            "reading": entry.get("p", ""),
            "definitions": list(entry.get("d") or []),
            "part_of_speech": list(entry.get("pos") or []),
        }
        for key, entry in candidates
    ]
    return rendered[0] if len(rendered) == 1 else {
        "canonical_candidates": rendered,
        "instruction": "choose the one whose lexical sense matches this occurrence",
    }
