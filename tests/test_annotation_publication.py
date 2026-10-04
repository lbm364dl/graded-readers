import copy
import json

import pytest

from pipeline.annotation_publication import (
    AnnotationPublicationError,
    bind_review_job,
    normal_review_receipt,
    persist_chunk_attempts,
    verify_normal_review_receipt,
    verify_chunk_attempt_receipts,
)


def reviewed_reader(tmp_path, surface_key="text"):
    chunks = ["猫。", "猫。"]
    items = [{
        "segments": [{surface_key: "猫", "meaning_en": meaning},
                     {surface_key: "。"}],
        "grammar_overlays": [{"start": 0, "end": 1, "text": "猫"}],
        "resolved": True,
        "attempts": [{"review": {"verdict": "pass"}}],
    } for meaning in ["cat", "the cat"]]
    receipts = persist_chunk_attempts(tmp_path, chunks, items, surface_key=surface_key)
    reader = {
        "segments": [segment for item in items for segment in item["segments"]],
        "grammar_overlays": [{**overlay, "start": overlay["start"] + index * 2,
                              "end": overlay["end"] + index * 2}
                             for index, item in enumerate(items)
                             for overlay in item["grammar_overlays"]],
        "annotation_audit": {"chunks": 2, "all_reviewed": True,
                             "chunk_review_receipts_version": 1,
                             "chunk_review_receipts": receipts},
    }
    return reader, items


@pytest.mark.parametrize("surface_key", ["text", "surface"])
def test_receipts_preserve_distinct_repeated_positions_and_local_overlay_offsets(tmp_path, surface_key):
    reader, items = reviewed_reader(tmp_path, surface_key)
    assert verify_chunk_attempt_receipts(
        tmp_path, reader, "猫。猫。", surface_key=surface_key) == items


@pytest.mark.parametrize("mutation", ["version", "missing_version", "missing_receipts",
                                      "source", "candidate", "order", "overlay"])
def test_versioned_proofs_fail_closed_on_stale_or_misbound_data(tmp_path, mutation):
    reader, _ = reviewed_reader(tmp_path)
    changed = copy.deepcopy(reader)
    source = "猫。猫。"
    audit = changed["annotation_audit"]
    if mutation == "version":
        audit["chunk_review_receipts_version"] = 2
    elif mutation == "missing_version":
        del audit["chunk_review_receipts_version"]
    elif mutation == "missing_receipts":
        del audit["chunk_review_receipts"]
    elif mutation == "source":
        source = "犬。猫。"
    elif mutation == "candidate":
        changed["segments"][0]["meaning_en"] = "dog"
    elif mutation == "order":
        audit["chunk_review_receipts"].reverse()
    elif mutation == "overlay":
        changed["grammar_overlays"][0]["end"] = 3
    with pytest.raises(AnnotationPublicationError):
        verify_chunk_attempt_receipts(tmp_path, changed, source, surface_key="text")


def test_changed_accepted_attempt_cannot_reuse_reader_receipt(tmp_path):
    reader, _ = reviewed_reader(tmp_path)
    path = tmp_path / "accepted-annotations/chunk_0000.json"
    item = json.loads(path.read_text())
    item["attempts"][0]["review"]["verdict"] = "revise"
    path.write_text(json.dumps(item))
    with pytest.raises(AnnotationPublicationError, match="evidence changed"):
        verify_chunk_attempt_receipts(tmp_path, reader, "猫。猫。", surface_key="text")


def test_receipt_absence_is_legacy_only_when_both_fields_are_absent(tmp_path):
    reader = {"annotation_audit": {"all_reviewed": True}}
    assert verify_chunk_attempt_receipts(tmp_path, reader, "猫", surface_key="text") is None


@pytest.mark.parametrize("changed", ["source", "candidate", "duplicate_child", "model", "effort"])
def test_normal_review_receipt_binds_actual_child_to_exact_inputs(tmp_path, changed):
    job = "review"
    path = tmp_path / "agents" / job
    path.mkdir(parents=True)
    review = {"verdict": "pass", "issues": []}
    candidate = {"segments": [{"text": "猫"}], "grammar_overlays": []}
    (path / "result.json").write_text(json.dumps(review))
    (path / "meta.json").write_text(json.dumps({
        "return_code": 0, "model": "gpt-6-luna", "effort": "low",
        "fingerprint": "fixture-input", "tool_profile": "offline"}))
    bind_review_job(tmp_path, job, candidate=candidate, source_text="猫")
    receipt = normal_review_receipt(tmp_path, [job], review, candidate, "猫")
    assert verify_normal_review_receipt(
        tmp_path, receipt, review=review, candidate=candidate,
        source_text="猫", expected_children=1) == [review]
    source = "猫"
    expected_children = 1
    if changed == "source":
        source = "犬"
    elif changed == "candidate":
        candidate = {"segments": [{"text": "猫", "meaning_en": "dog"}], "grammar_overlays": []}
    elif changed == "duplicate_child":
        receipt["components"].append(copy.deepcopy(receipt["components"][0]))
        expected_children = 2
    else:
        meta_path = path / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta[changed] = "gpt-6.1-sol" if changed == "model" else "medium"
        meta_path.write_text(json.dumps(meta))
    with pytest.raises(AnnotationPublicationError):
        verify_normal_review_receipt(tmp_path, receipt, review=review, candidate=candidate,
                                     source_text=source, expected_children=expected_children)
