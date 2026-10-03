"""Verify complete reviewed Japanese dictionary coverage against source and app assets."""
import argparse
from collections import Counter
from pipeline import japanese_usage_dictionary as words
from pipeline import japanese_grammar_dictionary as grammar


def audit(levels=words.LEVELS):
    annotations = words.annotations()
    selected = [a['level'] for a in annotations]
    if set(selected) != set(levels):
        raise ValueError(f'Incomplete level coverage: {selected}; required {list(levels)}')
    dictionary, lessons = words.build(), grammar.build()
    from pipeline import japanese_component_links
    if japanese_component_links.requests(dictionary):
        raise ValueError('Unreviewed dictionary component destinations remain')
    if dictionary != words.read(words.OUTPUT) or lessons != words.read(grammar.OUTPUT):
        raise ValueError('App dictionary assets differ from reviewed registries')
    sources, groups, lexical, _ = words.source_data(annotations)
    _, candidates = grammar.candidates(annotations)
    if dictionary['sources'] != sources or lessons['sources'] != sources:
        raise ValueError('Published dictionary source editions do not match coverage')
    expected_lexical = Counter(u['id'] for u in lexical if u.get('layer') != 'expression')
    actual_lexical = Counter(u['id'] for u in dictionary['occurrences']
                             if u.get('layer') != 'expression')
    if expected_lexical != actual_lexical:
        raise ValueError('Missing or duplicated source word occurrences')
    _, expressions, _ = words.expression_data(annotations)
    expected_expressions = Counter(u['id'] for u in expressions + lexical
                                   if u.get('layer') == 'expression')
    if expected_expressions != Counter(u['id'] for u in dictionary['occurrences']
                                      if u.get('layer') == 'expression'):
        raise ValueError('Missing or duplicated expression occurrences')
    if len({e['id'] for e in dictionary['entries']}) != len(dictionary['entries']):
        raise ValueError('Duplicate dictionary identities across word/expression stores')
    if Counter(r['id'] for r in candidates) != Counter(
            o['candidate_id'] for o in lessons['occurrences']):
        raise ValueError('Missing or duplicated grammar/form-step occurrences')
    known_words = {e['id'] for e in dictionary['entries']}
    entries_by_id = {e['id']: e for e in dictionary['entries']}
    grammar_ids = {e['id'] for e in lessons['entries']}
    expected_components = Counter()
    actual_components = Counter()
    for entry in dictionary['entries']:
        if entry.get('superseded_by'):
            target = entries_by_id.get(entry['superseded_by'])
            if not target or target.get('superseded_by') or any(
                    entry[k] != target[k] for k in ('headword', 'reading', 'kind')):
                raise ValueError('Invalid component compatibility destination')
        for part in entry['meaning_guide']['parts']:
            if not set(part.get('grammar_entry_ids', [])) <= grammar_ids:
                raise ValueError('Dangling component grammar link')
            if part.get('entry_id') and part['entry_id'] not in known_words:
                raise ValueError('Dangling lexical component link')
            if part.get('entry_id') and not entry.get('superseded_by'):
                target = entries_by_id[part['entry_id']]
                target_id = target.get('superseded_by', target['id'])
                expected_components[(target_id, entry['id'], part['contribution_en'])] += 1
        for use in entry.get('component_uses', []):
            actual_components[(entry['id'], use['parent_entry_id'], use['contribution_en'])] += 1
    if actual_components != expected_components:
        raise ValueError('Stale or missing reverse component links')
    coverage = {}
    for source, metadata in sources.items():
        annotation = words.read(words.ROOT / 'app' / source)
        text = metadata['text']
        if annotation['text'] != text or ''.join(
                s['text'] for s in annotation['segments']) != text:
            raise ValueError(f'App source text differs: {source}')
        uses = [u for u in dictionary['occurrences'] if u['source'] == source]
        grammar_uses = [u for u in lessons['occurrences'] if u['source'] == source]
        if metadata['level'] != 'N5' and any(
                not u.get('display_meaning_en') or not u.get('display_base_meaning_en')
                for u in grammar_uses if u['layer'] == 'form'):
            raise ValueError(f'Unreviewed whole-form meanings: {source}')
        base_meanings = {}
        for use in grammar_uses:
            if metadata['level'] == 'N5' or use['layer'] != 'form':
                continue
            identity = (use['start'], use.get('display_surface', use['surface']),
                        use.get('display_base_form'))
            meaning = use['display_base_meaning_en']
            if base_meanings.setdefault(identity, meaning) != meaning:
                raise ValueError(f'Inconsistent reviewed chain base meanings: {source}')
        coverage[source] = dict(level=metadata['level'], words=len(uses),
            grammar=len(grammar_uses), form_steps=sum(
                u['layer'] == 'form' for u in grammar_uses))
    return dict(verified=True, levels=selected, coverage=coverage,
        word_entries=len(dictionary['entries']), grammar_entries=len(lessons['entries']),
        word_asset_digest=words.decision_digest(dictionary),
        grammar_asset_digest=words.decision_digest(lessons))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--levels', nargs='+', choices=words.LEVELS, default=list(words.LEVELS))
    args = parser.parse_args()
    result = audit(args.levels)
    words.atomic_json(words.DIRECTORY / 'publication-audit.json', result)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
