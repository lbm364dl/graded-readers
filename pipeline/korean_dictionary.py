"""Publish reviewed, source-bound Korean word and grammar entries."""

from __future__ import annotations

import json
import hashlib
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
    latest_revisions = {}
    for revision in data.get('revision_reviews', []):
        fingerprint = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if (revision.get('proposal_digest') != fingerprint(revision.get('proposal'))
                or revision.get('review_digest') != fingerprint(revision.get('review'))
                or revision.get('review') != {'approved': True, 'issues': []}):
            raise ValueError('Korean dictionary revision lacks matching independent review')
        before = {entry['id']: entry for entry in revision['before_entries']}
        after = {entry['id']: entry for entry in revision['proposal']['entries']}
        if before.keys() != after.keys():
            raise ValueError('Korean dictionary revision changes identity coverage')
        for identity, entry in after.items():
            if any(entry[key] != before[identity][key] for key in ('id', 'headword', 'kind')):
                raise ValueError('Korean dictionary revision changes a lexical identity')
            if identity in latest_revisions and latest_revisions[identity] != before[identity]:
                raise ValueError('Korean dictionary revision history is discontinuous')
            latest_revisions[identity] = entry
    if any(by_id.get(identity) != entry for identity, entry in latest_revisions.items()):
        raise ValueError('Korean revised definition differs from independent review')
    return by_id


def _sentence(text: str, start: int) -> tuple[str, int]:
    before = max(text.rfind(mark, 0, start) for mark in ".!?。！？") + 1
    while before < len(text) and text[before].isspace():
        before += 1
    after = min((position + 1 for mark in ".!?。！？"
                 if (position := text.find(mark, start)) >= 0), default=len(text))
    return text[before:after], before


def build_assets(chapter: dict, output_dir: Path, *, source_id: str = SOURCE,
                 word_registry: dict | None = None, grammar_registry: dict | None = None,
                 write: bool = True) -> tuple[dict, dict]:
    """Reject stale or missing links before writing either published asset."""
    words = _registry(WORDS) if word_registry is None else word_registry
    grammar = _registry(GRAMMAR) if grammar_registry is None else grammar_registry
    segments = chapter["segments"]
    source_text = chapter["text"]
    offset = 0
    word_uses = []
    positions = []
    for index, segment in enumerate(segments):
        if not segment["text"]:
            raise ValueError("Korean dictionary tap cannot be empty")
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
            "id": f"{source_id}#word-{index}", "source": source_id,
            "segment_index": index, "start": start, "end": offset,
            "surface": segment["text"], "gloss": segment["meaning_en"],
            "entry_id": lexical["id"], "sentence": sentence,
            "sentence_start": sentence_start,
        })
    if "".join(segment["text"] for segment in segments) != source_text:
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
        stage_ids = {grammar_id for step in segments[index].get("form_steps", [])
                     for grammar_id in step["grammar_entry_ids"]}
        display_keys = {"display_form", "display_meaning_en", "display_end_segment_index"}
        display = {}
        if entry_id not in stage_ids and (segments[index].get("form_steps") or display_keys & link.keys()):
            if not display_keys <= link.keys():
                raise ValueError(f"Korean construction stage lacks complete form: {link}")
            last = link["display_end_segment_index"]
            if (not isinstance(last, int) or not index <= last < len(segments)
                    or not str(link["display_meaning_en"]).strip()
                    or link["display_form"] != source_text[start:positions[last][1]]):
                raise ValueError(f"invalid Korean construction stage: {link}")
            display = {key: link[key] for key in display_keys}
        elif display_keys & link.keys():
            raise ValueError(f"unexpected Korean construction stage: {link}")
        sentence, sentence_start = _sentence(source_text, start)
        grading = {}
        if 'curriculum' in chapter:
            evaluation = chapter['curriculum']['evaluation']
            actual = evaluation['grammar_levels'][entry_id]
            target = evaluation['target_level']
            grading = {'matched_curriculum_level': actual,
                       'target_curriculum_level': target,
                       'optional_for_level': actual is None or actual > target,
                       'optional_reason_en': evaluation['optional_grammar_reasons'].get(entry_id, '')}
        grammar_uses.append({
            "id": f"{source_id}#grammar-{index}-{entry_id}", "source": source_id,
            "segment_index": index, "start": start, "end": end,
            "surface": segments[index]["text"], "entry_id": entry_id,
            "context_en": link["context_en"], "sentence": sentence,
            "sentence_start": sentence_start,
            **display, **grading,
        })
    for index, segment in enumerate(segments):
        if segment["type"] == "word" and segment["lexical"]["kind"] == "grammar":
            if (index, segment["lexical"]["id"]) not in seen:
                raise ValueError(f"Korean grammar tap has no linked lesson: {index}")
    form_audit = chapter.get("form_audit", {})
    inflected = form_audit.get("inflected_segment_indices")
    if (form_audit.get("reviewed") is not True or not isinstance(inflected, list)
            or len(set(inflected)) != len(inflected)
            or set(inflected) != {i for i, segment in enumerate(segments)
                                       if segment.get("form_steps")}):
        raise ValueError("Korean form review is incomplete")
    form_uses = []
    for index in inflected:
        if not isinstance(index, int) or not 0 <= index < len(segments):
            raise ValueError("invalid Korean form-review index")
        segment = segments[index]
        if segment["type"] != "word" or segment["lexical"]["kind"] == "grammar":
            raise ValueError("Korean form chain needs an attested lexical base")
        steps = segment["form_steps"]
        forms = set()
        for step_index, step in enumerate(steps):
            if step.get('form') in forms:
                raise ValueError(f"Duplicate Korean complete-form transformation at segment index {index}, step {step_index}: {step['form']!r}. Each stage must have a distinct complete form. A grammar role that adds no new form belongs in a linked complete-phrase occurrence, not a repeated stage. Do not manufacture a bare-stem stage to make the forms differ.")
            if (set(step) != {"form", "reading", "label", "meaning_en",
                             "grammar_entry_ids"}
                    or not all(str(step[key]).strip() for key in
                               ("form", "label", "meaning_en"))
                    or len(step["grammar_entry_ids"]) != 1
                    or any((index, entry_id) not in seen
                           for entry_id in step["grammar_entry_ids"])):
                raise ValueError(f"Invalid Korean complete-form transformation at segment index {index} ({segment['text']!r}), step {step_index}: {step}. Steps need exactly one grammar ID linked on the same segment. The lexical dictionary-form base is already shown separately; do not include a duplicate base step with no grammar ID.")
            forms.add(step["form"])
            form_uses.append({"source": source_id, "segment_index": index,
                              "surface": segment["text"], "step_index": step_index,
                              **step})
        if steps[-1]["form"] != segment["text"]:
            raise ValueError(f"Korean form chain does not end at tap surface: {index}")
    used_words = {item["entry_id"] for item in word_uses}
    used_grammar = {item["entry_id"] for item in grammar_uses}
    if not used_words <= words.keys() or not used_grammar <= grammar.keys():
        raise ValueError("Korean dictionary entries do not match chapter usage")
    from pipeline.korean_levels import target_level
    level = target_level(chapter)
    source = {source_id: {
        "reader_id": f"honggildong_l{level}", "chapter": chapter.get("number", 1),
        "title": chapter["title"], "level": f"TOPIK {level}", "text": source_text,
    }}
    word_asset = {"schema_version": 1, "language": "korean", "sources": source,
                  "entries": list(words.values()), "occurrences": word_uses}
    grammar_asset = {"schema_version": 1, "language": "korean", "sources": source,
                     "entries": list(grammar.values()), "occurrences": grammar_uses,
                     "forms": form_uses}
    if write:
        write_assets(output_dir, word_asset, grammar_asset)
    return word_asset, grammar_asset


def write_assets(output_dir: Path, words: dict, grammar: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, asset in (("usage_dictionary_ko.json", words), ("grammar_dictionary_ko.json", grammar)):
        (output_dir / name).write_text(json.dumps(asset, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def build_collection(chapters: list[dict], output_dir: Path) -> tuple[dict, dict]:
    """Publish cumulative occurrences without discarding reusable entries."""
    words, grammar = _registry(WORDS), _registry(GRAMMAR)
    merged = [{"schema_version": 1, "language": "korean", "sources": {},
               "entries": list(registry.values()), "occurrences": []}
              for registry in (words, grammar)]
    merged[1]["forms"] = []
    for chapter in chapters:
        from pipeline.korean_levels import source_id as chapter_source_id
        source_id = chapter_source_id(chapter)
        assets = build_assets(chapter, output_dir, source_id=source_id,
                              word_registry=words, grammar_registry=grammar, write=False)
        for combined, asset in zip(merged, assets):
            if combined["sources"].keys() & asset["sources"].keys():
                raise ValueError("duplicate Korean dictionary chapter")
            combined["sources"].update(asset["sources"])
            combined["occurrences"].extend(asset["occurrences"])
        merged[1]["forms"].extend(assets[1]["forms"])
    write_assets(output_dir, *merged)
    return tuple(merged)
