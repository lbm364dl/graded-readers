"""Incrementally extend the reviewed dictionary across HSK1 through HSK6.

python -m pipeline.dictionary_corpus --update
python -m pipeline.dictionary_corpus --plan   # no model calls
python -m pipeline.dictionary_corpus --update --levels hsk1 hsk2 hsk3 hsk4 --workers 8
Registries retain their original HSK1 IDs; publication is shared across levels.
"""
import argparse
import asyncio
import fcntl
import json
from collections import Counter
from copy import deepcopy

from pipeline import usage_dictionary as lexical
from pipeline import chinese_reading_units as reading
from pipeline import expression_dictionary as expressions
from pipeline import dictionary_meaning_guides as guides
from pipeline import component_words
from pipeline.dictionary_component_links import extend as link_components
from pipeline.annotate_chinese import atomic_json

ROOT = lexical.ROOT
DIRECTORY = ROOT / 'content/lexicon'
MANIFEST = DIRECTORY / 'corpus.json'
EXPRESSIONS = DIRECTORY / 'corpus.expressions.json'
GUIDES = DIRECTORY / 'corpus.meaning-guides.json'
REQUESTS = DIRECTORY / 'corpus.editorial-requests.json'
REPORT = DIRECTORY / 'corpus.last-update.json'
LEVELS = ('hsk1', 'hsk2', 'hsk3', 'hsk4', 'hsk5', 'hsk6')


def selected_levels(levels=None):
    if levels is None:
        levels = json.loads(MANIFEST.read_text()).get('levels', ['hsk1', 'hsk2']) if MANIFEST.exists() else ['hsk1', 'hsk2']
    if not levels or set(levels) - set(LEVELS) or 'hsk1' not in levels:
        raise ValueError('Corpus levels must include HSK1 and use supported HSK1–HSK6 levels')
    return tuple(level for level in LEVELS if level in levels)


def selected_scope(scope=None):
    if scope is None:
        scope = json.loads(MANIFEST.read_text()).get('hsk2_scope', 'full') if MANIFEST.exists() else 'full'
    if scope not in {'sample', 'full'}:
        raise ValueError('Unknown HSK2 scope')
    return scope


def sample_range(text):
    paragraph = next(p for p in text.split('\n\n') if p.startswith('第二天，三人在桃园'))
    return text.index(paragraph), text.index(paragraph) + len(paragraph)


def sample_headwords(annotation):
    sources, _, occurrences, _ = lexical.source_data(annotation)
    ranges = {asset: sample_range(data['text']) for asset, data in sources.items()}
    return {o['surface'] for o in occurrences
            if ranges[o['source']][0] <= o['start'] and o['end'] <= ranges[o['source']][1]}


def reviewed_subset(decisions, headwords):
    if decisions.get('review_digest') != lexical.decision_digest(decisions['entries']):
        return False
    return decisions.get('reviewed') or all(decisions.get('reviewed_word_fingerprints', {}).get(word) ==
        lexical.decision_digest([e for e in decisions['entries'] if e['headword'] == word]) for word in headwords)


def select_sample(dictionary, units):
    """Index the oath/drinking paragraph; retain original text, offsets and IDs."""
    result, display = deepcopy(dictionary), deepcopy(units)
    selected = []
    for asset, data in result['sources'].items():
        text = data['text']
        start, end = sample_range(text)
        data['dictionary_coverage'] = dict(scope='sample', start=start, end=end)
        selected += [o for o in result['occurrences'] if o['source'] == asset and start <= o['start'] and o['end'] <= end]
        source_units = display['sources'][asset]
        segments = source_units['segments']
        source_units['units'] = [u for u in source_units['units'] if
            start <= sum(len(s[0]) for s in segments[:u['first_segment']]) and
            sum(len(s[0]) for s in segments[:u['last_segment'] + 1]) <= end]
    result['occurrences'] = selected
    used = {o['sense_id'] for o in selected}
    result['entries'] = [dict(e, senses=[s for s in e['senses'] if s['id'] in used])
                         for e in result['entries'] if any(s['id'] in used for s in e['senses'])]
    return result, display


def source(level):
    if level not in LEVELS:
        raise ValueError('Only HSK1 through HSK6 are supported')
    return ROOT / f'content/chinese/sanguoyanyi/{level}.annotations.json'


def merge_dictionaries(dictionaries):
    """Merge reviewed identities, never silently reconcile contradictory senses."""
    entries, sources, occurrences = {}, {}, []
    for dictionary in dictionaries:
        if sources.keys() & dictionary['sources'].keys():
            raise ValueError('Duplicate corpus source')
        sources.update(dictionary['sources'])
        occurrences += deepcopy(dictionary['occurrences'])
        for entry in dictionary['entries']:
            eid = entry['id']
            if eid not in entries:
                entries[eid] = deepcopy(entry)
                continue
            target = entries[eid]
            if any(target[k] != entry[k] for k in ('headword', 'reading', 'kind')):
                raise ValueError(f'Conflicting shared identity: {eid}')
            senses = {s['id']: s for s in target['senses']}
            for sense in entry['senses']:
                if sense['id'] in senses and senses[sense['id']] != sense:
                    raise ValueError(f'Conflicting shared sense: {sense["id"]}')
                senses[sense['id']] = sense
            target['senses'] = list(senses.values())
    if len({o['id'] for o in occurrences}) != len(occurrences):
        raise ValueError('Duplicate corpus occurrence')
    return dict(schema_version=1, source_fingerprint=lexical.decision_digest(occurrences),
                entries=sorted(entries.values(), key=lambda e: e['id']),
                sources=sources, occurrences=occurrences)


def record_observed_readings(dictionary):
    """Source pronunciation observations supplement, never rename, lexical IDs."""
    by_id = {entry['id']: entry for entry in dictionary['entries']}
    readings = {entry['id']: {entry['reading']} for entry in dictionary['entries']}
    for occurrence in dictionary['occurrences']:
        entry = by_id[occurrence['entry_id']]
        if occurrence['surface'] == entry['headword'] and occurrence.get('reading', '').strip():
            readings[entry['id']].add(occurrence['reading'])
    for entry in dictionary['entries']:
        entry['observed_readings'] = sorted(readings[entry['id']])
    return dictionary


def seed_senses(dictionary, path, annotation):
    """Only seed identities attested in this source; do not fabricate occurrences."""
    if path.exists():
        return
    _, words, _, _ = lexical.source_data(annotation)
    entries = []
    for entry in dictionary['entries']:
        if entry['headword'] not in words:
            continue
        seeded = {k: deepcopy(entry[k]) for k in ('id', 'headword', 'reading', 'kind', 'senses')}
        for sense in seeded['senses']:
            sense['occurrences'] = []
        entries.append(seeded)
    atomic_json(path, dict(schema_version=1, reviewed=False, entries=entries))


def seed_guides(dictionary):
    existing = json.loads(GUIDES.read_text()) if GUIDES.exists() else None
    if existing and (not existing.get('reviewed') or existing['review_digest'] != lexical.decision_digest(existing['guides'])):
        raise ValueError('Cannot seed into an unreviewed guide registry')
    rows = deepcopy(existing['guides']) if existing else []
    present = {r['entry_id'] for r in rows}
    needed = {e['id'] for e in dictionary['entries']}
    for path in (guides.DECISIONS, component_words.GUIDES):
        doc = json.loads(path.read_text())
        if not doc.get('reviewed') or doc['review_digest'] != lexical.decision_digest(doc['guides']):
            raise ValueError('Cannot seed unreviewed explanations')
        for row in doc['guides']:
            if row['entry_id'] in needed and row['entry_id'] not in present:
                rows.append(row)
                present.add(row['entry_id'])
    if not existing or rows != existing['guides']:
        atomic_json(GUIDES, dict(existing or {}, schema_version=2, reviewed=True, guides=rows,
                                review_digest=lexical.decision_digest(rows)))


def preserve_shared_metadata(decisions, known):
    """Occurrence linking cannot edit the shared dictionary's approved metadata.

    Additional senses are retained and reviewed. Corrections to approved senses
    must instead go through explicit dictionary editorial work.
    """
    old = {e['id']: e for e in known['entries']}
    result = deepcopy(decisions)
    changed_words = set()
    for entry in result['entries']:
        canonical = old.get(entry['id'])
        if canonical is None:
            continue
        if canonical['headword'] != entry['headword']:
            raise ValueError('Shared entry ID was reassigned')
        for key in ('reading', 'kind'):
            if entry[key] != canonical[key]:
                changed_words.add(entry['headword'])
            entry[key] = canonical[key]
        definitions = {s['id']: s['definition'] for s in canonical['senses']}
        for sense in entry['senses']:
            if sense['id'] in definitions:
                if sense['definition'] != definitions[sense['id']]:
                    changed_words.add(entry['headword'])
                sense['definition'] = definitions[sense['id']]
        present = {s['id'] for s in entry['senses']}
        entry['senses'] += [dict(s, occurrences=[]) for s in canonical['senses'] if s['id'] not in present]
    if result != decisions:
        # Newly approved shared senses with no local uses do not change any
        # contextual assignment. Changed metadata still requires review, but
        # must not discard valid approvals for unrelated headwords.
        words = {e['headword'] for e in decisions['entries']}
        whole_reviewed = (decisions.get('reviewed') is True and
                          decisions.get('review_digest') == lexical.decision_digest(decisions['entries']))
        previous_stamps = decisions.get('reviewed_word_fingerprints', {})
        approved = {word for word in words if whole_reviewed or previous_stamps.get(word) ==
                    lexical.decision_digest([e for e in decisions['entries'] if e['headword'] == word])}
        approved -= changed_words
        result['reviewed_word_fingerprints'] = {
            word: lexical.decision_digest([e for e in result['entries'] if e['headword'] == word])
            for word in approved}
        result['review_digest'] = lexical.decision_digest(result['entries'])
        result['reviewed'] = approved == words
    return result


def editorial_input(*, update=False, scope=None, levels=None):
    scope = selected_scope(scope)
    levels = selected_levels(levels)
    bases, units = [], dict(schema_version=1, sources={})
    pilot_decisions = json.loads(lexical.DECISIONS.read_text())
    pilot = lexical.build(pilot_decisions)
    pilot_units = reading.build(json.loads(reading.DECISIONS.read_text()))
    if update:
        # Validate and reuse the published identities before extending the corpus.
        # The source-local expression registry may already contain the next
        # corpus scope while its explanations are still pending. Rebuilding
        # the old manifest against that registry would fail as stale. The
        # published snapshot, not the in-progress registry, is the immutable
        # identity seed for an incremental update.
        published = json.loads(lexical.OUTPUT.read_text()) if MANIFEST.exists() else lexical.build_published(pilot_decisions)
        known = dict(entries=published['entries'], sources={}, occurrences=[])
    for level in levels:
        annotation = source(level)
        senses_path = DIRECTORY / f'{level}.senses.json'
        units_path = DIRECTORY / f'{level}.reading-units.json'
        subset = sample_headwords(annotation) if level == 'hsk2' and scope == 'sample' else None
        if update and level != 'hsk1':
            seed_senses(known, senses_path, annotation)
            run_dir = ROOT / f'runs/usage-dictionary-{level}'
            print(f'{level}: linking lexical occurrences (cached batches reused)', flush=True)
            asyncio.run(lexical.propose(senses_path, run_dir, 'gpt-6-luna', update=True, source=annotation))
            decisions = json.loads(senses_path.read_text())
            pinned = preserve_shared_metadata(decisions, known)
            if pinned != decisions:
                atomic_json(senses_path, pinned)
                decisions = pinned
            if not reviewed_subset(decisions, subset or {e['headword'] for e in decisions['entries']}):
                print(f'{level}: reviewing sense links and shared identities', flush=True)
                try:
                    asyncio.run(lexical.review(senses_path, run_dir, 'gpt-6-luna', source=annotation,
                                              frozen_entries=known['entries'], headwords=subset, rounds=6))
                except RuntimeError as error:
                    if 'agent timed out:' not in str(error):
                        raise
                    # Successful sibling reviews have already been persisted.
                    # Narrow only the remaining jobs, retaining all semantic gates.
                    print(f'{level}: timed-out review; retrying pending headwords in smaller batches', flush=True)
                    asyncio.run(lexical.review(senses_path, run_dir, 'gpt-6-luna', source=annotation,
                                              frozen_entries=known['entries'], headwords=subset, rounds=6,
                                              batch_size=5))
            final = json.loads(senses_path.read_text())
            if preserve_shared_metadata(final, known) != final:
                raise ValueError('Review attempted to rewrite shared definitions; use a targeted dictionary edit')
            print(f'{level}: reviewing learner tap units', flush=True)
            asyncio.run(reading.update(units_path, None, annotation))
            print(f'{level}: sense links and tap units ready', flush=True)
        decisions = json.loads(senses_path.read_text())
        if subset and not reviewed_subset(decisions, subset):
            raise ValueError('HSK2 excerpt has unreviewed sense links')
        level_base = lexical.build(decisions, annotation, require_review=subset is None)
        level_units = reading.build(json.loads(units_path.read_text()), annotation)
        if level == 'hsk2' and scope == 'sample':
            level_base, level_units = select_sample(level_base, level_units)
        bases.append(level_base)
        units['sources'].update(level_units['sources'])
        if update:
            # Later levels must reuse identities created by earlier levels in
            # this same run, not allocate duplicate IDs for shared new words.
            known = merge_dictionaries([known, dict(level_base, sources={}, occurrences=[])])
    base = merge_dictionaries(bases)
    if update and not EXPRESSIONS.exists():
        old = json.loads(expressions.DECISIONS.read_text())
        expressions.extend(pilot, pilot_units, old)
        old['candidate_fingerprints'] = expressions.candidate_fingerprints(pilot, pilot_units)
        atomic_json(EXPRESSIONS, old)
    if update:
        dictionary = asyncio.run(expressions.update(base, units, EXPRESSIONS))
        seed_guides(dictionary)
    else:
        dictionary = expressions.extend(base, units, json.loads(EXPRESSIONS.read_text()))
    return dictionary, units


def build(*, update=False, plan=False, publish=True, scope=None, workers=4, levels=None,
          editorial_only=False):
    scope = selected_scope(scope)
    levels = selected_levels(levels)
    dictionary, units = editorial_input(update=update and not editorial_only,
                                       scope=scope, levels=levels)
    input_digest = lexical.decision_digest([dictionary, units])
    previous = json.loads(GUIDES.read_text())
    requests = json.loads(REQUESTS.read_text()) if REQUESTS.exists() else []
    keep, jobs = guides.editorial_plan(dictionary, previous, requests)
    report = dict(levels=list(levels), hsk2_scope=scope, entries=len(dictionary['entries']),
                  occurrences=len(dictionary['occurrences']), reused_guides=len(keep),
                  editorial_jobs=[dict(id=j['id'], headword=j['item']['entry']['headword'], reason=j['reason'])
                                  for j in jobs])
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if plan:
        return report
    if update:
        dictionary = asyncio.run(guides.update(dictionary, GUIDES, requests=requests,
                                               run_dir=ROOT / 'runs/dictionary-meaning-guides-corpus', workers=workers))
    else:
        dictionary = guides.extend(dictionary, previous)
    edited_ids = {job['entry_id'] for job in jobs}
    report['editorial_routes'] = dict(Counter(
        entry['meaning_guide'].get('editorial_route', 'legacy')
        for entry in dictionary['entries'] if entry['id'] in edited_ids))
    dictionary = link_components(asyncio.run(component_words.expand(dictionary, update=update)))
    dictionary = record_observed_readings(dictionary)
    # Editorial jobs may run for a while. A concurrent source/registry edit must
    # never be hidden by publishing the old in-memory snapshot at the end.
    fresh_dictionary, fresh_units = editorial_input(scope=scope, levels=levels)
    if lexical.decision_digest([fresh_dictionary, fresh_units]) != input_digest:
        raise ValueError('Corpus changed during editing; completed jobs are cached, rerun --update')
    # Do not touch published assets until every pipeline stage has succeeded.
    if publish and update:
        from pipeline import dictionary_investigations as investigations
        rows = json.loads(GUIDES.read_text())['guides']
        if component_words.GUIDES.exists():
            rows += json.loads(component_words.GUIDES.read_text())['guides']
        previous_issues = (json.loads(investigations.PATH.read_text())
                           if investigations.PATH.exists() else None)
        notes = (json.loads(investigations.NOTES.read_text())
                 if investigations.NOTES.exists() else [])
        issue_document = investigations.refresh(dictionary['entries'], rows, previous_issues, notes)
    if publish:
        atomic_json(lexical.OUTPUT, dictionary)
        atomic_json(reading.OUTPUT, units)
        atomic_json(MANIFEST, dict(schema_version=1, levels=list(levels), hsk2_scope=scope))
        if update:
            atomic_json(REPORT, report)
            atomic_json(investigations.PATH, issue_document)
        print(f"Published {len(dictionary['entries'])} entries, {len(dictionary['occurrences'])} occurrences", flush=True)
    return dictionary


def run(**kwargs):
    with (DIRECTORY / 'corpus.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return build(**kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--edit-guides', action='store_true',
                        help='Update requested guides without redoing lexical/tap/expression agents')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--prepare', action='store_true', help='Match sources and print editorial plan without running dictionary editors')
    parser.add_argument('--scope', choices=['sample', 'full'], help='Default: full HSK2, or the already published scope')
    parser.add_argument('--levels', nargs='+', choices=LEVELS,
                        help='Enabled levels; defaults to the published corpus')
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=4,
                        help='Bound concurrent dictionary editorial jobs (default 4; maximum 8)')
    args = parser.parse_args()
    run(update=args.update or args.prepare or args.edit_guides,
        editorial_only=args.edit_guides, plan=args.plan or args.prepare,
        scope=args.scope, workers=args.workers, levels=args.levels)


if __name__ == '__main__':
    main()
