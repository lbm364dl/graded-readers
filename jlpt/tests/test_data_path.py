from pathlib import Path

from src.config import DATA_DIR


def test_jlpt_data_path_points_to_maintained_repository_corpus():
    assert DATA_DIR.name == "japanese"
    assert (DATA_DIR / "jlpt_levels.json").is_file()
    assert (DATA_DIR / "words" / "n5_words.csv").is_file()
    assert "jlpt/data" not in DATA_DIR.as_posix()
