from pathlib import Path
import json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
READERS_DIR = PROJECT_ROOT / "readers"

# The 95/5 rule: at most 5% of tokens may be above the target level
MAX_ABOVE_LEVEL_RATIO = 0.05

# HSK 3.0 has levels 1-6 individually, plus 7-9 combined
LEVELS = [1, 2, 3, 4, 5, 6, 7]
LEVEL_LABELS = {
    1: "HSK 1", 2: "HSK 2", 3: "HSK 3",
    4: "HSK 4", 5: "HSK 5", 6: "HSK 6",
    7: "HSK 7-9",
}
LEVEL_FILE_KEYS = {
    1: "hsk1", 2: "hsk2", 3: "hsk3",
    4: "hsk4", 5: "hsk5", 6: "hsk6",
    7: "hsk7to9",
}

# Chinese punctuation - excluded from vocabulary counts
CHINESE_PUNCTUATION = set("，。！？、；：""''（）《》【】……—·「」『』〈〉〔〕")
GENERAL_PUNCTUATION = set(",.!?;:'\"()-[]{}…—·/ \t\n\r")
ALL_PUNCTUATION = CHINESE_PUNCTUATION | GENERAL_PUNCTUATION


def load_level_metadata() -> dict:
    with open(DATA_DIR / "hsk_levels.json", encoding="utf-8") as f:
        return json.load(f)
