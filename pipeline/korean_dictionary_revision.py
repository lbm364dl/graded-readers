"""Explicitly review shared word-entry corrections, then resume generation.

Normal generation reuses immutable approved entries. When independent review
finds inadequate sense coverage, this editorial workflow checks all published
uses plus the new draft, with primary dictionary evidence and a separate critic.
It never changes prose, taps, dictionary identities, or grammatical lessons.
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

REVISION = contracts.obj({
    'entries': {'type': 'array', 'minItems': 1, 'items': contracts.WORD},
    'references': {'type': 'array', 'minItems': 1, 'items': contracts.obj({
        'entry_id': contracts.NONEMPTY, 'url': contracts.NONEMPTY,
        'paraphrase_en': contracts.NONEMPTY})}})
DOMAINS = {'krdict.korean.go.kr', 'stdict.korean.go.kr', 'korean.go.kr', 'www.korean.go.kr'}


def coverage(ids: set[str], draft: Path | None) -> dict:
    """Preserve each distinct occurrence and audit every published Korean level."""
    sources = sorted((dictionary.ROOT / 'content/korean').rglob('*.annotations.json'))
    if draft is not None:
        sources.append(draft)
    result = {'files': {}, 'occurrences': []}
    for path in sources:
        document = read(path)
        chapters = document.get('chapters', [document])
        result['files'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for chapter in chapters:
            segments = chapter['segments']
            text = ''.join(s['text'] for s in segments)
            offset = 0
            for index, segment in enumerate(segments):
                identity = segment.get('lexical', {}).get('id', segment.get('lexical_id'))
                if identity in ids:
                    sentence, sentence_start = dictionary._sentence(text, offset)
                    result['occurrences'].append({'file': str(path), 'chapter': chapter.get('number', 1),
                        'segment_index': index, 'entry_id': identity, 'surface': segment['text'],
                        'meaning_en': segment['meaning_en'], 'sentence': sentence,
                        'sentence_start': sentence_start, 'start': offset,
                        'form_steps': segment.get('form_steps', []),
                        'grammar_links': [g for g in chapter['grammar_links'] if g['segment_index'] == index]})
                offset += len(segment['text'])
    return result


def check_revision(value: dict, before: dict) -> None:
    validate(value, REVISION)
    entries = {entry['id']: entry for entry in value['entries']}
    if len(entries) != len(value['entries']) or entries.keys() != before.keys():
        raise ValueError('Dictionary revision must cover exactly the requested IDs')
    for identity, entry in entries.items():
        if any(entry[key] != before[identity][key] for key in ('id', 'headword', 'kind')):
            raise ValueError('Dictionary revision cannot change lexical identities')
        if not entry['definition_en'].strip():
            raise ValueError('Dictionary revision needs a standalone definition')
    if {ref['entry_id'] for ref in value['references']} != before.keys():
        raise ValueError('Dictionary revision needs primary evidence for every entry')
    for ref in value['references']:
        parsed = urlparse(ref['url'])
        if parsed.scheme != 'https' or parsed.hostname not in DOMAINS or parsed.path in ('', '/'):
            raise ValueError('Dictionary revisions require direct primary dictionary references')


async def revise(ids: list[str], run_dir: Path, draft: Path | None, *, promote: bool = True,
                 runner=None) -> dict:
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Select unique dictionary IDs for editorial review')
    original = dictionary.WORDS.read_bytes()
    document = json.loads(original)
    registry = dictionary._registry(dictionary.WORDS)
    before = {identity: registry[identity] for identity in ids}
    context = {'before_entries': list(before.values()), 'coverage': coverage(set(ids), draft)}
    run_dir.mkdir(parents=True, exist_ok=True)
    schema = run_dir / 'revision.schema.json'
    save(schema, REVISION)
    runner = runner or CodexRunner(run_dir, 'gpt-6-luna', asyncio.Semaphore(1), timeout=600)
    previous, issues = None, []
    for attempt in range(4):
        value = await runner.call(f'revision-{attempt}',
            'Review these shared Korean word entries using primary NIKL dictionaries. Use web search to verify the observed senses, sense identities and roles. '
            'Correct only the supplied entries, preserving IDs, headwords, kinds and the valid earlier senses. '
            'Definitions must stand alone, use plain English, and cover legitimate reviewed uses without copying passage-specific glosses. '
            'For polysemous words distinguish relevant core meanings concisely. Do not dump every dictionary sense or tense form. '
            'An auxiliary can have several grammatical functions; explain its independent helper role accurately and leave each complete pattern lesson to its grammar entry. '
            'Do not invent lexical entries for productive grammar. Do not embed reviewer instructions or one passage\'s ambiguities in learner-facing definitions. '
            'Keep uncertainty in research records. Give direct primary reference URLs and short paraphrases of the evidence for each entry. '
            + payload(**context, previous=previous, issues=issues), schema, 'low', tool_profile='research')
        check_revision(value, before)
        review = await runner.call(f'revision-review-{attempt}',
            'Independently review these proposed shared dictionary corrections against the primary evidence and ALL supplied published/draft occurrences. '
            'Check same lexical identity/readings/POS, preserved valid senses, additional sense coverage, standalone clear definitions and accurate auxiliary roles. '
            'Reject manufactured idiom meanings, unsupported senses, pattern catalogs and editorial disclaimers. '
            'There is no requirement to add unrelated senses; a concise sufficient definition is best. '
            'Approve only with no issues. Output JSON only and do not call tools. '
            + payload(**context, proposal=value), contracts.schema_path('review'), 'low', tool_profile='offline')
        validate(review, contracts.REVIEW)
        if approved(review):
            break
        previous, issues = value, review['issues']
    else:
        raise ValueError(f'Dictionary correction failed independent review: {issues}')
    record = {'before_entries': list(before.values()), 'proposal': value,
              'proposal_digest': digest(value), 'review': review, 'review_digest': digest(review),
              'context_digest': digest(context), 'coverage': context['coverage']}
    save(run_dir / 'accepted-revision.json', record)
    if promote:
        if dictionary.WORDS.read_bytes() != original:
            raise ValueError('Dictionary changed during editorial review; re-review before applying')
        revised = {entry['id']: entry for entry in value['entries']}
        document['entries'] = [revised.get(entry['id'], entry) for entry in document['entries']]
        document.setdefault('revision_reviews', []).append(record)
        save(dictionary.WORDS, document)
        dictionary._registry(dictionary.WORDS)  # Check the persisted evidence.
    return {'status': 'updated' if promote else 'reviewed', 'entries': ids,
            'audited_occurrences': len(context['coverage']['occurrences'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry-id', action='append', required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--draft-annotation', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(revise(args.entry_id, args.run_dir, args.draft_annotation,
        promote=not args.check)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
