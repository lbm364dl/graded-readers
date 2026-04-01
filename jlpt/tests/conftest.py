"""Shared test fixtures for JLPT Graded Readers tests."""
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def sample_n5_text():
    return "私は学生です。毎日学校に行きます。日本語を勉強しています。"


@pytest.fixture(scope="session")
def sample_n4_text():
    return "先週、友達と一緒に映画を見に行きました。とても面白かったです。来週また行く予定です。"


@pytest.fixture(scope="session")
def taketori_n5_path():
    return PROJECT_ROOT / "output" / "taketori" / "n5_taketori.md"


@pytest.fixture(scope="session")
def taketori_n3_path():
    return PROJECT_ROOT / "output" / "taketori" / "n3_taketori.md"
