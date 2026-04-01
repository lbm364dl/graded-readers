from dataclasses import dataclass
from src.segmentation.classifier import LevelClassifier
from src.config import MAX_ABOVE_LEVEL_RATIO


@dataclass
class CoverageStats:
    level: int
    total_tokens: int
    in_level_tokens: int
    above_level_tokens: int
    coverage_percent: float
    above_level_percent: float
    passes: bool
    top_unknown: list[tuple[str, int]]   # (word, frequency)


def coverage_statistics(
    text: str,
    target_level: int,
    classifier: LevelClassifier | None = None,
    allowed_words: set[str] | None = None,
) -> CoverageStats:
    clf = classifier or LevelClassifier()
    allowed = allowed_words or set()

    classified = clf.classify(text, allowed)
    total = len(classified)

    in_level = 0
    freq: dict[str, int] = {}

    for word, lvl in classified:
        if lvl is not None and lvl <= target_level:
            in_level += 1
        else:
            freq[word] = freq.get(word, 0) + 1

    above = total - in_level
    cov = (in_level / total * 100) if total > 0 else 100.0
    above_pct = (above / total * 100) if total > 0 else 0.0

    top_unknown = sorted(freq.items(), key=lambda x: -x[1])[:30]

    return CoverageStats(
        level=target_level,
        total_tokens=total,
        in_level_tokens=in_level,
        above_level_tokens=above,
        coverage_percent=cov,
        above_level_percent=above_pct,
        passes=above_pct / 100 <= MAX_ABOVE_LEVEL_RATIO,
        top_unknown=top_unknown,
    )
