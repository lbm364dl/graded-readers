"""Structural support dependencies; these never assert a linguistic defect."""


def repair_dependency_constraints(candidate, representation, *, version=2):
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unknown repair dependency packet version')
    packet = {'version': version, 'representation': representation, 'dependencies': []}
    # Chinese has no form-stage grammar identity and Japanese form stages do
    # not carry grammar IDs. Their overlays have independent construction IDs;
    # no Korean stage/link equality is inferred for those actual schemas.
    if representation not in {'korean-flat', 'korean-v4'}:
        return packet
    segments = candidate.get('segments', [])
    if representation == 'korean-v4':
        if candidate.get('format') != 'source-span-links-annotation-v4':
            raise ValueError('Korean v4 dependencies require the actual source-span-links representation')
        links = [(f'/segments/{owner}/grammar_links/{index}', owner, row)
                 for owner, segment in enumerate(segments)
                 for index, row in enumerate(segment.get('grammar_links', []))]
    else:
        links = [(f'/grammar_links/{index}', row.get('segment_index'), row)
                 for index, row in enumerate(candidate.get('grammar_links', []))]
    for owner, segment in enumerate(segments):
        steps = segment.get('form_steps', [])
        for index, step in enumerate(steps):
            identities = step.get('grammar_entry_ids', [])
            # The current Korean contract has exactly one identity per stage.
            if not isinstance(identities, list) or len(identities) != 1:
                continue
            identity = identities[0]
            direct = []
            complete = []
            alternatives = []
            if version == 2:
                for other_path, other_anchor, other in links:
                    if other_anchor != owner:
                        continue
                    meaning = other.get('display_meaning_en')
                    if not isinstance(meaning, str) or not meaning.strip():
                        continue
                    if representation == 'korean-flat':
                        last = other.get('display_end_segment_index')
                        if type(last) is not int or not owner <= last < len(segments):
                            continue
                        form = ''.join(row['text'] for row in segments[owner:last + 1])
                        if other.get('display_form') != form:
                            continue
                        source_range = [sum(len(row['text']) for row in segments[:owner]), sum(len(row['text']) for row in segments[:last + 1])]
                    else:
                        start, end = other.get('source_start'), other.get('source_end')
                        if type(start) is not int or type(end) is not int or not 0 <= start < end:
                            continue
                        if not start <= segment['source_start'] < segment['source_end'] <= end:
                            continue
                        # V4 intentionally has no copied source text. Expose its
                        # exact recorded tap-aligned range, never invent a form.
                        cursor = 0
                        coherent = True
                        starts, ends = set(), set()
                        for source_row in segments:
                            left,right = source_row.get('source_start'),source_row.get('source_end')
                            if type(left) is not int or type(right) is not int or left != cursor or right <= left:
                                coherent = False; break
                            starts.add(left);ends.add(right);cursor=right
                        if not coherent or start not in starts or end not in ends or end > cursor:
                            continue
                        form = None
                        source_range = [start,end]
                    if not isinstance(other.get('entry_id'), str) or not other['entry_id']:
                        continue
                    alternatives.append({'path':other_path, 'identity_path':other_path+'/entry_id',
                        'entry_id':other['entry_id'], 'source_range':source_range, 'source_form':form,
                        'display_meaning_en':meaning})
            for link_path, anchor, link in links:
                if link.get('entry_id') != identity:
                    continue
                if representation == 'korean-v4':
                    start, end = link.get('source_start'), link.get('source_end')
                    is_direct = anchor == owner and start == end == -1
                    is_complete = (type(start) is int and type(end) is int and 0 <= start < end
                                   and start <= segment['source_start'] < segment['source_end'] <= end)
                else:
                    end = link.get('display_end_segment_index')
                    is_direct = anchor == owner and link.get('display_form') == '' and end == -1
                    is_complete = (type(anchor) is int and type(end) is int
                                   and 0 <= anchor <= owner <= end < len(segments)
                                   and link.get('display_form') == ''.join(row['text'] for row in segments[anchor:end + 1]))
                if is_direct:
                    direct.append(link_path + '/entry_id')
                elif is_complete:
                    complete.append(link_path)
            if direct:
                dependency = {
                    'identity_path': f'/segments/{owner}/form_steps/{index}/grammar_entry_ids',
                    'owner_segment_index': owner, 'owner_surface': segment.get('text'),
                    'owner_source_range': [segment.get('source_start'), segment.get('source_end')]
                        if representation == 'korean-v4' else None,
                    'stage_form': step.get('form'), 'old_identity': identity,
                    'direct_link_identity_paths': direct,
                    'retained_stage_identity_paths': [f'/segments/{owner}/form_steps/{other}/grammar_entry_ids'
                        for other, row in enumerate(steps) if other != index
                        and identity in row.get('grammar_entry_ids', [])],
                    'complete_occurrence_paths': complete}
                if version == 2:
                    dependency['same_anchor_complete_occurrences'] = alternatives
                packet['dependencies'].append(dependency)
    return packet


def _changes_identity(target, path):
    operation, pointer = target['op'], target['path']
    if operation in {'set_field', 'replace_list'} and (pointer == path or pointer.startswith(path + '/')):
        return True
    stage = path.rsplit('/', 1)[0]
    if operation == 'remove_row' and pointer == stage:
        return True
    # Ancestor row/list replacements can change identity. A meaning-only
    # repair should select its meaning leaf to avoid this ambiguity.
    return operation in {'replace_row', 'replace_list'} and path.startswith(pointer + '/')


def _covers_link(target, path):
    return ((target['op'] == 'set_field' and target['path'] == path)
            or (target['op'] in {'remove_row', 'replace_row'} and target['path'] == path.rsplit('/', 1)[0])
            or (target['op'] == 'replace_list' and target['path'] == path.rsplit('/', 2)[0]))


def validate_plan_dependencies(plan, candidate, representation, constraints=None):
    version = constraints.get('version') if isinstance(constraints, dict) else 2
    expected = repair_dependency_constraints(candidate, representation, version=version)
    if constraints is not None and constraints != expected:
        raise ValueError('Repair dependency constraints differ from immutable candidate geometry')
    all_targets = [target for row in plan['issues'] for target in row['targets']]
    for row in plan['issues']:
        for dependency in expected['dependencies']:
            if not any(_changes_identity(target, dependency['identity_path']) for target in row['targets']):
                continue
            retained = any(not any(_changes_identity(target, path) for target in all_targets)
                           for path in dependency['retained_stage_identity_paths'])
            # A complete occurrence can justify removing a redundant direct
            # row, but cannot make a now-unsupported empty direct row valid.
            if retained:
                continue
            for path in dependency['direct_link_identity_paths']:
                if not any(_covers_link(target, path) for target in row['targets']):
                    raise ValueError('Identity repair removes sole direct-link support; same issue must plan its dependent link: ' + path)
