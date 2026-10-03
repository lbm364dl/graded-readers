import json
import asyncio
from argparse import Namespace
from pathlib import Path

import pytest

from pipeline.agent_harness import (
    CHINESE_ANNOTATION_CHUNK_POLICY,
    CHINESE_PINYIN_POLICY,
    ChapterHarness,
    DEFAULT_CHINESE_ANNOTATION_CHUNK_MAXIMUM,
    DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET,
    DEFAULT_LEVEL_TARGETS,
    BookHarness,
    CodexRunner,
    apply_compact_review_policy,
    accept_distributed_scene_lengths,
    best_scene_attempt,
    discard_incorrect_length_findings,
    digest,
    gather_all_or_raise,
    length_violations,
    load_promotion_candidate,
    materialize_recovery_beats,
    normalize_review_score_scale,
    parser,
    publish_chapter_candidate,
    reusable_passed_scene,
    run_status_for_verdicts,
    scene_repair_strategy,
    split_annotation_chunks,
    split_chinese_annotation_chunks,
    status,
)


@pytest.mark.asyncio
async def test_gather_all_or_raise_drains_siblings_before_failure():
    events = []

    async def failing():
        events.append("failed")
        raise ValueError("quality gate")

    async def sibling():
        await asyncio.sleep(0)
        events.append("sibling completed")
        return "cached result"

    with pytest.raises(ValueError, match="quality gate"):
        await gather_all_or_raise(failing(), sibling())
    assert events == ["failed", "sibling completed"]


def test_compact_policy_does_not_hide_explicit_omissions():
    review = {
        "source_fidelity": 8, "naturalness": 9, "readability": 8,
        "omissions": ["minor background"], "unsupported_additions": [],
        "distortions": [], "language_problems": [], "verdict": "revise",
    }
    assert apply_compact_review_policy(review)["verdict"] == "revise"


def test_compact_policy_accepts_unexplained_high_score_revise():
    review = {
        "source_fidelity": 8, "naturalness": 9, "readability": 8,
        "omissions": [], "unsupported_additions": [], "distortions": [],
        "language_problems": [], "verdict": "revise",
    }
    accepted = apply_compact_review_policy(review)
    assert accepted["verdict"] == "pass"
    assert accepted["reviewer_verdict"] == "revise"


def test_compact_policy_keeps_real_language_problem_as_revise():
    review = {
        "source_fidelity": 9, "naturalness": 9, "readability": 9,
        "omissions": [], "unsupported_additions": [], "distortions": [],
        "language_problems": ["awkward wording"], "verdict": "revise",
    }
    assert apply_compact_review_policy(review)["verdict"] == "revise"


def test_compact_policy_leaves_pass_with_only_omission_for_materiality_audit():
    review = {
        "source_fidelity": 9, "naturalness": 9, "readability": 9,
        "omissions": ["minor atmosphere"], "unsupported_additions": [],
        "distortions": [], "language_problems": [], "verdict": "pass",
    }
    assert apply_compact_review_policy(review) == review


def test_compact_policy_leaves_hedged_pass_findings_for_materiality_audit():
    review = {
        "source_fidelity": 9, "naturalness": 9, "readability": 9,
        "omissions": [], "unsupported_additions": ["无明显新增"],
        "distortions": ["minor compression"], "language_problems": [],
        "verdict": "pass",
    }
    assert apply_compact_review_policy(review) == review


def test_book_parser_accepts_source_directory():
    args = parser().parse_args([
        "book", "--source-dir", "chapters", "--book-run-id", "full",
    ])
    assert args.source is None
    assert args.source_dir == "chapters"


def test_promote_parser_defaults_to_luna_low_review():
    args = parser().parse_args([
        "promote", "--run-dir", "run", "--candidate", "candidate.txt",
    ])
    assert args.model == "gpt-6-luna"
    assert args.review_effort == "low"


def test_promote_parser_can_reuse_unchanged_passed_scenes():
    args = parser().parse_args([
        "promote", "--run-dir", "run", "--candidate", "candidate.txt",
        "--reuse-passed-scenes",
    ])
    assert args.reuse_passed_scenes is True


def test_promote_parser_can_enable_source_beat_recovery():
    args = parser().parse_args([
        "promote", "--run-dir", "run", "--candidate", "candidate.txt",
        "--recover-with-source-beats",
    ])
    assert args.recover_with_source_beats is True
    assert args.max_source_beat_repairs == 1


def test_recovery_beats_partition_parent_span_and_allocate_target():
    source = "甲乙丙丁戊己庚辛"
    scene = {"source_start": 0, "source_end": 8, "target_chars": 101}
    beats = materialize_recovery_beats(source, scene, [
        {
            "id": "first", "source_start_quote": "甲乙",
            "target_weight": 1, "paragraphs": 1,
        },
        {
            "id": "second", "source_start_quote": "戊己",
            "target_weight": 1, "paragraphs": 2,
        },
    ])

    assert [(item["source_start"], item["source_end"]) for item in beats] == [
        (0, 4), (4, 8),
    ]
    assert [item["target_chars"] for item in beats] == [50, 51]
    assert sum(item["target_chars"] for item in beats) == 101


def test_reusable_passed_scene_requires_unchanged_reviewed_pass():
    prior = {
        "text": "刘备见到榜文。",
        "resolved": True,
        "review": {"verdict": "pass"},
    }

    assert reusable_passed_scene(prior, "刘备见到榜文。")
    assert not reusable_passed_scene(prior, "刘备看到榜文。")
    assert not reusable_passed_scene({**prior, "resolved": False}, prior["text"])
    assert not reusable_passed_scene(
        {**prior, "review": {"verdict": "revise"}}, prior["text"]
    )


def test_promotion_candidate_can_select_attempt_by_stable_index(tmp_path):
    candidate = tmp_path / "scene.json"
    candidate.write_text(json.dumps({
        "text": "最后版本",
        "attempts": [
            {"stage": "repair_01", "text": "第一次修复"},
            {"stage": "repair_01", "text": "第二次修复"},
        ],
    }), encoding="utf-8")

    path, text, selector = load_promotion_candidate(
        f"{candidate}@attempt:0"
    )

    assert path == candidate
    assert text == "第一次修复"
    assert selector == "attempt:0:repair_01"


def test_mechanical_length_policy_discards_only_length_claims():
    review = {
        "distortions": ["篇幅超过目标两倍", "把冀州写成翼州"],
        "language_problems": ["length is above target", "指代含混"],
    }
    cleaned = discard_incorrect_length_findings(review)
    assert cleaned["distortions"] == ["把冀州写成翼州"]
    assert cleaned["language_problems"] == ["指代含混"]


def test_scene_repair_strategy_preserves_accurate_short_candidate():
    strategy = scene_repair_strategy(
        "刘备见到榜文，心里很着急。",
        {
            "omissions": [], "unsupported_additions": [], "distortions": [],
            "language_problems": [
                "mechanical length gate: 14 CJK, required 20-30"
            ],
        },
        20,
        30,
    )

    assert "length-only expansion" in strategy
    assert "Preserve every existing sentence and claim" in strategy
    assert "9 is the strict minimum" in strategy


def test_scene_repair_strategy_localizes_factual_correction():
    strategy = scene_repair_strategy(
        "曹操追击敌军，恢复了当地治安。",
        {
            "omissions": [],
            "unsupported_additions": ["恢复治安没有原文依据"],
            "distortions": [], "language_problems": [],
        },
        15,
        30,
    )

    assert "surgical factual repair" in strategy
    assert "Change or remove" in strategy
    assert "smallest sentence span" in strategy


def test_best_scene_attempt_prefers_accurate_near_length_candidate():
    attempts = [
        {
            "text": "甲" * 90,
            "review": {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [],
                "distortions": [],
                "language_problems": [
                    "mechanical length gate: 90 CJK, required 100-120"
                ],
            },
        },
        {
            "text": "甲" * 105,
            "review": {
                "source_fidelity": 7, "naturalness": 8, "readability": 8,
                "omissions": [], "unsupported_additions": ["虚构因果"],
                "distortions": [],
                "language_problems": ["review scores must each be at least 8/10"],
            },
        },
        {
            "text": "甲" * 99,
            "review": {
                "source_fidelity": 8.5, "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [],
                "distortions": [],
                "language_problems": [
                    "mechanical length gate: 99 CJK, required 100-120"
                ],
            },
        },
    ]

    assert best_scene_attempt(attempts, 100, 120) is attempts[2]


def test_distributed_length_acceptance_requires_clean_review_and_chapter_budget():
    results = [{
        "scene": {"id": "scene_01"},
        "resolved": False,
        "review": {
            "verdict": "revise",
            "source_fidelity": 9, "naturalness": 9, "readability": 9,
            "omissions": [], "unsupported_additions": [], "distortions": [],
            "language_problems": [
                "mechanical length gate: 90 CJK, required 100-120"
            ],
        },
    }]

    accepted = accept_distributed_scene_lengths(
        results, "甲" * 100, "hsk4", 100
    )

    assert accepted == ["scene_01"]
    assert results[0]["resolved"] is True
    assert results[0]["review"]["verdict"] == "pass"


def test_distributed_length_acceptance_never_hides_factual_finding():
    results = [{
        "scene": {"id": "scene_01"},
        "resolved": False,
        "review": {
            "verdict": "revise",
            "source_fidelity": 9, "naturalness": 9, "readability": 9,
            "omissions": [], "unsupported_additions": ["虚构结果"],
            "distortions": [],
            "language_problems": [
                "mechanical length gate: 90 CJK, required 100-120"
            ],
        },
    }]

    assert accept_distributed_scene_lengths(
        results, "甲" * 100, "hsk4", 100
    ) == []
    assert results[0]["review"]["verdict"] == "revise"


def test_normalizes_proportional_source_review_scores_to_schema_scale():
    review = {
        "source_fidelity": 0.97, "naturalness": 0.9, "readability": 1.0,
        "verdict": "pass",
    }

    normalized = normalize_review_score_scale(review)

    assert normalized["source_fidelity"] == 9.7
    assert normalized["naturalness"] == 9.0
    assert normalized["readability"] == 10.0
    assert normalized["harness_score_normalization"] == "proportion_to_zero_ten"


def test_focus_vocabulary_guidance_forbids_unplanned_name_forms():
    harness = object.__new__(ChapterHarness)
    harness.focus_vocabulary = {
        "names": [{"surface": "刘玄德", "reason_en": "courtesy name"}],
        "story_terms": [],
    }

    guidance = harness.focus_vocabulary_guidance

    assert "complete exception inventory" in guidance
    assert "other proper name, courtesy name, title" in guidance
    assert "partial name, alternate name form" in guidance
    assert "paraphrased term receives no exemption" in guidance


def test_focus_vocabulary_semantic_guidance_keeps_reviewed_meaning():
    harness = object.__new__(ChapterHarness)
    harness.focus_vocabulary = {
        "names": [{"surface": "张飞"}],
        "story_terms": [{
            "surface": "丈八点钢矛",
            "meaning_en": "Zhang Fei's eighteen-span steel spear",
        }],
    }

    guidance = json.loads(harness.focus_vocabulary_semantic_guidance)

    assert guidance["names"] == [{"surface": "张飞"}]
    assert guidance["story_terms"][0]["meaning_en"].startswith("Zhang Fei")


def test_prose_shape_guidance_uses_progressive_exact_limits():
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(level="hsk3")

    assert "sentence at or below 42" in harness.prose_shape_guidance
    assert "clause at or below 22" in harness.prose_shape_guidance
    assert "Use a full stop" in harness.prose_shape_guidance


class FakeAnnotationRunner:
    def __init__(self, candidates, reviews):
        self.candidates = iter(candidates)
        self.reviews = iter(reviews)
        self.calls = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append((job, schema.name, effort, prompt))
        if schema.name == "annotation.schema.json":
            return next(self.candidates)
        return next(self.reviews)


class FakeOutlineRunner:
    def __init__(self, outline):
        self.outline_result = outline

    async def call(self, *args, **kwargs):
        return self.outline_result


class FakeSourceReviewRunner:
    def __init__(self, review):
        self.review = review
        self.calls = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append((job, prompt, schema.name, effort))
        return dict(self.review)


def annotation_harness(runner, max_repairs=2):
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(
        annotation_effort="low", annotation_review_effort="medium",
        annotation_repair_effort="high", annotation_final_effort="xhigh",
        max_annotation_repairs=max_repairs, refresh=False,
    )
    harness.runner = runner
    return harness


def constrained_annotation_harness(runner, max_repairs=2):
    harness = annotation_harness(runner, max_repairs)
    harness.args.annotation_mode = "constrained"
    harness.args.level = "hsk4"
    return harness


def constrained_delta_harness(runner, max_repairs=2):
    harness = annotation_harness(runner, max_repairs)
    harness.args.annotation_mode = "constrained-delta"
    harness.args.annotation_review_policy = "exhaustive-chunks"
    harness.args.level = "hsk4"
    return harness


@pytest.mark.asyncio
async def test_annotation_generation_and_review_enforce_neutral_directional_lai():
    annotation = {"segments": [{
        "text": "泛上来", "type": "word", "pinyin": "fàn shànglai",
        "meaning_en": "surge up",
    }], "grammar_overlays": []}
    runner = FakeAnnotationRunner([annotation], [{"verdict": "pass", "issues": []}])
    harness = annotation_harness(runner)

    await harness.annotation_candidate(0, "海水泛上来。")
    await harness.review_annotation(0, "泛上来", annotation, "initial")

    prompts = [call[3] for call in runner.calls]
    assert all(CHINESE_PINYIN_POLICY in prompt for prompt in prompts)
    assert all("fàn shànglai" in prompt and "zǒu jìnqu" in prompt
               for prompt in prompts)


class FakeConstrainedRunner:
    def __init__(self, fixed, corrections, reviews):
        self.fixed = iter(fixed)
        self.corrections = iter(corrections)
        self.reviews = iter(reviews)
        self.calls = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append((job, schema.name, effort, prompt))
        if schema.name == "fixed-annotation.schema.json":
            return next(self.fixed)
        if schema.name == "fixed-annotation-correction.schema.json":
            return next(self.corrections)
        return next(self.reviews)


def test_annotation_chunks_reconstruct_exact_text():
    text = (
        "刘备看完告示，叹了一口气。张飞走过来问他为什么不高兴。\n\n"
        "两人正在说话，关羽也来到了店里。"
    )
    chunks = split_annotation_chunks(text, target=20, maximum=35)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 35 for chunk in chunks)


def test_long_sentence_is_split_without_data_loss():
    text = "刘" * 83 + "。"
    chunks = split_annotation_chunks(text, target=20, maximum=30)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 20 for chunk in chunks[:-1])


def test_digest_is_stable_and_order_sensitive():
    assert digest("a", "b") == digest("a", "b")
    assert digest("a", "b") != digest("b", "a")


def test_run_needs_review_when_any_scene_still_fails():
    assert run_status_for_verdicts({"scene_01": "pass"}) == "complete"
    assert run_status_for_verdicts({"scene_01": "revise"}) == "blocked"
    assert run_status_for_verdicts({}) == "blocked"


@pytest.mark.asyncio
async def test_hsk1_source_review_cannot_override_word_sentence_gate():
    text = (
        "东汉末年宦官专权，灾祸不断，盗贼蜂起。"
        "张角得天书，用符水治病，聚众起事。"
    )
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(
        level="hsk1", review_effort="low", refresh=False,
    )
    harness.source = text
    harness.runner = FakeSourceReviewRunner({
        "source_fidelity": 9, "naturalness": 9, "readability": 9,
        "omissions": [], "unsupported_additions": [], "distortions": [],
        "language_problems": [], "verdict": "pass",
    })
    scene = {
        "id": "scene_01", "source_start": 0, "source_end": len(text),
        "target_chars": 30,
    }

    result = await harness.review_scene(scene, text)

    assert result["verdict"] == "revise"
    assert result["harness_decision"] == (
        "rejected_by_mechanical_level_word_sentence_gate"
    )
    assert "naturally segmented word tokens" in result["language_problems"][-1]


def test_prior_review_traps_keep_semantic_findings_not_mechanical_noise():
    traps = ChapterHarness.prior_review_traps([{
        "review": {
            "omissions": [],
            "unsupported_additions": [],
            "distortions": ["誓言的意思不准确。"],
            "language_problems": [
                "这句话不自然。",
                "mechanical HSK3 word/sentence gate: too long",
                "review scores must each be at least 8/10",
            ],
        }
    }])

    assert traps == ["誓言的意思不准确。", "这句话不自然。"]


@pytest.mark.asyncio
async def test_hsk1_pass_with_explicit_language_problem_is_forced_to_revise():
    text = "很多人没有饭吃。刘备想帮大家。他认识了关羽。" * 6
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(
        level="hsk1", review_effort="low", refresh=False,
    )
    harness.source = text
    harness.runner = FakeSourceReviewRunner({
        "source_fidelity": 9, "naturalness": 8, "readability": 9,
        "omissions": [], "unsupported_additions": [], "distortions": [],
        "language_problems": ["有一句话不自然"], "verdict": "pass",
    })
    scene = {
        "id": "scene_01", "source_start": 0, "source_end": len(text),
        "target_chars": 120,
    }

    result = await harness.review_scene(scene, text)

    assert result["verdict"] == "revise"
    assert result["harness_decision"] == (
        "rejected_review_with_explicit_findings"
    )


def test_blocked_rerun_removes_stale_accepted_chapter_and_reader(tmp_path):
    (tmp_path / "chapter.txt").write_text("旧的已接受版本", encoding="utf-8")
    (tmp_path / "reader.json").write_text("{}", encoding="utf-8")

    result = publish_chapter_candidate(tmp_path, "新的候选版本\n", "blocked")

    assert result == tmp_path / "chapter-candidate.txt"
    assert result.read_text(encoding="utf-8") == "新的候选版本\n"
    assert not (tmp_path / "chapter.txt").exists()
    assert not (tmp_path / "reader.json").exists()


def test_complete_rerun_removes_stale_candidate_and_reader(tmp_path):
    (tmp_path / "chapter-candidate.txt").write_text("旧候选", encoding="utf-8")
    (tmp_path / "reader.json").write_text("{}", encoding="utf-8")

    result = publish_chapter_candidate(tmp_path, "新的已接受版本\n", "complete")

    assert result == tmp_path / "chapter.txt"
    assert result.read_text(encoding="utf-8") == "新的已接受版本\n"
    assert not (tmp_path / "chapter-candidate.txt").exists()
    assert not (tmp_path / "reader.json").exists()


def test_status_prints_manifest_and_report(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"status": "complete"}))
    (run_dir / "report.json").write_text(json.dumps({"scenes": 10}))
    assert status(run_dir) == 0
    output = capsys.readouterr().out
    assert '"status": "complete"' in output
    assert '"scenes": 10' in output
    assert "agent_jobs=0" in output


def test_status_rejects_missing_run(tmp_path, capsys):
    assert status(tmp_path / "missing") == 1
    assert "Run not found" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_annotation_self_heals_reconstruction_before_review():
    bad = {"segments": [{"text": "刘", "type": "word", "pinyin": "liú", "meaning_en": "Liu"}], "grammar_overlays": []}
    good = {"segments": [{"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"}], "grammar_overlays": []}
    passed = {"verdict": "pass", "issues": []}
    runner = FakeAnnotationRunner([bad, good], [passed])

    result = await annotation_harness(runner).annotate_chunk(0, "刘备")

    assert "".join(item["text"] for item in result["segments"]) == "刘备"
    assert [item["stage"] for item in result["attempts"]] == ["initial", "repair_01"]
    assert [call[2] for call in runner.calls if call[1] == "annotation.schema.json"] == ["low", "high"]


@pytest.mark.asyncio
async def test_annotation_review_repairs_over_grouped_ordinary_phrase():
    grouped = {"segments": [{
        "text": "两人喝酒时", "type": "idiom", "pinyin": "liǎng rén hē jiǔ shí",
        "meaning_en": "while the two men were drinking",
    }], "grammar_overlays": []}
    split = {"segments": [
        {"text": "两人", "type": "word", "pinyin": "liǎng rén", "meaning_en": "the two people"},
        {"text": "喝酒", "type": "word", "pinyin": "hē jiǔ", "meaning_en": "drink alcohol"},
        {"text": "时", "type": "particle", "pinyin": "shí", "meaning_en": "when; while"},
    ], "grammar_overlays": [{
        "start": 4, "end": 5, "text": "时",
        "grammar_candidate_key": "test.when", "pattern": "V时",
        "meaning_en": "when; while an action happens",
    }]}
    revise = {"verdict": "revise", "issues": [{
        "segment_text": "两人喝酒时", "problem": "over_grouped",
        "explanation": "This is a compositional clause, not an idiom.",
        "suggested_fix": "Split 两人 / 喝酒 / 时.",
    }]}
    # The deterministic four-character lexical ceiling catches this before a
    # model review; the repaired candidate still receives an independent pass.
    runner = FakeAnnotationRunner([grouped, split], [{"verdict": "pass", "issues": []}])

    result = await annotation_harness(runner).annotate_chunk(3, "两人喝酒时")

    assert [segment["text"] for segment in result["segments"]] == ["两人", "喝酒", "时"]
    repair_prompt = next(call[3] for call in runner.calls if call[0].endswith("repair_01"))
    assert "over_grouped" in repair_prompt


@pytest.mark.asyncio
async def test_annotation_review_defers_exactness_and_offsets_to_deterministic_checks():
    annotation = {
        "segments": [{
            "text": "走进了", "type": "word", "pinyin": "zǒu jìn le",
            "meaning_en": "walked in",
        }],
        "grammar_overlays": [{
            "start": 0, "end": 3, "text": "走进了",
            "grammar_candidate_key": "test.completed_action", "pattern": "V了",
            "meaning_en": "marks a completed action",
        }],
    }
    runner = FakeAnnotationRunner([], [{"verdict": "pass", "issues": []}])

    await annotation_harness(runner).review_annotation(0, "走进了", annotation, "initial")

    prompt = runner.calls[0][3]
    assert "already passed deterministic exactness" in prompt
    assert "Do not re-count offsets" in prompt
    assert "deterministic code is the sole" in prompt
    assert "reject generic sentence or" in prompt
    assert "clause summaries" in prompt
    assert "pragmatic learner reader" in prompt
    assert "Do not fail an otherwise useful annotation" in prompt
    assert "material problem remains" in prompt
    assert "start/end are REQUIRED zero-based Python character offsets" in prompt
    assert "TEXT[start:end] == segment_text" in prompt
    assert "COLUMNS=[CHAR_START,CHAR_END,TEXT,TYPE,PINYIN,MEANING_EN]" in prompt
    assert '[0,3,"走进了","word","zǒu jìn le","walked in"]' in prompt
    assert '"start": 0' not in prompt  # overlays are compact JSON, not pretty JSON
    assert "Do not use tools, a shell, Python, search, or external sources" in prompt
    assert "\nANNOTATION:\n" not in prompt
    review = await annotation_harness(
        FakeAnnotationRunner([], [{"verdict": "revise", "issues": [{
            "start": 1, "end": 2, "segment_text": "进", "problem": "meaning",
            "explanation": "context", "suggested_fix": "enter",
        }]}])
    ).review_annotation(0, "走进了", annotation, "offsets")
    assert review["offset_audit"] == {"valid": 1, "legacy_missing": 0, "invalid": 0}


@pytest.mark.asyncio
async def test_annotation_review_prompt_is_compact_for_large_chunk():
    segments = [{
        "text": "人", "type": "word", "pinyin": "rén", "meaning_en": "person",
    } for _ in range(300)]
    annotation = {"segments": segments, "grammar_overlays": []}
    runner = FakeAnnotationRunner([], [{"verdict": "pass", "issues": []}])
    await annotation_harness(runner).review_annotation(
        0, "人" * len(segments), annotation, "large"
    )
    prompt = runner.calls[0][3]
    legacy_pretty = json.dumps(annotation, ensure_ascii=False, indent=2)
    assert len(prompt) < len(legacy_pretty) * 0.65
    assert prompt.count("人") >= len(segments)  # full TEXT is still present
    assert "COLUMNS=[CHAR_START,CHAR_END,TEXT,TYPE,PINYIN,MEANING_EN]" in prompt
    assert "Do not use tools" in prompt


def test_annotation_contract_rejects_clause_sized_word_and_bad_grammar_span():
    clause = {
        "segments": [{
            "text": "他们一起走进了城里面", "type": "word",
            "pinyin": "tā men yì qǐ zǒu jìn le chéng lǐ miàn",
            "meaning_en": "they walked into the city together",
        }],
        "grammar_overlays": [],
    }
    assert not ChapterHarness.annotation_reconstructs("他们一起走进了城里面", clause)
    valid = {
        "segments": [
            {"text": "走进", "type": "word", "pinyin": "zǒu jìn", "meaning_en": "walk into"},
            {"text": "了", "type": "particle", "pinyin": "le", "meaning_en": "completed-action marker"},
        ],
        "grammar_overlays": [{
            "start": 0, "end": 3, "text": "走进了",
            "grammar_candidate_key": "test.completed_action", "pattern": "V了",
            "meaning_en": "marks a completed action",
        }],
    }
    assert ChapterHarness.annotation_reconstructs("走进了", valid)
    valid["grammar_overlays"][0]["text"] = "走进"
    assert not ChapterHarness.annotation_reconstructs("走进了", valid)
    issues = ChapterHarness.annotation_contract_issues("走进了", valid)
    assert [item["problem"] for item in issues] == ["grammar"]
    assert "TEXT[0:3]" in issues[0]["explanation"]


def test_annotation_candidate_canonicalizes_only_unambiguous_offsets_and_whitespace():
    raw = {
        "segments": [{"text": "走进了", "type": "word", "pinyin": "zǒu jìn le", "meaning_en": "walked in"}],
        "grammar_overlays": [{
            "start": 99, "end": 105, "text": "走进了",
            "grammar_candidate_key": "test.completed_action", "pattern": "V了",
            "meaning_en": "completed action",
        }],
    }
    fixed = ChapterHarness.canonicalize_annotation_candidate("走进了\n", raw)
    assert fixed["grammar_overlays"][0]["start"] == 0
    assert fixed["grammar_overlays"][0]["end"] == 3
    assert fixed["segments"][-1] == {
        "text": "\n", "type": "punctuation", "pinyin": "", "meaning_en": "",
    }
    assert raw["grammar_overlays"][0]["start"] == 99


def test_annotation_candidate_uses_uniquely_nearest_repeated_occurrence():
    raw = {"segments": [], "grammar_overlays": [{
        "start": 7, "end": 8, "text": "时",
        "grammar_candidate_key": "test.when", "pattern": "V时",
        "meaning_en": "when",
    }]}
    fixed = ChapterHarness.canonicalize_annotation_candidate("来时走，走时来", raw)
    assert fixed["grammar_overlays"][0]["start"] == 5


def test_annotation_candidate_does_not_guess_tied_repeated_occurrence():
    raw = {"segments": [], "grammar_overlays": [{
        "start": 3, "end": 4, "text": "时",
        "grammar_candidate_key": "test.when", "pattern": "V时",
        "meaning_en": "when",
    }]}
    fixed = ChapterHarness.canonicalize_annotation_candidate("来时走，走时来", raw)
    assert fixed["grammar_overlays"][0]["start"] == 3


def test_annotation_candidate_repairs_missing_leading_whitespace_only():
    raw = {"segments": [{
        "text": "正文", "type": "word", "pinyin": "zhèng wén",
        "meaning_en": "main text",
    }], "grammar_overlays": []}
    fixed = ChapterHarness.canonicalize_annotation_candidate("\n正文", raw)
    assert fixed["segments"][0] == {
        "text": "\n", "type": "punctuation", "pinyin": "", "meaning_en": "",
    }


def test_annotation_candidate_never_repairs_non_whitespace_difference():
    raw = {"segments": [{
        "text": "正文", "type": "word", "pinyin": "zhèng wén",
        "meaning_en": "main text",
    }], "grammar_overlays": []}
    assert ChapterHarness.canonicalize_annotation_candidate("甲正文", raw) == raw


@pytest.mark.asyncio
async def test_annotation_escalates_to_fresh_attempt_then_fails_closed():
    bad = {"segments": [{"text": "错", "type": "word", "pinyin": "cuò", "meaning_en": "wrong"}], "grammar_overlays": []}
    runner = FakeAnnotationRunner([bad, bad], [])

    with pytest.raises(ValueError, match="quality gate failed"):
        await annotation_harness(runner, max_repairs=0).annotate_chunk(1, "原文")

    candidate_calls = [call for call in runner.calls if call[1] == "annotation.schema.json"]
    assert [call[2] for call in candidate_calls] == ["low", "xhigh"]


@pytest.mark.asyncio
async def test_constrained_annotation_reviews_and_self_heals_with_lossless_patches():
    fixed = {"segments": [
        {"index": 0, "text": "两", "type": "word", "pinyin": "liǎng", "meaning_en": "two"},
        {"index": 1, "text": "人", "type": "word", "pinyin": "rén", "meaning_en": "people"},
        {"index": 2, "text": "喝", "type": "word", "pinyin": "hē", "meaning_en": "drink"},
        {"index": 3, "text": "酒", "type": "word", "pinyin": "jiǔ", "meaning_en": "alcohol"},
        {"index": 4, "text": "时", "type": "particle", "pinyin": "shí", "meaning_en": "when"},
    ], "grammar_overlays": []}
    correction = {"patches": [
        {"start_index": 0, "end_index": 2, "segments": [{
            "text": "两人", "type": "word", "pinyin": "liǎng rén",
            "meaning_en": "the two people",
        }]},
        {"start_index": 2, "end_index": 4, "segments": [{
            "text": "喝酒", "type": "word", "pinyin": "hē jiǔ",
            "meaning_en": "drink alcohol",
        }]},
    ], "grammar_overlays": [{
        "start": 4, "end": 5, "text": "时",
        "grammar_candidate_key": "test.when", "pattern": "V时",
        "meaning_en": "when an action happens",
    }]}
    revise = {"verdict": "revise", "issues": [{
        "segment_text": "两 / 人", "problem": "under_grouped",
        "explanation": "两人 is one learner-facing unit here.",
        "suggested_fix": "Merge 两 and 人; also merge 喝酒.",
    }]}
    runner = FakeConstrainedRunner(
        [fixed], [correction], [revise, {"verdict": "pass", "issues": []}]
    )

    result = await constrained_annotation_harness(runner).annotate_chunk(
        0, "两人喝酒时"
    )

    # Only the concrete 两/人 issue surface authorizes a mutation. A second
    # suggestion embedded in explanation prose is not an executable scope.
    assert [item["text"] for item in result["segments"]] == ["两人", "喝", "酒", "时"]
    assert "".join(item["text"] for item in result["segments"]) == "两人喝酒时"
    assert [item["stage"] for item in result["attempts"]] == ["initial", "correction_01"]
    assert result["reviewed"] is True
    correction_call = next(call for call in runner.calls if "correction_01" in call[0])
    assert correction_call[1] == "fixed-annotation-correction.schema.json"
    assert "non_authoritative_boundary_proposals_v1" in correction_call[3]


@pytest.mark.asyncio
async def test_constrained_annotation_fails_closed_when_fresh_review_still_revises():
    fixed = {"segments": [{
        "index": 0, "text": "刘备", "type": "name", "pinyin": "Liú Bèi",
        "meaning_en": "Liu Bei",
    }], "grammar_overlays": []}
    revise = {"verdict": "revise", "issues": [{
        "segment_text": "刘备", "problem": "meaning",
        "explanation": "Needs contextual identification.",
        "suggested_fix": "Identify the character concisely.",
    }]}
    runner = FakeConstrainedRunner([fixed, fixed], [], [revise, revise])

    with pytest.raises(ValueError, match="constrained annotation quality gate failed"):
        await constrained_annotation_harness(runner, max_repairs=0).annotate_chunk(
            0, "刘备"
        )

    fixed_calls = [call for call in runner.calls if call[1] == "fixed-annotation.schema.json"]
    assert [call[2] for call in fixed_calls] == ["low", "xhigh"]
    assert fixed_calls[0][0] != fixed_calls[1][0]


@pytest.mark.asyncio
async def test_constrained_correction_salvages_lossless_sibling_patches():
    annotation = {
        "segments": [
            {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
            {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come"},
        ],
        "grammar_overlays": [],
    }

    class Runner:
        async def call(self, *args, **kwargs):
            return {
                "patches": [
                    {"start_index": 0, "end_index": 1, "segments": [{
                        "text": "刘备", "type": "name", "pinyin": "Liú Bèi",
                        "meaning_en": "Liu Bei, the protagonist",
                    }]},
                    {"start_index": 1, "end_index": 2, "segments": [{
                        "text": "来了", "type": "word", "pinyin": "lái le",
                        "meaning_en": "came",
                    }]},
                ],
                "grammar_overlays": [],
            }

    harness = object.__new__(ChapterHarness)
    harness.runner = Runner()
    harness.args = Namespace(annotation_repair_effort="medium", refresh=False)
    corrected = await harness.constrained_annotation_correction(
        0, "刘备来", annotation, {"verdict": "revise", "issues": [{
            "segment_text": "刘备", "problem": "meaning",
            "explanation": "Needs a concise identity.",
            "suggested_fix": "Identify Liu Bei.",
        }]}, 1
    )
    assert "".join(item["text"] for item in corrected["segments"]) == "刘备来"
    assert corrected["segments"][0]["meaning_en"] == "Liu Bei, the protagonist"
    assert corrected["segments"][1]["text"] == "来"
    assert corrected["correction_evidence"]["applied_patch_count"] == 1
    assert corrected["correction_evidence"]["discarded_patches"]


@pytest.mark.asyncio
async def test_constrained_correction_allows_reviewed_story_term_scope():
    annotation = {
        "segments": [
            {"text": "丈", "type": "word", "pinyin": "zhàng", "meaning_en": "unit"},
            {"text": "八点", "type": "word", "pinyin": "bā diǎn", "meaning_en": "eight o'clock"},
            {"text": "钢矛", "type": "word", "pinyin": "gāng máo", "meaning_en": "steel spear"},
        ],
        "grammar_overlays": [],
    }

    class Runner:
        async def call(self, *args, **kwargs):
            return {
                "patches": [{"start_index": 0, "end_index": 3, "segments": [
                    {"text": "丈八", "type": "word", "pinyin": "zhàng bā", "meaning_en": "eighteen chi"},
                    {"text": "点钢", "type": "word", "pinyin": "diǎn gāng", "meaning_en": "refined steel"},
                    {"text": "矛", "type": "word", "pinyin": "máo", "meaning_en": "spear"},
                ]}],
                "grammar_overlays": [],
            }

    harness = object.__new__(ChapterHarness)
    harness.runner = Runner()
    harness.args = Namespace(annotation_repair_effort="low", refresh=False)
    harness.focus_vocabulary = {
        "names": [],
        "story_terms": [{"surface": "丈八点钢矛"}],
    }
    corrected = await harness.constrained_annotation_correction(
        0, "丈八点钢矛", annotation, {"verdict": "revise", "issues": [{
            "start": 1, "end": 3, "segment_text": "八点", "problem": "meaning",
            "explanation": "The weapon name is mis-segmented.",
            "suggested_fix": "Split 丈八点钢矛 as 丈八, 点钢, 矛.",
        }]}, 1,
    )

    assert [item["text"] for item in corrected["segments"]] == ["丈八", "点钢", "矛"]
    assert corrected["correction_evidence"]["planned_story_scope_patches"] == [
        {"range": [0, 3], "surface": "丈八点钢矛"}
    ]


def test_annotation_mode_is_opt_in_and_part_of_cli_contract():
    default = parser().parse_args(["run", "--source", "chapter.txt"])
    constrained = parser().parse_args([
        "run", "--source", "chapter.txt", "--annotation-mode", "constrained",
    ])
    assert default.annotation_mode == "generative"
    assert constrained.annotation_mode == "constrained"
    delta = parser().parse_args([
        "run", "--source", "chapter.txt", "--annotation-mode", "constrained-delta",
    ])
    assert delta.annotation_mode == "constrained-delta"


@pytest.mark.parametrize("command,required", [
    ("run", ["--source", "chapter.txt"]),
    ("annotate", ["--chapter", "adapted.txt", "--source", "original.txt",
                  "--run-dir", "out", "--level", "hsk4"]),
    ("book", ["--source", "chapter.txt", "--book-run-id", "book"]),
])
def test_all_chinese_entry_points_share_adaptive_chunk_cli(command, required):
    defaults = parser().parse_args([command, *required])
    assert defaults.annotation_final_effort == "low"
    assert defaults.annotation_chunk == DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET
    assert DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET == 350
    assert DEFAULT_CHINESE_ANNOTATION_CHUNK_MAXIMUM == 400
    assert defaults.annotation_chunk_maximum is None
    overridden = parser().parse_args([
        command, *required, "--annotation-chunk", "320",
        "--annotation-chunk-maximum", "480",
    ])
    assert overridden.annotation_chunk == 320
    assert overridden.annotation_chunk_maximum == 480

    legacy_override = parser().parse_args([
        command, *required, "--annotation-chunk", "800",
    ])
    harness = object.__new__(ChapterHarness)
    harness.args = legacy_override
    assert harness.annotation_chunk_maximum == 914


def test_delta_partition_uses_exact_sentence_safe_chunks():
    chapter = "刘备来到城里。关羽也来了。张飞正在等他们。三人一起吃饭。"
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(annotation_chunk=10, annotation_chunk_maximum=16)
    chunks = harness.chinese_annotation_chunks(chapter)
    assert chunks == split_chinese_annotation_chunks(chapter, 10, 16)
    assert "".join(chunks) == chapter
    assert all(chunk.endswith("。") for chunk in chunks)


def test_chunk_policy_and_overrides_invalidate_annotation_prompt_cache():
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(annotation_chunk=350, annotation_chunk_maximum=400)
    first = harness.annotation_chunk_cache_tag
    harness.args.annotation_chunk_maximum = 399
    second = harness.annotation_chunk_cache_tag
    assert CHINESE_ANNOTATION_CHUNK_POLICY in first
    assert first != second


def test_annotation_only_cli_is_isolated_and_defaults_to_delta():
    args = parser().parse_args([
        "annotate", "--chapter", "approved/chapter.txt",
        "--source", "original/chapter_001.txt", "--run-dir", "runs/smoke",
        "--level", "hsk4",
    ])
    assert args.command == "annotate"
    assert args.annotation_mode == "constrained-delta"
    assert args.annotation_review_policy == "chapter"
    assert args.concurrency == 1
    assert args.resume is False


def test_annotation_only_refuses_nonempty_output_without_explicit_resume(tmp_path):
    from pipeline.agent_harness import AnnotationOnlyHarness

    chapter = tmp_path / "chapter.txt"
    source = tmp_path / "source.txt"
    chapter.write_text("刘备来了。\n", encoding="utf-8")
    source.write_text("玄德见榜。", encoding="utf-8")
    output = tmp_path / "existing"
    output.mkdir()
    (output / "do-not-touch.txt").write_text("owned", encoding="utf-8")
    args = parser().parse_args([
        "annotate", "--chapter", str(chapter), "--source", str(source),
        "--run-dir", str(output), "--level", "hsk4",
    ])
    with pytest.raises(ValueError, match="not empty"):
        AnnotationOnlyHarness(args)
    assert (output / "do-not-touch.txt").read_text() == "owned"


@pytest.mark.asyncio
async def test_annotation_only_preserves_exact_text_and_provenance(tmp_path):
    from pipeline.agent_harness import AnnotationOnlyHarness

    chapter = tmp_path / "approved.txt"
    source = tmp_path / "source.txt"
    text = "刘备来了。\n"
    chapter.write_text(text, encoding="utf-8")
    source.write_text("玄德见榜。", encoding="utf-8")
    output = tmp_path / "isolated"
    args = parser().parse_args([
        "annotate", "--chapter", str(chapter), "--source", str(source),
        "--run-dir", str(output), "--level", "hsk4",
    ])
    harness = AnnotationOnlyHarness(args)
    segments = [
        {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
        {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come"},
        {"text": "了", "type": "particle", "pinyin": "le", "meaning_en": "completed-action marker"},
        {"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ]

    async def fake_delta(given, chunks):
        assert given == text and chunks == [text]
        return [{
            "segments": segments, "grammar_overlays": [], "resolved": True,
            "attempts": [{"review": {"verdict": "pass"}}],
        }]

    harness.annotate_chapter_constrained_delta = fake_delta
    report = await harness.run_annotation_only()
    reader = json.loads((output / "reader.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert report["exact_reconstruction"] is True
    assert reader["text"] == text == (output / "chapter.txt").read_text(encoding="utf-8")
    assert "".join(item["text"] for item in reader["segments"]) == text
    assert manifest["kind"] == "annotation-only"
    assert manifest["annotation_review_policy"] == "chapter"
    assert reader["annotation_audit"]["review_policy"] == "chapter"
    assert reader["annotation_audit"]["semantic_review_calls"] == 0
    assert manifest["source"] == str(source.resolve())
    assert manifest["chapter_input"] == str(chapter.resolve())


@pytest.mark.asyncio
async def test_constrained_delta_uses_one_chapter_seed_then_chunk_reviews():
    from pipeline.delta_boundary_annotation import (
        contextual_delta_batches, contextual_delta_targets, deterministic_baseline,
    )

    chapter = "玄德来了。\n刘备来了。\n"
    chunks = ["玄德来了。\n", "刘备来了。\n"]
    baseline, unknown = deterministic_baseline(chapter)
    target_batches = contextual_delta_batches(
        chapter, baseline, unknown, chunks, maximum_targets=150,
    )
    overrides = []
    for index in sorted(contextual_delta_targets(baseline, unknown)):
        surface = baseline[index]["text"]
        overrides.append({
            "index": index,
            "type": "name" if surface in {"玄德", "刘备"} else "word",
            "pinyin": baseline[index]["pinyin"],
            "meaning_en": "Liu Bei" if surface in {"玄德", "刘备"} else surface,
        })

    class Runner:
        def __init__(self):
            self.calls = []

        async def call(self, job, prompt, schema, effort, **kwargs):
            self.calls.append((job, schema.name, effort, prompt))
            if schema.name == "delta-annotation.schema.json":
                batch_number = int(job.split("batch_")[1].split("/")[0])
                allowed = set(target_batches[batch_number]["indices"])
                return {"overrides": [row for row in overrides
                                      if row["index"] in allowed],
                        "grammar_overlays": []}
            if schema.name == "annotation-review.schema.json":
                return {"verdict": "pass", "issues": []}
            raise AssertionError(schema.name)

    runner = Runner()
    results = await constrained_delta_harness(runner).annotate_chapter_constrained_delta(
        chapter, chunks
    )

    assert len([call for call in runner.calls if call[1] == "delta-annotation.schema.json"]) == 2
    assert len([call for call in runner.calls if call[1] == "annotation-review.schema.json"]) == 2
    assert "IMMUTABLE INDEXED BASELINE" not in runner.calls[0][3]
    assert all(item["resolved"] and item["reviewed"] for item in results)
    assert "".join(segment["text"] for item in results for segment in item["segments"]) == chapter


@pytest.mark.asyncio
async def test_constrained_delta_completes_only_missing_mandatory_indices():
    chapter = "玄德来了。"
    chunks = [chapter]

    class Runner:
        def __init__(self):
            self.calls = []

        async def call(self, job, prompt, schema, effort, **kwargs):
            self.calls.append((job, prompt, effort))
            if job.endswith("/initial"):
                return {"boundary_patches": [{
                    "start": 2, "end": 3, "segments": [{
                        "text": "来", "type": "word", "pinyin": "lái",
                        "meaning_en": "to come",
                    }],
                }], "overrides": [], "grammar_overlays": []}
            if job.endswith("/missing_completion"):
                assert "MISSING REQUIRED ROWS" in prompt
                from pipeline.delta_boundary_annotation import (
                    contextual_delta_targets, deterministic_baseline,
                )
                baseline, unknown = deterministic_baseline(chapter)
                required = contextual_delta_targets(baseline, unknown)
                return {"overrides": [{
                    "index": index, "type": "name" if baseline[index]["text"] == "玄德" else "word",
                    "pinyin": baseline[index]["pinyin"] or "lái",
                    "meaning_en": "Liu Bei" if baseline[index]["text"] == "玄德" else "contextual meaning",
                } for index in sorted(required)], "grammar_overlays": []}
            if job.endswith("_review"):
                return {"verdict": "pass", "issues": []}
            raise AssertionError(job)

    harness = constrained_delta_harness(Runner())
    results = await harness.annotate_chapter_constrained_delta(chapter, chunks)
    assert results[0]["resolved"] is True
    assert harness.annotation_metadata_model_calls == 2
    assert harness.annotation_boundary_normalization["discarded_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "duplicate", "non_target", "overlays"])
async def test_constrained_delta_missing_completion_rejects_bad_merge(fault):
    from pipeline.delta_boundary_annotation import (
        contextual_delta_targets, deterministic_baseline,
    )

    chapter = "玄德来了。"
    baseline, unknown = deterministic_baseline(chapter)
    required = contextual_delta_targets(baseline, unknown)
    assert required

    def override(index):
        return {
            "index": index, "type": "word",
            "pinyin": baseline[index]["pinyin"] or "xuán dé",
            "meaning_en": "contextual meaning",
        }

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            if job.endswith("/initial"):
                return {"overrides": [], "grammar_overlays": []}
            if job.endswith("/missing_completion"):
                rows = [override(index) for index in sorted(required)]
                overlays = []
                if fault == "missing":
                    rows = rows[:-1]
                elif fault == "duplicate":
                    rows.append(dict(rows[0]))
                elif fault == "non_target":
                    rows.append(override(next(
                        index for index in range(len(baseline)) if index not in required
                    )))
                elif fault == "overlays":
                    overlays = [{
                        "start": 0, "end": 2, "text": "玄德",
                        "grammar_candidate_key": "test.forbidden",
                        "pattern": "name", "meaning_en": "forbidden here",
                    }]
                return {"overrides": rows, "grammar_overlays": overlays}
            if job.endswith("_review"):
                return {"verdict": "pass", "issues": []}
            raise AssertionError(job)

    harness = constrained_delta_harness(Runner())
    if fault == "duplicate":
        result = await harness.annotate_chapter_constrained_delta(chapter, [chapter])
        assert result[0]["resolved"] is True
        assert harness.annotation_duplicate_override_collapses == 1
        return
    expected = (
        "cannot add overlays or boundary patches" if fault == "overlays"
        else "must return every and only missing index"
    )
    with pytest.raises(ValueError, match=expected):
        await harness.annotate_chapter_constrained_delta(chapter, [chapter])


@pytest.mark.asyncio
async def test_chapter_review_policy_uses_one_comprehensive_review_when_seed_passes():
    from pipeline.delta_boundary_annotation import (
        contextual_delta_targets, deterministic_baseline,
    )

    chapter = "东来了。\n"
    baseline, unknown = deterministic_baseline(chapter)
    required = contextual_delta_targets(baseline, unknown)
    overrides = [{
        "index": index, "type": "word",
        "pinyin": baseline[index]["pinyin"] or "lái",
        "meaning_en": "contextual meaning",
    } for index in sorted(required)]

    class Runner:
        def __init__(self): self.calls = []
        async def call(self, job, prompt, schema, effort, **kwargs):
            self.calls.append((job, schema.name, effort))
            if schema.name == "delta-annotation.schema.json":
                return {"overrides": overrides, "grammar_overlays": []}
            if schema.name == "annotation-review.schema.json":
                return {"verdict": "pass", "issues": []}
            raise AssertionError(schema.name)

    runner = Runner()
    harness = constrained_delta_harness(runner)
    harness.args.annotation_review_policy = "chapter"
    result = await harness.annotate_chapter_constrained_delta(chapter, [chapter])
    assert harness.annotation_metadata_model_calls == 1
    assert harness.annotation_semantic_review_calls == 1
    assert len(runner.calls) == 2
    assert result[0]["attempts"][-1]["review"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_chapter_review_policy_has_one_scoped_correction_and_final_review():
    from pipeline.delta_boundary_annotation import (
        contextual_delta_targets, deterministic_baseline,
    )

    chapter = "东来了。\n"
    baseline, unknown = deterministic_baseline(chapter)
    required = contextual_delta_targets(baseline, unknown)
    overrides = [{
        "index": index, "type": "word",
        "pinyin": baseline[index]["pinyin"] or "lái",
        "meaning_en": "contextual meaning",
    } for index in sorted(required)]
    revise = {"verdict": "revise", "issues": [{
        "start": 0, "end": 1, "segment_text": "东", "problem": "meaning",
        "explanation": "Here it means eastward.",
        "suggested_fix": "Use eastward.",
    }]}
    correction = {"patches": [{
        "start_index": 0, "end_index": 1, "segments": [{
            "text": "东", "type": "word", "pinyin": "dōng",
            "meaning_en": "eastward",
        }, {
            "text": "来", "type": "word", "pinyin": "lái",
            "meaning_en": "to come",
        }],
    }], "grammar_overlays": []}

    class Runner:
        def __init__(self): self.calls = []; self.reviews = iter([revise, {"verdict": "pass", "issues": []}])
        async def call(self, job, prompt, schema, effort, **kwargs):
            self.calls.append((job, schema.name, effort))
            if schema.name == "delta-annotation.schema.json":
                return {"overrides": overrides, "grammar_overlays": []}
            if schema.name == "fixed-annotation-correction.schema.json":
                return correction
            if schema.name == "annotation-review.schema.json":
                return next(self.reviews)
            raise AssertionError(schema.name)

    runner = Runner()
    harness = constrained_delta_harness(runner)
    harness.args.annotation_review_policy = "chapter"
    result = await harness.annotate_chapter_constrained_delta(chapter, [chapter])
    assert harness.annotation_semantic_review_calls == 2
    assert len(runner.calls) == 4
    assert "".join(x["text"] for x in result[0]["segments"]) == chapter
    assert result[0]["segments"][0]["meaning_en"] == "eastward"
    assert [x["stage"] for x in result[0]["attempts"]] == [
        "chapter_delta_initial", "chapter_delta_correction",
    ]


@pytest.mark.asyncio
async def test_chapter_review_policy_fails_closed_after_final_revise():
    from pipeline.delta_boundary_annotation import (
        contextual_delta_targets, deterministic_baseline,
    )

    chapter = "东。"
    baseline, unknown = deterministic_baseline(chapter)
    required = contextual_delta_targets(baseline, unknown)
    revise = {"verdict": "revise", "issues": [{
        "start": 0, "end": 1, "segment_text": "东", "problem": "meaning",
        "explanation": "Wrong context.", "suggested_fix": "Correct it.",
    }]}

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            if schema.name == "delta-annotation.schema.json":
                return {"overrides": [{
                    "index": index, "type": "word",
                    "pinyin": baseline[index]["pinyin"] or "dōng",
                    "meaning_en": "eastward",
                } for index in sorted(required)], "grammar_overlays": []}
            if schema.name == "fixed-annotation-correction.schema.json":
                return {"patches": [], "grammar_overlays": []}
            return revise

    harness = constrained_delta_harness(Runner())
    harness.args.annotation_review_policy = "chapter"
    with pytest.raises(ValueError, match="did not cover every finding span"):
        await harness.annotate_chapter_constrained_delta(chapter, [chapter])
    assert harness.annotation_semantic_review_calls == 1


@pytest.mark.asyncio
async def test_issue_scoped_correction_discards_collateral_patch():
    chapter = "东西。"
    annotation = {"segments": [
        {"text": "东", "type": "word", "pinyin": "dōng", "meaning_en": "Dong surname"},
        {"text": "西", "type": "word", "pinyin": "xī", "meaning_en": "west"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 0, "end": 1, "segment_text": "东", "problem": "meaning",
        "explanation": "Wrong sense.", "suggested_fix": "east",
    }]}

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            return {"patches": [
                {"start_index": 0, "end_index": 1, "segments": [{
                    "text": "东", "type": "word", "pinyin": "dōng", "meaning_en": "east",
                }]},
                {"start_index": 1, "end_index": 2, "segments": [{
                    "text": "西", "type": "word", "pinyin": "xī", "meaning_en": "western",
                }]},
            ], "grammar_overlays": []}

    result, evidence = await constrained_delta_harness(Runner()).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="medium",
    )
    assert result["segments"][0]["meaning_en"] == "east"
    assert result["segments"][1]["meaning_en"] == "west"
    assert evidence["exact_coverage"] is True


@pytest.mark.asyncio
async def test_issue_scoped_correction_rejects_overlapping_finding_scopes():
    chapter = "甲乙。"
    annotation = {"segments": [
        {"text": "甲", "type": "word", "pinyin": "jiǎ", "meaning_en": "first"},
        {"text": "乙", "type": "word", "pinyin": "yǐ", "meaning_en": "second"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [
        {"start": 0, "end": 2, "segment_text": "甲乙", "problem": "under_grouped"},
        {"start": 1, "end": 2, "segment_text": "乙", "problem": "meaning"},
    ]}
    with pytest.raises(ValueError, match="conflicting token scopes"):
        await constrained_delta_harness(None).issue_scoped_chapter_correction(
            chapter, annotation, review, stage="test", effort="medium",
        )


@pytest.mark.asyncio
async def test_issue_scoped_under_grouping_allows_one_adjacent_token_and_retries_missing():
    chapter = "南华老仙。"
    annotation = {"segments": [
        {"text": "南华", "type": "name", "pinyin": "Nán Huá", "meaning_en": "Nanhua"},
        {"text": "老仙", "type": "word", "pinyin": "lǎo xiān", "meaning_en": "old immortal"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 0, "end": 2, "segment_text": "南华", "problem": "under_grouped",
    }]}

    class Runner:
        def __init__(self): self.calls = 0
        async def call(self, job, prompt, schema, effort, **kwargs):
            self.calls += 1
            if not job.endswith("_missing"):
                return {"patches": [], "grammar_overlays": []}
            return {"patches": [{"start_index": 0, "end_index": 2, "segments": [{
                "text": "南华老仙", "type": "name", "pinyin": "Nánhuá Lǎoxiān",
                "meaning_en": "Nanhua Old Immortal",
            }]}], "grammar_overlays": []}

    runner = Runner()
    result, evidence = await constrained_delta_harness(runner).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="medium",
    )
    assert [item["text"] for item in result["segments"]] == ["南华老仙", "。"]
    assert evidence["calls"] == 2 and evidence["exact_coverage"] is True


@pytest.mark.asyncio
async def test_issue_scoped_correction_isolates_finding_after_collateral_missing_retry():
    chapter = "符水治病。"
    annotation = {"segments": [
        {"text": "符", "type": "word", "pinyin": "fú", "meaning_en": "talisman"},
        {"text": "水", "type": "word", "pinyin": "shuǐ", "meaning_en": "water"},
        {"text": "治病", "type": "word", "pinyin": "zhì bìng", "meaning_en": "treat illness"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 0, "end": 2, "segment_text": "符水", "problem": "under_grouped",
        "suggested_fix": "Annotate 符水 as one word.",
    }]}

    class Runner:
        def __init__(self): self.calls = 0
        async def call(self, job, prompt, schema, effort, **kwargs):
            self.calls += 1
            if job.endswith("_missing_0000"):
                return {"patches": [{
                    "start_index": 0, "end_index": 2, "segments": [{
                        "text": "符水", "type": "word", "pinyin": "fú shuǐ",
                        "meaning_en": "talisman water",
                    }],
                }], "grammar_overlays": []}
            if job.endswith("_missing"):
                return {"patches": [{
                    "start_index": 2, "end_index": 3, "segments": [{
                        "text": "治病", "type": "word", "pinyin": "zhì bìng",
                        "meaning_en": "to treat illness",
                    }],
                }], "grammar_overlays": []}
            return {"patches": [], "grammar_overlays": []}

    runner = Runner()
    result, evidence = await constrained_delta_harness(
        runner
    ).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="low",
    )

    assert [item["text"] for item in result["segments"]] == ["符水", "治病", "。"]
    assert evidence["calls"] == 3 and evidence["exact_coverage"] is True


@pytest.mark.asyncio
async def test_issue_scoped_correction_retries_schema_valid_missing_coverage():
    chapter = "符水。"
    annotation = {"segments": [
        {"text": "符", "type": "word", "pinyin": "fú", "meaning_en": "talisman"},
        {"text": "水", "type": "word", "pinyin": "shuǐ", "meaning_en": "water"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 0, "end": 2, "segment_text": "符水", "problem": "under_grouped",
        "suggested_fix": "Annotate 符水 as one word.",
    }]}

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            if "coverage_retry_01" not in job:
                return {"patches": [], "grammar_overlays": []}
            return {"patches": [{
                "start_index": 0, "end_index": 2, "segments": [{
                    "text": "符水", "type": "word", "pinyin": "fú shuǐ",
                    "meaning_en": "talisman water",
                }],
            }], "grammar_overlays": []}

    result, evidence = await constrained_delta_harness(
        Runner(), max_repairs=1
    ).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="low",
    )

    assert [item["text"] for item in result["segments"]] == ["符水", "。"]
    assert evidence["calls"] == 4
    assert evidence["exact_coverage"] is True


@pytest.mark.asyncio
async def test_meaning_finding_can_authorize_explicit_resegmentation():
    chapter = "梁上飞下来。"
    annotation = {"segments": [
        {"text": "梁", "type": "word", "pinyin": "liáng", "meaning_en": "beam"},
        {"text": "上飞", "type": "word", "pinyin": "shàng fēi", "meaning_en": "fly up"},
        {"text": "下来", "type": "word", "pinyin": "xiàlái", "meaning_en": "come down"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 1, "end": 3, "segment_text": "上飞", "problem": "meaning",
        "suggested_fix": "Segment as 梁 + 上 + 飞下来.",
    }]}

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            return {"patches": [{
                "start_index": 1, "end_index": 3, "segments": [
                    {"text": "上", "type": "particle", "pinyin": "shàng", "meaning_en": "on"},
                    {"text": "飞下来", "type": "word", "pinyin": "fēi xiàlái", "meaning_en": "fly down"},
                ],
            }], "grammar_overlays": []}

    result, evidence = await constrained_delta_harness(
        Runner()
    ).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="low",
    )

    assert [item["text"] for item in result["segments"]] == [
        "梁", "上", "飞下来", "。",
    ]
    assert evidence["exact_coverage"] is True


@pytest.mark.asyncio
async def test_explicit_resegmentation_can_span_three_tiny_tokens():
    chapter = "退入长社。"
    annotation = {"segments": [
        {"text": "退", "type": "word", "pinyin": "tuì", "meaning_en": "retreat"},
        {"text": "入长", "type": "word", "pinyin": "rù cháng", "meaning_en": "enter long"},
        {"text": "社", "type": "word", "pinyin": "shè", "meaning_en": "society"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 2, "end": 4, "segment_text": "长社", "problem": "under_grouped",
        "suggested_fix": "Keep 长社 together as the place name after 退入.",
    }]}

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            return {"patches": [{
                "start_index": 0, "end_index": 3, "segments": [
                    {"text": "退入", "type": "word", "pinyin": "tuìrù", "meaning_en": "retreat into"},
                    {"text": "长社", "type": "name", "pinyin": "Chángshè", "meaning_en": "Changshe"},
                ],
            }], "grammar_overlays": []}

    result, evidence = await constrained_delta_harness(
        Runner()
    ).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="low",
    )

    assert [item["text"] for item in result["segments"]] == [
        "退入", "长社", "。",
    ]
    assert evidence["exact_coverage"] is True


@pytest.mark.asyncio
async def test_issue_scoped_narrows_context_inclusive_review_to_suggested_word():
    chapter = "宦官干政。"
    annotation = {"segments": [
        {"text": "宦官", "type": "word", "pinyin": "huàn guān", "meaning_en": "eunuch"},
        {"text": "干", "type": "word", "pinyin": "gān", "meaning_en": "interfere"},
        {"text": "政", "type": "word", "pinyin": "zhèng", "meaning_en": "government"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 0, "end": 4, "segment_text": "宦官干政", "problem": "under_grouped",
        "suggested_fix": "segment 干政 as one word",
    }]}

    class Runner:
        async def call(self, job, prompt, schema, effort, **kwargs):
            return {"patches": [{"start_index": 1, "end_index": 3, "segments": [{
                "text": "干政", "type": "word", "pinyin": "gān zhèng",
                "meaning_en": "interfere in government affairs",
            }]}], "grammar_overlays": []}

    result, evidence = await constrained_delta_harness(Runner()).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="high",
    )
    assert [item["text"] for item in result["segments"]] == ["宦官", "干政", "。"]
    assert evidence["valid_finding_spans"] == 1


@pytest.mark.asyncio
async def test_issue_scoped_correction_discards_reviewer_explicit_no_op():
    chapter = "刘备来了。"
    annotation = {"segments": [
        {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
        {"text": "来了", "type": "word", "pinyin": "lái le", "meaning_en": "arrived"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{
        "start": 0, "end": 2, "segment_text": "刘备", "problem": "meaning",
        "explanation": "The current annotation itself is fine.",
        "suggested_fix": "No change needed.",
    }]}

    result, evidence = await constrained_delta_harness(
        None
    ).issue_scoped_chapter_correction(
        chapter, annotation, review, stage="test", effort="low",
    )

    assert result == annotation
    assert evidence["calls"] == 0
    assert evidence["discarded_findings"] == [{
        "kind": "issue", "reason": "reviewer_explicit_no_op",
    }]


@pytest.mark.asyncio
async def test_final_revise_is_exactly_remediated_with_truthful_state():
    chapter = "东来。"
    seeded = {"segments": [
        {"text": "东", "type": "word", "pinyin": "dōng", "meaning_en": "Dong surname"},
        {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "arrive"},
        {"text": "。", "type": "punctuation", "pinyin": "", "meaning_en": ""},
    ], "grammar_overlays": []}
    initial = {"verdict": "revise", "issues": [{
        "start": 0, "end": 1, "segment_text": "东", "problem": "meaning",
    }]}
    final = {"verdict": "revise", "issues": [{
        "start": 1, "end": 2, "segment_text": "来", "problem": "meaning",
    }]}

    class Runner:
        def __init__(self): self.reviews = iter([initial, final])
        async def call(self, job, prompt, schema, effort, **kwargs):
            if schema.name == "annotation-review.schema.json":
                return next(self.reviews)
            if "remediation" in job:
                return {"patches": [{"start_index": 1, "end_index": 2, "segments": [{
                    "text": "来", "type": "word", "pinyin": "lái", "meaning_en": "to come",
                }]}], "grammar_overlays": []}
            return {"patches": [{"start_index": 0, "end_index": 1, "segments": [{
                "text": "东", "type": "word", "pinyin": "dōng", "meaning_en": "east",
            }]}], "grammar_overlays": []}

    harness = constrained_delta_harness(Runner())
    result = await harness.review_constrained_delta_chapter(chapter, [chapter], seeded)
    assert harness.annotation_acceptance_state == "reviewed_and_remediated"
    assert harness.annotation_final_review_verdict == "revise"
    assert harness.annotation_remediation_calls == 1
    assert result[0]["acceptance_state"] == "reviewed_and_remediated"
    assert result[0]["segments"][1]["meaning_en"] == "to come"


@pytest.mark.asyncio
async def test_constrained_delta_final_xhigh_self_heal_passes_after_medium_budget():
    initial = {"segments": [{
        "text": "刘备", "type": "name", "pinyin": "Liú Bèi",
        "meaning_en": "Liu Bei",
    }], "grammar_overlays": []}
    medium_patch = {"patches": [{
        "start_index": 0, "end_index": 1, "segments": [{
            "text": "刘备", "type": "name", "pinyin": "Liú Bèi",
            "meaning_en": "Liu Bei, a man",
        }],
    }], "grammar_overlays": []}
    final_patch = {"patches": [{
        "start_index": 0, "end_index": 1, "segments": [{
            "text": "刘备", "type": "name", "pinyin": "Liú Bèi",
            "meaning_en": "Liu Bei, leader of Shu",
        }],
    }], "grammar_overlays": []}
    revise = {"verdict": "revise", "issues": [{
        "segment_text": "刘备", "problem": "meaning",
        "explanation": "The gloss remains unclear.",
        "suggested_fix": "Identify him concisely.",
    }]}
    runner = FakeConstrainedRunner(
        [], [medium_patch, final_patch],
        [revise, revise, {"verdict": "pass", "issues": []}],
    )
    harness = constrained_delta_harness(runner, max_repairs=1)
    harness.args.annotation_repair_effort = "medium"

    result = await harness.review_constrained_delta_chunk(3, "刘备", initial)

    assert result["resolved"] is True and result["reviewed"] is True
    assert [item["stage"] for item in result["attempts"]] == [
        "delta_initial", "delta_correction_01", "delta_final_correction",
    ]
    assert result["attempts"][-1]["correction_effort"] == "xhigh"
    assert result["attempts"][-1]["review_effort"] == "xhigh"
    correction_calls = [
        call for call in runner.calls
        if call[1] == "fixed-annotation-correction.schema.json"
    ]
    review_calls = [
        call for call in runner.calls if call[1] == "annotation-review.schema.json"
    ]
    assert [call[2] for call in correction_calls] == ["medium", "xhigh"]
    assert [call[2] for call in review_calls] == ["medium", "medium", "xhigh"]
    assert correction_calls[-1][0].endswith("constrained_delta_final_correction")
    assert review_calls[-1][0].endswith("delta_final_review")


@pytest.mark.asyncio
async def test_constrained_delta_final_xhigh_review_still_fails_closed():
    initial = {"segments": [{
        "text": "刘备", "type": "name", "pinyin": "Liú Bèi",
        "meaning_en": "Liu Bei",
    }], "grammar_overlays": []}
    final_patch = {"patches": [], "grammar_overlays": []}
    revise = {"verdict": "revise", "issues": [{
        "segment_text": "刘备", "problem": "meaning",
        "explanation": "Still materially misleading.",
        "suggested_fix": "Correct the gloss.",
    }]}
    runner = FakeConstrainedRunner([], [final_patch], [revise, revise])
    harness = constrained_delta_harness(runner, max_repairs=0)

    with pytest.raises(ValueError, match="quality gate failed.*2 reviewed attempts"):
        await harness.review_constrained_delta_chunk(4, "刘备", initial)

    correction_calls = [
        call for call in runner.calls
        if call[1] == "fixed-annotation-correction.schema.json"
    ]
    review_calls = [
        call for call in runner.calls if call[1] == "annotation-review.schema.json"
    ]
    assert len(correction_calls) == 1
    assert correction_calls[0][2] == "xhigh"
    assert [call[2] for call in review_calls] == ["medium", "xhigh"]


@pytest.mark.asyncio
async def test_constrained_delta_review_catches_unchanged_bad_known_metadata():
    # Context-known targets may be omitted from the sparse seed response, but
    # omission is never acceptance: the independent chunk review still gates
    # the unchanged deterministic dictionary metadata.
    initial = {"segments": [{
        "text": "东", "type": "name", "pinyin": "Dōng",
        "meaning_en": "Dong (surname)",
    }], "grammar_overlays": []}
    revise = {"verdict": "revise", "issues": [{
        "start": 0, "end": 1, "segment_text": "东", "problem": "meaning",
        "explanation": "Here it means eastward, not a surname.",
        "suggested_fix": "Use eastward.",
    }]}
    runner = FakeConstrainedRunner(
        [], [{"patches": [], "grammar_overlays": []}], [revise, revise],
    )
    harness = constrained_delta_harness(runner, max_repairs=0)

    with pytest.raises(ValueError, match="quality gate failed"):
        await harness.review_constrained_delta_chunk(0, "东", initial)


def test_delta_partition_fails_when_segment_or_overlay_crosses_chunk():
    annotation = {
        "segments": [{"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"}],
        "grammar_overlays": [],
    }
    with pytest.raises(ValueError, match="segment crosses"):
        ChapterHarness.split_chapter_annotation("刘备", ["刘", "备"], annotation)


async def _run_mocked_annotated_chapter(tmp_path, final_review, mode="constrained"):
    source_path = tmp_path / "chapter_001.txt"
    source_path.write_text("玄德见榜。", encoding="utf-8")
    run_dir = tmp_path / "chapter_001-hsk4"
    run_dir.mkdir()
    harness = object.__new__(ChapterHarness)
    harness.source_path = source_path
    harness.source = source_path.read_text(encoding="utf-8")
    harness.run_dir = run_dir
    harness.args = Namespace(
        level="hsk4", skip_annotations=False, annotation_chunk=200,
        annotation_mode=mode,
    )
    harness.write_manifest = lambda _status: None

    async def fake_outline():
        return {"chapter_title": "桃园结义", "scenes": [{"id": "scene_01"}]}

    async def fake_scene(_scene):
        return {
            "scene": {"id": "scene_01"}, "text": "刘备来了。",
            "review": {"verdict": "pass"}, "attempts": [{"stage": "adapt"}],
        }

    async def fake_annotation(_index, chunk):
        assert chunk == "刘备来了。\n"
        segments = [
            {"text": "刘备", "type": "name", "pinyin": "Liú Bèi", "meaning_en": "Liu Bei"},
            {"text": "来", "type": "word", "pinyin": "lái", "meaning_en": "come"},
            {"text": "了", "type": "particle", "pinyin": "le", "meaning_en": "completed-action marker"},
            {"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
        ]
        return {
            "segments": segments, "grammar_overlays": [], "resolved": True,
            "attempts": [{"stage": "initial", "annotation": {
                "segments": segments, "grammar_overlays": [],
            }, "review": final_review}],
        }

    harness.outline = fake_outline
    async def fake_focus_vocabulary(_outline):
        harness.focus_vocabulary = {"names": [], "story_terms": []}
        return harness.focus_vocabulary
    harness.plan_focus_vocabulary = fake_focus_vocabulary
    harness.process_scene = fake_scene
    harness.annotate_chunk = fake_annotation
    async def fake_delta(chapter, chunks):
        assert chapter == "刘备来了。\n" and chunks == [chapter]
        return [await fake_annotation(0, chapter)]
    harness.annotate_chapter_constrained_delta = fake_delta
    await harness.run()
    (run_dir / "outline.json").write_text(json.dumps({
        "chapter_title": "桃园结义", "scenes": [{"id": "scene_01"}],
    }), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps({
        "status": "complete", "level": "hsk4", "source": str(source_path.resolve()),
        "source_sha256": digest(source_path.read_text(encoding="utf-8")),
    }), encoding="utf-8")
    reader_path = run_dir / "reader.json"
    (run_dir / "annotation-report.json").write_text(json.dumps({
        "status": "complete",
        "chapter_sha256": __import__("hashlib").sha256(
            (run_dir / "chapter.txt").read_bytes()
        ).hexdigest(),
        "reader_sha256": __import__("hashlib").sha256(reader_path.read_bytes()).hexdigest(),
        "chunks": 1,
    }), encoding="utf-8")
    source = {
        "path": source_path.resolve(), "file": source_path.name,
        "sha256": __import__("hashlib").sha256(source_path.read_bytes()).hexdigest(),
    }
    return run_dir, source


@pytest.mark.asyncio
async def test_mocked_constrained_reader_is_publisher_compatible(tmp_path):
    from pipeline.publish_sanguoyanyi import _audit_chapter

    run_dir, source = await _run_mocked_annotated_chapter(
        tmp_path, {"verdict": "pass", "issues": []}
    )
    audited = _audit_chapter(run_dir, source, "hsk4", 1, True)

    assert audited["text"] == "刘备来了。\n"
    assert audited["annotation_audit"]["mode"] == "constrained"
    assert audited["annotation_audit"]["all_reviewed"] is True
    assert "".join(item["text"] for item in audited["segments"]) == audited["text"]


@pytest.mark.asyncio
async def test_mocked_constrained_delta_reader_records_truthful_one_call_audit(tmp_path):
    from pipeline.publish_sanguoyanyi import _audit_chapter

    run_dir, source = await _run_mocked_annotated_chapter(
        tmp_path, {"verdict": "pass", "issues": []}, "constrained-delta"
    )
    audited = _audit_chapter(run_dir, source, "hsk4", 1, True)
    assert audited["annotation_audit"]["mode"] == "constrained-delta"
    assert audited["annotation_audit"]["metadata_model_calls"] == 1
    assert audited["annotation_audit"]["all_reviewed"] is True


@pytest.mark.asyncio
async def test_annotation_audit_does_not_claim_reviewed_for_last_revise(tmp_path):
    from pipeline.publish_sanguoyanyi import PublicationError, _audit_chapter

    run_dir, source = await _run_mocked_annotated_chapter(
        tmp_path, {"verdict": "revise", "issues": []}
    )
    reader = json.loads((run_dir / "reader.json").read_text(encoding="utf-8"))
    assert reader["annotation_audit"]["all_reviewed"] is False
    with pytest.raises(PublicationError, match="not all reviewed"):
        _audit_chapter(run_dir, source, "hsk4", 1, True)


def test_level_targets_increase_strictly():
    values = [DEFAULT_LEVEL_TARGETS[f"hsk{i}"] for i in range(1, 7)]
    assert values == sorted(values)
    assert len(values) == len(set(values))


@pytest.mark.parametrize("level, scenes", [
    ("hsk1", 1), ("hsk2", 1), ("hsk3", 1),
    ("hsk4", 1), ("hsk5", 1), ("hsk6", 1),
])
def test_scene_count_scales_to_short_chapter_targets(level, scenes):
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(level=level, target_chars=None)
    assert harness.scene_count == scenes


@pytest.mark.asyncio
async def test_single_scene_outline_does_not_require_exact_first_anchor(tmp_path):
    harness = object.__new__(ChapterHarness)
    harness.args = Namespace(level="hsk4", target_chars=None, review_effort="medium", refresh=False)
    harness.source = "卻說闕澤字德潤，會稽山陰人也。"
    harness.run_dir = tmp_path
    harness.runner = FakeOutlineRunner({
        "chapter_title": "闞澤密獻詐書",
        "scenes": [{
            "id": "scene_01",
            "title": "闞澤",
            "source_start_quote": "卻說闞澤字德潤",
            "required_events": ["闞澤獻書"],
            "target_chars": 750,
        }],
    })

    outline = await harness.outline()

    assert outline["scenes"][0]["source_start"] == 0
    assert outline["scenes"][0]["source_end"] == len(harness.source)


def test_length_audit_groups_chapters_and_flags_non_increase():
    runs = [
        {"source": "/a", "level": "hsk4", "status": "complete", "chapter_cjk": 800},
        {"source": "/b", "level": "hsk4", "status": "complete", "chapter_cjk": 700},
        {"source": "/a", "level": "hsk5", "status": "complete", "chapter_cjk": 800},
        {"source": "/b", "level": "hsk5", "status": "complete", "chapter_cjk": 900},
    ]
    assert length_violations(runs, ["hsk4", "hsk5"]) == [{
        "source": "/a", "lower_level": "hsk4", "lower_cjk": 800,
        "upper_level": "hsk5", "upper_cjk": 800,
    }]


def test_book_parser_has_global_and_chapter_concurrency():
    args = parser().parse_args([
        "book", "--source", "chapter-1.txt", "--source", "chapter-2.txt",
        "--book-run-id", "book", "--concurrency", "9", "--chapter-concurrency", "3",
    ])
    assert args.source == ["chapter-1.txt", "chapter-2.txt"]
    assert args.concurrency == 9
    assert args.chapter_concurrency == 3
    assert args.levels == ["hsk1", "hsk2", "hsk3", "hsk4", "hsk5", "hsk6"]


@pytest.mark.asyncio
async def test_book_automatically_reruns_upper_level_for_length(monkeypatch):
    harness = object.__new__(BookHarness)
    harness.args = Namespace(
        source=["chapter.txt"], levels=["hsk4", "hsk5"],
        length_repair_rounds=2,
    )
    harness.write_report = lambda *args, **kwargs: None
    calls = []

    async def fake_run_one(source, level, target_chars=None):
        calls.append((level, target_chars))
        length = 800 if level == "hsk4" else (750 if target_chars is None else 900)
        return {
            "source": str((tmp_path := __import__("pathlib").Path(source)).resolve()),
            "level": level, "status": "complete", "chapter_cjk": length,
        }

    monkeypatch.setattr(harness, "run_one", fake_run_one)
    result = await harness.run()
    assert result["status"] == "complete"
    assert calls[-1][0] == "hsk5"
    assert calls[-1][1] > 800


@pytest.mark.asyncio
async def test_length_repair_target_grows_when_first_rerun_still_undershoots(monkeypatch):
    harness = object.__new__(BookHarness)
    harness.args = Namespace(
        source=["chapter.txt"], levels=["hsk4", "hsk5"],
        length_repair_rounds=2,
    )
    harness.write_report = lambda *args, **kwargs: None
    calls = []

    async def fake_run_one(source, level, target_chars=None):
        calls.append((level, target_chars))
        if level == "hsk4":
            length = 1000
        else:
            length = 900 if target_chars is None else 950
        return {
            "source": str(__import__("pathlib").Path(source).resolve()),
            "level": level, "status": "complete", "chapter_cjk": length,
            "target_chars": target_chars or DEFAULT_LEVEL_TARGETS[level],
        }

    monkeypatch.setattr(harness, "run_one", fake_run_one)
    result = await harness.run()
    repair_targets = [target for level, target in calls if level == "hsk5" and target]
    assert len(repair_targets) == 2
    assert repair_targets[1] > repair_targets[0]
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_book_retries_blocked_chapter_with_fresh_jobs(monkeypatch, tmp_path):
    source = tmp_path / "chapter_001.txt"
    source.write_text("原文", encoding="utf-8")
    harness = object.__new__(BookHarness)
    harness.args = Namespace(
        book_run_id="book", runs_dir=str(tmp_path), target_chars=None,
        chapter_retries=1, refresh=False,
    )
    harness.agent_semaphore = __import__("asyncio").Semaphore(1)
    harness.chapter_semaphore = __import__("asyncio").Semaphore(1)
    refresh_values = []

    class FakeChapterHarness:
        def __init__(self, args, semaphore=None):
            refresh_values.append(args.refresh)

        async def run(self):
            if len(refresh_values) == 1:
                return {
                    "status": "blocked", "chapter_cjk": 10,
                    "unresolved_scenes": ["scene_01"],
                }
            return {"status": "complete", "chapter_cjk": 12}

    monkeypatch.setattr("pipeline.agent_harness.ChapterHarness", FakeChapterHarness)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.sleep", lambda *_: _noop())

    result = await harness.run_one(str(source), "hsk1")
    assert result["status"] == "complete"
    assert result["attempts"] == 2
    assert refresh_values == [False, True]


async def _noop():
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_prompt', [False, True])
async def test_worker_policy_change_retains_exact_cache_only_evidence(tmp_path, changed_prompt):
    from pipeline.agent_harness import CachedCallUnavailable
    schema = tmp_path / 'schema.json'
    schema.write_text('{"type":"object"}')
    job = tmp_path / 'agents/review'
    job.mkdir(parents=True)
    result = {'approved': True, 'issues': []}
    (job / 'result.json').write_text(json.dumps(result))
    meta = {'model': 'gpt-6.1-sol', 'effort': 'high', 'return_code': 0,
        'fingerprint': digest('same context', schema.read_text(), 'gpt-6.1-sol', 'high')}
    (job / 'meta.json').write_text(json.dumps(meta))
    runner = CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1))
    if changed_prompt:
        with pytest.raises(CachedCallUnavailable):
            await runner.call('review', 'changed context', schema, 'low', cache_only=True)
    else:
        assert await runner.call('review', 'same context', schema, 'low', cache_only=True) == result
        assert json.loads((job / 'meta.json').read_text()) == meta


@pytest.mark.asyncio
@pytest.mark.parametrize('requested_effort', ['low', 'high'])
@pytest.mark.parametrize('benchmark_effort', [None, 'low', 'medium', 'high'])
async def test_codex_runner_uses_direct_log_files(monkeypatch, tmp_path, requested_effort, benchmark_effort):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    observed = {}

    class FakeStdin:
        def write(self, value):
            observed["prompt"] = value
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 12345
        returncode = 0
        stdin = FakeStdin()
        async def wait(self):
            return 0

    async def fake_exec(*command, **kwargs):
        observed.update(kwargs)
        observed['command'] = command
        output = command[command.index("-o") + 1]
        Path(command[command.index("-C") + 1], "candidate.json").write_text("{}")
        Path(output).write_text('{"candidate_path":"candidate.json"}')
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    runner = CodexRunner(tmp_path, "model", asyncio.Semaphore(1), 10, benchmark_effort=benchmark_effort)
    assert await runner.call("job", "hello", schema, requested_effort) == {}
    assert observed['command'][observed['command'].index('-m') + 1] == 'gpt-6-luna'
    effective_effort = benchmark_effort or 'low'
    assert f'model_reasoning_effort="{effective_effort}"' in observed['command']
    assert observed["stdout"] != asyncio.subprocess.PIPE
    assert observed["stderr"] != asyncio.subprocess.PIPE
    assert observed["start_new_session"] is True
    assert not any('shell_tool=false' in part or 'mcp_servers={}' in part for part in observed['command'])
    assert observed['command'][observed['command'].index('-C') + 1] == str(tmp_path / 'agents/job/workspace')
    assert (tmp_path / "agents/job/meta.json").is_file()
    meta = json.loads((tmp_path / 'agents/job/meta.json').read_text())
    assert (meta['model'], meta['effort']) == ('gpt-6-luna', benchmark_effort or 'low')


@pytest.mark.asyncio
async def test_codex_runner_launch_timeout_retries_once_then_succeeds(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = 0

    class FakeStdin:
        def write(self, _value): pass
        async def drain(self): pass
        def close(self): pass

    class FakeProcess:
        pid = 12345
        returncode = 0
        stdin = FakeStdin()
        async def wait(self): return 0

    async def fake_exec(*command, **kwargs):
        nonlocal launches
        launches += 1
        if launches == 1:
            await asyncio.Event().wait()
        Path(command[command.index("-o") + 1]).write_text("{}")
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.sleep", lambda *_: _noop())
    semaphore = asyncio.Semaphore(1)
    runner = CodexRunner(tmp_path, "model", semaphore, 10,
                         launch_timeout_seconds=0.01, launch_backoff_seconds=0, legacy_tool_restrictions=True)
    assert await runner.call("job", "hello", schema, "low") == {}
    attempts = json.loads((tmp_path / "agents/job/attempts.json").read_text())
    assert launches == 2 and attempts[0]["launch_timeout"] is True
    assert attempts[0]["return_code"] is None and attempts[1]["return_code"] == 0
    assert semaphore._value == 1


@pytest.mark.asyncio
async def test_codex_runner_permanent_launch_timeout_fails_closed(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')

    async def hanging_exec(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", hanging_exec)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.sleep", lambda *_: _noop())
    semaphore = asyncio.Semaphore(1)
    runner = CodexRunner(tmp_path, "model", semaphore, 10,
                         launch_timeout_seconds=0.01, launch_backoff_seconds=0, legacy_tool_restrictions=True)
    with pytest.raises(RuntimeError, match="launch timed out"):
        await runner.call("job", "hello", schema, "low")
    job = tmp_path / "agents/job"
    meta = json.loads((job / "meta.json").read_text())
    attempts = json.loads((job / "attempts.json").read_text())
    assert meta["error"] == "launch_timeout" and meta["return_code"] is None
    assert len(attempts) == 2 and all(item["launch_timeout"] for item in attempts)
    assert not (job / "result.json").exists() and semaphore._value == 1
    # File handles were closed when each timed-out launch was cancelled.
    for path in job.glob("*.log"):
        with path.open("ab") as handle:
            handle.write(b"")


@pytest.mark.asyncio
async def test_codex_runner_cache_fingerprint_invalidates_changed_boundary_prompt(
    monkeypatch, tmp_path
):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = []

    class FakeStdin:
        def write(self, value):
            launches[-1]["prompt"] = value.decode()
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 12345
        returncode = 0
        def __init__(self):
            self.stdin = FakeStdin()
        async def wait(self):
            return 0

    async def fake_exec(*command, **kwargs):
        launches.append({})
        output = command[command.index("-o") + 1]
        __import__("pathlib").Path(output).write_text("{}")
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    runner = CodexRunner(tmp_path, "model", asyncio.Semaphore(1), 10, legacy_tool_restrictions=True)
    await runner.call("same-job", "proposal boundaries: [1]", schema, "low")
    first = json.loads((tmp_path / "agents/same-job/meta.json").read_text())["fingerprint"]
    await runner.call("same-job", "proposal boundaries: [1]", schema, "low")
    assert len(launches) == 1
    await runner.call("same-job", "proposal boundaries: [1, 2]", schema, "low")
    second = json.loads((tmp_path / "agents/same-job/meta.json").read_text())["fingerprint"]
    assert len(launches) == 2
    assert first != second


@pytest.mark.asyncio
async def test_queued_cache_mismatch_preserves_result_until_launch_permit(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    job = tmp_path / "agents" / "job"
    job.mkdir(parents=True)
    (job / "result.json").write_text('{"old":true}')
    (job / "meta.json").write_text(json.dumps({
        "fingerprint": "stale", "return_code": 0,
    }))
    semaphore = asyncio.Semaphore(0)
    runner = CodexRunner(tmp_path, "model", semaphore, 10)
    task = asyncio.create_task(runner.call("job", "new prompt", schema, "low"))
    await asyncio.sleep(0)
    assert json.loads((job / "result.json").read_text()) == {"old": True}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert json.loads((job / "result.json").read_text()) == {"old": True}


@pytest.mark.asyncio
async def test_codex_runner_retries_explicit_429_without_holding_semaphore(
    monkeypatch, tmp_path
):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = []
    sleeps = []
    semaphore = asyncio.Semaphore(1)

    class FakeStdin:
        def write(self, value):
            pass
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 12345
        stdin = FakeStdin()
        def __init__(self, returncode):
            self.returncode = returncode
        async def wait(self):
            return self.returncode

    async def fake_exec(*command, **kwargs):
        launches.append(command)
        if len(launches) == 1:
            kwargs["stderr"].write(b"request failed: HTTP status code: 429\n")
            return FakeProcess(1)
        Path(command[command.index("-o") + 1]).write_text('{"ok":true}')
        return FakeProcess(0)

    async def fake_sleep(delay):
        # Backoff must not consume one of the global model-call permits.
        assert not semaphore.locked()
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.sleep", fake_sleep)
    runner = CodexRunner(
        tmp_path, "model", semaphore, 10,
        max_throttle_retries=2, throttle_backoff_seconds=0.25,
     legacy_tool_restrictions=True)
    assert await runner.call("job", "hello", schema, "low") == {"ok": True}
    assert len(launches) == 2
    assert sleeps == [0.25]
    meta = json.loads((tmp_path / "agents/job/meta.json").read_text())
    assert meta["attempt_count"] == 2
    assert meta["attempts"][0]["throttled"] is True
    assert (tmp_path / "agents/job/stderr.attempt-01.log").is_file()
    assert (tmp_path / "agents/job/stderr.attempt-02.log").is_file()


def test_codex_runner_recognizes_exact_selected_model_capacity_message(tmp_path):
    log = tmp_path / "stderr.log"
    log.write_text("Selected model is at capacity. Please try a different model.\n")
    assert CodexRunner._is_explicit_throttle(log)


@pytest.mark.asyncio
async def test_codex_runner_bounds_repeated_throttle_retries(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = []
    sleeps = []

    class FakeStdin:
        def write(self, value):
            pass
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 12345
        returncode = 1
        stdin = FakeStdin()
        async def wait(self):
            return 1

    async def fake_exec(*command, **kwargs):
        launches.append(command)
        kwargs["stdout"].write(b'{"error":"ThrottlerException"}\n')
        return FakeProcess()

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.sleep", fake_sleep)
    runner = CodexRunner(
        tmp_path, "model", asyncio.Semaphore(1), 10,
        max_throttle_retries=2, throttle_backoff_seconds=1,
     legacy_tool_restrictions=True)
    with pytest.raises(RuntimeError, match="agent failed"):
        await runner.call("job", "hello", schema, "low")
    assert len(launches) == 3
    assert sleeps == [1, 2]
    meta = json.loads((tmp_path / "agents/job/meta.json").read_text())
    assert meta["return_code"] == 1
    assert meta["attempt_count"] == 3


@pytest.mark.asyncio
async def test_codex_runner_does_not_retry_non_throttle_failure(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = []

    class FakeStdin:
        def write(self, value):
            pass
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 12345
        returncode = 1
        stdin = FakeStdin()
        async def wait(self):
            return 1

    async def fake_exec(*command, **kwargs):
        launches.append(command)
        kwargs["stderr"].write(b"output failed JSON schema validation\n")
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    runner = CodexRunner(tmp_path, "model", asyncio.Semaphore(1), 10, legacy_tool_restrictions=True)
    with pytest.raises(RuntimeError, match="agent failed"):
        await runner.call("job", "hello", schema, "low")
    assert len(launches) == 1
    meta = json.loads((tmp_path / "agents/job/meta.json").read_text())
    assert meta["attempt_count"] == 1
    assert meta["attempts"][0]["throttled"] is False


@pytest.mark.asyncio
async def test_codex_runner_timeout_kills_process_group_and_retries_once(
    monkeypatch, tmp_path
):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = []
    killed = []

    class FakeStdin:
        def write(self, value):
            pass
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 24680
        returncode = None
        stdin = FakeStdin()
        async def wait(self):
            return self.returncode

    async def fake_exec(*command, **kwargs):
        launches.append(command)
        return FakeProcess()

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.wait_for", fake_wait_for)
    monkeypatch.setattr(
        "pipeline.agent_harness.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )
    runner = CodexRunner(tmp_path, "model", asyncio.Semaphore(1), 10, legacy_tool_restrictions=True)
    with pytest.raises(RuntimeError, match="agent timed out"):
        await runner.call("job", "hello", schema, "low")
    assert len(launches) == 2
    assert killed == [(24680, __import__("signal").SIGKILL)] * 2
    meta = json.loads((tmp_path / "agents/job/meta.json").read_text())
    assert meta["error"] == "process_timeout"
    assert meta["attempt_count"] == 2
    assert all(item["process_timeout"] for item in meta["attempts"])


@pytest.mark.asyncio
async def test_codex_runner_process_timeout_retry_can_succeed(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = []
    killed = []

    class FakeStdin:
        def write(self, value):
            pass
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 13579
        stdin = FakeStdin()
        def __init__(self, returncode=None):
            self.returncode = returncode
        async def wait(self):
            return self.returncode

    async def fake_exec(*command, **kwargs):
        launches.append(command)
        if len(launches) == 2:
            kwargs["stdout"].write(b'{"type":"result"}\n')
            (tmp_path / "agents/job/result.json").write_text('{"ok":true}')
            return FakeProcess(0)
        return FakeProcess()

    real_wait_for = asyncio.wait_for
    waits = 0
    async def fake_wait_for(awaitable, timeout):
        nonlocal waits
        waits += 1
        if waits == 1:
            awaitable.close()
            raise TimeoutError
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.wait_for", fake_wait_for)
    monkeypatch.setattr(
        "pipeline.agent_harness.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )
    runner = CodexRunner(tmp_path, "model", asyncio.Semaphore(1), 10, legacy_tool_restrictions=True)
    assert await runner.call("job", "hello", schema, "low") == {"ok": True}
    assert len(launches) == 2
    assert killed == [(13579, __import__("signal").SIGKILL)]
    attempts = json.loads((tmp_path / "agents/job/attempts.json").read_text())
    assert attempts[0]["process_timeout"] is True
    assert attempts[1]["return_code"] == 0


@pytest.mark.asyncio
async def test_codex_runner_stdin_drain_timeout_uses_process_retry(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launches = []
    killed = []

    class FakeStdin:
        def __init__(self, hangs):
            self.hangs = hangs
        def write(self, _value):
            pass
        async def drain(self):
            if self.hangs:
                await asyncio.Event().wait()
        def close(self):
            pass

    class FakeProcess:
        pid = 97531
        def __init__(self, hangs, returncode):
            self.stdin = FakeStdin(hangs)
            self.returncode = returncode
        async def wait(self):
            return self.returncode

    async def fake_exec(*command, **kwargs):
        launches.append(command)
        if len(launches) == 1:
            return FakeProcess(True, None)
        kwargs["stdout"].write(b'{"type":"result"}\n')
        (tmp_path / "agents/job/result.json").write_text('{"ok":true}')
        return FakeProcess(False, 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(
        "pipeline.agent_harness.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )
    runner = CodexRunner(tmp_path, "model", asyncio.Semaphore(1), 0.01, legacy_tool_restrictions=True)
    assert await runner.call("job", "hello", schema, "low") == {"ok": True}
    assert len(launches) == 2
    assert killed == [(97531, __import__("signal").SIGKILL)]
    attempts = json.loads((tmp_path / "agents/job/attempts.json").read_text())
    assert attempts[0]["process_timeout"] is True
    assert attempts[1]["return_code"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_tools', [False, True])
async def test_codex_runner_recovers_schema_valid_result_when_wrapper_times_out(
    monkeypatch, tmp_path, legacy_tools
):
    schema = tmp_path / "schema.json"
    schema.write_text(
        '{"type":"object","properties":{"ok":{"type":"boolean"}},'
        '"required":["ok"],"additionalProperties":false}'
    )
    launches = []
    killed = []

    class FakeStdin:
        def write(self, _value):
            pass
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 86420
        returncode = None
        stdin = FakeStdin()
        async def wait(self):
            return self.returncode

    async def fake_exec(*command, **kwargs):
        launches.append(command)
        output = Path(command[command.index("-o") + 1])
        message_text = '{"ok":true}'
        if not legacy_tools:
            Path(command[command.index('-C') + 1], 'candidate.json').write_text(message_text)
            message_text = '{"candidate_path":"candidate.json"}'
        output.write_text(message_text)
        message = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": message_text},
        }
        kwargs["stdout"].write((json.dumps(message) + "\n").encode())
        return FakeProcess()

    real_wait_for = asyncio.wait_for
    waits = 0
    async def fake_wait_for(awaitable, timeout):
        nonlocal waits
        waits += 1
        if waits == 2:
            awaitable.close()
            raise TimeoutError
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("pipeline.agent_harness.asyncio.wait_for", fake_wait_for)
    monkeypatch.setattr(
        "pipeline.agent_harness.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )
    runner = CodexRunner(tmp_path, "model", asyncio.Semaphore(1), 10, legacy_tool_restrictions=legacy_tools)
    assert await runner.call("job", "hello", schema, "low") == {"ok": True}
    assert len(launches) == 1
    assert killed == [(86420, __import__("signal").SIGKILL)]
    meta = json.loads((tmp_path / "agents/job/meta.json").read_text())
    assert meta["return_code"] == 0
    assert meta["recovered_after_process_timeout"] is True
    assert meta["attempts"][0]["recovered_completed_result"] is True
