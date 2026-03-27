import csv
from functools import lru_cache
from src.config import DATA_DIR, LEVELS, LEVEL_FILE_KEYS
from src.vocab.models import Word, Character, HskLevel


def load_words(level: int) -> list[Word]:
    key = LEVEL_FILE_KEYS[level]
    path = DATA_DIR / "words" / f"{key}_words.csv"
    words = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            words.append(Word(
                word=row["word"],
                pinyin=row["pinyin"],
                pos=row["pos"],
                english=row["english"],
                level=level,
            ))
    return words


def load_characters(level: int) -> list[Character]:
    key = LEVEL_FILE_KEYS[level]
    path = DATA_DIR / "characters" / f"{key}_chars.csv"
    chars = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            chars.append(Character(
                character=row["character"],
                writing_level=row["writing_level"],
                traditional=row["traditional"],
                examples=row["examples"],
                level=level,
            ))
    return chars


@lru_cache(maxsize=1)
def load_all_levels() -> dict[int, HskLevel]:
    levels = {}
    for lvl in LEVELS:
        levels[lvl] = HskLevel(
            level=lvl,
            words=load_words(lvl),
            characters=load_characters(lvl),
        )
    return levels


def load_cumulative_words(up_to_level: int) -> set[str]:
    all_levels = load_all_levels()
    result = set()
    for lvl in LEVELS:
        if lvl <= up_to_level:
            result |= all_levels[lvl].word_set
    return result


def load_cumulative_characters(up_to_level: int) -> set[str]:
    all_levels = load_all_levels()
    result = set()
    for lvl in LEVELS:
        if lvl <= up_to_level:
            result |= all_levels[lvl].char_set
    return result
