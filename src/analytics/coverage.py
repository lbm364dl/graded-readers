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
    lower_level_coverage_percent: float
    target_band_tokens: int
    target_band_percent: float

    @property
    def passes(self) -> bool:
        from src.config import MAX_ABOVE_LEVEL_RATIO
        return self.above_level_percent / 100.0 <= MAX_ABOVE_LEVEL_RATIO

    @property
    def fits_target_band(self) -> bool:
        """Whether the text plausibly belongs in the requested band.

        This is deliberately a heuristic, not an official HSK classification.
        It combines the difficulty ceiling with a minimum amount of target-band
        vocabulary and rejects texts that also pass at the preceding level.
        """
        from src.config import (
            MAX_LOWER_LEVEL_COVERAGE_RATIO,
            MIN_TARGET_BAND_RATIO,
        )

        if self.target_level <= 1:
            return self.passes
        return (
            self.passes
            and self.lower_level_coverage_percent / 100.0
            < MAX_LOWER_LEVEL_COVERAGE_RATIO
            and self.target_band_percent / 100.0 >= MIN_TARGET_BAND_RATIO
        )


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
    target_band_tokens = result.level_distribution.get(target_level, 0)
    target_band_pct = (target_band_tokens / total * 100) if total > 0 else 0.0
    if target_level <= 1:
        lower_level_coverage_pct = 0.0
    else:
        lower_level_tokens = sum(
            count for level, count in result.level_distribution.items()
            if level is not None and level < target_level
        )
        lower_level_coverage_pct = (
            lower_level_tokens / total * 100 if total > 0 else 100.0
        )

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
        lower_level_coverage_percent=lower_level_coverage_pct,
        target_band_tokens=target_band_tokens,
        target_band_percent=target_band_pct,
    )
