"""Explicitly review shared Korean dictionary corrections, then resume generation.

Normal generation reuses immutable approved entries. When independent review
finds inadequate word-sense or grammar-function coverage, this editorial
workflow checks all published uses plus supplied drafts, with primary evidence
and a separate critic. It never changes prose, taps, or dictionary identities.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import validate

from pipeline.agent_harness import CodexRunner
from pipeline import korean_contracts as contracts
from pipeline import korean_dictionary as dictionary
from pipeline.korean_agent_harness import approved, digest, payload, read, save

WORD_REVISION = contracts.obj({
    'entries': {'type': 'array', 'minItems': 1, 'items': contracts.WORD},
    'references': {'type': 'array', 'minItems': 1, 'items': contracts.obj({
        'entry_id': contracts.NONEMPTY, 'url': contracts.NONEMPTY,
        'paraphrase_en': contracts.NONEMPTY})}})
GRAMMAR_REVISION = contracts.obj({
    'entries': {'type': 'array', 'minItems': 1, 'items': contracts.GRAMMAR},
    'references': {'type': 'array', 'minItems': 1, 'items': contracts.obj({
        'entry_id': contracts.NONEMPTY, 'url': contracts.NONEMPTY,
        'paraphrase_en': contracts.NONEMPTY})}})
# Preserve the old import name for callers that validate word revisions.
REVISION = WORD_REVISION
DOMAINS = {'krdict.korean.go.kr', 'stdict.korean.go.kr', 'korean.go.kr', 'www.korean.go.kr'}


def _draft_paths(draft: Path | list[Path] | tuple[Path, ...] | None) -> list[Path]:
    if draft is None:
        return []
    if isinstance(draft, (list, tuple)):
        return [Path(path) for path in draft]
    return [Path(draft)]


def coverage(ids: set[str], draft: Path | list[Path] | tuple[Path, ...] | None,
             entry_kind: str = 'word', *, _published_paths: list[Path] | None = None) -> dict:
    """Preserve occurrence positions across all published levels and supplied drafts."""
    if entry_kind not in {'word', 'grammar'}:
        raise ValueError('Dictionary coverage kind must be word or grammar')
    sources = (sorted((dictionary.ROOT / 'content/korean').rglob('*.annotations.json'))
               if _published_paths is None else sorted(Path(p) for p in _published_paths))
    drafts = _draft_paths(draft)
    published_sources = list(sources)
    sources.extend(drafts)
    result = {'entry_kind': entry_kind, 'files': {},
              'published_files': [str(path) for path in published_sources],
              'draft_files': [str(path) for path in drafts], 'occurrences': [],
              'chapter_digests': []}
    for path in sources:
        document = read(path)
        chapters = document.get('chapters', [document])
        result['files'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for chapter in chapters:
            if path in published_sources:
                result['chapter_digests'].append({'file': str(path),
                    'chapter': chapter.get('number', 1), 'digest': digest(chapter)})
            segments = chapter['segments']
            text = ''.join(s['text'] for s in segments)
            positions = []
            offset = 0
            for segment in segments:
                positions.append((offset, offset + len(segment['text'])))
                offset += len(segment['text'])
            if entry_kind == 'word':
                for index, segment in enumerate(segments):
                    identity = segment.get('lexical', {}).get('id', segment.get('lexical_id'))
                    if identity in ids:
                        sentence, sentence_start = dictionary._sentence(text, positions[index][0])
                        result['occurrences'].append({'file': str(path), 'chapter': chapter.get('number', 1),
                            'occurrence_kind': 'word', 'segment_index': index, 'entry_id': identity,
                            'surface': segment['text'], 'meaning_en': segment['meaning_en'],
                            'sentence': sentence, 'sentence_start': sentence_start,
                            'start': positions[index][0], 'form_steps': segment.get('form_steps', []),
                            'grammar_links': [g for g in chapter.get('grammar_links', [])
                                             if g['segment_index'] == index]})
                continue
            grammar_links = chapter.get('grammar_links', [])
            for index, segment in enumerate(segments):
                lexical = segment.get('lexical', {})
                lexical_id = lexical.get('id', segment.get('lexical_id'))
                if lexical.get('kind') == 'grammar' and lexical_id in ids:
                    sentence, sentence_start = dictionary._sentence(text, positions[index][0])
                    result['occurrences'].append({'file': str(path), 'chapter': chapter.get('number', 1),
                        'occurrence_kind': 'grammar_tap', 'segment_index': index,
                        'entry_id': lexical_id, 'surface': segment['text'], 'sentence': sentence,
                        'sentence_start': sentence_start, 'start': positions[index][0],
                        'end': positions[index][1]})
                for step_index, step in enumerate(segment.get('form_steps', [])):
                    for identity in step.get('grammar_entry_ids', []):
                        if identity in ids:
                            sentence, sentence_start = dictionary._sentence(text, positions[index][0])
                            result['occurrences'].append({
                                'file': str(path), 'chapter': chapter.get('number', 1),
                                'occurrence_kind': 'form_step', 'segment_index': index,
                                'form_step_index': step_index, 'entry_id': identity,
                                'segment_surface': segment['text'], 'surface': step.get('form', ''),
                                'label': step.get('label', ''), 'reading': step.get('reading', ''),
                                'meaning_en': step.get('meaning_en', ''),
                                'lexical_id': lexical.get('id', segment.get('lexical_id', '')),
                                'sentence': sentence, 'sentence_start': sentence_start,
                                'start': positions[index][0], 'end': positions[index][1],
                                'sibling_grammar_links': [g for g in grammar_links
                                                          if g['segment_index'] == index]})
                for link in grammar_links:
                    if link.get('entry_id') in ids and link.get('segment_index') == index:
                        # This branch is intentionally explicit: grammar-link identities
                        # are independent occurrences from same-index form-step uses.
                        last = link.get('display_end_segment_index', -1)
                        displayed = bool(link.get('display_form'))
                        end_segment = last if displayed and type(last) is int and index <= last < len(segments) else index
                        sentence, sentence_start = dictionary._sentence(text, positions[index][0])
                        result['occurrences'].append({
                            'file': str(path), 'chapter': chapter.get('number', 1),
                            'occurrence_kind': 'grammar_link', 'segment_index': index,
                            'end_segment_index': end_segment, 'entry_id': link['entry_id'],
                            'surface': link.get('display_form') or segment['text'],
                            'display_meaning_en': link.get('display_meaning_en', ''),
                            'context_en': link.get('context_en', ''),
                            'anchor_surface': segment['text'], 'sentence': sentence,
                            'sentence_start': sentence_start, 'start': positions[index][0],
                            'end': positions[end_segment][1],
                            'form_steps': segment.get('form_steps', [])})
    return result


def check_revision(value: dict, before: dict, entry_kind: str = 'word') -> None:
    if entry_kind not in {'word', 'grammar'}:
        raise ValueError('Dictionary revision kind must be word or grammar')
    validate(value, WORD_REVISION if entry_kind == 'word' else GRAMMAR_REVISION)
    entries = {entry['id']: entry for entry in value['entries']}
    if len(entries) != len(value['entries']) or entries.keys() != before.keys():
        raise ValueError('Dictionary revision must cover exactly the requested IDs')
    for identity, entry in entries.items():
        immutable = ('id', 'headword', 'kind') if entry_kind == 'word' else ('id', 'pattern')
        if any(entry[key] != before[identity][key] for key in immutable):
            identity_name = 'lexical identities' if entry_kind == 'word' else 'grammar identities or patterns'
            raise ValueError(f'Dictionary revision cannot change {identity_name}')
        required_text = ('definition_en',) if entry_kind == 'word' else ('title_en', 'explanation_en')
        if any(not entry[key].strip() for key in required_text):
            requirement = 'standalone definition' if entry_kind == 'word' else 'standalone grammar title and explanation'
            raise ValueError(f'Dictionary revision needs a {requirement}')
    if {ref['entry_id'] for ref in value['references']} != before.keys():
        raise ValueError('Dictionary revision needs primary evidence for every entry')
    for ref in value['references']:
        parsed = urlparse(ref['url'])
        if parsed.scheme != 'https' or parsed.hostname not in DOMAINS or parsed.path in ('', '/'):
            raise ValueError('Dictionary revisions require direct primary dictionary references')


def _verify_coverage_fresh(snapshot: dict) -> None:
    expected_published = sorted(snapshot.get('published_files', []))
    current_published = sorted(str(path) for path in
                               (dictionary.ROOT / 'content/korean').rglob('*.annotations.json'))
    if current_published != expected_published:
        raise ValueError('Published occurrence coverage changed during dictionary review')
    for filename, expected in snapshot.get('files', {}).items():
        path = Path(filename)
        try:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError(f'Occurrence coverage source disappeared during dictionary review: {path}') from error
        if current != expected:
            raise ValueError(f'Occurrence coverage changed during dictionary review: {path}')


async def revise(ids: list[str], run_dir: Path,
                 draft: Path | list[Path] | tuple[Path, ...] | None, *,
                 promote: bool = True, runner=None, entry_kind: str = 'word') -> dict:
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Select unique dictionary IDs for editorial review')
    if entry_kind not in {'word', 'grammar'}:
        raise ValueError('Dictionary revision kind must be word or grammar')
    registry_path = dictionary.WORDS if entry_kind == 'word' else dictionary.GRAMMAR
    original = registry_path.read_bytes()
    document = json.loads(original)
    registry = dictionary._registry(registry_path)
    missing = [identity for identity in ids if identity not in registry]
    if missing:
        raise ValueError(f'Selected {entry_kind} dictionary ID is not an approved entry: {missing}')
    before = {identity: registry[identity] for identity in ids}
    context = {'entry_kind': entry_kind, 'before_entries': list(before.values()),
               'coverage': coverage(set(ids), draft, entry_kind)}
    run_dir.mkdir(parents=True, exist_ok=True)
    schema = run_dir / f'{entry_kind}-revision.schema.json'
    save(schema, WORD_REVISION if entry_kind == 'word' else GRAMMAR_REVISION)
    runner = runner or CodexRunner(run_dir, 'gpt-6-luna', asyncio.Semaphore(1), timeout=600)
    previous, issues = None, []
    if entry_kind == 'word':
        research_prompt = (
            'Review these shared Korean word entries using primary NIKL dictionaries. Use web search to verify the observed senses, sense identities and roles. '
            'Correct only the supplied entries, preserving IDs, headwords, kinds and the valid earlier senses. '
            'Definitions must stand alone, use plain English, and cover legitimate reviewed uses without copying passage-specific glosses. '
            'For polysemous words distinguish relevant core meanings concisely. Do not dump every dictionary sense or tense form. '
            'An auxiliary can have several grammatical functions; explain its independent helper role accurately and leave each complete pattern lesson to its grammar entry. '
            'Do not invent lexical entries for productive grammar. Do not embed reviewer instructions or one passage\'s ambiguities in learner-facing definitions. '
            'Keep uncertainty in research records. Give direct primary reference URLs and short paraphrases of the evidence for each entry. ')
        review_prompt = (
            'Independently review these proposed shared dictionary corrections against the primary evidence and ALL supplied published/draft occurrences. '
            'Check same lexical identity/readings/POS, preserved valid senses, additional sense coverage, standalone clear definitions and accurate auxiliary roles. '
            'Reject manufactured idiom meanings, unsupported senses, pattern catalogs and editorial disclaimers. '
            'There is no requirement to add unrelated senses; a concise sufficient definition is best. ')
    else:
        research_prompt = (
            'Review these existing shared Korean grammar entries using primary Korean grammatical references and dictionaries. Use web search and supplied references to verify each claimed function. '
            'Revise only the supplied entries; preserve every stable entry ID and pattern. Explain the pattern itself in clear standalone English, covering only distinct legitimate functions supported by primary evidence and all supplied published/draft occurrences. '
            'Distinguish related but different constructions when the same form has separate functions; do not force an entry to cover a function its evidence does not support. '
            'Keep each entry focused on its pattern. Do not add catalogs of tense, politeness, negation, or unrelated combinations, passage-specific meanings, reviewer instructions, or learner-facing disclaimers. '
            'Do not create another identity or change pattern spelling to make an occurrence fit. Record uncertainty in research, not in the proposed learner-facing explanation. '
            'Give direct primary reference URLs and short paraphrases of the evidence for each entry. ')
        review_prompt = (
            'Independently review the proposed Korean grammar-entry revision against the primary evidence and ALL supplied published/draft occurrences. '
            'Check that IDs and patterns remain stable, each explanation stands alone and accurately covers the distinct functions actually supported by evidence, and unrelated grammar combinations remain separate. '
            'Reject unsupported function expansion, function conflation, pattern catalogs, passage-specific disclaimers, or any change that merely hides a mismatched occurrence. '
            'The review is not approval of the annotations; affected occurrences still undergo their existing independent review and publication checks. ')
    for attempt in range(4):
        value = await runner.call(f'revision-{attempt}',
            research_prompt + payload(**context, previous=previous, issues=issues),
            schema, 'low', tool_profile='research')
        check_revision(value, before, entry_kind)
        review_profile = 'research' if entry_kind == 'grammar' else 'offline'
        review_instruction = ('Approve only with no issues. Output JSON only and use tools to check primary references. '
            if entry_kind == 'grammar' else 'Approve only with no issues. Output JSON only and do not call tools. ')
        review = await runner.call(f'revision-review-{attempt}',
            review_prompt + review_instruction + payload(**context, proposal=value),
            contracts.schema_path('review'), 'low', tool_profile=review_profile)
        validate(review, contracts.REVIEW)
        if approved(review):
            break
        previous, issues = value, review['issues']
    else:
        raise ValueError(f'Dictionary correction failed independent review: {issues}')
    if registry_path.read_bytes() != original:
        raise ValueError('Dictionary changed during editorial review; re-review before applying')
    _verify_coverage_fresh(context['coverage'])
    record = {'entry_kind': entry_kind, 'before_entries': list(before.values()), 'proposal': value,
              'proposal_digest': digest(value), 'review': review, 'review_digest': digest(review),
              'context_digest': digest(context), 'coverage': context['coverage']}
    save(run_dir / 'accepted-revision.json', record)
    if promote:
        if registry_path.read_bytes() != original:
            raise ValueError('Dictionary changed during editorial review; re-review before applying')
        _verify_coverage_fresh(context['coverage'])
        revised = {entry['id']: entry for entry in value['entries']}
        document['entries'] = [revised.get(entry['id'], entry) for entry in document['entries']]
        history_key = 'revision_reviews' if entry_kind == 'word' else 'grammar_revision_reviews'
        document.setdefault(history_key, []).append(record)
        temp_path = run_dir / f'{entry_kind}-registry-proposal.json'
        temp_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        dictionary._registry(temp_path)
        save(registry_path, document)
        dictionary._registry(registry_path)
    return {'status': 'updated' if promote else 'reviewed', 'entry_kind': entry_kind, 'entries': ids,
            'audited_occurrences': len(context['coverage']['occurrences'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry-id', action='append', required=True)
    parser.add_argument('--entry-kind', choices=('word', 'grammar'), default='word')
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--draft-annotation', type=Path, action='append',
                        help='Draft annotation file to include; may be repeated')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    drafts = args.draft_annotation or None
    print(json.dumps(asyncio.run(revise(args.entry_id, args.run_dir, drafts,
        promote=not args.check, entry_kind=args.entry_kind)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
