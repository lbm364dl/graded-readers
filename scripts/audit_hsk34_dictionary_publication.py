"""Fail closed unless both complete first chapters use current dictionary assets.

Run from the repository root after publishing and generating annotation assets:
    python -m scripts.audit_hsk34_dictionary_publication
"""
import json

from pipeline import dictionary_corpus as corpus
from pipeline import usage_dictionary as lexical
from pipeline import chinese_reading_units as reading


def audit(levels=('hsk3', 'hsk4')):
    manifest = json.loads(corpus.MANIFEST.read_text())
    assert set(levels).issubset(manifest['levels']), 'Requested levels not published'
    assert set(manifest['levels']).issubset(corpus.LEVELS), 'Unsupported published levels'
    published = json.loads(lexical.OUTPUT.read_text())
    rebuilt = corpus.build(publish=False)
    assert rebuilt == published, 'Published dictionary differs from reviewed registries'
    display = json.loads(reading.OUTPUT.read_text())
    entries = {e['id']: e for e in published['entries']}
    assert len(entries) == len(published['entries'])
    senses = {s['id']: e['id'] for e in entries.values() for s in e['senses']}
    occurrences = {o['id']: o for o in published['occurrences']}
    assert len(occurrences) == len(published['occurrences'])
    for occurrence in occurrences.values():
        assert occurrence['entry_id'] in entries
        assert senses[occurrence['sense_id']] == occurrence['entry_id']
        text = published['sources'][occurrence['source']]['text']
        assert text[occurrence['start']:occurrence['end']] == occurrence['surface']
    for entry in entries.values():
        guide = entry['meaning_guide']
        assert guide['explanation_en'].strip(), entry['headword']
        for part in guide['parts']:
            assert part['contribution_en'].strip()
            if part.get('entry_id'):
                assert part['entry_id'] in entries
                if part.get('sense_id'):
                    assert senses[part['sense_id']] == part['entry_id']
            elif part.get('character_ref'):
                assert part['character_ref']['dictionary'] == 'hanzi-etymology'
            elif part.get('link_status') == 'whole_entry':
                assert part['text'] == entry['headword'], 'Invalid implicit self-reference'
            else:
                raise AssertionError(f'Unlinked component: {entry["headword"]} / {part["text"]}')
    bindings = published['reading_bindings']
    report = {}
    for level in levels:
        source = corpus.source(level)
        document = json.loads(source.read_text())
        assert [c['number'] for c in document['chapters']] == [1]
        _, _, expected, _ = lexical.source_data(source)
        for occurrence in expected:
            actual = occurrences[occurrence['id']]
            assert all(actual[k] == value for k, value in occurrence.items())
        units = reading.build(json.loads((corpus.DIRECTORY / f'{level}.reading-units.json').read_text()), source)
        count = 0
        for asset, data in units['sources'].items():
            assert display['sources'][asset] == data
            annotation = json.loads((corpus.ROOT / 'app' / asset).read_text())
            chapter = document['chapters'][0]
            assert annotation['text'] == chapter['text']
            expected_segments, offset = [], 0
            overlays = chapter.get('grammar_overlays', [])
            assert annotation.get('grammar_overlays', []) == overlays
            for segment in chapter['segments']:
                end = offset + len(segment['text'])
                expected_segment = {k: v for k, v in segment.items()
                                    if k not in {'explanation_zh', 'example_zh'}}
                expected_segment['grammar_candidate_keys'] = sorted({
                    o['grammar_candidate_key'] for o in overlays
                    if o['start'] < end and offset < o['end']})
                expected_segments.append(expected_segment)
                offset = end
            assert annotation['segments'] == expected_segments, 'Stale app annotations'
            for unit in data['units']:
                count += 1
                matches = [b for b in bindings if b['source'] == asset
                           and b['segment_index'] == unit['first_segment']
                           and b['surface'] == unit['text']]
                assert len(matches) == 1, unit['text']
                assert matches[0]['occurrence_id'] in occurrences
                assert matches[0]['explanation'] == unit['explanation_en']
        report[level] = dict(lexical_usages=len(expected), tap_units=count)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    audit()
