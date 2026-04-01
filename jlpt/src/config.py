from pathlib import Path
import json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
READERS_DIR = PROJECT_ROOT / "readers"

# The 95/5 rule: at most 5% of tokens may be above the target level
MAX_ABOVE_LEVEL_RATIO = 0.05

# Internal levels: 1=N5 (easiest) … 5=N1 (hardest), mirroring HSK convention
LEVELS = [1, 2, 3, 4, 5]
LEVEL_LABELS = {1: "N5", 2: "N4", 3: "N3", 4: "N2", 5: "N1"}
LEVEL_FILE_KEYS = {1: "n5", 2: "n4", 3: "n3", 4: "n2", 5: "n1"}

# Japanese punctuation — excluded from vocabulary counts
JAPANESE_PUNCTUATION = set("。、！？・；：「」『』（）《》【】…—～｛｝〔〕〈〉―")
GENERAL_PUNCTUATION = set(",.!?;:'\"()-[]{}…—· \t\n\r")
ALL_PUNCTUATION = JAPANESE_PUNCTUATION | GENERAL_PUNCTUATION

# SudachiPy part-of-speech categories to skip in vocabulary counting.
# Particles and auxiliaries are grammatical glue; they are never listed in
# JLPT vocabulary and should not count against the 95/5 limit.
SKIP_POS = {
    "助詞",      # particles: は、が、を、に、で、etc.
    "助動詞",    # auxiliary verbs: です、ます、た、etc.
    "接尾辞",    # suffixes
    "接頭辞",    # prefixes (counted with their noun)
    "記号",      # symbols
    "補助記号",  # supplementary symbols (punctuation)
    "空白",      # whitespace tokens
}


def load_level_metadata() -> dict:
    with open(DATA_DIR / "jlpt_levels.json", encoding="utf-8") as f:
        return json.load(f)
