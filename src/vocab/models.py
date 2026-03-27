from dataclasses import dataclass, field


@dataclass(frozen=True)
class Word:
    word: str
    pinyin: str
    pos: str
    english: str
    level: int


@dataclass(frozen=True)
class Character:
    character: str
    writing_level: str
    traditional: str
    examples: str
    level: int


@dataclass
class HskLevel:
    level: int
    words: list[Word] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)

    @property
    def word_set(self) -> set[str]:
        return {w.word for w in self.words}

    @property
    def char_set(self) -> set[str]:
        return {c.character for c in self.characters}
