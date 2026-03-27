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
    allowed_words: set[str] | None = None,
) -> ConstraintResult:
    """Check whether a text satisfies the vocabulary constraint for a given HSK level.

    The constraint passes if at most `max_ratio` (default 5%) of word tokens
    are above the target level or unknown.

    `allowed_words` is an optional set of words (e.g. glossary / proper nouns)
    that are treated as in-level regardless of their actual HSK level.
    """
    vocab = vocab_lookup or VocabLookup()
    seg = segmenter or ChineseSegmenter()
    allowed = allowed_words or set()

    words = seg.segment(text)
    total = len(words)
    in_level = 0
    above_words: list[str] = []
    seen_above: set[str] = set()

    for word in words:
        if word in allowed:
            in_level += 1
            continue

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


def check_character_constraint(
    text: str,
    target_level: int,
    max_ratio: float = MAX_ABOVE_LEVEL_RATIO,
    vocab_lookup: VocabLookup | None = None,
    allowed_chars: set[str] | None = None,
) -> ConstraintResult:
    """Check whether a text satisfies the character-level constraint for a given HSK level.

    Unlike check_vocabulary_constraint (which works on segmented word tokens),
    this checks each individual Chinese character against the cumulative HSK
    character list for the target level.

    The constraint passes if at most `max_ratio` (default 5%) of Chinese
    characters are above the target level.

    `allowed_chars` is an optional set of characters (e.g. from glossary words
    or taught vocabulary) treated as in-level regardless of their actual level.
    """
    vocab = vocab_lookup or VocabLookup()
    allowed = allowed_chars or set()
    cumul = vocab.get_cumulative_chars(target_level)

    total = 0
    in_level = 0
    above_list: list[str] = []
    seen_above: set[str] = set()

    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            total += 1
            if ch in allowed or ch in cumul:
                in_level += 1
            else:
                if ch not in seen_above:
                    seen_above.add(ch)
                    above_list.append(ch)

    above_count = total - in_level
    ratio = above_count / total if total > 0 else 0.0

    return ConstraintResult(
        passes=ratio <= max_ratio,
        total_tokens=total,
        in_level_tokens=in_level,
        above_level_tokens=above_count,
        above_level_ratio=ratio,
        above_level_words=above_list,
        max_allowed_ratio=max_ratio,
    )
