"""Publish reviewed, source-bound Korean word and grammar entries."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.korean_readability import ROOT


WORDS = ROOT / "content/lexicon/korean/l1.words.json"
GRAMMAR = ROOT / "content/lexicon/korean/l1.grammar.json"
SOURCE = "assets/annotations/korean_honggildong_l1_001.json"


def _registry(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("reviewed") is not True or not isinstance(data.get("entries"), list):
        raise ValueError(f"unreviewed Korean dictionary registry: {path}")
    entries = data["entries"]
    by_id = {entry["id"]: entry for entry in entries}
    if len(by_id) != len(entries):
        raise ValueError(f"duplicate Korean dictionary identity: {path}")
    return by_id


def _sentence(text: str, start: int) -> tuple[str, int]:
    before = max(text.rfind(mark, 0, start) for mark in ".!?。！？") + 1
    while before < len(text) and text[before].isspace():
        before += 1
    after = min((position + 1 for mark in ".!?。！？"
                 if (position := text.find(mark, start)) >= 0), default=len(text))
    return text[before:after], before


def build_assets(chapter: dict, output_dir: Path) -> tuple[dict, dict]:
    """Reject stale or missing links before writing either published asset."""
    words = _registry(WORDS)
    grammar = _registry(GRAMMAR)
    segments = chapter["segments"]
    source_text = chapter["text"]
    offset = 0
    word_uses = []
    positions = []
    for index, segment in enumerate(segments):
        start = offset
        offset += len(segment["text"])
        positions.append((start, offset))
        if segment["type"] != "word":
            continue
        lexical = segment["lexical"]
        if lexical["kind"] == "grammar":
            if lexical["id"] not in grammar:
                raise ValueError(f"missing Korean grammar entry: {lexical['id']}")
            continue
        entry = words.get(lexical["id"])
        if entry is None or entry["kind"] != lexical["kind"].replace("vocabulary", "word"):
            raise ValueError(f"missing Korean word entry: {lexical['id']}")
        sentence, sentence_start = _sentence(source_text, start)
        word_uses.append({
            "id": f"{SOURCE}#word-{index}", "source": SOURCE,
            "segment_index": index, "start": start, "end": offset,
            "surface": segment["text"], "gloss": segment["meaning_en"],
            "entry_id": lexical["id"], "sentence": sentence,
            "sentence_start": sentence_start,
        })
    if offset != len(source_text):
        raise ValueError("Korean dictionary positions do not reconstruct source")
    links = chapter.get("grammar_links")
    if not isinstance(links, list):
        raise ValueError("Korean grammar occurrence review missing")
    grammar_uses = []
    seen = set()
    for link in links:
        index = link["segment_index"]
        entry_id = link["entry_id"]
        if (not isinstance(index, int) or not 0 <= index < len(segments)
                or segments[index]["type"] != "word" or entry_id not in grammar
                or not str(link.get("context_en", "")).strip()
                or (index, entry_id) in seen):
            raise ValueError(f"invalid Korean grammar occurrence: {link}")
        seen.add((index, entry_id))
        start, end = positions[index]
        sentence, sentence_start = _sentence(source_text, start)
        grammar_uses.append({
            "id": f"{SOURCE}#grammar-{index}-{entry_id}", "source": SOURCE,
            "segment_index": index, "start": start, "end": end,
            "surface": segments[index]["text"], "entry_id": entry_id,
            "context_en": link["context_en"], "sentence": sentence,
            "sentence_start": sentence_start,
        })
    for index, segment in enumerate(segments):
        if segment["type"] == "word" and segment["lexical"]["kind"] == "grammar":
            if (index, segment["lexical"]["id"]) not in seen:
                raise ValueError(f"Korean grammar tap has no linked lesson: {index}")
    used_words = {item["entry_id"] for item in word_uses}
    used_grammar = {item["entry_id"] for item in grammar_uses}
    if set(words) != used_words or set(grammar) != used_grammar:
        raise ValueError("Korean dictionary entries do not match chapter usage")
    source = {SOURCE: {
        "reader_id": "honggildong_l1", "chapter": 1,
        "title": chapter["title"], "level": "Level 1", "text": source_text,
    }}
    word_asset = {"schema_version": 1, "language": "korean", "sources": source,
                  "entries": list(words.values()), "occurrences": word_uses}
    grammar_asset = {"schema_version": 1, "language": "korean", "sources": source,
                     "entries": list(grammar.values()), "occurrences": grammar_uses}
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, asset in (("usage_dictionary_ko.json", word_asset),
                        ("grammar_dictionary_ko.json", grammar_asset)):
        (output_dir / name).write_text(
            json.dumps(asset, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    return word_asset, grammar_asset
