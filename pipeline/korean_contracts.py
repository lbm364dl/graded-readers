"""Structured Korean generation contracts shared by agents and publication."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.korean_readability import ROOT, VOCAB_SOURCE


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": required or list(properties)}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
REVIEW = obj({"approved": {"type": "boolean"}, "issues": STRINGS})
STEP = obj({"form": STRING, "reading": STRING, "label": STRING,
            "meaning_en": STRING, "grammar_entry_ids": {"type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "string", "minLength": 1}}})
LINK = obj({"segment_index": {"type": "integer"}, "entry_id": STRING,
            "context_en": STRING, "display_form": STRING,
            "display_meaning_en": STRING, "display_end_segment_index": {"type": "integer"}})
NONEMPTY = {"type": "string", "minLength": 1}
WORD_SEGMENT = obj({"text": NONEMPTY, "type": {"type": "string", "enum": ["word"]},
    "meaning_en": NONEMPTY, "lemma": NONEMPTY,
    "lexical_kind": {"type": "string", "enum": ["vocabulary", "proper_name", "story_term", "grammar"]},
    "lexical_id": NONEMPTY, "story_importance_en": STRING,
    "form_steps": {"type": "array", "items": STEP}})
EMPTY = {"type": "string", "enum": [""]}
PUNCTUATION_SEGMENT = obj({"text": NONEMPTY, "type": {"type": "string", "enum": ["punctuation"]},
    "meaning_en": EMPTY, "lemma": EMPTY, "lexical_kind": EMPTY, "lexical_id": EMPTY,
    "story_importance_en": EMPTY, "form_steps": {"type": "array", "maxItems": 0, "items": STEP}})
SEGMENT = {"anyOf": [WORD_SEGMENT, PUNCTUATION_SEGMENT]}
ANNOTATION = obj({"segments": {"type": "array", "minItems": 1, "items": SEGMENT},
                  "grammar_links": {"type": "array", "items": LINK},
                  "inflected_segment_indices": {"type": "array", "items": {"type": "integer"}}})
PLAN = obj({"title": STRING, "beats": {"type": "array", "minItems": 1,
    "items": obj({"source_paragraph_index": {"type": "integer"}, "event_en": STRING})}})
FOCUS_ENTRY = obj({"id": NONEMPTY, "headword": NONEMPTY,
    "kind": {"type": "string", "enum": ["proper_name", "story_term"]},
    "aliases": {"type": "array", "minItems": 1, "items": NONEMPTY}, "role_en": NONEMPTY})
FOCUS = obj({"entries": {"type": "array", "items": FOCUS_ENTRY}})
PROSE = obj({"title": STRING, "text": STRING})
WORD = obj({"id": STRING, "headword": STRING,
            "kind": {"type": "string", "enum": ["word", "proper_name", "story_term"]},
            "definition_en": STRING})
GRAMMAR = obj({"id": STRING, "title_en": STRING, "pattern": STRING, "explanation_en": STRING})
DICTIONARY = obj({"words": {"type": "array", "items": WORD},
                  "grammar": {"type": "array", "items": GRAMMAR}})
PART = obj({"text": STRING, "explanation_en": STRING})
BREAKDOWNS = obj({"sentences": {"type": "array", "items": obj({
    "start": {"type": "integer"}, "sentence": STRING, "selected": {"type": "boolean"},
    "reason_en": STRING, "translation_en": STRING, "parts": {"type": "array", "items": PART}})}})


def schema_path(name: str) -> Path:
    return ROOT / "pipeline/schemas" / f"korean-{name}.schema.json"


def write_schemas() -> None:
    for name, schema in {"review": REVIEW, "plan": PLAN, "prose": PROSE,
                         "annotation": ANNOTATION, "lexical-plan": FOCUS, "dictionary": DICTIONARY,
                         "breakdowns": BREAKDOWNS}.items():
        schema_path(name).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n")


def lexical_catalog() -> dict[str, list[dict]]:
    """Exact headword candidates; homonym numbers remain in canonical IDs."""
    result: dict[str, list[dict]] = {}
    for row in VOCAB_SOURCE.read_text(encoding="utf-8").splitlines()[1:]:
        if not row:
            continue
        _, word, pos, meaning, grade = row.split("\t")
        headword = re.sub(r"\d+$", "", word)
        result.setdefault(headword, []).append({"id": f"{word}/{pos}",
            "headword": headword, "pos": pos, "meaning": meaning, "grade": grade})
    return result


def bind_plan(plan: dict, source: str, source_start: int) -> dict:
    bound = {"title": plan["title"], "beats": []}
    paragraphs = source.split("\n\n")
    indices = [beat["source_paragraph_index"] for beat in plan["beats"]]
    if indices != sorted(set(indices)):
        raise ValueError("Korean source plan repeats or reorders paragraphs")
    for beat in plan["beats"]:
        index = beat["source_paragraph_index"]
        if type(index) is not int or not 0 <= index < len(paragraphs) or not beat["event_en"].strip():
            raise ValueError("Korean source plan selects an invalid paragraph")
        quote = paragraphs[index]
        start = source_start + sum(len(p) + 2 for p in paragraphs[:index])
        bound["beats"].append({**beat, "quote": quote, "start": start, "end": start + len(quote)})
    return bound


def check_reconstruction(segments: list[dict], text: str) -> None:
    reconstructed = "".join(segment["text"] for segment in segments)
    if reconstructed != text:
        offset = next((i for i, (expected, actual) in enumerate(zip(text, reconstructed))
                       if expected != actual), min(len(text), len(reconstructed)))
        raise ValueError(f"Korean annotation reconstruction differs at Unicode offset {offset}: "
                         f"expected {text[offset:offset + 30]!r}, got {reconstructed[offset:offset + 30]!r}. "
                         "Preserve spaces and newlines in punctuation segments; do not rewrite prose.")


def canonical_annotation(value: dict, prose: dict, number: int, edition: str, plan: dict, focus: dict | None = None) -> dict:
    segments = []
    for segment in value["segments"]:
        item = {key: segment[key] for key in ("text", "type", "meaning_en")}
        if segment["type"] == "punctuation":
            if (segment["meaning_en"] or segment["lemma"] or segment["lexical_id"]
                    or segment["lexical_kind"] or segment["form_steps"]
                    or segment["story_importance_en"]):
                raise ValueError("Korean punctuation has lexical or form data")
        else:
            if not segment["lemma"].strip():
                raise ValueError("Korean word needs its reviewed dictionary headword")
            item["lexical"] = {"id": segment["lexical_id"], "kind": segment["lexical_kind"]}
            if segment["form_steps"]:
                item["form_steps"] = segment["form_steps"]
            if segment["story_importance_en"]:
                item["story_importance_en"] = segment["story_importance_en"]
        segments.append(item)
    links = []
    for link in value["grammar_links"]:
        item = {key: link[key] for key in ("segment_index", "entry_id", "context_en")}
        if link["display_form"]:
            item.update({key: link[key] for key in
                         ("display_form", "display_meaning_en", "display_end_segment_index")})
        elif link["display_meaning_en"] or link["display_end_segment_index"] != -1:
            raise ValueError("Korean construction display fields are incomplete")
        links.append(item)
    result = {"number": number, "title": f"{number}. {prose['title']}", "text": prose["text"],
            "segments": segments, "grammar_links": links,
            "annotation_audit": {"all_reviewed": True},
            "form_audit": {"reviewed": True, "inflected_segment_indices": value["inflected_segment_indices"]},
            "source_alignment": {"edition": edition, "reviewed": True, "beats": plan["beats"]}}
    if focus is not None:
        result["lexical_focus"] = focus
    return result


def annotation_chunks(text: str) -> list[str]:
    inventory = sentence_inventory(text)
    starts = [0] + [row["start"] for row in inventory[1:]] + [len(text)]
    return [text[start:end] for start, end in zip(starts, starts[1:])]


def combine_annotations(values: list[dict], texts: list[str]) -> dict:
    if len(values) != len(texts) or not values:
        raise ValueError("Korean annotation chunk coverage is incomplete")
    result = {"segments": [], "grammar_links": [], "inflected_segment_indices": []}
    for value, text in zip(values, texts):
        check_reconstruction(value["segments"], text)
        offset = len(result["segments"])
        for link in value["grammar_links"]:
            index, last = link["segment_index"], link["display_end_segment_index"]
            if not 0 <= index < len(value["segments"]) or (last != -1 and not index <= last < len(value["segments"])):
                raise ValueError("Korean annotation chunk link escapes its source")
            result["grammar_links"].append({**link, "segment_index": index + offset,
                "display_end_segment_index": last + offset if last != -1 else -1})
        if any(not 0 <= i < len(value["segments"]) for i in value["inflected_segment_indices"]):
            raise ValueError("Korean annotation chunk form index escapes its source")
        result["segments"].extend(value["segments"])
        result["inflected_segment_indices"].extend(i + offset for i in value["inflected_segment_indices"])
    return result


def sentence_inventory(text: str) -> list[dict]:
    result = []
    for match in re.finditer(r"[^.!?。！？]+[.!?。！？](?:[”’\"])?|[^.!?。！？]+$", text):
        sentence = match.group().strip()
        if sentence:
            start = match.start() + len(match.group()) - len(match.group().lstrip())
            result.append({"start": start, "sentence": sentence})
    return result


def selected_breakdowns(value: dict, chapter: dict, source: str) -> dict:
    inventory = sentence_inventory(chapter["text"])
    rows = value["sentences"]
    if [{"start": row["start"], "sentence": row["sentence"]} for row in rows] != inventory:
        raise ValueError("Korean sentence-help review must cover every sentence exactly once")
    selected = []
    for row in rows:
        if not row["reason_en"].strip():
            raise ValueError("Korean sentence-help decision lacks a reason")
        if row["selected"]:
            selected.append({"source": source, **{key: row[key] for key in
                ("start", "sentence", "translation_en", "parts")}})
        elif row["parts"] or row["translation_en"]:
            raise ValueError("Simple Korean sentences must not receive an unsolicited breakdown")
    return {"schema_version": 1, "reviewed": True, "breakdowns": selected, "audit": rows}
