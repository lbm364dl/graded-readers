from dataclasses import dataclass
from src.segmentation.classifier import LevelClassifier, TextClassification
from src.vocab.lookup import VocabLookup
from src.segmentation.segmenter import ChineseSegmenter


@dataclass
class CoverageStats:
    target_level: int
    total_tokens: int
    unique_tokens: int
    in_level_tokens: int
    above_level_tokens: int
    unknown_tokens: int
    coverage_percent: float
    above_level_percent: float
    level_distribution: dict[int | None, int]
    above_level_words: list[tuple[str, int | None]]  # (word, level)

    @property
    def passes(self) -> bool:
        from src.config import MAX_ABOVE_LEVEL_RATIO
        return self.above_level_percent / 100.0 <= MAX_ABOVE_LEVEL_RATIO


def coverage_statistics(
    text: str,
    target_level: int,
    classifier: LevelClassifier | None = None,
) -> CoverageStats:
    """Compute detailed HSK coverage statistics for a text."""
    cls = classifier or LevelClassifier()
    result = cls.classify_text(text, target_level)

    unique = set()
    above_words = []
    seen_above: set[str] = set()

    for seg in result.segments:
        unique.add(seg.word)
        if seg.is_above_target and seg.word not in seen_above:
            seen_above.add(seg.word)
            above_words.append((seg.word, seg.level))

    total = result.total_tokens
    coverage_pct = (result.in_level_count / total * 100) if total > 0 else 100.0
    above_pct = (100.0 - coverage_pct)

    return CoverageStats(
        target_level=target_level,
        total_tokens=total,
        unique_tokens=len(unique),
        in_level_tokens=result.in_level_count,
        above_level_tokens=result.above_level_count,
        unknown_tokens=result.unknown_count,
        coverage_percent=coverage_pct,
        above_level_percent=above_pct,
        level_distribution=result.level_distribution,
        above_level_words=above_words,
    )
