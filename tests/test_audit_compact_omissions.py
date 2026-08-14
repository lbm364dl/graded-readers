import json
from argparse import Namespace
from pathlib import Path

import pytest

from pipeline.audit_compact_omissions import (
    OmissionAuditor, assemble_chapter, authoritative_scene_paths,
    discover_risky_scenes, expand_run_dirs,
    enforce_deterministic_material_findings,
    parser, promote_passed_candidate, validate_classification,
    validate_finding_classification, resolve_manifest_source,
)
from pipeline.agent_harness import digest


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_run(tmp_path: Path, *, decision=True, omissions=None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "chapter_01.txt"
    source.write_text("刘备先遇见关羽。三人后来结义。", encoding="utf-8")
    run = tmp_path / "run"
    write_json(run / "manifest.json", {
        "source": str(source), "source_sha256": digest(source.read_text()),
        "level": "hsk4", "target_chars": 20, "status": "complete",
    })
    write_json(run / "report.json", {
        "status": "complete", "chapter_cjk": 7, "scenes": 1,
        "scene_verdicts": {"scene_01": "pass"},
    })
    write_json(run / "outline.json", {"scenes": [{"id": "scene_01"}]})
    review = {
        "verdict": "pass", "omissions": omissions or [],
        "harness_decision": "accepted_omission_only_compact_adaptation" if decision else None,
    }
    scene = {
        "scene": {"id": "scene_01", "source_start": 0, "source_end": len(source.read_text())},
        "text": "刘备遇见关羽。", "review": review,
    }
    write_json(run / "scenes" / "scene_01.json", scene)
    (run / "chapter.txt").write_text("刘备遇见关羽。\n", encoding="utf-8")
    return run


def args(tmp_path: Path) -> Namespace:
    return Namespace(
        output_dir=str(tmp_path / "audit"), model="fake", concurrency=2,
        timeout=10, classify_effort="high", repair_effort="xhigh",
        review_effort="high", refresh=False, promote_passed=False,
    )


def test_discovers_every_passed_nonempty_omission(tmp_path):
    risky = make_run(tmp_path, omissions=["三人结义被省略"])
    assert [item.scene_id for item in discover_risky_scenes([risky])] == ["scene_01"]

    empty_root = tmp_path / "other"
    empty_root.mkdir()
    safe = make_run(empty_root, decision=False, omissions=["省略"])
    assert [item.scene_id for item in discover_risky_scenes([safe])] == ["scene_01"]


def test_resolves_relocated_manifest_source(monkeypatch, tmp_path):
    source = tmp_path / "books" / "chinese" / "sanguoyanyi" / "chapter_001.txt"
    source.parent.mkdir(parents=True)
    source.write_text("刘备", encoding="utf-8")
    monkeypatch.setattr("pipeline.audit_compact_omissions.ROOT", tmp_path)
    manifest = {
        "source": "/old/checkout/books/chinese/sanguoyanyi/chapter_001.txt",
        "source_sha256": digest("刘备"),
    }
    assert resolve_manifest_source(manifest) == source


def test_does_not_discover_rejected_or_empty_omission_reviews(tmp_path):
    rejected = make_run(tmp_path / "rejected", decision=False, omissions=["省略"])
    scene_path = rejected / "scenes" / "scene_01.json"
    scene = json.loads(scene_path.read_text())
    scene["review"]["verdict"] = "revise"
    write_json(scene_path, scene)
    empty = make_run(tmp_path / "empty", omissions=[])
    assert discover_risky_scenes([rejected, empty]) == []


def test_discovery_requires_complete_accepted_run(tmp_path):
    run = make_run(tmp_path, omissions=["省略"])
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "running"
    write_json(manifest_path, manifest)
    assert discover_risky_scenes([run]) == []


def test_batch_root_discovers_nested_chapter_runs(tmp_path):
    run = make_run(tmp_path / "batch" / "chapter", omissions=["省略"])
    assert expand_run_dirs([tmp_path / "batch"]) == [run.resolve()]


def test_current_outline_ignores_stale_scene_and_assembles_exact_chapter(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])
    write_json(run / "scenes" / "scene_02.json", {
        "scene": {"id": "scene_02", "source_start": 0, "source_end": 2},
        "text": "陈旧内容", "review": {"verdict": "pass", "omissions": ["陈旧遗漏"]},
    })
    paths, evidence = authoritative_scene_paths(run)
    assert [path.name for path in paths] == ["scene_01.json"]
    assert evidence == {"authoritative_scene_count": 1, "ignored_stale_scene_count": 1}
    assert [item.scene_id for item in discover_risky_scenes([run])] == ["scene_01"]
    assert assemble_chapter(run) == (run / "chapter.txt").read_text()


def test_missing_authoritative_scene_fails_closed(tmp_path):
    run = make_run(tmp_path, omissions=["省略"])
    write_json(run / "outline.json", {"scenes": [{"id": "scene_02"}]})
    with pytest.raises(ValueError, match="authoritative scene file missing"):
        authoritative_scene_paths(run)


@pytest.mark.parametrize("ids, match", [
    (["scene_01", "scene_01"], "duplicate authoritative"),
    (["../scene_01"], "unsafe authoritative"),
])
def test_duplicate_or_unsafe_authoritative_scene_ids_fail(tmp_path, ids, match):
    run = make_run(tmp_path, omissions=["省略"])
    write_json(run / "outline.json", {"scenes": [{"id": item} for item in ids]})
    report = json.loads((run / "report.json").read_text())
    report["scenes"] = len(ids)
    write_json(run / "report.json", report)
    with pytest.raises(ValueError, match=match):
        authoritative_scene_paths(run)


def test_classification_must_reproduce_notes_exactly(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])
    scene = discover_risky_scenes([run])[0]
    with pytest.raises(ValueError, match="reproduce omission notes"):
        validate_classification(scene, {"assessments": [{
            "omission_note": "different", "classification": "material",
            "reason": "plot", "required_fix": "restore",
        }]})


def test_classification_normalizes_close_traditional_label_only(tmp_path):
    authoritative = "略去夏侯惇、夏侯渊等人支援的细节。"
    run = make_run(tmp_path, omissions=[authoritative])
    scene = discover_risky_scenes([run])[0]
    result = {"assessments": [{
        "omission_note": "略去夏侯惇，夏侯淵等人支援的细节",
        "classification": "material", "reason": "changes participants",
        "required_fix": "restore the supporting characters",
    }]}
    validate_classification(scene, result)
    assessment = result["assessments"][0]
    assert assessment["omission_note"] == authoritative
    assert assessment["classification"] == "material"
    assert assessment["reason"] == "changes participants"
    assert assessment["required_fix"] == "restore the supporting characters"
    assert result["label_normalization_evidence"][0]["similarity"] >= 0.86


@pytest.mark.parametrize("returned", [
    ["第二项事件被省略", "第一项事件被省略"],
    ["完全不同的内容", "第二项事件被省略"],
])
def test_classification_rejects_reordered_or_dissimilar_labels(tmp_path, returned):
    authoritative = ["第一项事件被省略", "第二项事件被省略"]
    run = make_run(tmp_path, omissions=authoritative)
    scene = discover_risky_scenes([run])[0]
    result = {"assessments": [{
        "omission_note": note, "classification": "material",
        "reason": "plot", "required_fix": "restore",
    } for note in returned]}
    with pytest.raises(ValueError, match="reproduce omission notes"):
        validate_classification(scene, result)


def test_classification_requires_fix_only_for_material_omissions(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])
    scene = discover_risky_scenes([run])[0]
    common = {"omission_note": scene.omissions[0], "reason": "plot"}
    with pytest.raises(ValueError, match="concrete required_fix"):
        validate_classification(scene, {"assessments": [{
            **common, "classification": "material", "required_fix": "",
        }]})
    with pytest.raises(ValueError, match="must have empty required_fix"):
        validate_classification(scene, {"assessments": [{
            **common, "classification": "permissible_compression",
            "required_fix": "restore it",
        }]})


def test_critical_finding_classification_is_exact_and_fail_closed(tmp_path):
    run = make_run(tmp_path, omissions=[])
    scene_path = run / "scenes" / "scene_01.json"
    value = json.loads(scene_path.read_text())
    value["review"].update({
        "unsupported_additions": [],
        "distortions": ["将朱雋写成朱俊"],
        "language_problems": [],
    })
    write_json(scene_path, value)
    scene = discover_risky_scenes([run])[0]
    with pytest.raises(ValueError, match="reproduce critical findings"):
        validate_finding_classification(scene, {"assessments": [{
            "field": "distortions", "finding_note": "different",
            "classification": "material", "reason": "name",
            "required_fix": "restore 朱雋",
        }]})
    with pytest.raises(ValueError, match="concrete required_fix"):
        validate_finding_classification(scene, {"assessments": [{
            "field": "distortions", "finding_note": "将朱雋写成朱俊",
            "classification": "material", "reason": "name", "required_fix": "",
        }]})


def test_critical_finding_normalizes_close_label_but_preserves_judgment(tmp_path):
    run = make_run(tmp_path, omissions=[])
    scene_path = run / "scenes" / "scene_01.json"
    value = json.loads(scene_path.read_text())
    authoritative = "略去夏侯惇、夏侯渊等人支援的细节。"
    value["review"].update({
        "unsupported_additions": [], "distortions": [authoritative],
        "language_problems": [],
    })
    write_json(scene_path, value)
    scene = discover_risky_scenes([run])[0]
    result = {"assessments": [{
        "field": "distortions", "finding_note": "略去夏侯惇、夏侯淵等人支援的细节",
        "classification": "minor_or_negated", "reason": "minor compression",
        "required_fix": "",
    }]}
    validate_finding_classification(scene, result)
    assessment = result["assessments"][0]
    assert assessment["finding_note"] == authoritative
    assert assessment["classification"] == "minor_or_negated"
    assert assessment["reason"] == "minor compression"


@pytest.mark.parametrize("note", [
    "将‘朱雋’写作‘朱俊’，人物姓名有误。",
    "将破张宝妖法的行动概括为刘备个人所为，略有归因偏差。",
    "鲍忠出战的行动主体有误。",
    "将程普刺杀胡轸的功劳归给了孙坚。",
])
def test_explicit_wrong_name_or_actor_cannot_be_downgraded(note):
    result = {"assessments": [{
        "field": "distortions", "finding_note": note,
        "classification": "minor_or_negated", "reason": "slight imprecision",
        "required_fix": "",
    }]}
    enforce_deterministic_material_findings(result)
    assessment = result["assessments"][0]
    assert assessment["classification"] == "material"
    assert assessment["required_fix"]


def test_harmless_compression_is_not_forced_material():
    result = {"assessments": [{
        "field": "distortions", "finding_note": "省略了次要地名，情节不受影响。",
        "classification": "minor_or_negated", "reason": "compression",
        "required_fix": "",
    }]}
    enforce_deterministic_material_findings(result)
    assert result["assessments"][0]["classification"] == "minor_or_negated"


class FakeRunner:
    def __init__(self, classification):
        self.classification = classification
        self.jobs = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.jobs.append(job)
        if job.endswith("/classify"):
            return self.classification
        if job.endswith("/chapter_repair"):
            return {"text": "刘备先遇见了关羽。刘备、关羽和张飞三人后来结义。"}
        return {
            "source_fidelity": 9, "naturalness": 9, "readability": 9,
            "material_omissions": [], "unsupported_additions": [],
            "distortions": [], "language_problems": [], "verdict": "pass",
        }


@pytest.mark.asyncio
async def test_classifier_receives_exact_source_adaptation_review_and_notes(tmp_path):
    run = make_run(tmp_path, omissions=["三人后来结义被省略"])
    scene = discover_risky_scenes([run])[0]

    class Capturing:
        async def call(self, job, prompt, *args, **kwargs):
            self.prompt = prompt
            return {"assessments": [{
                "omission_note": scene.omissions[0], "classification": "material",
                "reason": "relationship", "required_fix": "restore the oath",
            }]}

    auditor = OmissionAuditor(args(tmp_path))
    auditor.runner = Capturing()
    await auditor.classify(scene)
    assert f"ORIGINAL SCENE:\n{scene.source}" in auditor.runner.prompt
    assert f"ACCEPTED SCENE:\n{scene.adaptation}" in auditor.runner.prompt
    exact_review = json.dumps(scene.review, ensure_ascii=False, indent=2)
    assert f"ACCEPTED SOURCE REVIEW (EXACT JSON):\n{exact_review}" in auditor.runner.prompt
    assert json.dumps(list(scene.omissions), ensure_ascii=False, indent=2) in auditor.runner.prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(("note", "fix"), [
    ("将朱雋写成朱俊，人物姓名有误", "restore the source name 朱雋"),
    ("魏延救出马谡，但原文是马谡先逃走", "assign the action to the correct actor"),
    ("司马懿击退郭淮，原文并无此事", "remove the invented repelled event"),
    ("东南风起时行动，原文是孔明届时返回", "state that Kongming returns"),
])
async def test_known_factual_causal_findings_enter_reviewed_repair(tmp_path, note, fix):
    run = make_run(tmp_path, omissions=[])
    scene_path = run / "scenes" / "scene_01.json"
    value = json.loads(scene_path.read_text())
    value["review"].update({
        "unsupported_additions": [], "distortions": [note],
        "language_problems": [],
    })
    write_json(scene_path, value)

    class MaterialRunner(FakeRunner):
        async def call(self, job, prompt, schema, effort, **kwargs):
            self.jobs.append(job)
            if job.endswith("/classify_findings"):
                assert "ORIGINAL SCENE:" in prompt and "ACCEPTED SCENE:" in prompt
                assert note in prompt
                return {"assessments": [{
                    "field": "distortions", "finding_note": note,
                    "classification": "material", "reason": "changes source fact",
                    "required_fix": fix,
                }]}
            if job.endswith("/chapter_repair"):
                return {"text": "刘备先遇见了关羽。刘备、关羽和张飞三人后来结义。"}
            return {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "material_omissions": [], "unsupported_additions": [],
                "distortions": [], "language_problems": [], "verdict": "pass",
            }

    auditor = OmissionAuditor(args(tmp_path))
    auditor.runner = MaterialRunner({})
    report = await auditor.run([run])
    assert report["status"] == "complete"
    assert auditor.runner.jobs == [
        "run/scene_01/classify_findings", "run/chapter_repair",
        "run/chapter_repair_review",
    ]


@pytest.mark.asyncio
async def test_negated_or_minor_finding_does_not_repair(tmp_path):
    run = make_run(tmp_path, omissions=[])
    scene_path = run / "scenes" / "scene_01.json"
    value = json.loads(scene_path.read_text())
    note = "无明显无依据的新增情节"
    value["review"].update({"unsupported_additions": [note],
                            "distortions": [], "language_problems": []})
    write_json(scene_path, value)
    scene = discover_risky_scenes([run])[0]

    class MinorRunner:
        def __init__(self): self.jobs = []
        async def call(self, job, prompt, *args, **kwargs):
            self.jobs.append(job)
            return {"assessments": [{
                "field": "unsupported_additions", "finding_note": note,
                "classification": "minor_or_negated", "reason": "explicitly negated",
                "required_fix": "",
            }]}

    auditor = OmissionAuditor(args(tmp_path)); auditor.runner = MinorRunner()
    report = await auditor.run([run])
    assert report["status"] == "complete"
    assert auditor.runner.jobs == ["run/scene_01/classify_findings"]


@pytest.mark.asyncio
async def test_permissible_omission_does_not_trigger_repair(tmp_path):
    run = make_run(tmp_path, omissions=["重复描写被省略"])
    auditor = OmissionAuditor(args(tmp_path))
    auditor.runner = FakeRunner({"assessments": [{
        "omission_note": "重复描写被省略", "classification": "permissible_compression",
        "reason": "repetition only", "required_fix": "",
    }]})
    report = await auditor.run([run])
    assert report["status"] == "complete"
    assert auditor.runner.jobs == ["run/scene_01/classify"]


@pytest.mark.asyncio
async def test_material_omission_repairs_and_reviews_separate_candidate(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])
    auditor = OmissionAuditor(args(tmp_path))
    auditor.runner = FakeRunner({"assessments": [{
        "omission_note": "三人结义被省略", "classification": "material",
        "reason": "changes the relationship", "required_fix": "restore the oath",
    }]})
    report = await auditor.run([run])
    assert report["status"] == "complete"
    assert auditor.runner.jobs == [
        "run/scene_01/classify", "run/chapter_repair", "run/chapter_repair_review",
    ]
    assert (tmp_path / "audit" / "chapters" / "run" / "candidate.txt").exists()
    assert (run / "chapter.txt").read_text() == "刘备遇见关羽。\n"


@pytest.mark.asyncio
async def test_mechanical_in_range_count_discards_stale_length_only_review(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])

    class LengthOnlyRunner(FakeRunner):
        async def call(self, job, prompt, schema, effort, **kwargs):
            if job.endswith("/chapter_repair_review"):
                return {
                    "source_fidelity": 9, "naturalness": 9, "readability": 9,
                    "material_omissions": [], "unsupported_additions": [],
                    "distortions": [],
                    "language_problems": ["篇幅超过旧的字符上限。"],
                    "verdict": "revise",
                }
            return await super().call(job, prompt, schema, effort, **kwargs)

    classification = {"assessments": [{
        "omission_note": "三人结义被省略", "classification": "material",
        "reason": "relationship", "required_fix": "restore the oath",
    }]}
    auditor = OmissionAuditor(args(tmp_path))
    auditor.runner = LengthOnlyRunner(classification)
    report = await auditor.run([run])
    result = report["chapter_results"][0]
    assert report["status"] == "complete"
    assert result["review"]["verdict"] == "pass"
    assert result["review"]["reviewer_verdict"] == "revise"
    assert result["review"]["language_problems"] == []


@pytest.mark.asyncio
async def test_failed_independent_review_triggers_bounded_self_heal(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])

    class SelfHealingRunner:
        def __init__(self):
            self.jobs = []

        async def call(self, job, prompt, schema, effort, **kwargs):
            self.jobs.append(job)
            if job.endswith("/classify"):
                return {"assessments": [{
                    "omission_note": "三人结义被省略", "classification": "material",
                    "reason": "changes relationship", "required_fix": "restore oath",
                }]}
            if job.endswith("/chapter_repair"):
                return {"text": "刘备助关羽前往。三人后来结义。"}
            if job.endswith("/chapter_repair_review"):
                return {
                    "source_fidelity": 8, "naturalness": 7, "readability": 9,
                    "material_omissions": [], "unsupported_additions": [],
                    "distortions": ["刘备助关羽前往，人物关系有误。"],
                    "language_problems": [], "verdict": "revise",
                }
            if job.endswith("/chapter_repair_round_02"):
                assert "刘备助关羽前往，人物关系有误。" in prompt
                return {"text": "刘备先遇见关羽。刘备、关羽和张飞三人后来结义。"}
            assert job.endswith("/chapter_repair_review_round_02")
            return {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "material_omissions": [], "unsupported_additions": [],
                "distortions": [], "language_problems": [], "verdict": "pass",
            }

    auditor = OmissionAuditor(args(tmp_path))
    auditor.runner = SelfHealingRunner()
    report = await auditor.run([run])
    assert report["status"] == "complete"
    result = report["chapter_results"][0]
    assert [item["passed"] for item in result["repair_rounds"]] == [False, True]
    assert auditor.runner.jobs == [
        "run/scene_01/classify", "run/chapter_repair",
        "run/chapter_repair_review", "run/chapter_repair_round_02",
        "run/chapter_repair_review_round_02",
    ]


def test_parser_promotion_is_explicit_opt_in():
    base = ["--run-dir", "runs", "--output-dir", "audit"]
    assert parser().parse_args(base).promote_passed is False
    assert parser().parse_args(base).max_repair_rounds == 3
    assert parser().parse_args(base + ["--promote-passed"]).promote_passed is True


def test_promote_passed_backs_up_once_and_invalidates_annotations(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])
    original_manifest = (run / "manifest.json").read_bytes()
    write_json(run / "reader.json", {"text": "刘备遇见关羽。\n"})
    annotation_dir = run / "agents" / "annotations" / "chunk_0000"
    annotation_dir.mkdir(parents=True)
    (annotation_dir / "result.json").write_text("{}", encoding="utf-8")
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("刘备先遇见了关羽。三人后来结义。\n", encoding="utf-8")

    promoted = promote_passed_candidate(run, candidate)
    assert promoted["status"] == "promoted"
    assert (run / "chapter.txt").read_text() == candidate.read_text()
    assert (run / "chapter.before-omission-repair.txt").read_text() == "刘备遇见关羽。\n"
    assert not (run / "reader.json").exists()
    assert not (run / "agents" / "annotations").exists()
    assert (run / "reader.before-omission-repair.json").exists()
    assert (run / "agents" / "annotations.before-omission-repair").exists()
    assert (run / "manifest.json").read_bytes() == original_manifest
    report = json.loads((run / "report.json").read_text())
    assert report["chapter_cjk"] > 7
    assert report["omission_audit_promotion"]["annotation_rerun_required"] is True

    # A second promotion may update the candidate but never overwrites the
    # original chapter backup or recreates stale annotation artifacts.
    candidate.write_text("刘备、关羽、张飞三人后来结义。\n", encoding="utf-8")
    promote_passed_candidate(run, candidate)
    assert (run / "chapter.before-omission-repair.txt").read_text() == "刘备遇见关羽。\n"


def test_discovery_skips_verified_promoted_chapter_stale_scenes(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("刘备先遇见关羽，后来三人结义。\n", encoding="utf-8")
    promote_passed_candidate(run, candidate)
    assert discover_risky_scenes([run]) == []

    # An untracked later prose mutation is fail-closed, never reconstructed
    # from the stale pre-promotion scene artifacts.
    (run / "chapter.txt").write_text("又被改变。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after omission promotion"):
        discover_risky_scenes([run])


@pytest.mark.asyncio
async def test_run_promotes_only_independently_passed_material_candidate(tmp_path):
    run = make_run(tmp_path, omissions=["三人结义被省略"])
    options = args(tmp_path)
    options.promote_passed = True
    auditor = OmissionAuditor(options)
    auditor.runner = FakeRunner({"assessments": [{
        "omission_note": "三人结义被省略", "classification": "material",
        "reason": "changes relationship", "required_fix": "restore oath",
    }]})
    report = await auditor.run([run])
    assert len(report["promotions"]) == 1
    assert report["chapter_results"][0]["promotion"]["status"] == "promoted"
    assert (run / "chapter.txt").read_text() != "刘备遇见关羽。\n"
