import copy,json
from argparse import Namespace
import pytest
from pipeline.agent_harness import ChapterHarness,CodexRunner
from pipeline.japanese_agent_harness import JapaneseChapterHarness
@pytest.mark.parametrize("origin",["semantic","foreign_parent","generation","foreign_source","foreign_marker"])
@pytest.mark.asyncio
async def test_zh_actual_callback_normalized_semantic_parent(tmp_path, monkeypatch, origin):
    route_mode = "clear"
    from pipeline import annotation_repairs, annotation_adjudication

    candidate = {"segments": [{"text": "猫", "type": "word", "pinyin": "māo",
                               "meaning_en": "cat"}], "grammar_overlays": [{"start":0,"end":1,"text":"猫","grammar_candidate_key":"old-grammar","pattern":"猫","meaning_en":"cat"}]}
    issue = {"problem": "meaning", "segment_index": 0, "explanation": "Check this gloss."}

    repaired_candidates = {}
    route_candidates = []

    class Harness(ChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            return copy.deepcopy(next(reversed(repaired_candidates.values()))) if repaired_candidates else copy.deepcopy(candidate)

        async def review_annotation(self, index, chunk, annotation, stage):
            from pipeline.annotation_publication import bind_review_job
            job = f"annotations/chunk_{index:04d}/{stage}_review"
            root = tmp_path / "agents" / job
            root.mkdir(parents=True, exist_ok=True)
            raw_review = {"verdict": "revise", "issues": [issue]}
            (root / "meta.json").write_text(json.dumps({
                "fingerprint": "review-input", "return_code": 0,
                "model": "gpt-6-luna", "effort": "low", "tool_profile": "research"}))
            (root / "result.json").write_text(json.dumps(raw_review))
            bind_review_job(tmp_path, job, candidate=annotation, source_text=chunk)
            return {**raw_review, "offset_audit": {
                "valid": 0, "legacy_missing": 1, "invalid": 0}}

        def annotation_reconstructs(self, chunk, annotation):
            return True

        def annotation_contract_issues(self, chunk, annotation):
            return []

        def prepare_annotation_candidate(self, chunk, annotation):
            return annotation

    async def fake_repair(harness, job, base, issues, **kwargs):
        root = tmp_path / "agents" / job
        root.mkdir(parents=True, exist_ok=True)
        (root / "meta.json").write_text(json.dumps({"kind": ("generation" if origin=="generation" else "annotation_patch_assembly"),
            "status": "applied", "representation": "chinese-annotation", "return_code": 0}))
        derived = copy.deepcopy(base)
        if route_mode != "unchanged":
            derived["segments"][0]["meaning_en"] += " (repair)"
        repaired_candidates[job] = derived
        return {"status": "applied", "candidate": derived,
                "evidence": {"assembly_job": job}}

    def fake_replay(run_dir, job, *, validate_candidate):
        derived = repaired_candidates[job]
        validate_candidate(derived)
        return {"status": "applied", "candidate": derived}

    async def fake_adjudicate(*args, **kwargs):
        marker=kwargs["context"]["verified_semantic_derivation"]
        assert marker["candidate_digest"] == annotation_adjudication.digest(kwargs["candidate"])
        assert marker["semantic_assembly_job"] in repaired_candidates
        assert kwargs["current_review"]["issues"] == [issue]
        assert kwargs["prior_history"]

        assert kwargs["current_review"]["issues"] == [issue]
        assert kwargs["deterministic_gate_evidence"]["passed"] is True
        route_candidates.append(copy.deepcopy(kwargs["candidate"]))
        if route_mode in ("unchanged", "exhausted") or (route_mode in ("derived", "tail") and len(route_candidates) == 1):
            return {"status": "actionable", "approved": False,
                    "repair_diagnoses": [{"issue_id": "genuine", "diagnosis": "Correct the gloss.",
                        "paths": ["/segments/0/meaning_en"]}]}
        return {"status": "cleared", "approved": True, "job": "fixture-adjudication"}

    def fake_verify(run_dir, evidence, **kwargs):
        verified = {**evidence, "status": "cleared", "approved": True,
            "effective_review_kind": "adjudicated",
            "candidate_digest": annotation_adjudication.digest(kwargs["candidate"]),
            "model": "gpt-6-luna", "effort": "low", "tool_profile": "research",
            "worker_tool_profile": "workspace", "verified_result_digest": "f" * 64,
            "normal_review_receipt": kwargs["normal_review_receipt"],
            "review_digest": annotation_adjudication.digest(kwargs["current_review"])}
        for key in ("history_digest", "context_digest", "references_digest",
                    "gate_digest", "input_digest", "instructions_digest",
                    "normal_review_receipt_digest"):
            verified[key] = "a" * 64
        verified["preserved_rejected_review_digest"] = verified["review_digest"]
        verified["normal_review_receipt_digest"] = annotation_adjudication.digest(
            kwargs["normal_review_receipt"])
        return verified

    from pipeline import annotation_semantic_derivation as derivation
    from pipeline import annotation_run_knowledge_selection as selection
    normalized={}
    async def select(harness,index,value,source_text,**kwargs):
        if not repaired_candidates:return {"candidate":value}
        result=copy.deepcopy(value)
        result["grammar_overlays"][0]["grammar_candidate_key"]="approved-normalized"
        job="normalization-current"
        parent=next(reversed(repaired_candidates))
        if origin=="foreign_parent": parent="foreign"
        meta={"inputs":{"language":"zh","representation":"chinese-annotation","source_text":source_text,"context":{},"candidate":copy.deepcopy(value)},"base_record":{"job":parent},"edits":[{"path":"/grammar_overlays/0/grammar_candidate_key","new":"approved-normalized"}]}
        descriptor={"job":job,"meta_digest":annotation_adjudication.digest(meta),"result_digest":annotation_adjudication.digest(result)}
        if origin=="foreign_source":meta["inputs"]["source_text"]="別"
        if origin=="foreign_marker":descriptor["result_digest"]="0"*64
        harness._annotation_run_normalizations={index:descriptor}
        normalized[job]=(result,meta)
        return {"candidate":result}
    monkeypatch.setattr(selection,"select_and_normalize_run_knowledge",select)
    monkeypatch.setattr(selection,"replay_normalization",lambda root,job: normalized[job])
    monkeypatch.setattr(derivation,"replay_annotation_repair",fake_replay)

    monkeypatch.setattr(annotation_repairs, "repair_annotation", fake_repair)
    monkeypatch.setattr(annotation_repairs, "replay_annotation_repair", fake_replay)
    monkeypatch.setattr(annotation_adjudication, "adjudicate_annotation_review", fake_adjudicate)
    monkeypatch.setattr(annotation_adjudication, "verify_adjudication_evidence", fake_verify)
    harness = object.__new__(Harness)
    harness.args = Namespace(max_annotation_repairs=(1 if route_mode in ("clear", "tail") else 5), refresh=False,
        annotation_repair_effort="low", annotation_final_effort="low", annotation_effort="low", annotation_review_effort="low",
        annotation_chunk=350, annotation_chunk_maximum=400)
    harness.run_dir = tmp_path
    harness.runner = object()
    (tmp_path / "manifest.json").write_text(json.dumps({
        "annotation_chunk_target": 350, "annotation_chunk_maximum": 400}))
    (tmp_path / "focus-vocabulary-plan.json").write_text(json.dumps({
        "names": [], "story_terms": []}))
    monkeypatch.setattr(CodexRunner, "_check_tool_profile", staticmethod(lambda *_args: None))
    if route_mode in ("unchanged", "exhausted"):
        with pytest.raises(ValueError):
            await harness.annotate_chunk(0, "猫")
        assert len(route_candidates) == (1 if route_mode == "unchanged" else 3)
        assert len({json.dumps(row, sort_keys=True) for row in route_candidates}) == len(route_candidates)
        return
    if origin in ("foreign_parent", "generation", "foreign_source", "foreign_marker"):
        with pytest.raises(ValueError):
            await harness.annotate_chunk(0, "猫")
        assert route_candidates == []
        return
    result = await harness.annotate_chunk(0, "猫")
    assert len(route_candidates) == (1 if route_mode == "clear" else 2)
    if route_mode != "clear":
        assert route_candidates[0] != route_candidates[1]
        assert result["attempts"][-1]["review"]["verdict"] == "revise"
        assert any(row.get("adjudication", {}).get("status") == "actionable" for row in result["attempts"])
    assert result["effective_review"]["kind"] == "adjudicated"
    assert result["attempts"][-1]["review"] == {
        "verdict": "revise", "issues": [issue],
        "offset_audit": {"valid": 0, "legacy_missing": 1, "invalid": 0},
    }
    assert result["attempts"][-1]["effective_review"]["evidence"]["approved"] is True
    assert result["acceptance_state"] == "reviewed_and_adjudicated"


@pytest.mark.parametrize("origin",["semantic","foreign_parent","generation","foreign_source","foreign_marker","tail"])
@pytest.mark.asyncio
async def test_ja_actual_callback_normalized_semantic_parent(tmp_path, monkeypatch, origin):
    route_mode = "tail" if origin=="tail" else "clear"
    from pipeline import annotation_repairs, annotation_adjudication

    candidate = {"segments": [{"surface": "猫", "type": "word", "lemma": "猫",
        "surface_kana": "ねこ", "lemma_kana": "ねこ", "part_of_speech": "noun",
        "conjugation_form": "non-inflecting", "meaning_en": "cat",
        "grammar_candidate_key": "", "dictionary_key": "",
        "dictionary_definition_en": "", "form_steps": [], "story_role": "none",
        "story_importance_en": ""}], "grammar_overlays": []}
    issue = {"segment_text": "猫", "problem": "meaning",
             "explanation": "Check the contextual gloss.", "suggested_fix": "Review it."}

    repaired_candidates = {}
    route_candidates = []

    class Harness(JapaneseChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            if origin=="tail" and normalized:
                return copy.deepcopy(next(reversed(normalized.values()))[0])
            return copy.deepcopy(next(reversed(repaired_candidates.values()))) if repaired_candidates else copy.deepcopy(candidate)

        async def review_annotation(self, index, chunk, annotation, stage):
            receipts = getattr(self, "_annotation_review_receipts", None)
            if receipts is None:
                receipts = self._annotation_review_receipts = {}
            receipts[(index, stage)] = [
                {"job": "fixture/general", "input_digest": "input-1", "result_digest": "result-1"},
                {"job": "fixture/boundary", "input_digest": "input-2", "result_digest": "result-2"},
            ]
            if route_mode == "tail_pass" and stage == "fresh_repair_01":
                return {"verdict": "pass", "issues": []}
            return {"verdict": "revise", "issues": [issue]}

        def annotation_surfaces_reconstruct(self, chunk, annotation):
            return True

        def annotation_reconstructs(self, chunk, annotation):
            return True

        def annotation_contract_issues(self, chunk, annotation):
            return []

        def prepare_planned_annotation(self, chunk, annotation):
            return annotation

    async def fake_repair(harness, job, base, issues, **kwargs):
        root = tmp_path / "agents" / job
        root.mkdir(parents=True, exist_ok=True)
        (root / "meta.json").write_text(json.dumps({"kind": ("generation" if origin=="generation" else "annotation_patch_assembly"),
            "status": "applied", "representation": "japanese-annotation", "return_code": 0}))
        derived = copy.deepcopy(base)
        if route_mode != "unchanged":
            derived["segments"][0]["meaning_en"] += " (repair)"
        repaired_candidates[job] = derived
        return {"status": "applied", "candidate": derived,
                "evidence": {"assembly_job": job}}

    def fake_replay(run_dir, job, *, validate_candidate):
        derived = repaired_candidates[job]
        validate_candidate(derived)
        return {"status": "applied", "candidate": derived}

    async def fake_adjudicate(*args, **kwargs):
        marker=kwargs["context"].get("verified_semantic_derivation")
        if origin=="tail" and route_candidates:
            assert marker is None
        else:
            assert marker["candidate_digest"] == annotation_adjudication.digest(kwargs["candidate"])
            assert marker["semantic_assembly_job"] in repaired_candidates
        assert kwargs["current_review"]["issues"] == [issue]
        assert kwargs["prior_history"]

        assert kwargs["current_review"]["issues"] == [issue]
        assert len(kwargs["normal_review_receipt"]["components"]) == 2
        route_candidates.append(copy.deepcopy(kwargs["candidate"]))
        if route_mode in ("unchanged", "exhausted") or (route_mode in ("derived", "tail", "tail_pass") and len(route_candidates) == 1):
            return {"status": "actionable", "approved": False,
                    "repair_diagnoses": [{"issue_id": "genuine", "diagnosis": "Correct the gloss.",
                        "paths": ["/segments/0/meaning_en"]}]}
        return {"status": "cleared", "approved": True, "job": "fixture-adjudication"}

    from pipeline import annotation_semantic_derivation as derivation
    from pipeline import annotation_run_knowledge_selection as selection
    normalized={}
    async def select(harness,index,value,source_text,**kwargs):
        if not repaired_candidates:return {"candidate":value}
        result=copy.deepcopy(value)
        result["segments"][0]["grammar_candidate_key"]="approved-normalized"
        job="normalization-current"
        parent=next(reversed(repaired_candidates))
        if origin=="foreign_parent": parent="foreign"
        meta={"inputs":{"language":"ja","representation":"japanese-annotation","source_text":source_text,"context":{},"candidate":copy.deepcopy(value)},"base_record":{"job":parent},"edits":[{"path":"/segments/0/grammar_candidate_key","new":"approved-normalized"}]}
        descriptor={"job":job,"meta_digest":annotation_adjudication.digest(meta),"result_digest":annotation_adjudication.digest(result)}
        if origin=="foreign_source":meta["inputs"]["source_text"]="別"
        if origin=="foreign_marker":descriptor["result_digest"]="0"*64
        harness._annotation_run_normalizations={index:descriptor}
        normalized[job]=(result,meta)
        return {"candidate":result}
    monkeypatch.setattr(selection,"select_and_normalize_run_knowledge",select)
    monkeypatch.setattr(selection,"replay_normalization",lambda root,job: normalized[job])
    monkeypatch.setattr(derivation,"replay_annotation_repair",fake_replay)

    monkeypatch.setattr(annotation_repairs, "repair_annotation", fake_repair)
    monkeypatch.setattr(annotation_repairs, "replay_annotation_repair", fake_replay)
    monkeypatch.setattr(annotation_adjudication, "adjudicate_annotation_review", fake_adjudicate)
    harness = object.__new__(Harness)
    harness.args = Namespace(level="n5", annotation_chunk=None, annotation_chunk_maximum=None,
        max_annotation_repairs=(1 if route_mode in ("clear", "tail", "tail_pass") else 5), max_annotation_fresh_repairs=(1 if route_mode in ("tail", "tail_pass") else 0),
        max_annotation_adjudications=0, annotation_repair_effort="low",
        annotation_final_effort="low", annotation_review_effort="low",
        refresh=False, annotation_chunk_indices=None, no_grammar_overlays=False)
    harness.run_dir = tmp_path
    harness.runner = object()
    harness.story_vocabulary_plan = {"terms": []}
    if route_mode in ("unchanged", "exhausted"):
        with pytest.raises(ValueError):
            await harness.annotate_chunk(0, "猫")
        assert len(route_candidates) == (1 if route_mode == "unchanged" else 3)
        assert len({json.dumps(row, sort_keys=True) for row in route_candidates}) == len(route_candidates)
        return
    if origin in ("foreign_parent", "generation", "foreign_source", "foreign_marker"):
        with pytest.raises(ValueError):
            await harness.annotate_chunk(0, "猫")
        assert route_candidates == []
        return
    result = await harness.annotate_chunk(0, "猫")
    if route_mode == "tail_pass":
        assert len(route_candidates) == 1  # The earlier actionable route consumed a slot.
        assert result["resolved"] is True
        final = result["attempts"][-1]
        assert final["stage"] == "fresh_repair_01"
        assert final["review"] == {"verdict": "pass", "issues": []}
        assert final["annotation"] != route_candidates[0]
        assert final["semantic_repair"]["status"] == "applied"
        assert "adjudication" not in final
        assert any(row.get("adjudication", {}).get("status") == "actionable" for row in result["attempts"])
        stored = json.loads((tmp_path / "accepted-annotations" / "chunk_0000.json").read_text())
        assert stored["attempts"][-1]["review"]["verdict"] == "pass"
        return
    assert len(route_candidates) == (1 if route_mode == "clear" else 2)
    if route_mode != "clear":
        assert route_candidates[0] != route_candidates[1]
        assert result["attempts"][-1]["review"]["verdict"] == "revise"
        assert any(row.get("adjudication", {}).get("status") == "actionable" for row in result["attempts"])
    assert result["effective_review"]["kind"] == "adjudicated"
    assert result["attempts"][-1]["review"]["verdict"] == "revise"
    stored = json.loads((tmp_path / "accepted-annotations" / "chunk_0000.json").read_text())
    replay = stored["attempts"][-1]["adjudication_replay"]
    assert replay["prior_history"] == stored["attempts"][:-1]
