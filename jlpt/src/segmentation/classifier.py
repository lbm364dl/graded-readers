from src.segmentation.segmenter import JapaneseSegmenter
from src.vocab.lookup import VocabLookup
from src.config import LEVELS, LEVEL_LABELS


class LevelClassifier:
    """Classify each word token with its JLPT level."""

    def __init__(self):
        self._segmenter = JapaneseSegmenter()
        self._vocab = VocabLookup()

    def classify(self, text: str, allowed_words: set[str] | None = None) -> list[tuple[str, int | None]]:
        """Return (word, level_or_None) for every content word in *text*.

        *allowed_words* (glossary / taught vocab) are counted as in-level
        without needing to appear in the JLPT lists.
        """
        allowed = allowed_words or set()
        words = self._segmenter.segment(text)
        result = []
        for word in words:
            if word in allowed:
                result.append((word, 0))  # 0 = explicitly allowed
            else:
                lvl = self._vocab.get_word_level(word)
                result.append((word, lvl))
        return result

    def above_level_words(self, text: str, target_level: int,
                          allowed_words: set[str] | None = None) -> list[str]:
        classified = self.classify(text, allowed_words)
        seen: set[str] = set()
        result = []
        for word, lvl in classified:
            if lvl is None or lvl > target_level:
                if word not in seen:
                    seen.add(word)
                    result.append(word)
        return result
