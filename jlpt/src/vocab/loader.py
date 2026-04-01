import csv
from functools import lru_cache
from src.config import DATA_DIR, LEVELS, LEVEL_FILE_KEYS
from src.vocab.models import Word, JlptLevel


def load_words(level: int) -> list[Word]:
    key = LEVEL_FILE_KEYS[level]
    path = DATA_DIR / "words" / f"{key}_words.csv"
    words = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            words.append(Word(
                word=row["word"],
                reading=row["reading"],
                pos=row["pos"],
                english=row["english"],
                level=level,
            ))
    return words


@lru_cache(maxsize=1)
def load_all_levels() -> dict[int, JlptLevel]:
    levels = {}
    for lvl in LEVELS:
        levels[lvl] = JlptLevel(
            level=lvl,
            words=load_words(lvl),
        )
    return levels


def load_cumulative_words(up_to_level: int) -> set[str]:
    all_levels = load_all_levels()
    result: set[str] = set()
    for lvl in LEVELS:
        if lvl <= up_to_level:
            result |= all_levels[lvl].word_set
    return result
