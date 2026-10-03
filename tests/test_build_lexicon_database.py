import json
import sqlite3

from scripts.build_lexicon_database import build_database


def test_builds_queryable_indexed_lexicon(tmp_path):
    output = tmp_path / "lexicon.sqlite3"
    metadata = build_database(output)

    assert metadata["dictionary_zh_entries"] > 30_000
    assert metadata["dictionary_ja_entries"] > 10_000
    assert metadata["schema_version"] == 3
    assert metadata["etymology_entries"] > 20_000
    assert metadata["glyph_entries"] > 1_000
    assert metadata["character_level_entries"] == 3_000
    assert metadata["bytes"] == output.stat().st_size
    assert len(metadata["sha256"]) == 64

    database = sqlite3.connect(f"file:{output}?mode=ro", uri=True)
    try:
        reading, definitions = database.execute(
            "SELECT reading, definitions FROM dictionary "
            "WHERE language = ? AND word = ?",
            ("zh", "民心"),
        ).fetchone()
        assert reading
        assert "popular sentiment" in json.loads(definitions)

        # Canonical Japanese headwords and reading aliases are distinct. This
        # lets authored links reject a kana alias (and prevents quotative と
        # from falling through to 戸 "door").
        japanese_to = database.execute(
            "SELECT word, definitions, is_alias FROM dictionary "
            "WHERE language = ? AND lookup_key = ?",
            ("ja", "と"),
        ).fetchone()
        assert japanese_to[0] == "と"
        assert json.loads(japanese_to[1]) == ["and"]
        assert japanese_to[2] == 0
        kana_alias = database.execute(
            "SELECT word, is_alias FROM dictionary "
            "WHERE language = ? AND lookup_key = ?",
            ("ja", "つかまえる"),
        ).fetchone()
        assert kana_alias == ("捕まえる", 1)

        # A one-character word can have a later vocabulary band than the
        # character itself. The UI must use this New HSK character table for
        # character lookup badges instead of conflating the two curricula.
        assert database.execute(
            "SELECT level FROM character_level WHERE character = ?", ("批",)
        ).fetchone()[0] == 3
        assert database.execute(
            "SELECT level FROM character_level WHERE character = ?", ("评",)
        ).fetchone()[0] == 3
        assert database.execute(
            "SELECT level FROM dictionary WHERE language = ? AND word = ?",
            ("zh", "批"),
        ).fetchone()[0] == 4
        assert database.execute(
            "SELECT level FROM dictionary WHERE language = ? AND word = ?",
            ("zh", "评"),
        ).fetchone()[0] == 6

        etymology = database.execute(
            "SELECT payload FROM etymology WHERE character = ?", ("民",)
        ).fetchone()
        assert isinstance(json.loads(etymology[0]), dict)

        glyph = database.execute(
            "SELECT payload FROM glyph WHERE character = ?", ("民",)
        ).fetchone()
        assert any("<svg" in svg for svg in json.loads(glyph[0]).values())
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        database.close()
