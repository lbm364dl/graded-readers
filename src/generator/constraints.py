from dataclasses import dataclass
from src.segmentation.segmenter import ChineseSegmenter
from src.vocab.lookup import VocabLookup
from src.config import MAX_ABOVE_LEVEL_RATIO


@dataclass
class ConstraintResult:
    passes: bool
    total_tokens: int
    in_level_tokens: int
    above_level_tokens: int
    above_level_ratio: float
    above_level_words: list[str]
    max_allowed_ratio: float


def check_vocabulary_constraint(
    text: str,
    target_level: int,
    max_ratio: float = MAX_ABOVE_LEVEL_RATIO,
    vocab_lookup: VocabLookup | None = None,
    segmenter: ChineseSegmenter | None = None,
) -> ConstraintResult:
    """Check whether a text satisfies the vocabulary constraint for a given HSK level.

    The constraint passes if at most `max_ratio` (default 5%) of word tokens
    are above the target level or unknown.
    """
    vocab = vocab_lookup or VocabLookup()
    seg = segmenter or ChineseSegmenter()

    words = seg.segment(text)
    total = len(words)
    in_level = 0
    above_words: list[str] = []
    seen_above: set[str] = set()

    for word in words:
        level = vocab.get_word_level(word)
        if level is None and len(word) == 1:
            level = vocab.get_char_level(word)

        if level is not None and level <= target_level:
            in_level += 1
        else:
            if word not in seen_above:
                seen_above.add(word)
                above_words.append(word)

    above_count = total - in_level
    ratio = above_count / total if total > 0 else 0.0

    return ConstraintResult(
        passes=ratio <= max_ratio,
        total_tokens=total,
        in_level_tokens=in_level,
        above_level_tokens=above_count,
        above_level_ratio=ratio,
        above_level_words=above_words,
        max_allowed_ratio=max_ratio,
    )
