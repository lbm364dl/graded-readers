from argparse import Namespace
import asyncio
import json

import pytest

from pipeline.japanese_agent_harness import (
    CHAPTER_TARGETS, DEFAULT_JLPT_TARGETS, JLPT_LEVELS,
    JapaneseBookHarness, JapaneseChapterHarness,
    apply_reader_useful_annotation_review_policy, japanese_char_count, parser,
    resolve_source_boundary, strip_duplicate_source_header, target_for_source,
)
from pipeline.japanese_agent_harness import japanese_segment_issue
from pipeline.agent_harness import digest


def test_jlpt_defaults_and_book_totals_increase_strictly():
    defaults = [DEFAULT_JLPT_TARGETS[level] for level in JLPT_LEVELS]
    totals = [sum(CHAPTER_TARGETS[level]) for level in JLPT_LEVELS]
    assert defaults == sorted(defaults) and len(defaults) == len(set(defaults))
    assert totals == [18000, 30000, 48000, 72000, 105000]
    assert all(
        CHAPTER_TARGETS[lower][chapter] < CHAPTER_TARGETS[upper][chapter]
        for lower, upper in zip(JLPT_LEVELS, JLPT_LEVELS[1:])
        for chapter in range(11)
    )


def test_target_for_known_chapter_and_generic_source():
    assert target_for_source("chapter_02.txt", "n1") == 11200
    assert target_for_source("preface.txt", "n1") == DEFAULT_JLPT_TARGETS["n1"]


def test_japanese_count_includes_kana_and_kanji_not_punctuation_or_latin():
    assert japanese_char_count("吾輩は猫である。ABC カタカナ！") == 11
    assert japanese_char_count("時々") == 2


def test_duplicate_extracted_chapter_header_is_stripped_only_at_start():
    assert strip_duplicate_source_header("第11章　十一\n\n吾輩は猫。") == "吾輩は猫。"
    assert strip_duplicate_source_header("吾輩は第11章を読む。") == "吾輩は第11章を読む。"


def test_source_boundary_snaps_unique_single_character_drift():
    source = "前段。主人は腹這いになって春日に甲羅を干している。後段。"
    quote = "主人は腹這になって春日に甲羅を干している。"
    assert resolve_source_boundary(source, quote) == source.index("主人は")


def test_source_boundary_rejects_ambiguous_or_material_mismatch():
    repeated = "主人は腹這いになって春日に甲羅を干す。別。主人は腹這いになって春日に甲羅を干す。"
    drifted = "主人は腹這になって春日に甲羅を干す。"
    assert resolve_source_boundary(repeated, drifted) == -1
    assert resolve_source_boundary("主人は庭で静かに本を読んでいる。", "主人は部屋で大声で歌っている。") == -1


def test_book_parser_defaults_to_all_jlpt_levels():
    args = parser().parse_args([
        "book", "--source-dir", "chapters", "--book-run-id", "wagahai",
    ])
    assert args.levels == JLPT_LEVELS
    assert args.concurrency == 12
    assert args.chapter_concurrency == 6
    assert args.source_dir == "chapters"


def test_large_chapter_target_splits_under_japanese_scene_limit(tmp_path):
    source = tmp_path / "chapter_11.txt"
    source.write_text("原文", encoding="utf-8")
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n1", target_chars=None)
    harness.source_path = source
    harness.source = source.read_text(encoding="utf-8")
    assert harness.target_chars == 11850
    assert harness.scene_count == 7


def test_long_source_forces_event_units_even_for_short_n5_output(tmp_path):
    source = tmp_path / "chapter_02.txt"
    source.write_text("猫" * 46322, encoding="utf-8")
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n5", target_chars=None)
    harness.source_path, harness.source = source, source.read_text()
    assert harness.target_chars == 1900
    assert harness.scene_count == 6


class CapturingRunner:
    def __init__(self):
        self.call_args = None

    async def call(self, *args, **kwargs):
        self.call_args = (args, kwargs)
        return {"segments": [{
            "surface": "猫", "type": "word", "lemma": "猫", "kana": "ねこ",
            "meaning_en": "cat",
        }], "grammar_overlays": []}


@pytest.mark.asyncio
async def test_japanese_annotation_requests_reading_and_dictionary_segmentation():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_effort="low", refresh=False)
    harness.runner = CapturingRunner()
    result = await harness.annotation_candidate(0, "猫")
    prompt = harness.runner.call_args[0][1]
    schema = harness.runner.call_args[0][2]
    assert result["segments"][0]["kana"] == "ねこ"
    assert "dictionary words" in prompt
    assert "never for an ordinary phrase or clause" in prompt
    assert schema.name == "japanese-annotation.schema.json"


def test_annotation_review_policy_ignores_pos_only_disputes():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "で", "problem": "wrong_type",
            "explanation": "This may be labelled a copular auxiliary, not a particle.",
            "suggested_fix": "Change only the POS label.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_ignores_explicitly_equivalent_grouping_only():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "では", "problem": "under_grouped",
            "explanation": "Splitting で and は is an equally valid segmentation.",
            "suggested_fix": "Either analysis is acceptable.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review)["verdict"] == "pass"


@pytest.mark.parametrize("problem", [
    "reconstruction", "reading", "meaning", "lemma", "grammar",
])
def test_annotation_review_policy_keeps_reader_harming_issues_blocking(problem):
    issue = {
        "segment_text": "猫", "problem": problem,
        "explanation": "The learner-facing annotation is incorrect.",
        "suggested_fix": "Correct it.",
    }
    accepted = apply_reader_useful_annotation_review_policy({
        "verdict": "revise", "issues": [issue],
    })
    assert accepted == {"verdict": "revise", "issues": [issue]}


def test_annotation_review_policy_keeps_misleading_grouping_blocking():
    issue = {
        "segment_text": "普通の長い文", "problem": "over_grouped",
        "explanation": "An ordinary clause was grouped as one word.",
        "suggested_fix": "Split it into dictionary words and particles.",
    }
    accepted = apply_reader_useful_annotation_review_policy({
        "verdict": "revise", "issues": [issue],
    })
    assert accepted["verdict"] == "revise"
    assert accepted["issues"] == [issue]


def test_annotation_contract_validates_segments_and_overlay_offsets():
    good = {
        "segments": [
            {"surface": "猫", "type": "word", "lemma": "猫", "kana": "ねこ", "meaning_en": "cat"},
            {"surface": "なら", "type": "particle", "lemma": "なら", "kana": "なら", "meaning_en": "if"},
        ],
        "grammar_overlays": [{
            "start": 1, "end": 3, "surface": "なら", "grammar": "Nなら",
            "meaning_en": "if it is N",
        }],
    }
    assert JapaneseChapterHarness.annotation_reconstructs("猫なら", good)
    bad = {**good, "grammar_overlays": [{**good["grammar_overlays"][0], "surface": "ならば"}]}
    assert not JapaneseChapterHarness.annotation_reconstructs("猫なら", bad)


def test_japanese_canonicalizer_repairs_unique_overlay_offset_drift():
    text = "彼が騙した話を読まれていたらと心配すると言った。\n"
    candidate = {
        "segments": [{
            "surface": text.rstrip("\n"), "type": "word", "lemma": "言う",
            "kana": "かれがだましたはなしをよまれていたらとしんぱいするといった",
            "meaning_en": "placeholder segmentation",
        }],
        "grammar_overlays": [
            {"start": 2, "end": 8, "surface": "騙した話", "grammar": "VたN",
             "meaning_en": "the story that someone told deceptively"},
            {"start": 9, "end": 24, "surface": "読まれていたらと心配する",
             "grammar": "Vられたらと心配する", "meaning_en": "worry that it might be read"},
        ],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        text, candidate
    )
    assert normalized["segments"][-1]["surface"] == "\n"
    for overlay in normalized["grammar_overlays"]:
        assert text[overlay["start"]:overlay["end"]] == overlay["surface"]


def test_annotation_contract_rejects_romaji_empty_meaning_and_clause_word():
    base = {"grammar_overlays": []}
    for segment in (
        {"surface": "猫", "type": "word", "lemma": "猫", "kana": "neko", "meaning_en": "cat"},
        {"surface": "猫", "type": "word", "lemma": "猫", "kana": "ねこ", "meaning_en": ""},
        {"surface": "これは普通の長い文節ですから", "type": "word", "lemma": "文節", "kana": "ぶんせつ", "meaning_en": "clause"},
    ):
        assert not JapaneseChapterHarness.annotation_reconstructs(
            segment["surface"], {**base, "segments": [segment]}
        )


@pytest.mark.parametrize("surface", ["想像せずにはいられなかった", "捕っていない"])
def test_annotation_contract_rejects_compositional_grammar_as_one_word(surface):
    assert japanese_segment_issue(surface, "word")
    result = {"segments": [{"surface": surface, "type": "word", "lemma": "想像する",
                            "kana": "そうぞう", "meaning_en": "impossible grouping"}],
              "grammar_overlays": []}
    assert not JapaneseChapterHarness.annotation_reconstructs(surface, result)


def test_wagahai_is_pronoun_not_name_but_real_names_and_idioms_are_allowed():
    assert japanese_segment_issue("吾輩", "name")
    assert japanese_segment_issue("苦沙弥", "name") is None
    assert japanese_segment_issue("油を売る", "idiom") is None
    assert japanese_segment_issue("想像せずにはいられなかった", "idiom")


class OutlineRunner:
    async def call(self, *args, **kwargs):
        return {
            "chapter_title": "一",
            "scenes": [{
                "id": "scene_01", "title": "猫", "source_start_quote": "同じ境界",
                "required_events": ["猫がいる"], "target_chars": 12,
            }],
        }


@pytest.mark.asyncio
async def test_outline_accepts_repeated_boundary_and_overrides_target(tmp_path):
    source = tmp_path / "chapter_01.txt"
    source.write_text("同じ境界。途中。同じ境界。", encoding="utf-8")
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n5", target_chars=None, review_effort="medium", refresh=False)
    harness.source_path, harness.source = source, source.read_text(encoding="utf-8")
    harness.run_dir, harness.runner = tmp_path / "run", OutlineRunner()
    harness.run_dir.mkdir()
    outline = await harness.outline()
    assert outline["scenes"][0]["source_start"] == 0
    assert outline["scenes"][0]["target_chars"] == 1200


class ChapterReviewRunner:
    def __init__(self):
        self.prompt = ""

    async def call(self, job, prompt, *args, **kwargs):
        self.prompt = prompt
        assert job == "chapter/review"
        return {
            "source_fidelity": 9, "naturalness": 9, "readability": 9,
            "omissions": [], "unsupported_additions": [], "distortions": [],
            "language_problems": [], "verdict": "pass",
        }


@pytest.mark.asyncio
async def test_whole_chapter_review_receives_required_event_ledger():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n5", target_chars=8, review_effort="medium", refresh=False)
    harness.source = "猫の原文。"
    harness.runner = ChapterReviewRunner()
    outline = {"scenes": [{
        "id": "scene_01", "source_start": 0, "source_end": 5,
        "required_events": ["猫が家に来る"],
    }]}
    result = await harness.review_chapter(outline, "猫が家に来る。")
    assert result["verdict"] == "pass"
    assert "猫が家に来る" in harness.runner.prompt
    assert "cross-scene continuity" in harness.runner.prompt


@pytest.mark.asyncio
async def test_scene_review_pass_with_material_finding_forces_repair():
    class ContradictoryRunner:
        async def call(self, *args, **kwargs):
            return {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [],
                "distortions": ["右目を両目に変えている"],
                "language_problems": [], "verdict": "pass",
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n4", review_effort="medium", refresh=False)
    harness.source = "右目を閉じた。"
    harness.runner = ContradictoryRunner()
    scene = {"id": "scene_01", "source_start": 0,
             "source_end": len(harness.source), "target_chars": 6}
    result = await harness.review_scene(scene, "両目を閉じた。")
    assert result["verdict"] == "revise"
    assert result["harness_decision"] == "rejected_material_findings"


@pytest.mark.asyncio
async def test_post_fresh_repair_is_single_issue_scoped_final_step(tmp_path):
    class Runner:
        def __init__(self):
            self.jobs = []

        async def call(self, job, prompt, schema, effort, **kwargs):
            self.jobs.append(job)
            if schema.name == "adaptation.schema.json":
                if job.endswith("post_fresh_repair"):
                    assert "赤十字総会で上京し" in prompt
                    assert "issues only" in prompt
                    return {"text": "総会に出るため上京した。"}
                return {"text": "赤十字総会で上京した。"}
            passed = job.endswith("post_fresh_repair_review")
            return {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [], "distortions": [],
                "language_problems": [] if passed else [
                    "「赤十字総会で上京し」は不自然。"
                ],
                "verdict": "pass" if passed else "revise",
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(
        level="n4", adapt_effort="xhigh", review_effort="medium",
        repair_effort="xhigh", final_effort="max", max_repairs=0,
        fresh_rewrite=True, refresh=False,
    )
    harness.source = "赤十字総会に出るため上京した。"
    harness.runner, harness.run_dir = Runner(), tmp_path
    scene = {"id": "scene_01", "source_start": 0,
             "source_end": len(harness.source), "target_chars": 12,
             "required_events": ["赤十字総会のため上京する"]}
    result = await harness.process_scene(scene)
    assert result["resolved"] is True
    assert result["attempts"][-1]["stage"] == "post_fresh_repair"
    assert harness.runner.jobs == [
        "scene_01/adapt", "scene_01/review", "scene_01/fresh_rewrite",
        "scene_01/fresh_rewrite_review", "scene_01/post_fresh_repair",
        "scene_01/post_fresh_repair_review",
    ]


class ChapterHealRunner:
    def __init__(self, final_verdict="pass"):
        self.calls = []
        self.final_verdict = final_verdict

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append((job, prompt, schema.name, effort))
        if schema.name == "adaptation.schema.json":
            return {"text": "猫が家に来て魚を食べた。"}
        return {
            "source_fidelity": 9, "naturalness": 9, "readability": 9,
            "omissions": [] if self.final_verdict == "pass" else ["犬を省略"],
            "unsupported_additions": [], "distortions": [],
            "language_problems": [], "verdict": self.final_verdict,
        }


def chapter_heal_harness(runner, max_repairs=2):
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(
        level="n5", target_chars=10, review_effort="medium",
        repair_effort="high", refresh=False, max_repairs=max_repairs,
    )
    harness.source = "原文では猫が家に来て魚を食べた。"
    harness.runner = runner
    return harness


@pytest.mark.asyncio
async def test_chapter_heal_is_issue_scoped_resumable_and_independently_reviewed():
    runner = ChapterHealRunner()
    harness = chapter_heal_harness(runner)
    outline = {"scenes": [{
        "id": "scene_01", "source_start": 0, "source_end": len(harness.source),
        "required_events": ["猫が家に来る", "魚を食べる"],
    }]}
    initial = {
        "source_fidelity": 7, "naturalness": 9, "readability": 9,
        "omissions": ["魚を食べる出来事"], "unsupported_additions": [],
        "distortions": [], "language_problems": [], "verdict": "revise",
    }
    chapter, review, attempts = await harness.heal_chapter_review(
        outline, "猫が家に来た。\n", initial
    )
    assert review["verdict"] == "pass"
    assert chapter == "猫が家に来て魚を食べた。\n"
    assert [call[0] for call in runner.calls] == [
        "chapter/repair_01", "chapter/repair_01_review",
    ]
    repair_prompt = runner.calls[0][1]
    assert "魚を食べる出来事" in repair_prompt
    assert "原文では猫が家に来て魚を食べた" in repair_prompt
    assert "preserve all other wording" in repair_prompt
    assert "70%-130% of 10" in repair_prompt
    assert attempts[0]["repair_job"] == "chapter/repair_01"


@pytest.mark.asyncio
async def test_chapter_heal_is_bounded_and_keeps_last_reviewed_candidate():
    runner = ChapterHealRunner(final_verdict="revise")
    harness = chapter_heal_harness(runner, max_repairs=2)
    outline = {"scenes": [{
        "id": "scene_01", "source_start": 0, "source_end": len(harness.source),
        "required_events": ["猫が家に来る"],
    }]}
    initial = {
        "source_fidelity": 7, "naturalness": 9, "readability": 9,
        "omissions": ["犬を省略"], "unsupported_additions": [],
        "distortions": [], "language_problems": [], "verdict": "revise",
    }
    chapter, review, attempts = await harness.heal_chapter_review(
        outline, "猫が家に来た。\n", initial
    )
    assert review["verdict"] == "revise"
    assert chapter.endswith("\n")
    assert len(attempts) == 2
    assert [call[0] for call in runner.calls] == [
        "chapter/repair_01", "chapter/repair_01_review",
        "chapter/repair_02", "chapter/repair_02_review",
    ]


def book_harness_args(tmp_path, *, skip_annotations=True):
    return Namespace(
        runs_dir=str(tmp_path / "runs"), book_run_id="book",
        concurrency=1, chapter_concurrency=1, chapter_retries=0,
        target_chars=None, skip_annotations=skip_annotations,
    )


def write_completed_book_artifact(tmp_path, *, reader=True, source_sha=None):
    source = tmp_path / "chapter_01.txt"
    source.write_text("猫の原文。", encoding="utf-8")
    run = tmp_path / "runs" / "book" / "chapter_01-n5"
    run.mkdir(parents=True)
    chapter = "猫が来た。\n"
    (run / "chapter.txt").write_text(chapter, encoding="utf-8")
    count = japanese_char_count(chapter)
    (run / "manifest.json").write_text(json.dumps({
        "status": "complete", "source": str(source.resolve()),
        "source_sha256": source_sha or digest(source.read_text()),
        "level": "n5", "target_chars": 1200,
    }), encoding="utf-8")
    (run / "report.json").write_text(json.dumps({
        "status": "complete", "japanese_chars": count, "chapter_cjk": count,
    }), encoding="utf-8")
    if reader:
        (run / "reader.json").write_text("{}", encoding="utf-8")
    return source


@pytest.mark.asyncio
async def test_book_run_skips_only_verified_completed_artifact(tmp_path, monkeypatch):
    source = write_completed_book_artifact(tmp_path, reader=False)
    harness = JapaneseBookHarness(book_harness_args(tmp_path))

    async def must_not_run(*args, **kwargs):
        raise AssertionError("verified completed chapter must not invoke chapter harness")

    monkeypatch.setattr(JapaneseChapterHarness, "run", must_not_run)
    result = await harness.run_one(str(source), "n5")
    assert result["status"] == "complete"
    assert result["cached"] is True
    assert result["attempts"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["provenance", "reader"])
async def test_book_run_reruns_stale_provenance_or_missing_reader(
    tmp_path, monkeypatch, stale
):
    source = write_completed_book_artifact(
        tmp_path, reader=stale != "reader",
        source_sha="0" * 64 if stale == "provenance" else None,
    )
    harness = JapaneseBookHarness(book_harness_args(
        tmp_path, skip_annotations=stale != "reader"
    ))
    calls = []

    class RerunHarness:
        def __init__(self, args, semaphore=None):
            calls.append((args, semaphore))

        async def run(self):
            return {"status": "complete", "chapter_cjk": 4, "japanese_chars": 4}

    monkeypatch.setattr(
        "pipeline.japanese_agent_harness.JapaneseChapterHarness", RerunHarness
    )
    result = await harness.run_one(str(source), "n5")
    assert result["status"] == "complete"
    assert len(calls) == 1
    assert "cached" not in result
