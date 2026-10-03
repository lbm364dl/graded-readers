"""Link reviewed meaning-guide parts without changing lexical segmentation.

Multi-character components must already have reviewed recursive word entries.
Unattested single-character words link to the character dictionary instead.
"""
from copy import deepcopy


def character_reference(character):
    return dict(dictionary='hanzi-etymology', character=character,
                id=f'character:U+{ord(character):04X}')


def extend(dictionary):
    output = deepcopy(dictionary)
    original = list(output['entries'])
    by_word = {}
    for entry in original:
        by_word.setdefault(entry['headword'], []).append(entry)
    for parent in original:
        if len(parent['headword']) == 1:
            parent['character_ref'] = character_reference(parent['headword'])
        offset = 0
        for part in parent.get('meaning_guide', {}).get('parts', []):
            word = part['text']
            if not word or parent['headword'][offset:offset + len(word)] != word:
                raise ValueError('Component spans must reconstruct the parent word')
            part.update(start=offset, end=offset + len(word),
                        entry_id=None, sense_id=None, character_ref=None)
            offset += len(word)
            if word == parent['headword']:
                # An explanation of the whole word is not a decomposition.
                part['link_status'] = 'whole_entry'
                continue
            matches = by_word.get(word, [])
            if len(matches) == 1:
                target = matches[0]
                part['link_status'] = 'entry'
                # The contribution is contextual; spelling does not prove that
                # any particular standalone sense applies inside this word.
            else:
                if len(word) != 1:
                    raise ValueError('Missing or ambiguous component word entry; editorial resolution required')
                part.update(link_status='character', character_ref=character_reference(word))
                continue
            part['entry_id'] = target['id']
            target.setdefault('component_uses', []).append(dict(
                parent_entry_id=parent['id'], start=part['start'], end=part['end'],
                contribution_en=part['contribution_en']))
        if offset and offset != len(parent['headword']):
            raise ValueError('Component spans must reconstruct the parent word')
    ids = [e['id'] for e in output['entries']]
    if len(ids) != len(set(ids)):
        raise ValueError('Component entry ID collision')
    return output
