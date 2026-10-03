import json

from pipeline.retry_blocked_retellings import blocked_sources


def test_selects_only_noncomplete_sources(tmp_path):
    report = tmp_path / "book-report.json"
    report.write_text(json.dumps({"runs": [
        {"source": "/a", "status": "complete"},
        {"source": "/b", "status": "blocked"},
        {"source": "/c", "status": "failed"},
    ]}), encoding="utf-8")
    assert blocked_sources(report) == ["/b", "/c"]
