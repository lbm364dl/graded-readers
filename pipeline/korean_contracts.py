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
EXPRESSION_LINK = obj({'segment_index': {'type': 'integer', 'minimum': 0},
    'end_segment_index': {'type': 'integer', 'minimum': 0}, 'entry_id': STRING,
    'form': STRING, 'meaning_en': STRING, 'context_en': STRING})
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
                  "inflected_segment_indices": {"type": "array", "items": {"type": "integer"}},
                  'expression_links': {'type': 'array', 'items': EXPRESSION_LINK}},
                 required=['segments', 'grammar_links', 'inflected_segment_indices'])
PLAN = obj({"title": STRING, "scope_reason_en": NONEMPTY,
    "last_source_paragraph_index": {"type": "integer", "minimum": 0}, "beats": {"type": "array", "minItems": 1,
    "items": obj({"source_paragraph_index": {"type": "integer"}, "event_en": STRING})}})
FOCUS_ENTRY = obj({"id": NONEMPTY, "headword": NONEMPTY,
    "kind": {"type": "string", "enum": ["proper_name", "story_term"]},
    "aliases": {"type": "array", "minItems": 1, "items": NONEMPTY}, "role_en": NONEMPTY})
FOCUS = obj({"entries": {"type": "array", "items": FOCUS_ENTRY}})
PROSE = obj({"title": NONEMPTY, "text": NONEMPTY, "length_reason_en": NONEMPTY})
LEXICAL_CANDIDATES = obj({'headwords': {'type': 'array', 'items': NONEMPTY}})
WORD = obj({"id": STRING, "headword": STRING,
            "kind": {"type": "string", "enum": ["word", "proper_name", "story_term"]},
            "definition_en": STRING})
GRAMMAR = obj({"id": STRING, "title_en": STRING, "pattern": STRING, "explanation_en": STRING})
DICTIONARY = obj({"words": {"type": "array", "items": WORD},
                  "grammar": {"type": "array", "items": GRAMMAR}})
GRAMMAR_BINDINGS = obj({'bindings': {'type': 'array', 'items': obj({
    'draft_id': NONEMPTY, 'entry_id': NONEMPTY})}})
CURRICULUM_BINDINGS = obj({'level_reason_en': NONEMPTY, 'prose_revision_reason_en': STRING, 'bindings': {'type': 'array', 'items': obj({
    'entry_id': NONEMPTY, 'kind': {'type': 'string', 'enum': ['vocabulary', 'grammar']},
    'source_ids': {'type': 'array', 'items': NONEMPTY},
    'equivalence': {'type': 'string', 'enum': ['listed', 'productive', 'grammatical', 'unlisted']},
    'analysis_en': NONEMPTY, 'optional_reason_en': STRING})}})
ANNOTATION_REPAIR_PLAN = obj({'repairs': {'type': 'array', 'items': obj({
    'chunk_index': {'type': 'integer', 'minimum': 1},
    'issues': {'type': 'array', 'minItems': 1, 'items': NONEMPTY}})},
    'prose_revision_reason_en': STRING,
    'dictionary_revision_entry_ids': {'type': 'array', 'items': NONEMPTY}},
    required=['repairs', 'prose_revision_reason_en'])
ANNOTATION_REUSE_PLAN = obj({'reused_chunks': {'type': 'array', 'items': obj({
    'old_chunk_index': {'type': 'integer', 'minimum': 1},
    'new_chunk_index': {'type': 'integer', 'minimum': 1}})}})
PART = obj({"text": STRING, "explanation_en": STRING})
BREAKDOWNS = obj({"sentences": {"type": "array", "items": obj({
    "start": {"type": "integer"}, "sentence": STRING, "selected": {"type": "boolean"},
    "reason_en": STRING, "translation_en": STRING, "parts": {"type": "array", "items": PART}})}})


def schema_path(name: str) -> Path:
    return ROOT / "pipeline/schemas" / f"korean-{name}.schema.json"


def write_schemas() -> None:
    from pipeline.korean_annotation_chunks import SCHEMA as CHUNK_ANNOTATION, LEGACY_SCHEMA
    for name, schema in {"review": REVIEW, "plan": PLAN, "prose": PROSE,
                         "annotation": ANNOTATION, "chunk-annotation": LEGACY_SCHEMA,
                         "chunk-annotation-v2": CHUNK_ANNOTATION, "lexical-plan": FOCUS, "dictionary": DICTIONARY,
                         'grammar-bindings': GRAMMAR_BINDINGS,
                         'lexical-candidates': LEXICAL_CANDIDATES,
                         'curriculum': CURRICULUM_BINDINGS,
                         'annotation-repair-plan': ANNOTATION_REPAIR_PLAN,
                         'annotation-reuse-plan': ANNOTATION_REUSE_PLAN,
                         "breakdowns": BREAKDOWNS}.items():
        # Fresh structured outputs require all properties; older retained repair
        # evidence has no dictionary revision field and remains readable.
        output_schema = {**schema, "required": list(schema["properties"])}
        schema_path(name).write_text(json.dumps(output_schema, ensure_ascii=False, indent=2) + "\n")


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
    from pipeline.korean_curriculum import additional_lexical_candidates
    for entry in additional_lexical_candidates(set(result)):
        result.setdefault(entry['headword'], []).append(entry)
    from pipeline.korean_lexical_research import candidates as researched_candidates
    for entry in researched_candidates():
        result.setdefault(entry['headword'], []).append(entry)
    from pipeline.korean_dictionary import _registry, WORDS
    for entry in _registry(WORDS).values():
        if entry['kind'] not in ('word', 'story_term'):
            continue
        candidates = result.setdefault(entry['headword'], [])
        if not any(c['id'] == entry['id'] for c in candidates):
            candidates.append({'id': entry['id'], 'headword': entry['headword'],
                'pos': entry['id'].rsplit('/', 1)[-1] if '/' in entry['id'] else '',
                'meaning': entry['definition_en'], 'grade': None})
    return result


def bind_plan(plan: dict, source: str, source_start: int) -> dict:
    bound = {"title": plan["title"], "beats": []}
    if 'scope_reason_en' in plan:
        bound['scope_reason_en'] = plan['scope_reason_en']
    paragraphs = source.split("\n\n")
    if 'last_source_paragraph_index' in plan:
        last = plan['last_source_paragraph_index']
        if type(last) is not int or not 0 <= last < len(paragraphs):
            raise ValueError('Korean source stopping point is outside the remaining source')
        bound['scope'] = {'start': source_start,
                          'end': source_start + sum(len(p) + 2 for p in paragraphs[:last]) + len(paragraphs[last])}
    indices = [beat["source_paragraph_index"] for beat in plan["beats"]]
    if indices != sorted(set(indices)):
        raise ValueError("Korean source plan repeats or reorders paragraphs")
    for beat in plan["beats"]:
        index = beat["source_paragraph_index"]
        if (type(index) is not int or not 0 <= index < len(paragraphs)
                or ('last_source_paragraph_index' in plan and index > last)
                or not beat["event_en"].strip()):
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


def canonical_annotation(value: dict, prose: dict, number: int, edition: str, plan: dict, focus: dict | None = None, *, level: int = 1) -> dict:
    from pipeline.korean_levels import LEVEL_GOALS
    if type(level) is not int or level not in LEVEL_GOALS:
        raise ValueError("Korean target level must be 1–6")
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
    if level > 1:
        result["target_level"] = level
    if value.get('expression_links'):
        result['expression_links'] = value['expression_links']
    if focus is not None:
        result["lexical_focus"] = focus
    if 'scope' in plan:
        result['source_alignment']['unit'] = {'number': number, **plan['scope'], 'label': plan['title']}
    if 'length_reason_en' in prose:
        result['adaptation_decisions'] = {
            'scope_reason_en': plan['scope_reason_en'],
            'length_reason_en': prose['length_reason_en']}
    return result


def annotation_chunks(text: str, *, batch_characters: int = 0) -> list[str]:
    """Group complete adjacent sentences for model jobs, never shorten prose.

    A sentence longer than the job budget remains intact. Zero preserves the
    original one-sentence partition and its existing publication evidence.
    """
    if type(batch_characters) is not int or batch_characters < 0:
        raise ValueError('Korean annotation batch budget must be nonnegative')
    inventory = sentence_inventory(text)
    starts = [0] + [row["start"] for row in inventory[1:]] + [len(text)]
    sentences = [text[start:end] for start, end in zip(starts, starts[1:])]
    if not batch_characters:
        return sentences
    chunks, pending = [], ''
    for sentence in sentences:
        if pending and len(pending) + len(sentence) > batch_characters:
            chunks.append(pending)
            pending = ''
        pending += sentence
    if pending:
        chunks.append(pending)
    return chunks


def reference_view(entries: list[dict]) -> dict:
    """Keep every reference value while naming shared fields only once."""
    columns = list(entries[0]) if entries else []
    if any(set(entry) != set(columns) for entry in entries):
        raise ValueError('Reference rows need uniform fields; do not omit differing reference data')
    return {'format': 'lossless_reference_rows', 'columns': columns,
            'rows': [[entry[column] for column in columns] for entry in entries]}


def expand_reference_view(value):
    if not isinstance(value, dict) or value.get('format') != 'lossless_reference_rows':
        return value
    columns, rows = value['columns'], value['rows']
    if len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in rows):
        raise ValueError('Invalid lossless reference columns or rows')
    return [dict(zip(columns, row)) for row in rows]


def annotation_view(value: dict, *, max_characters: int = 800000) -> dict:
    """Losslessly pack large agent inputs; never trim annotation coverage."""
    import json
    if len(json.dumps(value, ensure_ascii=False)) <= max_characters:
        return value
    columns = list(WORD_SEGMENT['properties'])
    step_columns = list(STEP['properties'])
    link_columns = list(LINK['properties'])
    rows = []
    for index, segment in enumerate(value['segments']):
        row = []
        for column in columns:
            if column == 'form_steps':
                row.append([[step[field] for field in step_columns] for step in segment[column]])
            else:
                row.append(segment[column])
        rows.append([index, *row])
    result = {'format': 'lossless_annotation_rows', 'segment_columns': ['index', *columns],
        'form_step_columns': step_columns, 'segments': rows,
        'grammar_link_columns': link_columns,
        'grammar_links': [[link[column] for column in link_columns] for link in value['grammar_links']],
        'inflected_segment_indices': value['inflected_segment_indices']}
    if 'expression_links' in value:
        result['expression_links'] = value['expression_links']
    return result


def bind_grammar_identities(value: dict, bindings: dict, approved_ids: set) -> dict:
    """Apply explicitly proposed identities; never infer a pattern from spelling."""
    mapping = {row['draft_id']: row['entry_id'] for row in bindings['bindings']}
    used = {link['entry_id'] for link in value['grammar_links']}
    used.update(s['lexical_id'] for s in value['segments'] if s['lexical_kind'] == 'grammar')
    new = used - approved_ids
    if len(mapping) != len(bindings['bindings']) or mapping.keys() != new:
        raise ValueError('Korean grammar bindings must cover only new draft identities exactly once')
    if any(target not in used | approved_ids or mapping.get(target, target) != target
           for target in mapping.values()):
        raise ValueError('Korean grammar bindings need an existing or self-bound canonical identity')
    result = json.loads(json.dumps(value))
    for link in result['grammar_links']:
        link['entry_id'] = mapping.get(link['entry_id'], link['entry_id'])
    for segment in result['segments']:
        if segment['lexical_kind'] == 'grammar':
            segment['lexical_id'] = mapping.get(segment['lexical_id'], segment['lexical_id'])
        for step in segment['form_steps']:
            step['grammar_entry_ids'] = [mapping.get(identity, identity) for identity in step['grammar_entry_ids']]
    return result


def repair_selection(plan: dict, chunk_count: int) -> dict:
    selected = {row['chunk_index']: row['issues'] for row in plan['repairs']}
    if (len(selected) != len(plan['repairs']) or not selected
            or any(type(index) is not int or not 1 <= index <= chunk_count for index in selected)):
        raise ValueError('Korean repair plan repeats a chunk or escapes chapter coverage')
    return selected


def reuse_selection(plan: dict, old_texts: list[str], new_texts: list[str]) -> dict:
    selected, old_indices = {}, set()
    for row in plan['reused_chunks']:
        old, new = row['old_chunk_index'], row['new_chunk_index']
        if (type(old) is not int or type(new) is not int
                or not 1 <= old <= len(old_texts) or not 1 <= new <= len(new_texts)
                or old in old_indices or new in selected
                or old_texts[old - 1] != new_texts[new - 1]):
            raise ValueError('Korean reuse needs distinct occurrences with identical source text')
        selected[new] = old
        old_indices.add(old)
    if [selected[i] for i in sorted(selected)] != sorted(old_indices):
        raise ValueError('Korean reuse reorders source occurrences')
    return selected


def slice_annotations(value: dict, texts: list[str]) -> list[dict]:
    check_reconstruction(value['segments'], ''.join(texts))
    chunks, first = [], 0
    for text in texts:
        last, length = first, 0
        while length < len(text):
            length += len(value['segments'][last]['text'])
            last += 1
        if length != len(text):
            raise ValueError('Korean annotation slice crosses a tap')
        links = []
        for link in value['grammar_links']:
            if first <= link['segment_index'] < last:
                if link['display_end_segment_index'] >= last:
                    raise ValueError('Korean annotation slice crosses a construction')
                links.append({**link, 'segment_index': link['segment_index'] - first,
                    'display_end_segment_index': link['display_end_segment_index'] - first
                    if link['display_end_segment_index'] != -1 else -1})
        chunks.append({'segments': value['segments'][first:last], 'grammar_links': links,
                       'inflected_segment_indices': [index - first for index in value['inflected_segment_indices'] if first <= index < last]})
        if 'expression_links' in value:
            expressions = []
            for link in value['expression_links']:
                if first <= link['segment_index'] < last:
                    if link['end_segment_index'] >= last:
                        raise ValueError('Korean annotation slice crosses a lexical expression')
                    expressions.append({**link, 'segment_index': link['segment_index'] - first,
                        'end_segment_index': link['end_segment_index'] - first})
            chunks[-1]['expression_links'] = expressions
        first = last
    return chunks


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
        if 'expression_links' in value:
            result.setdefault('expression_links', [])
            for link in value['expression_links']:
                if not 0 <= link['segment_index'] <= link['end_segment_index'] < len(value['segments']):
                    raise ValueError('Korean expression link escapes its source chunk')
                result.setdefault('expression_links', []).append({**link,
                    'segment_index': link['segment_index'] + offset,
                    'end_segment_index': link['end_segment_index'] + offset})
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
