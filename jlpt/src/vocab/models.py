from dataclasses import dataclass, field


@dataclass
class Word:
    word: str       # Japanese word (kanji/kana as written in JLPT lists)
    reading: str    # hiragana reading
    pos: str        # part of speech abbreviation
    english: str    # English gloss
    level: int      # internal JLPT level (1=N5 … 5=N1)

    @property
    def key(self) -> str:
        return self.word


@dataclass
class JlptLevel:
    level: int
    words: list[Word] = field(default_factory=list)

    @property
    def word_set(self) -> set[str]:
        return {w.word for w in self.words}

    @property
    def reading_map(self) -> dict[str, str]:
        """Map word → hiragana reading."""
        return {w.word: w.reading for w in self.words}
