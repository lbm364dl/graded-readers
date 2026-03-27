from dataclasses import dataclass, field
from src.segmentation.segmenter import ChineseSegmenter
from src.vocab.lookup import VocabLookup
from src.config import MAX_ABOVE_LEVEL_RATIO


@dataclass
class ClassifiedSegment:
    word: str
    level: int | None  # None = not in any HSK level
    is_above_target: bool


@dataclass
class TextClassification:
    segments: list[ClassifiedSegment]
    target_level: int
    total_tokens: int
    in_level_count: int
    above_level_count: int
    unknown_count: int  # not in HSK at all

    @property
    def coverage_ratio(self) -> float:
        if self.total_tokens == 0:
            return 1.0
        return self.in_level_count / self.total_tokens

    @property
    def above_level_ratio(self) -> float:
        if self.total_tokens == 0:
            return 0.0
        return (self.above_level_count + self.unknown_count) / self.total_tokens

    @property
    def passes_threshold(self) -> bool:
        return self.above_level_ratio <= MAX_ABOVE_LEVEL_RATIO

    @property
    def above_level_words(self) -> list[str]:
        seen = set()
        result = []
        for seg in self.segments:
            if seg.is_above_target and seg.word not in seen:
                seen.add(seg.word)
                result.append(seg.word)
        return result

    @property
    def level_distribution(self) -> dict[int | None, int]:
        dist: dict[int | None, int] = {}
        for seg in self.segments:
            dist[seg.level] = dist.get(seg.level, 0) + 1
        return dist


class LevelClassifier:
    """Classifies each word in a text by its HSK level."""

    def __init__(self, vocab_lookup: VocabLookup | None = None,
                 segmenter: ChineseSegmenter | None = None):
        self.vocab = vocab_lookup or VocabLookup()
        self.segmenter = segmenter or ChineseSegmenter()

    def classify_text(self, text: str, target_level: int) -> TextClassification:
        words = self.segmenter.segment(text)
        segments = []
        in_level = 0
        above_level = 0
        unknown = 0

        for word in words:
            level = self.vocab.get_word_level(word)
            if level is None:
                # Try character-level fallback for single chars
                if len(word) == 1:
                    level = self.vocab.get_char_level(word)

            if level is not None and level <= target_level:
                is_above = False
                in_level += 1
            else:
                is_above = True
                if level is None:
                    unknown += 1
                else:
                    above_level += 1

            segments.append(ClassifiedSegment(
                word=word, level=level, is_above_target=is_above
            ))

        return TextClassification(
            segments=segments,
            target_level=target_level,
            total_tokens=len(words),
            in_level_count=in_level,
            above_level_count=above_level,
            unknown_count=unknown,
        )
