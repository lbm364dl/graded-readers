"""Publish selected, reviewed Korean sentence analyses without moving taps."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.korean_readability import ROOT


SOURCE = "assets/annotations/korean_honggildong_l1_001.json"
BREAKDOWNS = ROOT / "content/korean/honggildong/l1.sentence-breakdowns.json"


def build(chapter: dict, output_dir: Path) -> dict:
    data = json.loads(BREAKDOWNS.read_text(encoding="utf-8"))
    if (data.get("schema_version") != 1 or data.get("reviewed") is not True
            or not isinstance(data.get("breakdowns"), list)):
        raise ValueError("unreviewed Korean sentence breakdowns")
    text = chapter["text"]
    boundaries = {0}
    offset = 0
    for segment in chapter["segments"]:
        offset += len(segment["text"])
        boundaries.add(offset)
    if offset != len(text):
        raise ValueError("Korean breakdown source segmentation changed")
    spans = []
    for item in data["breakdowns"]:
        start, sentence = item["start"], item["sentence"]
        end = start + len(sentence)
        if (item["source"] != SOURCE or start not in boundaries
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
    result = {"schema_version": 1, "sources": {SOURCE: text},
              "breakdowns": data["breakdowns"]}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "korean_sentence_breakdowns.json").write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return result
