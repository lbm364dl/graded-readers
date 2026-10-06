"""Pure, fail-closed migration helpers for independently reviewed boundaries.

Callers own source snapshot checks, backups, locking and atomic publication.
These helpers never approve new senses or silently reinterpret unchanged uses.
"""
from copy import deepcopy

from pipeline import usage_dictionary as lexical
from pipeline.chinese_readability import classify_reviewed_segment


def corrected_chapter(chapter, revisions, level):
    """Apply exact, non-overlapping lexical spans without changing prose."""
    result = deepcopy(chapter)
    original = chapter['segments']
    ordered = sorted(revisions, key=lambda r: r['first_segment'])
    previous_end = -1
    output = []
    index_map = {}
    cursor = 0
    starts, offset = [], 0
    for segment in original:
        starts.append(offset)
        offset += len(segment['text'])
    assert ''.join(s['text'] for s in original) == chapter['text']
    for revision in ordered:
        first, last = revision['first_segment'], revision['last_segment']
        if (type(first) is not int or type(last) is not int or
                not 0 <= first <= last < len(original) or first <= previous_end):
            raise ValueError('Invalid or overlapping correction range')
        span = original[first:last + 1]
        parts = revision['segments']
        text = ''.join(s['text'] for s in span)
        if (any(s['type'] == 'punctuation' for s in span) or
                revision['text'] != text or not parts or
                ''.join(s['text'] for s in parts) != text):
            raise ValueError('Boundary correction changes source or crosses punctuation')
        for index in range(cursor, first):
            index_map[index] = len(output)
            output.append(deepcopy(original[index]))
        for index in range(first, last + 1):
            index_map[index] = len(output)
        for part in parts:
            if (part['type'] not in {'word', 'name', 'particle', 'idiom'} or
                    not all(str(part[k]).strip() for k in ('text', 'pinyin', 'meaning_en')) or
                    any(c.isspace() for c in part['text'])):
                raise ValueError('Correction lacks lexical metadata')
            new_segment = deepcopy(span[0])
            new_segment.update(part)
            # A combined token's old story/composition claims must not be
            # inherited by unrelated newly separated parts.
            for field in ('story_term', 'story_term_meaning_en',
                          'story_term_importance_en', 'composition_en', 'focus_review_note_en'):
                if field in new_segment:
                    new_segment[field] = ''
            if 'subsegments' in new_segment:
                new_segment['subsegments'] = []
            story = [s for s in span if s.get('story_term')]
            if story and (part['text'] == text or part['text'] == story[0]['story_term']):
                fields = ('story_term', 'story_term_meaning_en', 'story_term_importance_en')
                if len({tuple(s.get(k, '') for k in fields) for s in story}) == 1:
                    for field in fields:
                        new_segment[field] = story[0].get(field, '')
            new_segment.update(classify_reviewed_segment(part['text'], part['type'], level,
                is_story_term=bool(new_segment.get('story_term'))))
            output.append(new_segment)
        explanation = revision.get('grammar_explanation_en', '').strip()
        if explanation:
            result.setdefault('grammar_overlays', []).append(dict(
                start=starts[first], end=starts[last] + len(original[last]['text']),
                text=text, grammar_candidate_key='reviewed-boundary-construction',
                pattern=' | '.join(p['text'] for p in parts), meaning_en=explanation))
        previous_end, cursor = last, last + 1
    for index in range(cursor, len(original)):
        index_map[index] = len(output)
        output.append(deepcopy(original[index]))
    result['segments'] = output
    assert ''.join(s['text'] for s in output) == chapter['text']
    return result, index_map


def migrate_links(decisions, old_uses, new_uses, fingerprint, affected_words):
    """Retain exact unchanged spans and valid per-word approvals.

    Returns unlinked new occurrences for a source-local proposal/review. Until
    they have assignments, the returned registry is deliberately not publishable.
    """
    key = lambda o: (o['source'], o['start'], o['end'], o['surface'])
    new_spans = {key(o): o for o in new_uses}
    if len(new_spans) != len(new_uses):
        raise ValueError('Duplicate new occurrence span')
    mapping = {}
    old_by_id = {o['id']: o for o in old_uses}
    if len(old_by_id) != len(old_uses):
        raise ValueError('Duplicate old occurrence ID')
    for old in old_uses:
        new = new_spans.get(key(old))
        if new is None:
            if old['surface'] not in affected_words:
                raise ValueError('Unrelated occurrence disappeared')
            continue
        if any(old[k] != new[k] for k in ('reading', 'gloss', 'sentence', 'sentence_start')):
            if old['surface'] in affected_words:
                # Reuse neither the old link nor its approval when a reviewed
                # correction also changes the reading/contextual gloss.
                continue
            raise ValueError('Unchanged span has changed linguistic evidence')
        mapping[old['id']] = new['id']
    result = deepcopy(decisions)
    for entry in result['entries']:
        for sense in entry['senses']:
            if any(oid not in mapping and (oid not in old_by_id or
                   old_by_id[oid]['surface'] not in affected_words) for oid in sense['occurrences']):
                raise ValueError('Unknown old occurrence link')
            sense['occurrences'] = [mapping[oid] for oid in sense['occurrences'] if oid in mapping]
    stamps = {}
    all_approved = (decisions.get('reviewed') and decisions.get('review_digest') ==
                    lexical.decision_digest(decisions['entries']))
    for word in {e['headword'] for e in decisions['entries']} - set(affected_words):
        old = [e for e in decisions['entries'] if e['headword'] == word]
        if all_approved or decisions.get('reviewed_word_fingerprints', {}).get(word) == lexical.decision_digest(old):
            stamps[word] = lexical.decision_digest([e for e in result['entries'] if e['headword'] == word])
    linked = {o for e in result['entries'] for s in e['senses'] for o in s['occurrences']}
    unlinked = [o for o in new_uses if o['id'] not in linked]
    if any(o['surface'] not in affected_words for o in unlinked):
        raise ValueError('Unrelated new occurrence is unlinked')
    result.update(source_fingerprint=fingerprint, reviewed=False,
                  reviewed_word_fingerprints=stamps,
                  review_digest=lexical.decision_digest(result['entries']))
    return result, unlinked, mapping
