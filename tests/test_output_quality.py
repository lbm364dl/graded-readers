"""Integrity checks for the clean Chinese catalogue shipped by the app."""

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
APP_CONTENT = ROOT / "app" / "assets" / "content.json"
CONTENT_DIR = ROOT / "content" / "chinese" / "sanguoyanyi"
HSK_LEVELS = range(1, 7)


@pytest.fixture(scope="module")
def content_json():
    return json.loads(APP_CONTENT.read_text("utf-8"))


def test_clean_chinese_sources_have_every_published_level():
    for level in HSK_LEVELS:
        assert (CONTENT_DIR / f"hsk{level}.md").is_file()
        assert (CONTENT_DIR / f"hsk{level}.annotations.json").is_file()


def test_content_json_is_only_the_current_clean_catalogue(content_json):
    expected = {f"sanguoyanyi_hsk{level}" for level in HSK_LEVELS}
    assert {entry["id"] for entry in content_json} == expected
    assert {entry["book"] for entry in content_json} == {"sanguoyanyi"}


def test_content_json_entries_are_complete(content_json):
    required = {"id", "book", "bookTitle", "bookTitleEn", "level", "chapters"}
    ids = []
    for entry in content_json:
        ids.append(entry["id"])
        assert not required - entry.keys()
        assert isinstance(entry["level"], int)
        assert 1 <= entry["level"] <= 6
        assert entry["chapters"]
        for chapter in entry["chapters"]:
            assert chapter.get("title")
            assert chapter.get("content", "").strip()
    assert len(ids) == len(set(ids))


def test_content_json_matches_clean_markdown(content_json):
    by_level = {entry["level"]: entry for entry in content_json}
    for level in HSK_LEVELS:
        source = (CONTENT_DIR / f"hsk{level}.md").read_text("utf-8").strip()
        published = "\n\n".join(
            chapter["content"].strip() for chapter in by_level[level]["chapters"]
        )
        source_prose = "\n\n".join(
            block.strip()
            for block in source.split("\n\n")
            if block.strip() and not block.lstrip().startswith("#")
        )
        assert published == source_prose


def test_book_titles_are_consistent(content_json):
    assert len({entry["bookTitle"] for entry in content_json}) == 1
    assert len({entry["bookTitleEn"] for entry in content_json}) == 1
