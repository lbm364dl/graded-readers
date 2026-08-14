"""Editorial policy for progressively freer graded-reader adaptations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdaptationPolicy:
    level: str
    scope: str
    language: str
    fidelity: str
    continuity: str
    audit_source_omissions: bool
    max_above_level_ratio: float


_POLICIES = {
    "hsk1": AdaptationPolicy(
        "hsk1",
        "Retell only one central action or decision. Freely omit politics, offices, dates, troop counts, genealogy, descriptions, and minor characters.",
        "Use very short sentences and overwhelmingly HSK1 words and characters. Keep only indispensable names; explain ideas with common concrete words.",
        "Preserve the broad outcome and do not contradict the story. Coverage of source events is not a goal.",
        "Judge only whether the simplified story makes sense from one adapted chapter to the next.",
        False, 0.20,
    ),
    "hsk2": AdaptationPolicy(
        "hsk2",
        "Retell the main action and its simplest cause and result. Freely omit secondary episodes, titles, dates, numbers, genealogy, and minor characters.",
        "Use short direct sentences and overwhelmingly HSK1-HSK2 language. Keep specialized historical vocabulary only when indispensable.",
        "Preserve major characters, outcomes, and basic causality; detailed correspondence with the source is not required.",
        "Judge the adapted narrative's own causal and character continuity, allowing large source omissions.",
        False, 0.12,
    ),
    "hsk3": AdaptationPolicy(
        "hsk3",
        "Produce a coherent abridged story focused on major characters and turning points. Merge or omit subplots and minor events freely.",
        "Use clear modern Chinese centered on HSK1-HSK3 language. Paraphrase literary, military, bureaucratic, and historical terminology.",
        "Preserve the broad arc, major outcomes, identities, and motivations; exhaustive event coverage is not required.",
        "Judge internal narrative continuity first; source details matter only when the adaptation explicitly uses them.",
        False, 0.08,
    ),
    "hsk4": AdaptationPolicy(
        "hsk4",
        "Retain major events and causal links while omitting minor description and secondary action.",
        "Use natural modern HSK4-centered Chinese; limit specialized terms to those needed by the story.",
        "Preserve major events and outcomes, but allow substantial compression.",
        "Check both internal continuity and major source-grounded handoffs.",
        True, 0.06,
    ),
    "hsk5": AdaptationPolicy(
        "hsk5",
        "Retain most consequential events, motivations, and important secondary characters.",
        "Use natural modern HSK5-centered Chinese with selective historical vocabulary.",
        "Remain close to consequential source content while allowing abridgment.",
        "Check internal continuity and consequential source-grounded handoffs.",
        True, 0.05,
    ),
    "hsk6": AdaptationPolicy(
        "hsk6",
        "Provide a detailed modern retelling while trimming repetition and ornamental material.",
        "Use rich natural modern Chinese suitable for HSK6 readers.",
        "Remain substantially faithful to events, motivations, identities, and chronology.",
        "Check internal continuity and detailed source-grounded handoffs.",
        True, 0.05,
    ),
}


def policy_for(level: str) -> AdaptationPolicy:
    try:
        return _POLICIES[level.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported adaptation level: {level}") from exc
