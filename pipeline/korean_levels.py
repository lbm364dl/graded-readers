"""Explicit edition targets and stable asset identities for all TOPIK levels."""
from pathlib import Path

LEVEL_POLICY = Path(__file__).with_name('korean_level_instructions.md')
LEVEL_GOALS = {
    1: 'Beginner: familiar concrete words, simple clauses and explicit connections.',
    2: 'Elementary: connected everyday narration, common modifiers and explanations; keep unfamiliar historical ideas clear.',
    3: 'Intermediate: coherent narrative paragraphs, causes, contrasts and character perspectives using familiar intermediate constructions.',
    4: 'Upper intermediate: richer description, connected events and nuanced social relationships; explain unfamiliar historical vocabulary.',
    5: 'Advanced: varied narrative syntax, implied motivations supported by the source and precise abstract vocabulary with selective help.',
    6: 'Proficient: fluent modern literary narration, nuanced characterization and complex relationships; retain archaic source meaning without reproducing archaic language.',
}


def target_level(chapter: dict) -> int:
    level = chapter.get('target_level', 1)
    if type(level) is not int or level not in LEVEL_GOALS:
        raise ValueError('Korean target level must be 1–6')
    return level


def source_id(chapter: dict) -> str:
    return f"assets/annotations/korean_honggildong_l{target_level(chapter)}_{chapter['number']:03d}.json"
