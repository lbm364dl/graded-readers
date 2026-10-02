"""Publish selected, reviewed Korean sentence analyses without moving taps."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.korean_readability import ROOT


SOURCE = "assets/annotations/korean_honggildong_l1_001.json"
BREAKDOWNS = ROOT / "content/korean/honggildong/l1.sentence-breakdowns.json"


def build(chapter: dict, output_dir: Path, *, source_id: str = SOURCE,
          data: dict | None = None, write: bool = True) -> dict:
    data = json.loads(BREAKDOWNS.read_text(encoding="utf-8")) if data is None else data
    if (data.get("schema_version") != 1 or data.get("reviewed") is not True
            or not isinstance(data.get("breakdowns"), list)):
        raise ValueError("unreviewed Korean sentence breakdowns")
    text = chapter["text"]
    boundaries = {0}
    offset = 0
    for segment in chapter["segments"]:
        # Sentence punctuation and following spaces can share one segment.
        # Analysis may end before those spaces without splitting a word tap.
        if segment['type'] == 'punctuation':
            boundaries.update(range(offset, offset + len(segment['text']) + 1))
        offset += len(segment["text"])
        boundaries.add(offset)
    if "".join(segment["text"] for segment in chapter["segments"]) != text:
        raise ValueError("Korean breakdown source segmentation changed")
    spans = []
    for item in data["breakdowns"]:
        start, sentence = item["start"], item["sentence"]
        end = start + len(sentence)
        if (item["source"] != source_id or start not in boundaries
                or end not in boundaries or text[start:end] != sentence
                or not item["translation_en"].strip()
                or not item["parts"]
                or "".join(part["text"] for part in item["parts"]) != sentence):
            raise ValueError("Korean sentence breakdown lost source alignment")
        cursor = start
        for part in item["parts"]:
            cursor += len(part["text"])
            if not part["explanation_en"].strip() or cursor not in boundaries:
                raise ValueError("Korean sentence breakdown splits a tap")
        if any(start < previous_end and previous_start < end
               for previous_start, previous_end in spans):
            raise ValueError("overlapping Korean sentence breakdowns")
        spans.append((start, end))
    result = {"schema_version": 1, "sources": {source_id: text},
              "breakdowns": data["breakdowns"]}
    if write:
        write_asset(output_dir, result)
    return result


def write_asset(output_dir: Path, result: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "korean_sentence_breakdowns.json").write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def build_collection(chapters: list[dict], output_dir: Path) -> dict:
    from pipeline.korean_levels import source_id as chapter_source_id, target_level
    result = {"schema_version": 1, "sources": {}, "breakdowns": []}
    by_level = {}
    for chapter in chapters:
        by_level.setdefault(target_level(chapter), []).append(chapter)
    for level, edition_chapters in by_level.items():
        reviewed = json.loads(BREAKDOWNS.with_name(f'l{level}.sentence-breakdowns.json').read_text(encoding='utf-8'))
        expected = {chapter_source_id(c) for c in edition_chapters}
        if any(item['source'] not in expected for item in reviewed['breakdowns']):
            raise ValueError('Korean sentence breakdown refers to an unpublished chapter')
        for chapter in edition_chapters:
            source_id = chapter_source_id(chapter)
            data = {**reviewed, 'breakdowns': [item for item in reviewed['breakdowns'] if item['source'] == source_id]}
            asset = build(chapter, output_dir, source_id=source_id, data=data, write=False)
            if source_id in result['sources']:
                raise ValueError('duplicate Korean sentence-help chapter')
            result['sources'].update(asset['sources'])
            result['breakdowns'].extend(asset['breakdowns'])
    write_asset(output_dir, result)
    return result
