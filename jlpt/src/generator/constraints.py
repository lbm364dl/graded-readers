from dataclasses import dataclass
from src.segmentation.segmenter import JapaneseSegmenter
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
    segmenter: JapaneseSegmenter | None = None,
    allowed_words: set[str] | None = None,
) -> ConstraintResult:
    """Check whether *text* satisfies the 95/5 vocabulary rule for *target_level*.

    Content words (nouns, verbs, adjectives, adverbs) are segmented and looked
    up in the cumulative JLPT vocabulary up to *target_level*.  Particles and
    auxiliaries are excluded from counting.

    *allowed_words* lists glossary entries (proper nouns, story-specific terms)
    that are treated as in-level regardless of their actual JLPT level.
    """
    vocab = vocab_lookup or VocabLookup()
    seg = segmenter or JapaneseSegmenter()
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

        lvl = vocab.get_word_level(word)
        if lvl is not None and lvl <= target_level:
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
