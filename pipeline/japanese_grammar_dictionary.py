"""Reviewed, reusable Japanese grammar entries and edition-safe occurrence links."""
import argparse
import asyncio
import fcntl
import json
import re
from collections import Counter
from jsonschema import ValidationError

from pipeline import japanese_usage_dictionary as words
from pipeline.annotate_chinese import atomic_json
from pipeline.usage_dictionary import decision_digest, ROOT

REGISTRY = words.DIRECTORY / 'n5.grammar.json'
REQUESTS = words.DIRECTORY / 'grammar-editorial-requests.json'
OUTPUT = ROOT / 'app/assets/grammar_dictionary_ja.json'
SCHEMA = ROOT / 'pipeline/schemas/grammar-dictionary.schema.json'
ENTRY_FOCUS_POLICY = """Each reusable grammar entry covers ONE grammatical concept,
independently and concisely. Explain its meaning, own formation and essential
usage constraints. Do not append polite, past, negative, imperative or other
inflected variants just because they appear in supplied passages. Those are
separate lessons linked by the occurrence's form chain. For example, ことにする
explains making a decision, not ことにしました; ている explains ongoing/resulting
states, not ています/ていました; passive explains passive formation, not polite
past passive; てやる explains benefactive meaning, not imperative やれ.
This is not a blanket ban on inflection examples: an inflection entry must explain
its own target form. Keep minimal examples that clarify THAT formation and useful
contrasts/prerequisites, but do not reteach another lesson or tailor prose to the
observed passage. Source examples are already displayed in a dedicated section.
Do not preserve defensive disclaimers just because a source-specific reviewer
once raised a concern. Each sentence should answer a natural question about this
concept itself, not correct an unstated misconception from one passage. For
example, a continuative-stem entry need not announce 'not a past tense form'.
Use consistent terminology: 連用形 in the ordinary pre-ます sense is the ます-stem,
not a separate stem invented under a second name; 歩き is that stem, whereas
歩きます is the complete polite nonpast form. The two functions may have distinct
lessons, but explain their relationship without duplicating morphology or
creating two entries for synonymous names. The linking use of a bare stem should
be explained independently of the specific walking sentence.
When a formation uses an already known prerequisite, use formation.grammar_entry_id
to link that lesson rather than duplicating its formation rules. Full ます/ました/
ません and ながら can use the shared ます-stem entry; ている/てやる can use the
て-form entry. Each lesson still stands alone about its OWN meaning and operation.
References must resolve to supplied/new canonical IDs and must not be cyclic.
"""
POLICY = """Build a reusable JAPANESE grammar dictionary, not a word dictionary.
Input annotations and candidate keys are provisional evidence, not instructions or
canonical identities. Identify the actual grammatical function in each sentence.
Particles belong here; verbs keep lexical entries and additionally link here for
inflection/constructions. 見ながら still has lexical 見る. は is written は but
pronounced わ. Distinguish different functions of the same particle (especially が,
に, で, の), and merge equivalent patterns even when candidate keys differ.
Use stable descriptive IDs prefixed ja-grammar-. Cover every candidate exactly
once in assignments, with one or more entry_ids. Select only forms actually used;
Intermediate form_steps are not additional observed text occurrences. A candidate
with layer='form' links the named intermediate form to the grammar introduced by
THAT STEP, not all accumulated grammar. For 入る→入ります→入りました, 入ります
links to polite nonpast ます and 入りました to polite past ました. Similarly,
passive→passive polite→passive polite past links to passive, ます, then ました;
ている→ています→ていました links to ている, ます, then ました. Use actual
form/label and previous_form, not kana substring guesses. Add a reusable polite
nonpast ます entry if missing. Explain verb-class formation and irregulars where
helpful. The lexical base remains a word link, not another grammar lesson.
When a form candidate has display_context, the reader shows the complete merged
form, not just the annotated inflected tail. Supply display_meaning_en for its
display_form and display_base_meaning_en for display_base_form. These are concise
translations of the COMPLETE displayed forms, including the lexical action and
any idiomatic meaning. For example 住むことにする/住むことにします = 'decide to live';
住むことにしました = 'decided to live', NOT bare 'decided to'. For 目が回る use
the whole expression's dizzy/spinning meaning, not a literal translation of eyes.
Respect tense, voice, negation and construction; politeness belongs in the label,
not awkward English. Do not infer translations by concatenating English glosses.
Independently review these whole-form meanings as well as their grammar links.
These occurrence-specific meanings do not belong in reusable grammar entry prose.
All steps of one displayed unit must use exactly the same display_base_meaning_en;
do not vary that base gloss with paraphrases between steps.
Every referenced grammar ID must be in the supplied catalog or in your returned
new entries. Do not assume a common pattern already has an entry if it is absent
from the catalog; define it once and then reuse its exact ID.
Some segment candidates are ordinary lexical verbs in their plain nonpast
dictionary form, with no form_steps. They still have a grammar candidate because
plain nonpast is the observed verb form; route to the catalogued
ja-grammar-plain-nonpast lesson where that is its function. The lexical verb
remains a separate word link. Never use an empty entry ID or omit an assignment
just because no additional inflection was added.
Match existing lessons by their grammatical function, title and summary, not by
your preferred ID spelling. Equivalent terminology is not a new concept. When
repairing an unresolved ID, first redirect it to an equivalent catalogued lesson;
create a new lesson only if that concept is genuinely absent.
Repair outputs are complete replacements, not patches. Return ALL new lesson
definitions referenced by the repaired assignments, including definitions from
earlier attempts that were already correct. Fixing one missing lesson does not
permit dropping the other new definitions. Only supplied catalog entries are
retained automatically; newly proposed entries must remain in every replacement.
Do not invent a word entry for inflection or treat intermediate forms as sentences
observed in the text. Keep generic explanations independent of specific passages.
Explain
polite past, negative, passive, ている, ながら, benefactive やる, imperative,
embedded questions and ことにする where present. Do not mistake inflection for
a new lexical word or put a whole story-specific clause in an entry title.
An entry has id, title (canonical Japanese pattern), reading (kana), kind
(particle, conjugation, construction), summary_en, explanation_en, formation
(ordered objects form and explanation_en), and notes_en (strings). Explanations
stand alone: no narrator, this passage/example, or specific story referents.
Give intuitive role, formation, restrictions and useful contrasts, not merely
an English translation. No fabricated history or mandatory external research
for ordinary modern grammar. Do not claim exhaustive level coverage.
Preserve all existing entries byte-for-byte, including IDs and explanations;
reuse their IDs for new examples. The returned entries array should contain only
genuinely NEW entries: do not reprint the approved entries or their explanations.
The host combines unchanged approved entries with your additions. If all functions
already have entries, return entries=[]. Add an entry only for a genuinely new function.
Separate context explanation in assignment.context_en from reusable entry prose.
Return new entries and complete assignments for the supplied candidates. No tools. JSON only.
""" + ENTRY_FOCUS_POLICY


def surface_reading(chapter, start, end):
    """Reading of an aligned source span, never the different head-lemma form."""
    covered, offset = [], 0
    for segment in chapter['segments']:
        next_offset = offset + len(segment['surface'])
        if next_offset > start and offset < end:
            if offset < start or next_offset > end:
                return ''  # Kana cannot safely be sliced by written-character offset.
            covered.append(segment['surface_kana'])
        offset = next_offset
    return ''.join(covered)


def candidates(annotation=None):
    annotation = annotation or words.annotations()
    if isinstance(annotation, list):
        parts = [candidates(a) for a in annotation]
        sources = {key: value for part in parts for key, value in part[0].items()}
        rows = [row for part in parts for row in part[1]]
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('Duplicate grammar candidates across levels')
        return sources, rows
    sources, _, _, _ = words.source_data(annotation)
    result = []
    for chapter in annotation['chapters']:
        source = f"assets/annotations/japanese_wagahai_{annotation['level']}_{chapter['number']:03}.json"
        text = chapter['text']
        offset = 0
        for index, segment in enumerate(chapter['segments']):
            end = offset + len(segment['surface'])
            if (segment['type'] in ('particle', 'grammar') or segment['form_steps']
                    or segment['conjugation_form'] not in ('', 'non-inflecting')):
                if segment['type'] != 'punctuation':
                    result.append(dict(id=f'{source}#grammar-segment-{index}',
                        source=source, segment_index=index, start=offset, end=end,
                        surface=segment['surface'], reading=segment['surface_kana'],
                        layer='segment', annotation=segment, sentence=text))
                    for step_index, step in enumerate(segment['form_steps']):
                        result.append(dict(id=f'{source}#grammar-segment-{index}-step-{step_index}',
                            source=source, segment_index=index, start=offset, end=end,
                            surface=segment['surface'], reading=segment['surface_kana'],
                            layer='form', step_index=step_index, form=step['form'],
                            form_reading=step['reading'], form_label=step['label'],
                            form_meaning_en=step['meaning_en'],
                            previous_form=(segment['lemma'] if step_index == 0 else
                                           segment['form_steps'][step_index - 1]['form']),
                            annotation=dict(lemma=segment['lemma'], lemma_kana=segment['lemma_kana'],
                                            part_of_speech=segment['part_of_speech'], step=step),
                            sentence=text))
            offset = end
        for index, overlay in enumerate(chapter.get('grammar_overlays', [])):
            if overlay['grammar_candidate_key'] in ('otoko-no-hito', 'onna-no-hito', 'me-ga-mawaru'):
                continue  # Reviewed lexical phrases, not grammar identities.
            if text[overlay['start']:overlay['end']] != overlay['surface']:
                raise ValueError('Grammar overlay does not match its source span')
            result.append(dict(id=f'{source}#grammar-overlay-{index}', source=source,
                segment_index=-1, start=overlay['start'], end=overlay['end'],
                surface=overlay['surface'], reading=surface_reading(chapter, overlay['start'], overlay['end']),
                layer='overlay', annotation=overlay, sentence=text))
    # Keep source/form identities unchanged. Enrich only the tail steps of a
    # reviewed, aligned merged unit; never infer morphology from kana suffixes.
    for chapter in annotation['chapters']:
        source = f"assets/annotations/japanese_wagahai_{annotation['level']}_{chapter['number']:03}.json"
        spans, offset = [], 0
        for index, segment in enumerate(chapter['segments']):
            end = offset + len(segment['surface'])
            spans.append((index, offset, end, segment))
            offset = end
        for overlay in sorted(chapter.get('grammar_overlays', []),
                              key=lambda o: o['end'] - o['start'], reverse=True):
            covered = [p for p in spans
                       if p[1] >= overlay['start'] and p[2] <= overlay['end']]
            if (len(covered) < 2 or covered[0][1] != overlay['start']
                    or covered[-1][2] != overlay['end']
                    or any(p[3]['type'] == 'punctuation' for p in covered)
                    or not covered[-1][3]['form_steps']):
                continue
            # Same compact-unit restriction as the reader: do not turn clauses
            # with several lexical words into a guessed verb chain.
            description = ' '.join(overlay.get(k, '') for k in
                ('grammar_candidate_key', 'pattern', 'form_label', 'head_lemma')).lower()
            explanation = overlay.get('explanation_en', '').lower()
            collocation = any(k in description for k in
                ('collocation', 'fixed expression')) or any(k in explanation for k in
                ('collocation', 'fixed expression', 'conventional expression')) or 'お腹がすく' in overlay['pattern']
            benefactive = ('benefactive' in description or 'morau' in description
                or overlay['head_lemma'] in ('もらう', 'くれる', 'あげる', 'やる'))
            connective_naku = ('n-ga-naku' in description or
                (overlay['surface'].endswith('がなく') and overlay['head_lemma'].endswith('がない')))
            if (benefactive and covered[0][0] > 0 and covered[0][3]['surface'] in ('て', 'で')
                    and spans[covered[0][0]-1][2] == overlay['start']
                    and spans[covered[0][0]-1][3]['type'] in ('word', 'idiom')):
                covered = [spans[covered[0][0]-1], *covered]
                action = covered[0][3]
                overlay = dict(overlay, start=covered[0][1],
                    surface=chapter['text'][covered[0][1]:overlay['end']],
                    head_lemma=action['lemma'], head_lemma_kana=action['lemma_kana'])
            content = [p for p in covered if p[3]['type'] in ('word', 'name', 'idiom')]
            if (len(content) > (2 if collocation or benefactive or connective_naku else 1)
                    or covered[0][3]['type'] == 'particle' or len(overlay['surface']) > 18):
                continue
            tail_index, tail_start, _, tail = covered[-1]
            prefix = chapter['text'][overlay['start']:tail_start]
            prefix_reading = ''.join(p[3]['surface_kana'] for p in covered[:-1])
            for row in result:
                if (row['source'] != source or row['layer'] != 'form'
                        or row['segment_index'] != tail_index
                        or 'display_context' in row):
                    continue
                row.update(display_surface=overlay['surface'],
                    display_form=prefix + row['form'],
                    display_reading=prefix_reading + row['form_reading'],
                    display_base_form=overlay['head_lemma'],
                    display_base_reading=overlay['head_lemma_kana'],
                    display_context=dict(overlay=overlay,
                        parts=[p[3] for p in covered]))
    return sources, result


def validate(data, rows, previous=(), *, require_form_meanings=False):
    import jsonschema
    jsonschema.validate(data, words.read(SCHEMA))
    entries = {e['id']: e for e in data['entries']}
    if len(entries) != len(data['entries']):
        raise ValueError('Duplicate grammar entry IDs')
    visiting, done = set(), set()
    def visit(entry_id):
        if entry_id in visiting:
            raise ValueError('Cyclic grammar prerequisites')
        if entry_id in done:
            return
        visiting.add(entry_id)
        for form in entries[entry_id]['formation']:
            target = form.get('grammar_entry_id')
            if target:
                if target not in entries:
                    raise ValueError('Unresolved grammar prerequisite: ' + target)
                visit(target)
        visiting.remove(entry_id)
        done.add(entry_id)
    for entry_id in entries:
        visit(entry_id)
    for old in previous:
        if entries.get(old['id']) != old:
            raise ValueError('Previously reviewed grammar entry changed')
    assignments = {a['candidate_id']: a for a in data['assignments']}
    expected = Counter(r['id'] for r in rows)
    actual = Counter(a['candidate_id'] for a in data['assignments'])
    if actual != expected:
        raise ValueError('Grammar candidates must be assigned exactly once: '
                         f'missing={list((expected - actual).elements())}; '
                         f'extra={list((actual - expected).elements())}')
    unresolved = set()
    for a in assignments.values():
        if len(a['entry_ids']) != len(set(a['entry_ids'])):
            raise ValueError('Duplicate grammar reference')
        unresolved.update(set(a['entry_ids']) - entries.keys())
    if unresolved:
        # Report the entire batch so one repair can define every missing lesson,
        # rather than spending a separate model call on each unresolved ID.
        raise ValueError('Unresolved grammar reference: ' + ', '.join(sorted(unresolved)))
    required_meanings = ('display_meaning_en', 'display_base_meaning_en')
    missing_meanings = {}
    for row in rows:
        if 'display_context' in row or (require_form_meanings and row['layer'] == 'form'):
            missing = [key for key in required_meanings
                       if not assignments[row['id']].get(key, '').strip()]
            if missing:
                missing_meanings[row['id']] = missing
    if missing_meanings:
        raise ValueError('complete displayed forms need reviewed meanings; missing fields: ' +
                         json.dumps(missing_meanings, ensure_ascii=False))
    base_meanings = {}
    for row in rows:
        if 'display_context' in row or (require_form_meanings and row['layer'] == 'form'):
            assignment = assignments[row['id']]
            identity = (row['source'], row['start'], row.get('display_surface', row['surface']),
                        row.get('display_base_form', row['annotation'].get('lemma')))
            meaning = assignment['display_base_meaning_en']
            base_meanings.setdefault(identity, {})[row['id']] = meaning
        if row['layer'] == 'form' and row['form_label'] in ('polite', 'polite nonpast', 'polite past'):
            selected = [entries[eid] for eid in assignments[row['id']]['entry_ids']]
            if any(e['kind'] != 'conjugation' for e in selected):
                raise ValueError(f"{row['id']}: a {row['form_label']} step links to the inflection "
                                 "introduced here, not to the larger construction")
    conflicts = [group for group in base_meanings.values() if len(set(group.values())) > 1]
    if conflicts:
        raise ValueError('A displayed base needs one consistent reviewed meaning; conflicting stages: ' +
                         json.dumps(conflicts, ensure_ascii=False))


def model_candidate(row, *, include_display=False):
    """Give agents the relevant sentence, not a chapter repeated for every token."""
    text = row['sentence']
    left = max(text.rfind(c, 0, row['start']) for c in '。！？\n') + 1
    match = re.search('[。！？\n]', text[row['end']:])
    right = row['end'] + match.end() if match else len(text)
    result = dict(row, sentence=text[left:right])
    if include_display and row['layer'] == 'form' and 'display_context' not in row:
        result.update(display_surface=row['surface'], display_form=row['form'],
            display_reading=row['form_reading'],
            display_base_form=row['annotation']['lemma'],
            display_base_reading=row['annotation']['lemma_kana'],
            display_context=dict(standalone=True))
    return result


def candidate_fingerprint(row):
    compact = model_candidate(row)
    return decision_digest({k: v for k, v in compact.items()
                            if k not in ('start', 'end', 'segment_index', 'id')})


def routing_payload(batch):
    """Local aliases bound output size without changing durable identities."""
    aliases = {row['id']: f'C{i}' for i, row in enumerate(batch)}
    rows = [dict(model_candidate(row, include_display=True), id=aliases[row['id']])
            for row in batch]
    return rows, aliases


def routing_error(error, aliases):
    """Use the same candidate identities in repair errors and agent inputs."""
    message = str(error)
    for canonical in sorted(aliases, key=len, reverse=True):
        message = message.replace(canonical, aliases[canonical])
    return message


def routing_batches(rows, limit=96):
    """Preserve source order without splitting contiguous stages of one tap unit."""
    start = 0
    while start < len(rows):
        stop = min(start + limit, len(rows))
        identity = lambda row: (row['source'], row['start'])
        while stop > start and stop < len(rows) and identity(rows[stop - 1]) == identity(rows[stop]):
            stop -= 1
        if stop == start:
            # An unusually long chain is safer as one larger review than as
            # independent fragments with potentially inconsistent base glosses.
            stop = min(start + limit, len(rows))
            while stop < len(rows) and identity(rows[stop - 1]) == identity(rows[stop]):
                stop += 1
        yield rows[start:stop]
        start = stop


def routing_ids(data, mapping):
    return dict(data, assignments=[dict(a,
        candidate_id=mapping.get(a['candidate_id'], a['candidate_id']))
        for a in data['assignments']])


def complete_entries(data, previous):
    """Reuse reviewed prose locally; agents need return only genuinely new lessons."""
    existing = {e['id']: e for e in previous}
    additions = []
    seen = set()
    for entry in data['entries']:
        if entry['id'] in seen:
            raise ValueError('Duplicate grammar entry IDs')
        seen.add(entry['id'])
        if entry['id'] in existing:
            if any(entry[k] != existing[entry['id']][k] for k in ('title', 'reading', 'kind')):
                raise ValueError('Previously reviewed grammar identity changed')
            # Reused prose comes from the registry, never from an agent echo.
            # Deliberate prose changes use the separate scoped editorial route.
        else:
            additions.append(entry)
    return dict(data, entries=list(previous) + additions)


def repair_payload(data, previous):
    """Approved prose remains local; repair only new lessons and assignments."""
    known = {entry['id'] for entry in previous}
    return dict(data, entries=[entry for entry in data['entries'] if entry['id'] not in known])


def build():
    sources, rows = candidates()
    registry = words.read(REGISTRY)
    data = registry['data']
    if registry.get('source_fingerprint') != decision_digest(rows):
        raise ValueError('Japanese grammar links need updating')
    if not registry.get('reviewed') or registry.get('review_digest') != decision_digest(data):
        raise ValueError('Japanese grammar registry lacks valid review')
    validate(data, rows)
    assignments = {a['candidate_id']: a for a in data['assignments']}
    occurrences = []
    for row in rows:
        use = {k: v for k, v in row.items() if k not in ('annotation', 'display_context')}
        use.update(assignments[row['id']])
        if row['layer'] == 'form' and 'display_meaning_en' in use and 'display_context' not in row:
            use.update(display_surface=row['surface'], display_form=row['form'],
                display_reading=row['form_reading'], display_base_form=row['annotation']['lemma'],
                display_base_reading=row['annotation']['lemma_kana'])
        text = sources[row['source']]['text']
        left = max(text.rfind(c, 0, row['start']) for c in '。！？\n') + 1
        match = re.search('[。！？\n]', text[row['end']:])
        right = row['end'] + match.end() if match else len(text)
        use['sentence'] = text[left:right]
        use['sentence_start'] = left
        occurrences.append(use)
    _, groups, lexical_uses, _ = words.source_data()
    kinds = {e['id']: e['kind'] for e in words.read(words.SENSES, {}).get('entries', [])}
    functional = {g['id'] for g in groups if kinds.get(g['id']) in ('particle', 'construction') or all(
        u['source_type'] == 'particle' or g['headword'] in ('です', 'か', 'ことにする')
        for u in g['uses'])}
    routes = {}
    lexical_by_segment = {(u['source'], u['segment_index']): u for u in lexical_uses
                          if u.get('layer') != 'expression'}
    for use in occurrences:
        use['lexical_entry_ids'] = list(dict.fromkeys(u['entry_id'] for u in lexical_uses
            if u['source'] == use['source'] and u['start'] >= use['start']
            and u['end'] <= use['end'] and u['entry_id'] not in functional))
        lexical = lexical_by_segment.get((use['source'], use['segment_index']))
        if lexical and lexical['entry_id'] in functional and use['layer'] != 'form':
            route = routes.setdefault(lexical['entry_id'], [])
            route.extend(eid for eid in use['entry_ids'] if eid not in route)
    used_ids = {eid for use in occurrences for eid in use['entry_ids']}
    from pipeline import japanese_component_links
    binding_registry = words.read(japanese_component_links.REGISTRY, {})
    bindings = binding_registry.get('bindings', [])
    if binding_registry and (not binding_registry.get('reviewed') or
            binding_registry.get('review_digest') != decision_digest(bindings)):
        raise ValueError('Unreviewed component grammar references')
    used_ids.update(eid for binding in bindings for eid in binding['grammar_entry_ids'])
    by_id = {e['id']: e for e in data['entries']}
    pending = list(used_ids)
    while pending:
        for form in by_id[pending.pop()]['formation']:
            target = form.get('grammar_entry_id')
            if target and target not in used_ids:
                used_ids.add(target)
                pending.append(target)
    return dict(schema_version=1, language='japanese', sources=sources,
                entries=[e for e in data['entries'] if e['id'] in used_ids],
                occurrences=occurrences, word_routes=routes)


async def edit_occurrence_notes(registry, rows, requests, run_dir, workers):
    """Scoped reviewed context/form-meaning edits, without changing any links."""
    from pipeline.agent_harness import CodexRunner
    targets = {r['candidate_id'] for r in requests}
    selected_rows = [r for r in rows if r['id'] in targets]
    if targets != {r['id'] for r in selected_rows}:
        raise ValueError('Unknown grammar occurrence editorial target')
    assignments = {a['candidate_id']: a for a in registry['data']['assignments']}
    prompt = ('Edit only the supplied JAPANESE occurrence notes/form translations. '
        'These notes may explain their actual sentence, unlike independent dictionary entries. '
        'Remove irrelevant defensive disclaimers, but retain useful contextual grammar. '
        'Preserve candidate_id and entry_ids EXACTLY. Return entries=[] and only the requested '
        'assignments. Do not alter source text, readings, spans, forms, links or reusable lessons. '
        'For a form-meaning edit supply display_meaning_en for the complete form and '
        'display_base_meaning_en for the base; use display_form/base when supplied, otherwise '
        'form/annotation.lemma. Respect the form itself and construction, not a guessed English '
        'suffix. Keep politeness/voice/tense descriptions separate from translations. For '
        'context-only edits retain approved display meanings unchanged. Use no tools. JSON only.\n' +
        json.dumps(dict(requests=requests, candidates=[model_candidate(r) for r in selected_rows],
            assignments=[assignments[r['id']] for r in selected_rows]), ensure_ascii=False))
    runner = CodexRunner(run_dir or ROOT / 'runs/japanese-grammar-n5',
                         'gpt-6-luna', asyncio.Semaphore(workers), 600)
    job = 'edit-occurrences-' + decision_digest(requests)[:16]
    proposed = await runner.call(job + '/propose', prompt, SCHEMA, 'low', tool_profile='offline')
    reviewed = await runner.call(job + '/review', prompt +
        '\nIndependently verify all requested corrections, meanings and unchanged links.\n' +
        json.dumps(proposed, ensure_ascii=False), SCHEMA, 'high', tool_profile='offline')
    if reviewed['entries']:
        raise ValueError('Occurrence edits cannot replace grammar entries')
    replacements = {a['candidate_id']: a for a in reviewed['assignments']}
    if len(replacements) != len(reviewed['assignments']) or replacements.keys() != targets:
        raise ValueError('Occurrence edit changed unrequested identities')
    for key, replacement in replacements.items():
        if replacement['entry_ids'] != assignments[key]['entry_ids']:
            raise ValueError('Occurrence prose edits cannot change grammar links')
        replacements[key] = dict(assignments[key], **replacement)
    validate(dict(entries=registry['data']['entries'], assignments=list(replacements.values())), selected_rows)
    registry['data']['assignments'] = [replacements.get(a['candidate_id'], a)
                                     for a in registry['data']['assignments']]
    validate(registry['data'], rows)
    registry['review_digest'] = decision_digest(registry['data'])
    registry['applied_requests'] = registry.get('applied_requests', []) + [r['id'] for r in requests]


async def update(workers=4, run_dir=None, editorial_criteria="current"):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with (REGISTRY.parent / 'grammar.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return await _update(workers=workers, run_dir=run_dir,editorial_criteria=editorial_criteria)


async def _update(workers=4, run_dir=None, editorial_criteria="current"):
    from pipeline.dictionary_editorial_criteria import append
    from pipeline.agent_harness import CodexRunner
    _, rows = candidates()
    registry = words.read(REGISTRY, {})
    fingerprint = decision_digest(rows)
    requests = words.read(REQUESTS, [])
    pending = [r for r in requests if r['id'] not in registry.get('applied_requests', [])]
    if registry.get('source_fingerprint') == fingerprint:
        build()  # Validate stored review before editing or reusing it.
        if 'candidate_fingerprints' not in registry:
            registry['candidate_fingerprints'] = {r['id']: candidate_fingerprint(r) for r in rows}
            atomic_json(REGISTRY, registry)
        entry_pending = [r for r in pending if 'entry_id' in r]
        occurrence_pending = [r for r in pending if 'candidate_id' in r]
        if len(entry_pending) + len(occurrence_pending) != len(pending):
            raise ValueError('Unknown grammar editorial request kind')
        if entry_pending:
            runner = CodexRunner(run_dir or ROOT / 'runs/japanese-grammar-n5',
                                 'gpt-6-luna', asyncio.Semaphore(workers), 600)
            targets = {r['entry_id'] for r in entry_pending}
            entries = registry['data']['entries']
            selected = [e for e in entries if e['id'] in targets]
            if targets != {e['id'] for e in selected}:
                raise ValueError('Unknown grammar editorial request target')
            prompt = ('Revise these reusable JAPANESE grammar entries according to the explicit '
                'editorial requests. Use no tools. Preserve every entry ID; correct reliable modern '
                'grammar only, without historical claims. Reading is actual kana pronunciation, '
                'not necessarily orthographic kana. No references to source passages or research '
                'process in generic explanations. Return only the requested entries and assignments=[].\n' +
                ENTRY_FOCUS_POLICY + '\n' +
                json.dumps(dict(entries=selected, requests=entry_pending,
                    related_entry_catalog=[{k:e[k] for k in ('id', 'title', 'summary_en')}
                                           for e in entries]), ensure_ascii=False))
            prompt=append(prompt,editorial_criteria)
            job = 'edit-' + decision_digest(entry_pending)[:16]
            proposed = await runner.call(job + '/propose', prompt, SCHEMA, 'low', tool_profile='offline')
            reviewed = await runner.call(job + '/review', prompt + '\nIndependently check and correct the '
                'revision.\n' + json.dumps(proposed, ensure_ascii=False), SCHEMA, 'high', tool_profile='offline')
            import jsonschema
            jsonschema.validate(reviewed, words.read(SCHEMA))
            if reviewed['assignments']:
                raise ValueError('Entry edits cannot change occurrence assignments')
            replacements = {e['id']: e for e in reviewed['entries']}
            if replacements.keys() != targets:
                raise ValueError('Grammar edit changed unrequested identities')
            registry['data']['entries'] = [replacements.get(e['id'], e) for e in entries]
            validate(registry['data'], rows, [e for e in entries if e['id'] not in targets])
            registry['review_digest'] = decision_digest(registry['data'])
            registry['applied_requests'] = registry.get('applied_requests', []) + [r['id'] for r in entry_pending]
            atomic_json(REGISTRY, registry)
        if occurrence_pending:
            await edit_occurrence_notes(registry, rows, occurrence_pending, run_dir, workers)
            atomic_json(REGISTRY, registry)
        result = build()
        atomic_json(OUTPUT, result)
        return result
    previous = registry.get('data', {}).get('entries', [])
    if registry and (not registry.get('reviewed') or
            registry.get('review_digest') != decision_digest(registry.get('data'))):
        raise ValueError('Cannot reuse unreviewed grammar registry')
    old_assignments = {a['candidate_id']: a for a in registry.get('data', {}).get('assignments', [])}
    reused = [old_assignments[r['id']] for r in rows
              if r['id'] in old_assignments and registry.get('candidate_fingerprints', {}).get(r['id'])
              == candidate_fingerprint(r)]
    reused_ids = {a['candidate_id'] for a in reused}
    changed = [r for r in rows if r['id'] not in reused_ids]
    runner = CodexRunner(run_dir or ROOT / 'runs/japanese-grammar-n5',
                         'gpt-6-luna', asyncio.Semaphore(workers), 600)
    # Identity and functional summaries suffice for routing. Full approved
    # explanation prose stays local and is never regenerated for new uses.
    catalog = [{k: e[k] for k in ('id', 'title', 'reading', 'kind', 'summary_en')}
               for e in previous]
    completed = list(reused)
    reviewed_count = 0
    for batch in routing_batches(changed):
        previous = registry.get('data', {}).get('entries', previous)
        catalog = [{k: e[k] for k in ('id', 'title', 'reading', 'kind', 'summary_en')}
                   for e in previous]
        compact_rows, aliases = routing_payload(batch)
        restored = {short: canonical for canonical, short in aliases.items()}
        prompt = POLICY + json.dumps(dict(existing_entries=catalog,
            candidates=compact_rows), ensure_ascii=False)
        prompt=append(prompt,editorial_criteria)
        batch_key = decision_digest([batch, catalog])[:16]
        proposed = await runner.call('grammar-' + batch_key + '/propose', prompt,
                                     SCHEMA, 'low', tool_profile='offline')
        reviewed = routing_ids(await runner.call('grammar-' + batch_key + '/review',
            prompt + '\nIndependently review every context, function, formation rule and assignment; '
            'correct mistakes and omissions. Return complete data.\n' + json.dumps(proposed, ensure_ascii=False),
            SCHEMA, 'high', tool_profile='offline'), restored)
        for attempt in range(3):
            try:
                reviewed = complete_entries(reviewed, previous)
                validate(reviewed, batch, previous, require_form_meanings=True)
                break
            except (ValueError, ValidationError) as error:
                if attempt == 2:
                    raise
                reviewed = routing_ids(await runner.call('grammar-' + batch_key + f'/repair-{attempt + 1}',
                    prompt + '\nCorrect the following deterministic validation error, preserving '
                    'reviewed content otherwise. Return only genuinely new entries and the complete '
                    'assignments; existing catalog entries are preserved locally. Return ALL new '
                    'definitions needed by those assignments, not only definitions changed in this '
                    'repair. Your output replaces the entire previous attempt.\n' +
                    routing_error(error, aliases) + '\n' + json.dumps(routing_ids(repair_payload(reviewed, previous), aliases), ensure_ascii=False),
                    SCHEMA, 'high', tool_profile='offline'), restored)
        completed.extend(reviewed['assignments'])
        checkpoint = dict(entries=reviewed['entries'], assignments=list(completed))
        done_ids = {a['candidate_id'] for a in completed}
        registry = dict(registry, reviewed=True, source_fingerprint=None,
            data=checkpoint, review_digest=decision_digest(checkpoint),
            candidate_fingerprints={r['id']: candidate_fingerprint(r) for r in rows if r['id'] in done_ids})
        atomic_json(REGISTRY, registry)
        reviewed_count += len(batch)
        print(f'Grammar: reviewed {reviewed_count}/{len(changed)} changed candidates', flush=True)
    reviewed = dict(entries=registry.get('data', {}).get('entries', previous), assignments=completed)
    validate(reviewed, rows, previous)
    atomic_json(REGISTRY, dict(reviewed=True, source_fingerprint=fingerprint,
        review_digest=decision_digest(reviewed), data=reviewed,
        candidate_fingerprints={r['id']: candidate_fingerprint(r) for r in rows},
        applied_requests=registry.get('applied_requests', [])))
    if pending:
        return await _update(workers=workers, run_dir=run_dir,editorial_criteria=editorial_criteria)
    result = build()
    atomic_json(OUTPUT, result)
    return result


def plan():
    _, rows = candidates()
    registry = words.read(REGISTRY, {})
    current = (registry.get('source_fingerprint') == decision_digest(rows)
               and registry.get('reviewed')
               and registry.get('review_digest') == decision_digest(registry.get('data')))
    if current:
        validate(registry['data'], rows)
    return dict(grammar_candidates=len(rows), grammar_entries=len(
        build()['entries']) if current else 0, grammar_linking_required=not current,
        pending_grammar_edits=sum(r['id'] not in registry.get('applied_requests', [])
                                 for r in words.read(REQUESTS, [])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--request', help='Queue a scoped grammar edit by ID or exact title')
    parser.add_argument('--occurrence-request', help='Queue a context/form-meaning edit by candidate ID; links stay unchanged')
    parser.add_argument('--reason', default='')
    args = parser.parse_args()
    if args.request and args.occurrence_request:
        parser.error('Choose an entry or an occurrence request, not both')
    if args.request or args.occurrence_request:
        if not args.reason.strip():
            parser.error('--request requires --reason')
        entries = words.read(REGISTRY, {}).get('data', {}).get('entries', [])
        matches = ([dict(id=r['id']) for r in candidates()[1] if r['id'] == args.occurrence_request]
                   if args.occurrence_request else
                   [e for e in entries if args.request in (e['id'], e['title'])])
        if len(matches) != 1:
            parser.error('Request must identify exactly one grammar entry')
        request = dict(id='ja-grammar-request-' + decision_digest([matches[0]['id'], args.reason])[:16],
                       reason=args.reason)
        request['candidate_id' if args.occurrence_request else 'entry_id'] = matches[0]['id']
        requests = words.read(REQUESTS, [])
        if request not in requests:
            atomic_json(REQUESTS, requests + [request])
        print(json.dumps(request, ensure_ascii=False))
        return
    if args.plan:
        print(json.dumps(plan(), indent=2))
        return
    result = asyncio.run(update()) if args.update else build()
    atomic_json(OUTPUT, result)
    print(json.dumps(dict(entries=len(result['entries']), usages=len(result['occurrences']))))


if __name__ == '__main__':
    main()
