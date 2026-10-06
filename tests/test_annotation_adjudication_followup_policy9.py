"""Regression coverage for the shared bounded follow-up research route."""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from pipeline import annotation_adjudication as adjudication
from pipeline import annotation_research as research


def _inputs():
    return {
        "candidate": {"segments": [{"text": "x", "meaning_en": "x"}]},
        "known_reference_input": {},
        "context": {"source_id": "followup-policy-test"},
    }


def _terminal():
    return {
        "reference_research_version": 2,
        "status": "uncertain",
        "job": "prior-terminal",
        "reference_research": {"references": {}},
        "initial_adjudication": {"status": "uncertain"},
        "reference_research_chain_digest": "prior-chain",
    }


def _research_result(version):
    reference = {"kind": "approved_lesson", "content": {
        "_annotation_research_fact": True,
        "research_policy_version": version,
        "issue_ids": ["issue-followup"],
        "target_paths": ["/segments/0/meaning_en"],
        "fact": "A field-scoped reviewed fact.",
    }, "issue_ids": ["issue-followup"]}
    evidence = {"version": version, "status": "approved", "references": {"lesson-new": reference}}
    return {"evidence": evidence, "references": evidence["references"]}


@pytest.mark.parametrize("policy_version", [7, 8, 9])
def test_followup_runs_and_exactly_replays_historical7_8_and_new9(policy_version, tmp_path, monkeypatch):
    inputs = _inputs()
    terminal = _terminal()
    research_result = _research_result(policy_version)
    calls = {"research": 0, "final": 0}

    async def fake_research(_runner, _run_dir, *, initial_adjudication, **_kwargs):
        calls["research"] += 1
        assert initial_adjudication == terminal
        return research_result

    async def fake_final(*_args, **_kwargs):
        calls["final"] += 1
        return {"job": "final-job", "status": "uncertain", "result": "unchanged by research"}

    def verify_final(_run_dir, evidence, **_kwargs):
        assert evidence == {"job": "final-job", "status": "uncertain", "result": "unchanged by research"}
        return evidence

    monkeypatch.setattr(research, "RESEARCH_POLICY_VERSION", policy_version)
    monkeypatch.setattr(research, "research_uncertain_review", fake_research)
    monkeypatch.setattr(adjudication, "_followup_base", lambda *_args, **_kwargs: inputs)
    monkeypatch.setattr(adjudication, "_adjudicate_once", fake_final)
    monkeypatch.setattr(adjudication, "_verify_adjudication_once", verify_final)
    monkeypatch.setattr(adjudication, "_write_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(research, "verify_research_evidence",
        lambda *_args, **_kwargs: research_result)

    receipt = asyncio.run(adjudication.followup_uncertain_annotation_review(
        object(), tmp_path, terminal_evidence=terminal, **inputs))

    assert receipt["reference_research_version"] == 3
    assert receipt["reference_research"]["version"] == policy_version
    assert calls == {"research": 1, "final": 1}
    assert adjudication.verify_adjudication_evidence(tmp_path, receipt, **inputs) == receipt
    assert calls == {"research": 1, "final": 1}


def test_unsupported_followup_policy_stops_before_research_worker(tmp_path, monkeypatch):
    policy_version = 6
    calls = []
    monkeypatch.setattr(research, "RESEARCH_POLICY_VERSION", policy_version)
    monkeypatch.setattr(adjudication, "_followup_base", lambda *_args, **_kwargs: _inputs())

    async def fake_research(*_args, **_kwargs):
        calls.append("worker")
        return _research_result(policy_version)

    monkeypatch.setattr(research, "research_uncertain_review", fake_research)
    with pytest.raises(adjudication.AdjudicationError, match="supported research policy"):
        asyncio.run(adjudication.followup_uncertain_annotation_review(
            object(), tmp_path, terminal_evidence=_terminal(), **_inputs()))
    assert calls == []



def test_policy8_critic_prompt_keeps_legacy_golden_bytes():
    review = {"approved": False, "issues": [{
        "explanation": "meaning question",
        "candidate_paths": ["/segments/0/meaning_en"],
        "supporting_paths": [],
    }], "prose_revision_reason_en": ""}
    issue_id = adjudication.normalize_review("ko", review)["issues"][0]["issue_id"]
    candidate = {"segments": [{"text": "x", "meaning_en": "x"}]}
    source_text = "x"
    initial = {"status": "uncertain", "classifications": [{
        "issue_id": issue_id, "disposition": "uncertain",
        "target_dispositions": [{"path": "/segments/0/meaning_en", "disposition": "uncertain"}],
    }]}
    gate = {"passed": True, "issues": [], "candidate_digest": research._digest(candidate),
        "source_text_digest": research._digest(source_text)}
    known = {"lesson": {"kind": "approved_lesson", "content": {"meaning": "x"}}}
    inputs = research._build_inputs(language="ko", representation="annotation", candidate=candidate,
        current_review=review, prior_history=[], context={"source_id": "followup-policy-test"},
        source_text=source_text, deterministic_gate_evidence=gate, initial_adjudication=initial,
        known_reference_input=known, normal_review_receipt={"digest": "normal"}, policy_version=8)
    result = {"findings": [{"issue_id": issue_id, "status": "supported",
        "fact": "The supported scoped fact.",
        "citations": [{"reference_id": "lesson", "path": "/meaning"}], "gap": ""}]}
    resolved = {"facts": [], "unresolved": [], "validation_error": None,
        "capture_failures": [], "captured_reference_ids": []}
    review_inputs = {**inputs, "research_result": result, "resolved_research": resolved,
        "research_result_digest": research._digest(result),
        "resolved_research_digest": research._digest(resolved),
        "source_capture": {"requests": [], "references": [], "failures": [],
            "first_result_digest": research._digest(result)}}
    fingerprint = hashlib.sha256(research._review_prompt(review_inputs).encode()).hexdigest()
    assert fingerprint == "77eb89d31e0dd5ac1025fc5592206035faf5e4ff5a3c78613ff0a8bf12fe091f"
