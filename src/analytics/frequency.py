from collections import Counter
from src.segmentation.segmenter import ChineseSegmenter
from src.vocab.lookup import VocabLookup


def word_frequency(text: str, segmenter: ChineseSegmenter | None = None) -> dict[str, int]:
    """Count word frequencies in a Chinese text."""
    seg = segmenter or ChineseSegmenter()
    words = seg.segment(text)
    return dict(Counter(words).most_common())


def level_frequency(
    text: str,
    vocab_lookup: VocabLookup | None = None,
    segmenter: ChineseSegmenter | None = None,
) -> dict[int | None, int]:
    """Count word tokens by HSK level. None key = unknown words."""
    vocab = vocab_lookup or VocabLookup()
    seg = segmenter or ChineseSegmenter()
    words = seg.segment(text)

    counts: dict[int | None, int] = {}
    for word in words:
        level = vocab.get_word_level(word)
        if level is None and len(word) == 1:
            level = vocab.get_char_level(word)
        counts[level] = counts.get(level, 0) + 1
    return counts
