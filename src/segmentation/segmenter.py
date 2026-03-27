import jieba
from src.config import ALL_PUNCTUATION, DATA_DIR, LEVEL_FILE_KEYS
import csv


class ChineseSegmenter:
    """Chinese word segmentation using jieba with HSK custom dictionary."""

    _initialized = False
    _hsk_words: set[str] = set()

    def __init__(self):
        if not ChineseSegmenter._initialized:
            self._load_hsk_dict()
            ChineseSegmenter._initialized = True

    def _load_hsk_dict(self):
        """Load HSK word lists into jieba for better segmentation accuracy."""
        all_hsk_words = set()
        for level, key in LEVEL_FILE_KEYS.items():
            path = DATA_DIR / "words" / f"{key}_words.csv"
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        word = row["word"]
                        all_hsk_words.add(word)
                        jieba.add_word(word, freq=50000)

        ChineseSegmenter._hsk_words = all_hsk_words

        # Remove jieba default dict entries that block correct HSK word boundaries
        for word in list(jieba.dt.FREQ.keys()):
            if len(word) > 1 and word not in all_hsk_words:
                if self._is_decomposable(word, all_hsk_words):
                    jieba.del_word(word)

    @staticmethod
    def _is_decomposable(word: str, vocab: set[str]) -> bool:
        """Check if a word can be split into known vocabulary words using DP."""
        n = len(word)
        if n <= 1:
            return False
        # DP: can the word be fully segmented into vocab words?
        dp = [False] * (n + 1)
        dp[0] = True
        for i in range(1, n + 1):
            for j in range(i):
                if dp[j] and word[j:i] in vocab:
                    dp[i] = True
                    break
        return dp[n]

    def segment(self, text: str) -> list[str]:
        """Segment Chinese text, then post-process to split non-HSK compounds."""
        raw = jieba.lcut(text)
        result = []
        for token in raw:
            if not token.strip() or self._is_punctuation(token):
                continue
            # If token is in HSK, keep as-is
            if token in self._hsk_words:
                result.append(token)
            # If token is not in HSK, try to split into HSK sub-words
            elif len(token) > 1:
                sub = self._split_into_hsk(token)
                result.extend(sub)
            else:
                result.append(token)
        return result

    def _split_into_hsk(self, token: str) -> list[str]:
        """Try to split a non-HSK token into known HSK words.
        Falls back to individual characters if no full decomposition found."""
        n = len(token)
        # DP to find best split
        # dp[i] = list of words covering token[:i], or None
        dp: list[list[str] | None] = [None] * (n + 1)
        dp[0] = []
        for i in range(1, n + 1):
            # Prefer longer matches
            for j in range(i - 1, -1, -1):
                if dp[j] is not None:
                    sub = token[j:i]
                    if sub in self._hsk_words or (len(sub) == 1 and '\u4e00' <= sub <= '\u9fff'):
                        dp[i] = dp[j] + [sub]
                        break
        if dp[n] is not None:
            return dp[n]
        # Fallback: return individual characters
        return [c for c in token if c.strip() and not self._is_punctuation(c)]

    def segment_with_positions(self, text: str) -> list[tuple[str, int, int]]:
        """Segment with start/end character offsets in the original text."""
        results = []
        pos = 0
        for word in jieba.lcut(text):
            start = text.find(word, pos)
            if start == -1:
                start = pos
            end = start + len(word)
            if word.strip() and not self._is_punctuation(word):
                results.append((word, start, end))
            pos = end
        return results

    def segment_raw(self, text: str) -> list[str]:
        """Segment without filtering - includes punctuation and whitespace."""
        return jieba.lcut(text)

    @staticmethod
    def _is_punctuation(token: str) -> bool:
        return all(c in ALL_PUNCTUATION or c.isdigit() or c.isascii()
                   or ChineseSegmenter._is_non_cjk_letter(c) for c in token)

    @staticmethod
    def _is_non_cjk_letter(c: str) -> bool:
        """Check if character is a non-CJK letter (e.g. accented Latin from pinyin)."""
        return c.isalpha() and not ('\u4e00' <= c <= '\u9fff')
