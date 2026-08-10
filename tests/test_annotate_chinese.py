from argparse import Namespace
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from pipeline.annotate_chinese import (
    ChapterFingerprintRunner, ChineseAnnotationHarness,
    discover_complete_chapters, parser, reusable_reader,
    split_chinese_annotation_chunks,
)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def accepted_run(tmp_path: Path, text: str = "修复后的正文。\n") -> Path:
    run = tmp_path / "book" / "chapter_001-hsk4"
    run.mkdir(parents=True)
    (run / "chapter.txt").write_text(text, encoding="utf-8")
    write_json(run / "manifest.json", {"status": "complete", "level": "hsk4"})
    write_json(run / "report.json", {"status": "complete"})
    write_json(run / "outline.json", {"chapter_title": "修复章"})
    return run


def args() -> Namespace:
    return Namespace(
        model="gpt-5.6-luna", timeout=10, annotation_chunk=200,
        annotation_effort="low", annotation_review_effort="medium",
        annotation_repair_effort="medium", annotation_final_effort="xhigh",
        annotation_mode="generative", annotation_review_policy="exhaustive-chunks",
        max_annotation_repairs=2, refresh=False,
    )


def fake_annotation(chunk: str) -> dict:
    return {
        "segments": [{
            "text": chunk, "type": "word", "pinyin": "xiū fù",
            "meaning_en": "repaired text",
        }],
        "grammar_overlays": [],
        "attempts": [{
            "stage": "initial", "annotation": {},
            "review": {"verdict": "pass", "issues": []},
        }],
        "resolved": True,
    }


def test_annotation_batch_parser_defaults_to_production_chapter_policy():
    parsed = parser().parse_args(["--run-dir", "runs/book"])
    assert parsed.annotation_mode == "constrained-delta"
    assert parsed.annotation_review_policy == "chapter"


@pytest.mark.asyncio
async def test_annotation_only_uses_constrained_delta_chapter_policy_and_audits_calls(
    tmp_path, monkeypatch
):
    text = "刘备来了。\n"
    run = accepted_run(tmp_path, text)
    production = args()
    production.annotation_mode = "constrained-delta"
    production.annotation_review_policy = "chapter"
    harness = ChineseAnnotationHarness(run, production, asyncio.Semaphore(1))

    async def must_not_use_chunk_generator(*_args, **_kwargs):
        raise AssertionError("production path bypassed constrained-delta")

    async def chapter_delta(chapter, chunks):
        assert chapter == text and chunks == [text]
        harness.annotation_metadata_model_calls = 2
        harness.annotation_semantic_review_calls = 2
        harness.annotation_duplicate_override_collapses = 1
        item = fake_annotation(text)
        item["attempts"] = [
            {"stage": "chapter_delta_initial", "review": {"verdict": "revise"}},
            {"stage": "chapter_delta_correction", "review": {"verdict": "pass"}},
        ]
        return [item]

    monkeypatch.setattr(harness, "annotate_chunk", must_not_use_chunk_generator)
    monkeypatch.setattr(harness, "annotate_chapter_constrained_delta", chapter_delta)
    report = await harness.annotate_accepted_chapter()
    reader = json.loads((run / "reader.json").read_text())
    audit = reader["annotation_audit"]
    assert report["status"] == "complete"
    assert report["metadata_model_calls"] == 2
    assert report["semantic_review_calls"] == 2
    assert audit == {
        "mode": "constrained-delta", "review_policy": "chapter",
        "chapter_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "chunks": 1, "attempts_per_chunk": [2], "all_reviewed": True,
        "metadata_model_calls": 2, "semantic_review_calls": 2,
        "metadata_batches": 1, "metadata_completion_calls": 0,
        "duplicate_override_collapses": 1,
        "boundary_override_drops": 0,
        "acceptance_state": "reviewed_pass", "final_review_verdict": "pass",
        "remediation_calls": 0,
        "boundary_normalization": {
            "discarded_count": 0, "duplicate_collapses": 0, "discarded": [],
        },
        "override_normalization": {
            "discarded_count": 0, "duplicate_collapses": 0, "discarded": [],
        },
        "overlay_normalization": {
            "discarded_count": 0, "duplicate_collapses": 0, "discarded": [],
        },
    }


def test_adaptive_chunks_minimize_calls_without_crossing_sentences():
    sentences = ["刘备来到城里。", "关羽也来了。", "张飞正在等他们。", "三人一起吃饭。"]
    text = "".join(sentences)
    chunks = split_chinese_annotation_chunks(text, target=10, maximum=16)
    assert "".join(chunks) == text
    assert len(chunks) == 2
    assert chunks == ["".join(sentences[:2]), "".join(sentences[2:])]
    assert all(chunk.endswith("。") for chunk in chunks)


def test_adaptive_chunks_fail_closed_on_unsafe_sentence_split():
    text = "刘备" * 20 + "。"
    with pytest.raises(ValueError, match="exceeding the safe"):
        split_chinese_annotation_chunks(text, target=20, maximum=30)


def test_adaptive_chunks_preserve_paragraph_whitespace():
    text = "刘备来了。\n\n关羽也来了。\n"
    chunks = split_chinese_annotation_chunks(text, target=5, maximum=9)
    assert "".join(chunks) == text
    assert chunks == ["刘备来了。\n\n", "关羽也来了。\n"]


def test_adaptive_chunks_keep_closing_quotes_with_sentence():
    text = "刘备说：“我来了。”关羽点头。张飞也来了。"
    chunks = split_chinese_annotation_chunks(text, target=8, maximum=12)
    assert "".join(chunks) == text
    assert chunks == ["刘备说：“我来了。”", "关羽点头。张飞也来了。"]
    assert not any(chunk.startswith("”") for chunk in chunks)


@pytest.mark.asyncio
async def test_repaired_chapter_is_preserved_and_stale_reader_replaced(tmp_path, monkeypatch):
    text = "连续性修复后的正文。\n"
    run = accepted_run(tmp_path, text)
    write_json(run / "reader.json", {
        "text": "旧正文。\n", "segments": [{"text": "旧正文。\n"}],
    })
    before = (run / "chapter.txt").read_bytes()

    async def annotate(self, index, chunk):
        return fake_annotation(chunk)

    monkeypatch.setattr(ChineseAnnotationHarness, "annotate_chunk", annotate)
    harness = ChineseAnnotationHarness(run, args(), asyncio.Semaphore(1))
    report = await harness.annotate_accepted_chapter()

    assert (run / "chapter.txt").read_bytes() == before
    reader = json.loads((run / "reader.json").read_text())
    assert reader["text"] == text
    assert "".join(x["text"] for x in reader["segments"]) == text
    assert reader["annotation_audit"]["chapter_sha256"] == hashlib.sha256(before).hexdigest()
    assert report["reader_replaced"] is True
    assert json.loads((run / "manifest.json").read_text()) == {"status": "complete", "level": "hsk4"}
    assert json.loads((run / "report.json").read_text()) == {"status": "complete"}


@pytest.mark.asyncio
async def test_concurrent_prose_change_fails_without_overwriting_it(tmp_path, monkeypatch):
    run = accepted_run(tmp_path)
    stale_reader = {"text": "旧。", "segments": [{"text": "旧。"}]}
    write_json(run / "reader.json", stale_reader)

    async def mutate_then_annotate(self, index, chunk):
        (self.run_dir / "chapter.txt").write_text("外部修复。\n", encoding="utf-8")
        return fake_annotation(chunk)

    monkeypatch.setattr(ChineseAnnotationHarness, "annotate_chunk", mutate_then_annotate)
    harness = ChineseAnnotationHarness(run, args(), asyncio.Semaphore(1))
    with pytest.raises(RuntimeError, match="changed during annotation"):
        await harness.annotate_accepted_chapter()
    assert (run / "chapter.txt").read_text() == "外部修复。\n"
    assert json.loads((run / "reader.json").read_text()) == stale_reader
    assert json.loads((run / "annotation-report.json").read_text())["status"] == "failed"


def test_discovery_includes_only_complete_generation_runs(tmp_path):
    complete = accepted_run(tmp_path)
    incomplete = tmp_path / "book" / "chapter_002-hsk4"
    incomplete.mkdir()
    (incomplete / "chapter.txt").write_text("候选。")
    write_json(incomplete / "manifest.json", {"status": "running"})
    write_json(incomplete / "report.json", {"status": "complete"})
    assert discover_complete_chapters([tmp_path / "book"]) == [complete.resolve()]


class PromptRunner:
    def __init__(self):
        self.prompts = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.prompts.append(prompt)
        return {"segments": [{
            "text": prompt.split("TEXT:\n", 1)[1], "type": "word",
            "pinyin": "", "meaning_en": "text",
        }], "grammar_overlays": []}


@pytest.mark.asyncio
async def test_annotation_cache_input_contains_exact_changed_text(tmp_path):
    run = accepted_run(tmp_path)
    harness = ChineseAnnotationHarness(run, args(), asyncio.Semaphore(1))
    harness.runner = PromptRunner()
    await harness.annotation_candidate(0, "连续性版本甲")
    await harness.annotation_candidate(0, "连续性版本乙")
    assert harness.runner.prompts[0] != harness.runner.prompts[1]
    assert "连续性版本甲" in harness.runner.prompts[0]
    assert "连续性版本乙" in harness.runner.prompts[1]


@pytest.mark.asyncio
async def test_full_chapter_hash_salts_every_chunk_and_review_fingerprint():
    class SaltCapture:
        def __init__(self):
            self.prompts = []

        async def call(self, job, prompt, *args, **kwargs):
            self.prompts.append(prompt)
            return {}

    base = SaltCapture()
    first = ChapterFingerprintRunner(base, "a" * 64)
    second = ChapterFingerprintRunner(base, "b" * 64)
    await first.call("annotations/chunk", "same chunk", None, "low")
    await second.call("annotations/chunk", "same chunk", None, "low")
    assert base.prompts[0] != base.prompts[1]
    assert "a" * 64 in base.prompts[0]
    assert "b" * 64 in base.prompts[1]


@pytest.mark.asyncio
async def test_valid_reviewed_reader_is_reused_without_annotation_calls(tmp_path, monkeypatch):
    text = "已经审核的正文。\n"
    run = accepted_run(tmp_path, text)
    reader = {
        "title": "修复章", "level": "HSK4", "text": text,
        "segments": [
            {"text": "已经", "type": "word", "pinyin": "yǐ jīng", "meaning_en": "already"},
            {"text": "审核", "type": "word", "pinyin": "shěn hé", "meaning_en": "review"},
            {"text": "的", "type": "particle", "pinyin": "de", "meaning_en": "attributive particle"},
            {"text": "正文", "type": "word", "pinyin": "zhèng wén", "meaning_en": "main text"},
            {"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
        ],
        "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True, "chunks": 1},
    }
    write_json(run / "reader.json", reader)

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("annotation model path must not run")

    monkeypatch.setattr(ChineseAnnotationHarness, "annotate_chunk", must_not_run)
    harness = ChineseAnnotationHarness(run, args(), asyncio.Semaphore(1))
    before = (run / "reader.json").read_bytes()
    report = await harness.annotate_accepted_chapter()
    assert report["reused"] is True
    assert report["attempts"] == 0
    assert (run / "reader.json").read_bytes() == before


@pytest.mark.parametrize("mutation", [
    "text", "segments", "audit", "level", "malformed",
])
def test_reuse_fails_closed_for_every_stale_or_malformed_case(tmp_path, mutation):
    text = "当前正文。\n"
    run = accepted_run(tmp_path, text)
    reader = {
        "level": "HSK4", "text": text,
        "segments": [
            {"text": "当前正文", "type": "word", "pinyin": "dāng qián zhèng wén", "meaning_en": "current text"},
            {"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
        ], "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True},
    }
    path = run / "reader.json"
    if mutation == "text":
        reader["text"] = "旧正文。\n"
    elif mutation == "segments":
        reader["segments"][0]["text"] = "不重建"
    elif mutation == "audit":
        reader["annotation_audit"]["all_reviewed"] = False
    elif mutation == "level":
        reader["level"] = "HSK5"
    if mutation == "malformed":
        path.write_text("{broken", encoding="utf-8")
    else:
        write_json(path, reader)
    assert reusable_reader(path, text, "HSK4") is None


@pytest.mark.asyncio
async def test_refresh_forces_reannotation_of_otherwise_reusable_reader(tmp_path, monkeypatch):
    text = "已审核。\n"
    run = accepted_run(tmp_path, text)
    write_json(run / "reader.json", {
        "level": "HSK4", "text": text,
        "segments": [
            {"text": "已审核", "type": "word", "pinyin": "yǐ shěn hé", "meaning_en": "reviewed"},
            {"text": "。\n", "type": "punctuation", "pinyin": "", "meaning_en": ""},
        ],
        "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True, "chunks": 1},
    })
    called = []

    async def annotate(self, index, chunk):
        called.append(chunk)
        return fake_annotation(chunk)

    forced = args()
    forced.refresh = True
    monkeypatch.setattr(ChineseAnnotationHarness, "annotate_chunk", annotate)
    report = await ChineseAnnotationHarness(
        run, forced, asyncio.Semaphore(1)
    ).annotate_accepted_chapter()
    assert called == [text]
    assert report["reused"] is False
