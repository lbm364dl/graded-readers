import hashlib
import importlib
import json

import pytest

from pipeline.annotation_publication import bind_review_job, normal_review_receipt, persist_chunk_attempts


@pytest.mark.parametrize("language", ["chinese", "japanese"])
@pytest.mark.parametrize("change", [None, "unknown_version", "missing_version", "stripped_proof", "rejected_review", "missing_review_receipt", "changed_child"])
def test_publishers_recheck_chunk_proofs_before_accepting_summary(tmp_path, language, change):
    chinese = language == "chinese"
    fixtures = importlib.import_module(
        "tests.test_publish_sanguoyanyi" if chinese else "tests.test_publish_wagahai")
    sources, runs = fixtures._fixture(tmp_path)
    reader_path = next(runs.glob("*/reader.json"))
    reader = json.loads(reader_path.read_text())
    candidate = {"segments": reader["segments"], "grammar_overlays": reader["grammar_overlays"]}
    review = {"verdict": "pass", "issues": []}
    if chinese:
        review["offset_audit"] = {"valid": 0, "legacy_missing": 0, "invalid": 0}
    item = {**candidate, "resolved": True,
            "attempts": [{"annotation": candidate, "review": review}]}
    if change == "rejected_review":
        item["attempts"][0]["review"]["verdict"] = "revise"
    # Stored worker-artifact fixtures exercise host receipt validation without
    # claiming these are real model runs. Legacy offline jobs remain supported.
    jobs = ["fixture-review"] if chinese else ["fixture-general", "fixture-boundary"]
    raw = {key: value for key, value in review.items() if key != "offset_audit"}
    for job in jobs:
        path = reader_path.parent / "agents" / job
        path.mkdir(parents=True)
        (path / "result.json").write_text(json.dumps(raw))
        (path / "meta.json").write_text(json.dumps({
            "return_code": 0, "model": "gpt-6-luna", "effort": "low",
            "fingerprint": "fixture-input", "tool_profile": "offline"}))
        bind_review_job(reader_path.parent, job, candidate=candidate, source_text=reader["text"])
    item["attempts"][0]["normal_review_receipt"] = normal_review_receipt(
        reader_path.parent, jobs, review, candidate, reader["text"])
    if change == "missing_review_receipt":
        del item["attempts"][0]["normal_review_receipt"]
    elif change == "changed_child":
        (reader_path.parent / "agents" / jobs[0] / "result.json").write_text(
            json.dumps({"verdict": "revise", "issues": ["a real defect"]}))
    receipts = persist_chunk_attempts(
        reader_path.parent, [reader["text"]], [item],
        surface_key="text" if chinese else "surface")
    reader["annotation_audit"].update({"chunks": 1, "chunk_review_receipts_version": 1,
                                       "chunk_review_receipts": receipts})
    if change == "unknown_version":
        reader["annotation_audit"]["chunk_review_receipts_version"] = 2
    elif change == "missing_version":
        del reader["annotation_audit"]["chunk_review_receipts_version"]
    elif change == "stripped_proof":
        del reader["annotation_audit"]["chunk_review_receipts_version"]
        del reader["annotation_audit"]["chunk_review_receipts"]
    reader_path.write_text(json.dumps(reader, ensure_ascii=False))
    report_path = reader_path.parent / ("annotation-report.json" if chinese else "report.json")
    report = json.loads(report_path.read_text())
    report["reader_sha256"] = hashlib.sha256(reader_path.read_bytes()).hexdigest()
    report["chunk_review_receipts_version"] = 1
    report_path.write_text(json.dumps(report))
    levels = ["hsk1", "hsk2"] if chinese else ["n5", "n4"]
    if change is None:
        assert fixtures.audit_runs([runs], sources, levels, expected_chapters=2)
    else:
        with pytest.raises(fixtures.PublicationError):
            fixtures.audit_runs([runs], sources, levels, expected_chapters=2)
