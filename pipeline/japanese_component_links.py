"""Reviewed dictionary-part destinations: words and grammar, not fake stem words."""
from pipeline import japanese_usage_dictionary as words
from collections import Counter
import json
import jsonschema

REGISTRY = words.DIRECTORY / 'component-links.json'
SCHEMA = words.ROOT / 'pipeline/schemas/japanese-component-links.schema.json'
POLICY = """Resolve reusable JAPANESE dictionary components to words and grammar.
Input parts are literal written spans, NOT necessarily dictionary headwords.
Inflected stems must link to their base verb/adjective and the relevant grammar:
食べ in 食べ物 can link to 食べる and the verb stem; 悪く links to 悪い and the
i-adjective adverbial form. Do not invent dictionary words for inflected kana
fragments. Likewise 投げ/取り/動き in verb compounds usually name verb stems;
do not substitute a same-written noun when the contribution is verbal.
Pure grammatical parts (e.g. まい) link to grammar, not a fabricated lexical word.
Genuine compound members, prefixes and suffixes may have lexical entries. Reuse
the supplied word and grammar IDs. Never merge different readings or functions.
For a functional particle inside a construction, select the precise lesson for
its role in that construction, not every lesson associated with the spelling.
Do not omit prefixes or honorific suffixes merely because they are not standalone
words: a genuine lexical affix can have its own entry, while a grammatical
transformation uses a grammar link. Every requested part needs a valid destination.
Create a new word only when a genuine lexical base/member is absent from the
catalog. New word definitions must stand alone, not describe this parent word.
The functional_entries catalog lists existing particles and constructions that
are deliberately excluded from words. Do not recreate those identities as new
words: select the grammar lesson matching the component's actual function.
Return every requested candidate exactly once. For an existing word set entry_id;
for a new word set new_headword/new_reading; for grammar use grammar_entry_ids.
Unused string fields are empty. Every component needs at least one destination.
New entries have headword, reading, kind=word/name/expression, and definitions;
the host assigns stable IDs. No historical claims or tools. JSON only.
"""


def requests(dictionary):
    """Stable, passage-independent routing evidence for unresolved word parts."""
    result = []
    by_id = {e['id']: e for e in dictionary['entries']}
    for entry in dictionary['entries']:
        if entry.get('superseded_by'):
            continue
        for index, part in enumerate(entry['meaning_guide']['parts']):
            if part['text'] == entry['headword']:
                continue
            if part.get('grammar_entry_ids'):
                continue
            target = by_id.get(part.get('entry_id'), {})
            functional = target.get('kind') in ('particle', 'construction')
            if part.get('entry_id') and not functional:
                continue
            if len(part['text']) < 2 and not functional:
                continue
            identity = dict(parent_entry_id=entry['id'], part_index=index,
                text=part['text'], contribution_en=part['contribution_en'])
            result.append(dict(id='ja-part-' + words.decision_digest(identity)[:16],
                **identity, parent_headword=entry['headword'],
                parent_reading=entry['reading'], senses=entry['senses'],
                explanation_en=entry['meaning_guide']['explanation_en']))
    return result


def attach(dictionary):
    registry = words.read(REGISTRY, {})
    if not registry:
        return reverse_links(dictionary)
    bindings = registry['bindings']
    if not registry.get('reviewed') or registry.get('review_digest') != words.decision_digest(bindings):
        raise ValueError('Unreviewed Japanese component destinations')
    by_parent = {e['id']: e for e in dictionary['entries']}
    for binding in bindings:
        entry = by_parent.get(binding['parent_entry_id'])
        if not entry or binding['part_index'] >= len(entry['meaning_guide']['parts']):
            continue
        part = entry['meaning_guide']['parts'][binding['part_index']]
        if (part['text'], part['contribution_en']) != (binding['text'], binding['contribution_en']):
            continue  # A changed explanation requires a fresh reviewed binding.
        if binding['entry_id']:
            if binding['entry_id'] not in by_parent:
                raise ValueError('Missing Japanese component word destination')
            part['entry_id'] = binding['entry_id']
            part.pop('character_ref', None)
        elif binding['grammar_entry_ids']:
            part.pop('entry_id', None)  # Remove the superseded generic grammar lookup.
        if binding['grammar_entry_ids']:
            part['grammar_entry_ids'] = binding['grammar_entry_ids']
    return reverse_links(dictionary)


def reverse_links(dictionary):
    """Index final reviewed destinations, not superseded preliminary guesses."""
    entries = {e['id']: e for e in dictionary['entries']}
    for entry in entries.values():
        entry.pop('component_uses', None)
    for parent in entries.values():
        if parent.get('superseded_by'):
            continue
        for part in parent['meaning_guide']['parts']:
            target = entries.get(part.get('entry_id'))
            if not target:
                continue
            if target.get('superseded_by'):
                target = entries[target['superseded_by']]
            target.setdefault('component_uses', []).append(dict(
                parent_entry_id=parent['id'], contribution_en=part['contribution_en']))
    return dictionary


def validate(data, rows, dictionary, grammar):
    jsonschema.validate(data, words.read(SCHEMA))
    wanted = {r['id'] for r in rows}
    assignments = data['bindings']
    if len(assignments) != len(wanted) or {b['candidate_id'] for b in assignments} != wanted:
        returned = [b['candidate_id'] for b in assignments]
        raise ValueError('Components need exact reviewed destination coverage: '
            f'missing={sorted(wanted - set(returned))}; '
            f'unknown={sorted(set(returned) - wanted)}; '
            f'duplicated={sorted({i for i in returned if returned.count(i) > 1})}')
    existing = {e['id']: e for e in dictionary['entries']}
    known = {(e['headword'], e['reading']) for e in existing.values()}
    new = {(e['headword'], e['reading']) for e in data['entries']}
    if len(new) != len(data['entries']) or new & known:
        copies = Counter((e['headword'], e['reading']) for e in data['entries'])
        conflicts = new & known | {identity for identity, count in copies.items() if count > 1}
        raise ValueError('Duplicate component lexical identity: ' + ', '.join(
            f'{headword} ({reading})' for headword, reading in sorted(conflicts)) +
            '. Reuse existing lexical words; existing functional particles/constructions '
            'must use grammar_entry_ids, not new word entries.')
    grammar_ids = {e['id'] for e in grammar['entries']}
    declared = {(b['new_headword'], b['new_reading']) for b in assignments if b['new_headword']}
    definition_errors = []
    for label, identities in [('Undefined new component word', declared - new),
                              ('Unrequested component entries', new - declared)]:
        if identities:
            definition_errors.append(label + ': ' + ', '.join(
                f'{headword} ({reading})' for headword, reading in sorted(identities)))
    if definition_errors:
        raise ValueError('; '.join(definition_errors))
    referenced = set()
    for b in assignments:
        if len(b['grammar_entry_ids']) != len(set(b['grammar_entry_ids'])):
            raise ValueError('Duplicate component grammar destination')
        is_new = bool(b['new_headword'] or b['new_reading'])
        if bool(b['new_headword']) != bool(b['new_reading']) or (is_new and b['entry_id']):
            raise ValueError('Ambiguous component lexical destination')
        if b['entry_id'] and b['entry_id'] not in existing:
            raise ValueError('Unknown component word destination: ' + b['entry_id'])
        if b['entry_id'] and existing[b['entry_id']]['kind'] in ('particle', 'construction'):
            raise ValueError('Functional components must link to grammar lessons')
        if is_new:
            key = (b['new_headword'], b['new_reading'])
            referenced.add(key)
        if not set(b['grammar_entry_ids']) <= grammar_ids:
            raise ValueError('Unknown component grammar destination')
        if not b['entry_id'] and not is_new and not b['grammar_entry_ids']:
            raise ValueError('Component has no destination: ' + b['candidate_id'])
    if referenced != new:
        raise ValueError('Unrequested component entries: ' + ', '.join(
            f'{headword} ({reading})' for headword, reading in sorted(new - referenced)))


def routing_payload(rows):
    aliases = {row['id']: f'P{i}' for i, row in enumerate(rows)}
    return [dict(row, id=aliases[row['id']]) for row in rows], aliases


def routing_ids(data, mapping):
    return dict(data, bindings=[dict(binding,
        candidate_id=mapping.get(binding['candidate_id'], binding['candidate_id']),
        **({'entry_id': mapping.get(binding['entry_id'], binding['entry_id'])}
           if 'entry_id' in binding else {}))
        for binding in data['bindings']])


async def resolve(runner, dictionary, grammar):
    rows = requests(dictionary)
    if not rows:
        return []
    catalog = [{k: e[k] for k in ('id', 'headword', 'reading', 'kind', 'senses')}
               for e in dictionary['entries'] if not e.get('superseded_by')
               and e['kind'] not in ('particle', 'construction')]
    lessons = [{k: e[k] for k in ('id', 'title', 'reading', 'kind', 'summary_en')}
               for e in grammar['entries']]
    functional = [{k: e[k] for k in ('headword', 'reading', 'kind')}
                  for e in dictionary['entries'] if not e.get('superseded_by')
                  and e['kind'] in ('particle', 'construction')]
    compact_rows, aliases = routing_payload(rows)
    aliases.update({entry['id']: f'W{i}' for i, entry in enumerate(catalog)})
    compact_catalog = [dict(entry, id=aliases[entry['id']]) for entry in catalog]
    restored = {short: canonical for canonical, short in aliases.items()}
    prompt = POLICY + json.dumps(dict(parts=compact_rows, words=compact_catalog,
        functional_entries=functional, grammar=lessons), ensure_ascii=False)
    job = 'component-links-' + words.decision_digest([rows, catalog, functional, lessons])[:16]
    draft = await runner.call(job+'/propose', prompt, SCHEMA, 'low', tool_profile='offline')
    reviewed = routing_ids(await runner.call(job+'/review', prompt + '\nIndependently review lexical status, '
        'base forms and grammatical destinations. Correct all errors.\n' +
        json.dumps(draft, ensure_ascii=False), SCHEMA, 'high', tool_profile='offline'), restored)
    for attempt in range(3):
        try:
            validate(reviewed, rows, dictionary, grammar)
            break
        except (ValueError, jsonschema.ValidationError) as error:
            if attempt == 2:
                raise
            message = str(error)
            for canonical, short in aliases.items():
                message = message.replace(canonical, short)
            reviewed = routing_ids(await runner.call(job+f'/repair-{attempt}', prompt +
                '\nRepair this deterministic error: '+message+'\n'+
                json.dumps(routing_ids(reviewed, aliases), ensure_ascii=False),
                SCHEMA, 'high', tool_profile='offline'), restored)
    entries = []
    new_ids = {}
    for e in reviewed['entries']:
        eid = 'ja-component-' + words.decision_digest([e['headword'], e['reading']])[:16]
        new_ids[(e['headword'], e['reading'])] = eid
        entries.append(dict(id=eid, headword=e['headword'], reading=e['reading'], kind=e['kind'],
            senses=[dict(id=f'{eid}-{i+1}', definition=d, occurrences=[])
                    for i,d in enumerate(e['definitions'])]))
    components = words.read(words.COMPONENTS, {}).get('entries', [])
    components.extend(entries)
    if entries:
        words.atomic_json(words.COMPONENTS, dict(reviewed=True, entries=components,
            review_digest=words.decision_digest(components)))
    prior = words.read(REGISTRY, {}).get('bindings', [])
    by_id = {b['id']: b for b in prior}
    assignments = {b['candidate_id']: b for b in reviewed['bindings']}
    for row in rows:
        b = assignments[row['id']]
        by_id[row['id']] = {k: row[k] for k in
            ('id', 'parent_entry_id', 'part_index', 'text', 'contribution_en')}
        by_id[row['id']].update(entry_id=b['entry_id'] or new_ids.get(
            (b['new_headword'], b['new_reading']), ''), grammar_entry_ids=b['grammar_entry_ids'])
    bindings = list(by_id.values())
    words.atomic_json(REGISTRY, dict(reviewed=True, bindings=bindings,
        review_digest=words.decision_digest(bindings)))
    return entries
