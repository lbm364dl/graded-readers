import asyncio
import hashlib
import json
from pathlib import Path

import pytest

import pipeline.annotation_research as research

# Use the same exact issue identity as the shared normalizer in fresh packets.
FINDING_A = "issue-000-" + research._digest({
    "segment_index": 0, "candidate_path": "/segments/0/meaning_en",
    "problem": "meaning", "explanation": "The gloss may omit a contribution."})[:12]
from pipeline.worker_workspace import build


def _write_job(run_dir, job, prompt, schema_text, context, result):
    job_dir = run_dir / "agents" / job
    workspace = job_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    schema = json.loads(schema_text)
    _, workspace_digest = build(workspace, prompt, schema, context=context)
    artifact = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    (workspace / "candidate.json").write_text(artifact, encoding="utf-8")
    (job_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    fingerprint = research._request_fingerprint(prompt, schema_text, context)
    meta = {
        "job": job, "return_code": 0, "model": "gpt-6-luna", "effort": "low",
        "tool_profile": "workspace", "fingerprint": fingerprint,
        "workspace_digest": workspace_digest, "artifact_path": "candidate.json",
        "artifact_digest": hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return job_dir


class FakeRunner:
    model = "gpt-6-luna"
    benchmark_effort = None
    legacy_tool_restrictions = False

    def __init__(self, run_dir, research_output=None, review_output=None):
        self.run_dir = run_dir
        self.research_output = research_output or {
            "findings": [{"issue_id": FINDING_A, "status": "supported",
                          "fact": "The linked lesson describes the disputed contribution.",
                          "citations": [{"reference_id": "lesson-a", "path": "/meaning"}],
                          "gap": ""}],
        }
        self.review_output = review_output or {"approved": True, "issues": []}
        self.calls = []

    async def call(self, job, prompt, schema, effort, *, tool_profile, workspace_context):
        self.calls.append((job, effort, tool_profile))
        text = schema.read_text(encoding="utf-8")
        value = (self.research_output if job.startswith("annotation-uncertainty-research-")
                 else self.review_output)
        _write_job(self.run_dir, job, prompt, text, workspace_context, value)
        return value


class CaptureRunner(FakeRunner):
    def __init__(self, run_dir, source_url="https://dictionary.example/eng/dicSearch/SearchView?ParaWordNo=86133"):
        super().__init__(run_dir)
        self.captured_refs = []
        self.source_url = source_url
    async def call(self, job, prompt, schema, effort, *, tool_profile, workspace_context):
        self.calls.append((job, effort, tool_profile))
        context = workspace_context["annotation_uncertainty_research"]
        if job.startswith("annotation-uncertainty-research-"):
            value = {"findings": [{"issue_id": FINDING_A, "status": "unresolved",
                "fact": "", "citations": [], "gap": "The supplied references lack the rule."}],
                "source_requests": [{"url": self.source_url,
                    "issue_ids": [FINDING_A], "reason": "Read the cited official entry."}]}
        elif job.startswith("annotation-uncertainty-continuation-"):
            refs = context["known_reference_input"]
            captured = [key for key, row in refs.items() if key.startswith("captured-primary-")]
            assert len(captured) == 1
            self.captured_refs = captured
            value = {"findings": [{"issue_id": FINDING_A, "status": "supported",
                "fact": "The official entry explicitly distinguishes the form.",
                "citations": [{"reference_id": captured[0], "path": "/captured_text"}], "gap": ""}]}
        else:
            value = {"approved": True, "issues": []}
        _write_job(self.run_dir, job, prompt, schema.read_text(encoding="utf-8"),
                   workspace_context, value)
        return value


def _request_inputs():
    candidate = {"segments": [{"text": "x", "meaning_en": "means X"}], "grammar_overlays": []}
    source_text = "x"
    issue = {"segment_index": 0, "candidate_path": "/segments/0/meaning_en",
             "problem": "meaning", "explanation": "The gloss may omit a contribution."}
    review = {"verdict": "revise", "issues": [issue]}
    references = {"lesson-a": {"kind": "approved_lesson", "content": {"meaning": "means X"}}}
    gate = {"passed": True, "issues": [], "candidate_digest": research._digest(candidate),
            "source_text_digest": research._digest(source_text)}
    initial = {"status": "uncertain", "approved": False,
               "prose_requests": [], "prose_revision_reason": "",
               "classifications": [{"issue_id": FINDING_A, "disposition": "uncertain",
                   "candidate_paths": ["/segments/0/meaning_en"],
                   "reason": "The gloss contribution needs authoritative evidence."}]}
    return {
        "language": "ko", "representation": "korean-v4", "candidate": candidate,
        "current_review": review, "prior_history": [], "context": {"source_id": "s1"},
        "source_text": source_text, "deterministic_gate_evidence": gate,
        "initial_adjudication": initial, "known_reference_input": references,
        "normal_review_receipt": {"kind": "ordinary", "review_digest": research._digest(review)},
    }


def _allow_initial_replay(monkeypatch):
    monkeypatch.setattr(research, "verify_adjudication_evidence",
                        lambda *_args, **_kwargs: {"status": "uncertain"})


def test_research_and_review_add_only_cited_scoped_facts_and_replay(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    runner = FakeRunner(tmp_path)
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))

    assert result["status"] == "approved"
    assert len(runner.calls) == 2
    reference_id, reference = next(iter(result["references"].items()))
    assert reference["kind"] == "approved_lesson"
    assert reference["issue_ids"] == [FINDING_A]
    assert reference["content"]["_annotation_research_fact"] is True
    assert reference["content"]["citations"][0]["value"] == "means X"
    assert "lesson-a" not in result["references"]

    replayed = research.verify_research_evidence(tmp_path, result["evidence"], **request)
    assert replayed == result
    assert replayed["references"][reference_id] == reference


def test_research_fact_can_explain_an_unrepaired_candidate_defect(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    original_candidate = json.loads(json.dumps(request["candidate"]))
    runner = FakeRunner(tmp_path, research_output={
        "findings": [{"issue_id": FINDING_A, "status": "supported",
            "fact": "The cited rule requires a boundary meaning; the candidate currently assigns continuation to this form.",
            "citations": [{"reference_id": "lesson-a", "path": "/meaning"}], "gap": ""}],
    }, review_output={"approved": True, "issues": []})
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))

    assert result["status"] == "approved"
    assert request["candidate"] == original_candidate
    assert result["evidence"]["inputs"]["candidate"] == original_candidate
    assert "candidate is intentionally immutable" in research._review_prompt(
        result["evidence"]["inputs"])


def test_unknown_citation_never_becomes_a_reference_even_if_reviewer_approves(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    invalid = {
        "findings": [{"issue_id": FINDING_A, "status": "supported",
                      "fact": "An unsupported assertion.",
                      "citations": [{"reference_id": "invented", "path": "/meaning"}],
                      "gap": ""}],
    }
    facts, gaps, error = research._resolve_research_output(
        invalid, [FINDING_A],
        {"lesson-a": {"kind": "approved_lesson", "content": {"meaning": "means X"}}})
    assert facts == [] and gaps
    assert "valid reference IDs: ['lesson-a']" in error
    runner = FakeRunner(tmp_path, research_output={
        "findings": [{"issue_id": FINDING_A, "status": "unresolved", "fact": "",
                      "citations": [], "gap": "No supplied reference supports this point."}],
    })
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result["status"] == "unresolved"
    assert result["references"] == {}
    assert result["evidence"]["resolved_research"]["facts"] == []
    assert research.verify_research_evidence(tmp_path, result["evidence"], **request) == result


def test_metadata_only_citation_cannot_support_a_fact():
    request = _request_inputs()
    request["known_reference_input"]["lesson-a"]["content"]["title"] = "A lesson title"
    invalid = {
        "findings": [{"issue_id": FINDING_A, "status": "supported",
                      "fact": "A title does not support a linguistic fact.",
                      "citations": [{"reference_id": "lesson-a", "path": "/title"}],
                      "gap": ""}],
    }
    facts, gaps, error = research._resolve_research_output(
        invalid, [FINDING_A], request["known_reference_input"])
    assert facts == [] and gaps
    assert "metadata" in error


def test_researcher_and_critic_share_form_scope_and_source_request_rules():
    request = _request_inputs()
    inputs = research._build_inputs(**request)
    research_prompt = research._research_prompt(inputs)
    review_prompt = research._review_prompt(inputs)
    assert research.FORM_STAGE_EVIDENCE_GUIDANCE in research_prompt
    assert research.FORM_STAGE_EVIDENCE_GUIDANCE in review_prompt
    assert "Do not demand a separate dictionary entry for every exact inflected substring" in research_prompt
    assert "dependent contribution as a standalone assertion" in review_prompt
    assert "do not infer an idiom's combined meaning" in research_prompt.lower()
    assert "request that exact direct page" in research_prompt
    assert "Flag an avoidable omitted request" in review_prompt
    assert "do not require a supported fact when evidence remains insufficient" in review_prompt
    assert "candidate is intentionally immutable" in research_prompt.lower()
    assert "do not reject that fact merely because the submitted candidate still contains" in review_prompt.lower()
    assert "request that exact page in source_requests" in research_prompt
    continuation = {**inputs, "source_capture": {"references": [{"reference_id": "captured-primary-a"}]}}
    continuation_prompt = research._research_prompt(continuation)
    assert "single bounded continuation" in continuation_prompt
    assert "do not issue additional source requests" in continuation_prompt
    assert "request that exact page in source_requests" not in continuation_prompt


@pytest.mark.parametrize('version', [2, 3, 4, 5, 6])
def test_historical_research_receipt_replays_with_exact_policy_after_v7_default(
        tmp_path, monkeypatch, version):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    original_current = research.RESEARCH_POLICY_VERSION
    with monkeypatch.context() as legacy:
        legacy.setattr(research, "RESEARCH_POLICY_VERSION", version)
        legacy.setattr(research, "RESEARCH_EVIDENCE_VERSION", version)
        runner = FakeRunner(tmp_path)
        result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
        assert result["evidence"]["version"] == version
        assert ("research_policy_version" not in result["evidence"]["inputs"]) == (version == 2)
        assert (research.SCOPED_FACT_REVIEW_GUIDANCE in research._review_prompt(
            result["evidence"]["inputs"])) == (version >= 6)
    assert research.RESEARCH_POLICY_VERSION == original_current == 7
    assert research.verify_research_evidence(tmp_path, result["evidence"], **request) == result


def test_reviewer_rejection_preserves_research_but_adds_no_references(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    runner = FakeRunner(tmp_path, review_output={"approved": False, "issues": ["Claim exceeds cited source."]})
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result["status"] == "unresolved"
    assert not result["references"]
    assert result["evidence"]["research_result_digest"]
    assert research.verify_research_evidence(tmp_path, result["evidence"], **request) == result


def test_replay_rejects_changed_reference_and_worker_metadata(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    runner = FakeRunner(tmp_path)
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    changed = _request_inputs()
    changed["known_reference_input"]["lesson-a"]["content"]["meaning"] = "different meaning"
    with pytest.raises(research.AnnotationResearchError, match="research-input"):
        research.verify_research_evidence(tmp_path, result["evidence"], **changed)

    job_dir = tmp_path / "agents" / result["evidence"]["research_job"]
    meta_path = job_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["effort"] = "medium"
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(research.AnnotationResearchError, match="low-Luna workspace"):
        research.verify_research_evidence(tmp_path, result["evidence"], **request)


def test_no_authoritative_references_stays_uncertain_without_model_calls(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    request["known_reference_input"] = {}
    runner = FakeRunner(tmp_path)
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result["status"] == "unresolved"
    assert result["references"] == {}
    assert runner.calls == []
    assert research.verify_research_evidence(tmp_path, result["evidence"], **request) == result


@pytest.mark.parametrize("change, message", [
    ("cleared", "uncertain adjudication"),
    ("prose", "Prose-revision"),
    ("gate", "deterministic gate"),
])
def test_research_is_not_started_for_cleared_prose_or_gate_blocked_inputs(
        tmp_path, monkeypatch, change, message):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    if change == "cleared":
        request["initial_adjudication"]["status"] = "cleared"
    elif change == "prose":
        request["initial_adjudication"]["prose_requests"] = [{"why": "rewrite"}]
    else:
        request["deterministic_gate_evidence"]["passed"] = False
    runner = FakeRunner(tmp_path)
    with pytest.raises(research.AnnotationResearchError, match=message):
        asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert runner.calls == []


def _dictionary_pair(tmp_path, *, reviewer_output=None, reviewer_context=None, writer_context=None):
    writer_job, reviewer_job = "dictionary-0", "dictionary-review-0"
    lesson = {"id": "DRAFT-grammar-x", "pattern": "-x", "explanation_en": "A reviewed lesson."}
    writer_result = {"words": [], "grammar": [lesson]}
    context = writer_context or {"chapter": {"id": "chapter-1", "text": "source"},
               "target_occurrence": {"source_start": 2, "surface": "x"},
               "official_primary_sources": [{"id": "ref-1", "path": "source.json"}]}
    schema = json.dumps({"type": "object"}, indent=2) + "\n"
    _write_job(tmp_path, writer_job, "writer prompt", schema, context, writer_result)
    reviewer_input = {"context": context if reviewer_context is None else reviewer_context,
                      "output": writer_result}
    _write_job(tmp_path, reviewer_job, "review prompt", schema, reviewer_input,
               reviewer_output or {"approved": True, "issues": []})
    return writer_job, reviewer_job, context


def test_imported_lesson_requires_exact_writer_output_and_independent_clean_review(tmp_path):
    writer, reviewer, context = _dictionary_pair(tmp_path)
    receipt = research.import_reviewed_lesson(tmp_path, writer, reviewer,
        lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A])
    assert receipt["references"]
    assert receipt["evidence"]["version"] == 2
    reference = next(iter(receipt["references"].values()))
    assert reference["issue_ids"] == [FINDING_A]
    assert reference["content"]["id"] == "DRAFT-grammar-x"
    assert reference["content"]["explanation_en"] == "A reviewed lesson."
    assert reference["content"]["_annotation_research_fact"] is True
    assert reference["content"]["issue_ids"] == [FINDING_A]
    assert "lesson" not in reference["content"]
    assert "source_context" not in reference["content"]
    assert research.verify_imported_reviewed_lesson(tmp_path, receipt["evidence"],
        lesson_id="DRAFT-grammar-x", expected_context=context,
        issue_ids=[FINDING_A]) == receipt


def test_version1_imported_lesson_replays_without_rewriting_old_layout(tmp_path):
    writer, reviewer, context = _dictionary_pair(tmp_path)
    old = research.import_reviewed_lesson(tmp_path, writer, reviewer,
        lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A],
        _receipt_version=1)
    old_path = (tmp_path / "annotation-research" / "imports" /
                f"{old['evidence']['evidence_digest']}.json")
    before = old_path.read_bytes()
    replayed = research.verify_imported_reviewed_lesson(tmp_path, old["evidence"],
        lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A])
    assert replayed == old
    assert old_path.read_bytes() == before
    content = next(iter(replayed["references"].values()))["content"]
    assert content["lesson"]["id"] == "DRAFT-grammar-x"
    assert content["source_context"] == context


def test_imported_lesson_rejects_changed_context_reviewer_input_and_unapproved_result(tmp_path):
    writer, reviewer, context = _dictionary_pair(tmp_path, reviewer_context={"chapter": {"id": "other"}})
    with pytest.raises(research.AnnotationResearchError, match="source context"):
        research.import_reviewed_lesson(tmp_path, writer, reviewer,
            lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A])

    tmp_path2 = tmp_path / "second"
    writer, reviewer, context = _dictionary_pair(tmp_path2,
        reviewer_output={"approved": False, "issues": ["Not supported."]})
    with pytest.raises(research.AnnotationResearchError, match="clean independent approval"):
        research.import_reviewed_lesson(tmp_path2, writer, reviewer,
            lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A])


def test_imported_lesson_rejects_reviewer_output_not_equal_to_writer_result(tmp_path):
    writer, reviewer, context = _dictionary_pair(tmp_path)
    review_ws = tmp_path / "agents" / reviewer / "workspace"
    index = json.loads((review_ws / "INDEX.json").read_text())
    output_row = next(row for row in index if row["field"] == "output")
    (review_ws / output_row["path"]).write_text(json.dumps({"grammar": [], "words": []}))
    with pytest.raises(research.AnnotationResearchError, match="manifest/profile"):
        research.import_reviewed_lesson(tmp_path, writer, reviewer,
            lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A])


def test_imported_lesson_replay_rejects_changed_reviewer_metadata(tmp_path):
    writer, reviewer, context = _dictionary_pair(tmp_path)
    receipt = research.import_reviewed_lesson(tmp_path, writer, reviewer,
        lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A])
    meta_path = tmp_path / "agents" / reviewer / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["model"] = "gpt-6-astra"
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(research.AnnotationResearchError, match="low-Luna workspace"):
        research.verify_imported_reviewed_lesson(tmp_path, receipt["evidence"],
            lesson_id="DRAFT-grammar-x", expected_context=context, issue_ids=[FINDING_A])


def _capturable_request():
    request = _request_inputs()
    request["known_reference_input"]["primary-a"] = {"kind": "primary_source", "content": {
        "title": "Official reference", "url": "https://dictionary.example/about"}}
    return request


def _fake_page(url, _allowed_origins):
    raw = b"<html><head><title>Official entry</title></head><body><p>Form A marks completed action.</p><script>ignore</script></body></html>"
    text = research._visible_text(raw, "text/html; charset=utf-8")
    return {"requested_url": url, "final_url": url, "status": 200,
            "captured_at": "2026-10-04T00:00:00+00:00", "content_type": "text/html; charset=utf-8",
            "bytes_sha256": hashlib.sha256(raw).hexdigest(),
            "text_sha256": research._digest(text), "bytes": raw, "text": text}


def test_primary_page_capture_continuation_review_and_offline_replay(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _capturable_request()
    fetches = []
    def fetch(url, origins):
        fetches.append(url)
        return _fake_page(url, origins)
    monkeypatch.setattr(research, "_fetch_primary_page", fetch)
    runner = CaptureRunner(tmp_path)
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result["status"] == "approved"
    assert len(runner.calls) == 3
    assert len(fetches) == 1
    evidence = result["evidence"]
    assert evidence["version"] == 7
    assert evidence["continuation_job"]
    assert evidence["captured_references"][0]["content"]["captured_text"].find("Form A") >= 0
    assert "ignore" not in evidence["captured_references"][0]["content"]["captured_text"]
    assert evidence["review_output"] == {"approved": True, "issues": []}

    monkeypatch.setattr(research, "_fetch_primary_page",
                        lambda *_args: pytest.fail("replay must not access the network"))
    replay = research.verify_research_evidence(tmp_path, evidence, **request)
    assert replay == result
    cached = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert cached == result
    assert len(runner.calls) == 3

    raw_path = next((tmp_path / "annotation-research" / evidence["request_digest"] / "captures").glob("*.bin"))
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered")
    with pytest.raises(research.AnnotationResearchError, match="Captured primary-source bytes changed"):
        research.verify_research_evidence(tmp_path, evidence, **request)


def _reviewed_cache_request(language="ko"):
    request = _request_inputs()
    request["language"] = language
    request["representation"] = {
        "ko": "korean-v4", "ja": "japanese-annotation",
        "zh": "chinese-annotation"}[language]
    request["initial_adjudication"]["classifications"][0]["candidate_paths"] = [
        "/segments/0/meaning_en"]
    if language in {"ja", "zh"}:
        request["candidate"]["segments"][0] = {"surface": "x", "meaning_en": "means X"}
        request["context"] = {"chunk_text": "x"}
    else:
        request["context"] = {"chunk_review_context": {
            "chapter_text": "a x b", "source_start": 2}, "chunk_text": "x"}
    request["source_text"] = "x"
    request["deterministic_gate_evidence"]["candidate_digest"] = research._digest(request["candidate"])
    return request


def _reviewed_cache_context():
    return {"chapter": {"id": "chapter-1", "text": "a x b",
                         "segments": [{"text": "x"}]},
        "target_occurrence": {"target_path": "/segments/0/meaning_en",
            "segment_index": 0, "surface": "x", "source_start": 2,
            "source_text": "x"}}


def _register_reviewed_cache(tmp_path, monkeypatch, language="ko", *, extra_issue=False):
    monkeypatch.setattr(research, "_repository_root", lambda: tmp_path)
    _allow_initial_replay(monkeypatch)
    request = _reviewed_cache_request(language)
    if extra_issue:
        request["candidate"]["segments"][0]["meaning_es"] = "otro significado"
        extra = {"candidate_paths": ["/segments/0/meaning_es"],
                 "supporting_paths": [], "explanation": "Investigate the other language gloss."}
        request["current_review"]["issues"].append(extra)
        request["normal_review_receipt"]["review_digest"] = research._digest(request["current_review"])
        request["initial_adjudication"]["classifications"].append({
            "issue_id": "issue-001-" + research._digest(extra)[:12], "disposition": "uncertain",
            "candidate_paths": ["/segments/0/meaning_es"], "reason": "Other gloss needs evidence."})
        request["deterministic_gate_evidence"]["candidate_digest"] = research._digest(request["candidate"])
    source = tmp_path / "source-run"
    destination = tmp_path / "destination-run"
    source.mkdir()
    destination.mkdir()
    writer, reviewer, _ = _dictionary_pair(source,
        writer_context=_reviewed_cache_context(), reviewer_context=_reviewed_cache_context())
    result = research.register_reviewed_lesson_cache(destination,
        source_run_dir=source, writer_job=writer, reviewer_job=reviewer,
        lesson_id="DRAFT-grammar-x", expected_context=_reviewed_cache_context(), **request)
    return request, source, destination, writer, reviewer, result


@pytest.mark.parametrize("language", ["ko", "ja", "zh"])
def test_reviewed_lesson_cache_is_exact_scoped_and_replays_before_research(
        tmp_path, monkeypatch, language):
    request, _source, destination, _writer, _reviewer, registered = _register_reviewed_cache(
        tmp_path, monkeypatch, language)
    assert registered["status"] == "reviewed_lesson_reused"
    assert registered["evidence"]["candidate_approved"] is False
    reference_id, reference = next(iter(registered["references"].items()))
    assert reference["issue_ids"] == [FINDING_A]
    assert reference["content"]["id"] == "DRAFT-grammar-x"

    replay = research.verify_research_evidence(destination, registered["evidence"], **request)
    assert replay == registered
    runner = FakeRunner(destination)
    reused = asyncio.run(research.research_uncertain_review(runner, destination, **request))
    assert reused == registered
    assert runner.calls == []
    assert reused["references"][reference_id] == reference


@pytest.mark.parametrize("change", ["candidate", "source", "issue", "chapter_context"])
def test_reviewed_lesson_cache_refuses_changed_scope(tmp_path, monkeypatch, change):
    request, _source, destination, _writer, _reviewer, registered = _register_reviewed_cache(
        tmp_path, monkeypatch)
    changed = dict(request)
    if change == "candidate":
        changed["candidate"] = {"segments": [{"text": "x", "meaning_en": "different"}],
                                "grammar_overlays": []}
    elif change == "source":
        changed["source_text"] = "y"
    elif change == "issue":
        changed["initial_adjudication"] = dict(request["initial_adjudication"])
        changed["initial_adjudication"]["classifications"] = [{
            "issue_id": "other-finding", "disposition": "uncertain",
            "candidate_paths": ["/segments/0/meaning_en"]}]
    else:
        changed["context"] = {"chunk_review_context": {
            "chapter_text": "a y b", "source_start": 2}, "chunk_text": "y"}
    with pytest.raises(research.AnnotationResearchError):
        research.verify_research_evidence(destination, registered["evidence"], **changed)


def test_reviewed_lesson_cache_rejects_changed_writer_and_reviewer_artifacts(tmp_path, monkeypatch):
    _request, source, destination, writer, reviewer, registered = _register_reviewed_cache(
        tmp_path, monkeypatch)
    writer_result = source / "agents" / writer / "result.json"
    original = json.loads(writer_result.read_text(encoding="utf-8"))
    original["grammar"][0]["explanation_en"] = "Changed after review."
    writer_result.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(research.AnnotationResearchError):
        research.verify_research_evidence(destination, registered["evidence"],
            **_reviewed_cache_request())

    writer_result.write_text(json.dumps({"words": [], "grammar": [{
        "id": "DRAFT-grammar-x", "pattern": "-x",
        "explanation_en": "A reviewed lesson."}]}), encoding="utf-8")
    meta = source / "agents" / reviewer / "meta.json"
    value = json.loads(meta.read_text(encoding="utf-8"))
    value["effort"] = "medium"
    meta.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(research.AnnotationResearchError):
        research.verify_research_evidence(destination, registered["evidence"],
            **_reviewed_cache_request())


def test_reviewed_lesson_cache_rejects_bad_tap_and_unsafe_source_run(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_repository_root", lambda: tmp_path)
    _allow_initial_replay(monkeypatch)
    request = _reviewed_cache_request()
    source = tmp_path / "source-run"
    destination = tmp_path / "destination-run"
    source.mkdir()
    destination.mkdir()
    writer, reviewer, _ = _dictionary_pair(source,
        writer_context=_reviewed_cache_context(), reviewer_context=_reviewed_cache_context())
    wrong = _reviewed_cache_context()
    wrong["target_occurrence"]["surface"] = "y"
    with pytest.raises(research.AnnotationResearchError, match="tap"):
        research.register_reviewed_lesson_cache(destination,
            source_run_dir=source, writer_job=writer, reviewer_job=reviewer,
            lesson_id="DRAFT-grammar-x", expected_context=wrong, **request)
    wrong_pointer = _reviewed_cache_context()
    wrong_pointer["target_occurrence"]["target_path"] = "/segments/0/meaning_es"
    with pytest.raises(research.AnnotationResearchError, match="current uncertain candidate field"):
        research.register_reviewed_lesson_cache(destination,
            source_run_dir=source, writer_job=writer, reviewer_job=reviewer,
            lesson_id="DRAFT-grammar-x", expected_context=wrong_pointer, **request)
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(research.AnnotationResearchError, match="symlink"):
        research.register_reviewed_lesson_cache(destination,
            source_run_dir=alias, writer_job=writer, reviewer_job=reviewer,
            lesson_id="DRAFT-grammar-x", expected_context=_reviewed_cache_context(), **request)
    outside = tmp_path.parent / "outside-review-source"
    outside.mkdir()
    with pytest.raises(research.AnnotationResearchError, match="inside this repository"):
        research.register_reviewed_lesson_cache(destination,
            source_run_dir=outside, writer_job=writer, reviewer_job=reviewer,
            lesson_id="DRAFT-grammar-x", expected_context=_reviewed_cache_context(), **request)


def test_reviewed_lesson_reference_is_scoped_only_to_its_target_issue(tmp_path, monkeypatch):
    _request, _source, _destination, _writer, _reviewer, registered = _register_reviewed_cache(
        tmp_path, monkeypatch, extra_issue=True)
    reference = next(iter(registered["references"].values()))
    assert reference["issue_ids"] == [FINDING_A]
    assert _request["initial_adjudication"]["classifications"][1]["issue_id"] not in reference["content"]["issue_ids"]


def _register_reviewed_source(tmp_path, monkeypatch, language="ko"):
    monkeypatch.setattr(research, "_repository_root", lambda: tmp_path)
    _allow_initial_replay(monkeypatch)
    request = _reviewed_cache_request(language)
    source = tmp_path / "source-run"
    destination = tmp_path / "destination-run"
    source.mkdir()
    destination.mkdir()
    writer, reviewer, _ = _dictionary_pair(source,
        writer_context=_reviewed_cache_context(), reviewer_context=_reviewed_cache_context())
    descriptor = research.register_reviewed_lesson_source(destination,
        source_run_dir=source, writer_job=writer, reviewer_job=reviewer,
        lesson_id="DRAFT-grammar-x", expected_context=_reviewed_cache_context())
    return request, source, destination, writer, reviewer, descriptor


@pytest.mark.parametrize("language", ["ko", "ja", "zh"])
def test_registered_lesson_source_automatically_builds_scoped_cache_without_paid_calls(
        tmp_path, monkeypatch, language):
    request, _source, destination, _writer, _reviewer, _descriptor = _register_reviewed_source(
        tmp_path, monkeypatch, language)
    runner = FakeRunner(destination)
    result = asyncio.run(research.research_uncertain_review(runner, destination, **request))
    assert result["status"] == "reviewed_lesson_reused"
    assert not runner.calls
    assert next(iter(result["references"].values()))["issue_ids"] == [FINDING_A]
    replay = research.verify_research_evidence(destination, result["evidence"], **request)
    assert replay == result
    again = asyncio.run(research.research_uncertain_review(runner, destination, **request))
    assert again == result
    assert not runner.calls


def test_registered_lesson_source_ignores_nonmatching_case_and_keeps_normal_research_route(
        tmp_path, monkeypatch):
    _request, _source, destination, _writer, _reviewer, _descriptor = _register_reviewed_source(
        tmp_path, monkeypatch)
    request = _reviewed_cache_request()
    request["candidate"]["segments"][0]["meaning_es"] = "otro"
    request["deterministic_gate_evidence"]["candidate_digest"] = research._digest(request["candidate"])
    request["initial_adjudication"]["classifications"][0]["candidate_paths"] = [
        "/segments/0/meaning_es"]
    runner = FakeRunner(destination)
    result = asyncio.run(research.research_uncertain_review(runner, destination, **request))
    assert result["status"] == "approved"
    assert len(runner.calls) == 2


@pytest.mark.parametrize("variation", ["different_source", "different_offset", "different_surface"])
def test_same_pointer_other_chunk_does_not_claim_registered_lesson(
        tmp_path, monkeypatch, variation):
    _request, _source, destination, _writer, _reviewer, _descriptor = _register_reviewed_source(
        tmp_path, monkeypatch)
    request = _reviewed_cache_request()
    if variation == "different_source":
        request["candidate"]["segments"][0]["text"] = "y"
        request["source_text"] = "y"
        request["context"] = {"chunk_review_context": {
            "chapter_text": "a y b", "source_start": 2}, "chunk_text": "y"}
    elif variation == "different_offset":
        request["context"] = {"chunk_review_context": {
            "chapter_text": "a x b x", "source_start": 5}, "chunk_text": "x"}
    else:
        request["candidate"]["segments"][0]["text"] = "y"
        request["source_text"] = "y"
        request["context"] = {"chunk_review_context": {
            "chapter_text": "a y b", "source_start": 2}, "chunk_text": "y"}
    request["deterministic_gate_evidence"]["candidate_digest"] = research._digest(request["candidate"])
    request["deterministic_gate_evidence"]["source_text_digest"] = research._digest(request["source_text"])
    runner = FakeRunner(destination)
    result = asyncio.run(research.research_uncertain_review(runner, destination, **request))
    assert result["status"] == "approved"
    assert len(runner.calls) == 2


def test_matching_registered_lesson_source_tamper_fails_before_paid_research(
        tmp_path, monkeypatch):
    _request, source, destination, _writer, reviewer, _descriptor = _register_reviewed_source(
        tmp_path, monkeypatch)
    meta_path = source / "agents" / reviewer / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["effort"] = "medium"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    runner = FakeRunner(destination)
    with pytest.raises(research.AnnotationResearchError, match="source jobs no longer verify"):
        asyncio.run(research.research_uncertain_review(
            runner, destination, **_reviewed_cache_request()))
    assert runner.calls == []


def test_primary_capture_rejects_unapproved_origin_and_search_pages(tmp_path, monkeypatch):
    request = _capturable_request()
    fetches = []
    monkeypatch.setattr(research, "_fetch_primary_page", lambda *args: fetches.append(args))
    unresolved = [{"issue_id": FINDING_A}]
    with pytest.raises(research.AnnotationResearchError, match="direct page"):
        research._validated_source_requests(
            {"source_requests": [{"url": "https://other.example/page",
                "issue_ids": [FINDING_A], "reason": "not supplied"}]},
            [FINDING_A], request["known_reference_input"], unresolved)
    assert fetches == []

    accepted = research._validated_source_requests(
        {"source_requests": [{"url": "https://dictionary.example/eng/dicSearch/SearchView?ParaWordNo=86133",
            "issue_ids": [FINDING_A], "reason": "specific entry"}]},
        [FINDING_A], request["known_reference_input"], unresolved)
    assert len(accepted) == 1

    with pytest.raises(research.AnnotationResearchError, match="direct page"):
        research._validated_source_requests(
            {"source_requests": [{"url": "https://dictionary.example/search?word=x",
                "issue_ids": [FINDING_A], "reason": "search"}]},
            [FINDING_A], request["known_reference_input"],
            [{"issue_id": FINDING_A}])


def test_pdf_capture_fails_closed_and_is_replayable_as_a_gap(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _capturable_request()
    def pdf_rejected(*_args):
        raise research.AnnotationResearchError("PDF capture is unsupported; the source remains an explicit evidence gap")
    monkeypatch.setattr(research, "_fetch_primary_page", pdf_rejected)
    runner = CaptureRunner(tmp_path)
    result = asyncio.run(research.research_uncertain_review(runner, tmp_path, **request))
    assert result["status"] == "unresolved"
    assert not result["references"]
    assert "PDF capture is unsupported" in result["evidence"]["capture_failures"][0]["gap"]
    assert research.verify_research_evidence(tmp_path, result["evidence"], **request) == result


def test_legacy_research_v1_receipts_are_not_misread_as_capture_proofs(tmp_path, monkeypatch):
    _allow_initial_replay(monkeypatch)
    request = _request_inputs()
    result = asyncio.run(research.research_uncertain_review(FakeRunner(tmp_path), tmp_path, **request))
    legacy = dict(result["evidence"])
    legacy["version"] = 1
    with pytest.raises(research.AnnotationResearchError, match="Unsupported or missing research evidence version"):
        research.verify_research_evidence(tmp_path, legacy, **request)
