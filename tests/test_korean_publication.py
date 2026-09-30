import json
from pathlib import Path

import pytest

from scripts import generate_app_content_json as publication


def test_korean_pilot_has_reviewed_tap_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "ASSET_ROOT", tmp_path)
    entries = publication.build_language("korean", {"l1": 1})
    assert len(entries) == 1
    chapter = entries[0]["chapters"][0]
    annotation = json.loads((tmp_path / "annotations" /
        Path(chapter["annotationAsset"]).name).read_text())
    assert annotation["text"] == chapter["content"]
    assert "".join(segment["text"] for segment in annotation["segments"]) == chapter["content"]
    assert any(segment["text"] == "홍길동" for segment in annotation["segments"])


def test_korean_publication_rejects_unreviewed_or_shifted_segments(tmp_path):
    source = Path("content/korean/honggildong/l1.annotations.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    chapter = [{"title": "1. 길동의 집", "content": document["chapters"][0]["text"]}]
    document["chapters"][0]["segments"][0]["text"] = "옛날"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="do not reconstruct"):
        publication.load_korean_annotations(path, chapter, book_id="test",
                                            level_key="l1", require_complete=True)
    document["chapters"][0]["segments"][0]["text"] = "옛날에"
    document["chapters"][0]["annotation_audit"]["all_reviewed"] = False
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="unreviewed"):
        publication.load_korean_annotations(path, chapter, book_id="test",
                                            level_key="l1", require_complete=True)
