"""Publish reviewed, source-bound Korean word and grammar entries."""

from __future__ import annotations

import json
import hashlib
from functools import lru_cache
from pathlib import Path

from pipeline.korean_readability import ROOT


WORDS = ROOT / "content/lexicon/korean/l1.words.json"
GRAMMAR = ROOT / "content/lexicon/korean/l1.grammar.json"
SOURCE = "assets/annotations/korean_honggildong_l1_001.json"


def lexical_kind_matches(entry_kind: str, occurrence_kind: str) -> bool:
    """Story vocabulary is a passage role, not a different lexical identity.

    Preserve the historical registry tag while allowing that same ordinary word
    to appear without an exemption. Proper names and grammar remain distinct.
    """
    if entry_kind in ('word', 'story_term'):
        return occurrence_kind in ('word', 'vocabulary', 'story_term')
    return entry_kind == occurrence_kind


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
    if path.resolve() == GRAMMAR.resolve() or 'grammar_revision_reviews' in data:
        _verify_grammar_revision_history(data, by_id)
    return by_id


def _verify_grammar_revision_history(data: dict, by_id: dict[str, dict]) -> None:
    """Accept predecessor grammar entries only through complete reviewed coverage."""
    from pipeline.korean_agent_harness import digest
    latest: dict[str, dict] = {}
    for record in data.get('grammar_revision_reviews', []):
        if record.get('entry_kind') != 'grammar':
            raise ValueError('Korean grammar revision has the wrong entry kind')
        proposal, review = record.get('proposal'), record.get('review')
        coverage = record.get('coverage')
        context = {'entry_kind': 'grammar', 'before_entries': record.get('before_entries'),
                   'coverage': coverage}
        promotion = record.get('authenticated_dictionary_promotion')
        if 'dictionary_objection_accountability' in record and promotion is None:
            raise ValueError('Accountability history requires authenticated promotion')
        if promotion is not None:
            from pipeline.dictionary_reviewed_promotion import verify_history
            verify_history(record, promotion)
        if (not isinstance(proposal, dict) or not isinstance(review, dict)
                or record.get('proposal_digest') != digest(proposal)
                or record.get('review_digest') != digest(review)
                or (promotion is None and review != {'approved': True, 'issues': []})
                or (promotion is not None and (review.get('approved') is not True or review.get('issues') != []))
                or record.get('context_digest') != digest(context)):
            raise ValueError('Korean grammar revision lacks matching independent review')
        before_rows, after_rows = record.get('before_entries'), proposal.get('entries')
        if not isinstance(before_rows, list) or not isinstance(after_rows, list):
            raise ValueError('Korean grammar revision has malformed entry history')
        before = {entry['id']: entry for entry in before_rows}
        after = {entry['id']: entry for entry in after_rows}
        if not before or len(before) != len(before_rows) or len(after) != len(after_rows) or before.keys() != after.keys():
            raise ValueError('Korean grammar revision changes identity coverage')
        from pipeline.korean_dictionary_revision import check_revision
        try:
            check_revision(proposal, before, 'grammar')
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Korean grammar revision proposal is invalid') from error
        for identity, entry in after.items():
            if entry.get('id') != identity or entry.get('pattern') != before[identity].get('pattern'):
                raise ValueError('Korean grammar revision changes an identity or pattern')
            if identity in latest and latest[identity] != before[identity]:
                raise ValueError('Korean grammar revision history is discontinuous')
            latest[identity] = entry
        if not isinstance(coverage, dict) or coverage.get('entry_kind') != 'grammar':
            raise ValueError('Korean grammar revision lacks occurrence coverage')
        covered_files = coverage.get('files')
        published_files = coverage.get('published_files')
        chapter_digests = coverage.get('chapter_digests')
        if (not isinstance(covered_files, dict) or not isinstance(published_files, list)
                or not isinstance(chapter_digests, list)
                or len(set(published_files)) != len(published_files)
                or not set(published_files).issubset(covered_files)
                or {row.get('file') for row in chapter_digests if isinstance(row, dict)}
                   != set(published_files)
                or any(not isinstance(covered_files.get(name), str)
                       or len(covered_files[name]) != 64 for name in published_files)):
            raise ValueError('Korean grammar revision lacks complete published occurrence provenance')
        seen_chapters = set()
        for chapter_row in chapter_digests:
            if (not isinstance(chapter_row, dict) or chapter_row.get('file') not in published_files
                    or type(chapter_row.get('chapter')) is not int
                    or not isinstance(chapter_row.get('digest'), str)
                    or len(chapter_row['digest']) != 64):
                raise ValueError('Korean grammar revision has malformed chapter coverage')
            key = (chapter_row['file'], chapter_row['chapter'])
            if key in seen_chapters:
                raise ValueError('Korean grammar revision has duplicate chapter coverage')
            seen_chapters.add(key)
        occurrence_rows = coverage.get('occurrences')
        if not isinstance(occurrence_rows, list) or any(
                row.get('entry_id') not in before for row in occurrence_rows):
            raise ValueError('Korean grammar revision coverage has an invalid occurrence')
    if any(by_id.get(identity) != entry for identity, entry in latest.items()):
        raise ValueError('Korean revised grammar differs from independent review')


@lru_cache(maxsize=32)
def _sentence_ranges(text: str) -> tuple[tuple[str, int], ...]:
    from pipeline.korean_contracts import sentence_inventory
    return tuple((row['sentence'], row['start']) for row in sentence_inventory(text))


def _sentence(text: str, start: int) -> tuple[str, int]:
    for sentence, before in _sentence_ranges(text):
        if before <= start < before + len(sentence):
            return sentence, before
    raise ValueError('Korean dictionary occurrence is outside a source sentence')


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
        if entry is None or not lexical_kind_matches(entry['kind'], lexical['kind']):
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
    expression_seen = set()
    for link in chapter.get('expression_links', []):
        first, last, identity = link['segment_index'], link['end_segment_index'], link['entry_id']
        if (type(first) is not int or type(last) is not int or not 0 <= first <= last < len(segments)
                or segments[first]['type'] != 'word' or segments[last]['type'] != 'word'
                or identity not in words or words[identity]['kind'] != 'word'
                or not str(link['meaning_en']).strip() or not str(link['context_en']).strip()):
            raise ValueError('Invalid Korean lexical expression destination or span')
        surface = ''.join(segment['text'] for segment in segments[first:last + 1])
        if link['form'] != surface or not any(segment.get('lexical', {}).get('id') == identity
                for segment in segments[first:last + 1]):
            raise ValueError('Korean lexical expression must preserve its exact source form and attested component identity')
        key = (first, last, identity)
        if key in expression_seen:
            raise ValueError('Duplicate Korean lexical expression occurrence')
        expression_seen.add(key)
        start, end = positions[first][0], positions[last][1]
        sentence, sentence_start = _sentence(source_text, start)
        word_uses.append({'id': f'{source_id}#expression-{first}-{last}-{identity}',
            'source': source_id, 'occurrence_kind': 'expression', 'segment_index': first,
            'end_segment_index': last, 'start': start, 'end': end, 'surface': surface,
            'gloss': link['meaning_en'], 'context_en': link['context_en'], 'entry_id': identity,
            'sentence': sentence, 'sentence_start': sentence_start})
    links = chapter.get("grammar_links")
    if not isinstance(links, list):
        raise ValueError("Korean grammar occurrence review missing")
    grammar_uses = []
    seen = set()
    for link in links:
        index = link["segment_index"]
        entry_id = link["entry_id"]
        if entry_id in words:
            raise ValueError('Korean grammar link points to a word entry; use expression_links for a lexical expression')
        if (not isinstance(index, int) or not 0 <= index < len(segments)
                or segments[index]["type"] != "word" or entry_id not in grammar
                or not str(link.get("context_en", "")).strip()
                or (index, entry_id) in seen):
            nearby = ([{'segment_index': i, 'text': segments[i]['text'], 'type': segments[i]['type']}
                for i in range(max(0, index - 2), min(len(segments), index + 3))]
                if isinstance(index, int) else [])
            raise ValueError(f"invalid Korean grammar occurrence: {link}. Anchor indices count every segment, including spaces and punctuation. Nearby indexed segments: {nearby}. Grammar entry exists: {entry_id in grammar}; duplicate anchor/entry: {isinstance(index, int) and (index, entry_id) in seen}. Correct the occurrence anchor from the actual segment list; preserve source text and tap boundaries.")
        seen.add((index, entry_id))
        start, end = positions[index]
        stage_ids = {grammar_id for step in segments[index].get("form_steps", [])
                     for grammar_id in step["grammar_entry_ids"]}
        display_keys = {"display_form", "display_meaning_en", "display_end_segment_index"}
        display = {}
        if display_keys & link.keys() or (entry_id not in stage_ids and segments[index].get("form_steps")):
            if not display_keys <= link.keys():
                covering = []
                for other in links:
                    first, last = other.get('segment_index'), other.get('display_end_segment_index')
                    if (other is not link and other.get('entry_id') == entry_id
                            and type(first) is int and type(last) is int
                            and 0 <= first <= index <= last < len(segments)
                            and str(other.get('display_meaning_en', '')).strip()
                            and other.get('display_form') == source_text[positions[first][0]:positions[last][1]]):
                        covering.append({key: other[key] for key in
                            ('segment_index', 'display_end_segment_index', 'display_form', 'display_meaning_en')})
                advice = (' Existing source-aligned rows for this same lesson already cover this tap: '
                    + str(covering) + '. If this is the same construction, retain its original complete row and remove the redundant ending link; do not omit a distinct construction.') if covering else ''
                raise ValueError(f"Korean construction stage lacks complete form at segment {index} ({segments[index]['text']}): {link}. Supply its complete source phrase, whole meaning and inclusive ending index.{advice}")
            last = link["display_end_segment_index"]
            if (not isinstance(last, int) or not index <= last < len(segments)
                    or not str(link["display_meaning_en"]).strip()
                    or link["display_form"] != source_text[start:positions[last][1]]):
                raise ValueError(f"invalid Korean construction stage: {link}")
            display = {key: link[key] for key in display_keys}
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
    missing_lessons = [{'segment_index': index, 'text': segment['text'],
                        'entry_id': segment['lexical']['id']}
        for index, segment in enumerate(segments)
        if segment['type'] == 'word' and segment['lexical']['kind'] == 'grammar'
        and (index, segment['lexical']['id']) not in seen]
    if missing_lessons:
        raise ValueError(f"Korean grammar tap has no linked lesson: {missing_lessons}. "
            'Each grammar-kind tap needs its own grammar_links occurrence with that exact '
            'segment_index and entry_id. A complete phrase link beginning on another tap '
            'does not supply this direct lesson. In source-span format, attach the direct '
            'lesson to this tap with (-1, -1) and an empty display meaning; retain any '
            'distinct complete phrase occurrence. Preserve source text and tap boundaries.')
    form_audit = chapter.get("form_audit", {})
    inflected = form_audit.get("inflected_segment_indices")
    if form_audit.get('reviewed') is not True or not isinstance(inflected, list):
        raise ValueError("Korean form review is incomplete")
    if any(type(i) is not int or not 0 <= i < len(segments) for i in inflected):
        raise ValueError('invalid Korean form-review index')
    built = {i for i, segment in enumerate(segments) if segment.get('form_steps')}
    if len(set(inflected)) != len(inflected) or set(inflected) != built:
        missing_steps = [(i, segments[i]['text']) for i in sorted(set(inflected) - built)]
        missing_audit = [(i, segments[i]['text']) for i in sorted(built - set(inflected))]
        raise ValueError(f'Korean form review is incomplete: declared inflected taps without form_steps: {missing_steps}; '
            f'taps with form_steps missing from inflected_segment_indices: {missing_audit}; '
            f'duplicate audit indices: {len(inflected) - len(set(inflected))}. '
            'Review the actual forms; do not omit a real inflection or invent an intermediate stage just to satisfy the audit.')
    form_uses = []
    for index in inflected:
        if not isinstance(index, int) or not 0 <= index < len(segments):
            raise ValueError("invalid Korean form-review index")
        segment = segments[index]
        if segment["type"] != "word" or segment["lexical"]["kind"] == "grammar":
            raise ValueError(f"Korean form chain at segment {index} ({segment['text']!r}) needs an attested lexical base (a word or name), not a grammar identity. Retrieve the actual dictionary-form adjective/verb and link productive transformations separately; do not invent a lexical lemma or hide a missing base with a grammar tap.")
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
