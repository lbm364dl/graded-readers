"""Coverage-checked editorial audit of the complete published Japanese N5 pilot.

This does not regenerate dictionary entries or alter source annotations. Reviewed
findings can be queued through the existing scoped editorial routes.
"""
import argparse
import asyncio
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from pipeline import japanese_usage_dictionary as words
from pipeline import japanese_grammar_dictionary as grammar

SCHEMA = words.ROOT / 'pipeline/schemas/japanese-dictionary-audit.schema.json'
REPORT = words.DIRECTORY / 'n5.editorial-audit.json'
POLICY = """Audit EVERY supplied Japanese N5 pilot entry, segment, overlay and grammar
occurrence for learner-facing consistency, independence and useful explanations.
This is a targeted editorial audit, not an invitation to rewrite correct prose
or add more theory/history. Return exact coverage IDs for all supplied records
and only actionable issues, with concrete narrowly scoped requested changes.
Review the complete set, not just examples mentioned below.
Look for passage-bound dictionary prose; defensive caveats correcting an unstated
misconception; irrelevant negative claims; duplicated inflection catalogs;
unclear or synonymous terminology presented as unrelated concepts; wrong word/
grammar destinations; incomplete meanings of whole displayed forms; legacy
component/lookup information that contradicts the canonical dictionary links.
For instance 'not by itself a past-tense form' in the continuative-stem entry is
a source-driven correction, not needed to explain the stem. The ます-stem and
ordinary pre-ます 連用形 name the same stem; the FULL ます form is distinct. Explain
this relationship clearly without merging the bare linking use with politeness.
Grammar entries teach their own concept, not the specific narrator's sentence.
Word explanations describe lexical identity, not a catalog of conjugations.
Some word entries are compatibility_only and intentionally redirect to grammar:
do not treat their retention as duplicate learner-facing entries.
Contextual translations and occurrence notes MAY describe the sentence. Source
examples are separately displayed and legitimately contain actual inflected forms.
Do not purge meaningful constraints, useful contrasts, explicit uncertainty based
on evidence, or minimal generic formation examples. Independence does NOT mean
'no examples' or 'just define the
word'. Keep intuitive, supported component-to-whole meaning bridges, relevant
concise word-formation history and useful valency notes. A canonical reusable
example is not passage-bound merely because this corpus also uses it. Do not
erase 名前's honest unresolved 前 contribution, 主人's useful 主+人 explanation,
or a brief idiomatic extension of 回る just to shorten the entry. Structured
parts may exist for links; check top-level character links before calling them
duplicates. Focus on substantive confusion and boilerplate, not stylistic taste.
Do not force a particular
number of findings. Do not require external research for this editorial review,
invent facts, or propose source/tap-boundary changes merely to simplify the UI.
Ordinary inflected cards show base word → reviewed linked form_steps. Compact
merged cards show the lexical base, construction if distinct, then complete forms
composed from exact reviewed source spans, with reviewed display_meaning_en.
Existing ID assignments and entry bodies are separate from original annotations.
Issues must target exact supplied IDs. Report word/grammar prose problems on
those entries, not on source annotations; occurrence problems on occurrence IDs.
Use no tools. Return JSON only.
""" + grammar.ENTRY_FOCUS_POLICY


def inputs():
    dictionary = words.build()
    lessons = grammar.build()
    annotations = words.annotations()
    lexical = []
    for entry in dictionary['entries']:
        guide = entry['meaning_guide']
        lexical.append(dict(id=entry['id'], headword=entry['headword'],
            reading=entry['reading'], kind=entry['kind'], senses=entry['senses'],
            compatibility_only=(entry['kind'] in ('particle', 'construction')
                                or entry['headword'] in ('です', 'か')),
            character_ref=entry.get('character_ref'),
            meaning_guide={k: guide[k] for k in ('explanation_en', 'parts', 'caveat_en')}))
    segments, overlays = [], []
    for annotation in annotations:
        for chapter in annotation['chapters']:
            source = f"assets/annotations/japanese_wagahai_{annotation['level']}_{chapter['number']:03}.json"
            segments.extend(dict(id=f'{source}#segment-{i}', annotation=s)
                            for i, s in enumerate(chapter['segments']))
            overlays.extend(dict(id=f'{source}#overlay-{i}', annotation=o)
                            for i, o in enumerate(chapter['grammar_overlays']))
    # Source sentences already appear once in sources. Avoid repeating chapter
    # prose or research dossiers in every item of the audit prompt.
    occurrences = [{k: v for k, v in use.items()
                    if k not in ('sentence', 'sentence_start', 'lexical_entry_ids')}
                   for use in lessons['occurrences']]
    return dict(word_entries=lexical, grammar_entries=lessons['entries'],
                sources=lessons['sources'], segments=segments, overlays=overlays,
                occurrences=occurrences)


def model_inputs(payload):
    """Short IDs avoid asking agents to echo thousands of path/identity tokens.

    The host retains the complete canonical manifest; review coverage is checked
    against it after decoding. Meaningful source data is not summarized away.
    """
    aliases = {}
    for prefix, key in [('W', 'word_entries'), ('G', 'grammar_entries'),
                        ('S', 'segments'), ('O', 'overlays'), ('R', 'occurrences')]:
        aliases.update({row['id']: f'{prefix}{index}' for index, row in enumerate(payload[key])})
    aliases.update({source: f'T{index}' for index, source in enumerate(payload.get('sources', {}))})
    def encode(value):
        if isinstance(value, str):
            return aliases.get(value, value)
        if isinstance(value, list):
            return [encode(v) for v in value]
        if isinstance(value, dict):
            return {aliases.get(k, k): encode(v) for k, v in value.items()}
        return value
    return encode(payload), {short: canonical for canonical, short in aliases.items()}


def decode_report(data, aliases):
    result = deepcopy(data)
    for key in ('word_entry_ids', 'grammar_entry_ids', 'segment_ids', 'overlay_ids', 'occurrence_ids'):
        result[key] = [aliases.get(value, value) for value in result[key]]
    for issue in result['issues']:
        issue['target_id'] = aliases.get(issue['target_id'], issue['target_id'])
    return result


def validate(data, payload):
    import jsonschema
    jsonschema.validate(data, words.read(SCHEMA))
    routes = dict(word=('word_entry_ids', 'word_entries'),
                  grammar=('grammar_entry_ids', 'grammar_entries'),
                  annotation=('segment_ids', 'segments'),
                  overlay=('overlay_ids', 'overlays'),
                  occurrence=('occurrence_ids', 'occurrences'))
    for coverage_key, input_key in routes.values():
        wanted = [row['id'] for row in payload[input_key]]
        if Counter(data[coverage_key]) != Counter(wanted):
            raise ValueError(f'Incomplete or duplicated audit coverage: {coverage_key}')
    targets = set()
    for issue in data['issues']:
        coverage_key, _ = routes[issue['kind']]
        key = (issue['kind'], issue['target_id'])
        if issue['target_id'] not in data[coverage_key] or key in targets:
            raise ValueError('Unknown or duplicated editorial audit target')
        targets.add(key)


async def audit():
    from pipeline.agent_harness import CodexRunner
    payload = inputs()
    fingerprint = words.decision_digest(dict(policy=POLICY, inputs=payload, manifest_protocol=2))
    cached = words.read(REPORT, {})
    if (cached.get('input_fingerprint') == fingerprint and cached.get('reviewed')
            and cached.get('review_digest') == words.decision_digest(cached.get('data'))):
        validate(cached['data'], payload)
        return cached
    runner = CodexRunner(words.ROOT / 'runs/japanese-n5-editorial-audit',
                         'gpt-6-luna', asyncio.Semaphore(1), 600)
    compact, aliases = model_inputs(payload)
    prompt = POLICY + '\nINPUT (use the supplied short IDs exactly):\n' + json.dumps(compact, ensure_ascii=False)
    job = 'audit-' + fingerprint[:16]
    proposed = await runner.call(job + '/propose', prompt, SCHEMA, 'low', tool_profile='offline')
    reviewed = await runner.call(job + '/review', prompt +
        '\nIndependently audit ALL records, check findings for false positives and '
        'missed cases. Return complete exact coverage and corrected findings. DRAFT:\n' +
        json.dumps(proposed, ensure_ascii=False), SCHEMA, 'high', tool_profile='offline')
    reviewed = decode_report(reviewed, aliases)
    validate(reviewed, payload)
    report = dict(reviewed=True, input_fingerprint=fingerprint,
                  review_digest=words.decision_digest(reviewed), data=reviewed)
    words.atomic_json(REPORT, report)
    return report


def queue_fixes(report, decisions=None):
    validate(report['data'], inputs())
    if not report.get('reviewed') or report.get('review_digest') != words.decision_digest(report['data']):
        raise ValueError('Cannot queue unreviewed audit findings')
    if report.get('input_fingerprint') != words.decision_digest(dict(policy=POLICY, inputs=inputs(), manifest_protocol=2)):
        raise ValueError('Cannot queue findings from a stale audit')
    issues = report['data']['issues']
    if decisions is not None:
        if decisions.get('audit_digest') != report['review_digest']:
            raise ValueError('Editorial triage belongs to another audit')
        rows = decisions.get('decisions', [])
        if Counter(row['index'] for row in rows) != Counter(range(len(issues))):
            raise ValueError('Every audit finding needs an explicit triage decision')
        selected = []
        for row in rows:
            if row['action'] not in ('edit', 'publication', 'presentation', 'retain') or not row['reason'].strip():
                raise ValueError('Invalid editorial triage decision')
            if row['action'] == 'edit':
                issue = dict(issues[row['index']])
                if 'requested_change' in row:
                    issue['requested_change'] = row['requested_change']
                selected.append(issue)
        issues = selected
    supported = ('word', 'grammar', 'occurrence') if decisions is not None else ('word', 'grammar')
    unsupported = [issue for issue in issues
                   if issue['kind'] not in supported]
    if unsupported:
        raise ValueError('Annotation/routing findings need explicit pipeline correction: ' +
                         json.dumps(unsupported, ensure_ascii=False))
    for kind, path in [('word', words.REQUESTS), ('grammar', grammar.REQUESTS)]:
        requests = words.read(path, [])
        for issue in issues:
            if issue['kind'] != kind and not (kind == 'grammar' and issue['kind'] == 'occurrence'):
                continue
            reason = issue['reason'] + ' Requested change: ' + issue['requested_change']
            request = dict(id='ja-audit-request-' + words.decision_digest(
                [issue['target_id'], reason])[:16], reason=reason)
            request['candidate_id' if issue['kind'] == 'occurrence' else 'entry_id'] = issue['target_id']
            if kind == 'word':
                request['force_research'] = False
                request['reuse_reviewed_evidence'] = True
            if request not in requests:
                requests.append(request)
        words.atomic_json(path, requests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue-fixes', action='store_true')
    parser.add_argument('--triage', type=Path,
                        help='Explicit decisions for every reviewed audit finding')
    args = parser.parse_args()
    report = asyncio.run(audit())
    if args.queue_fixes:
        queue_fixes(report, words.read(args.triage) if args.triage else None)
    print(json.dumps(report['data'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
