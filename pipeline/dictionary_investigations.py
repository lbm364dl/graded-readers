"""Internal, persistent follow-up issues; never schedules model or web calls."""
from copy import deepcopy

from pipeline.usage_dictionary import ROOT, decision_digest

PATH = ROOT / 'content/lexicon/dictionary.investigations.json'
NOTES = ROOT / 'content/lexicon/dictionary.investigation-notes.json'


def refresh(entries, rows, previous=None, notes=()):
    by_id = {entry['id']: entry for entry in entries}
    issues = {issue['id']: deepcopy(issue)
              for issue in (previous or {}).get('issues', [])}
    for issue in issues.values():
        issue['current'] = False
    candidates = list(notes)
    for row in rows:
        if row['entry_id'] not in by_id:
            continue  # Registries also retain inactive component identities.
        for question in row.get('investigation_questions', []):
            candidates.append(dict(entry_id=row['entry_id'], **question,
                                   origin='editor', evidence=row.get('research', {})))
        for gap in row.get('research', {}).get('gaps', []):
            candidates.append(dict(entry_id=row['entry_id'], component='', question=gap,
                                   origin='research_gap', evidence=row['research']))
    for candidate in candidates:
        eid, component = candidate['entry_id'], candidate.get('component', '')
        question = candidate['question'].strip()
        if eid not in by_id or not question:
            raise ValueError('Invalid dictionary investigation target/question')
        if component and component not in by_id[eid]['headword']:
            raise ValueError('Investigation component is not in its headword')
        iid = 'investigate-' + decision_digest([eid, component, question])[:20]
        issue = issues.setdefault(iid, dict(id=iid, status='open'))
        issue.update(entry_id=eid, headword=by_id[eid]['headword'], component=component,
                     question=question, current=True, origin=candidate.get('origin', 'reader'),
                     evidence=deepcopy(candidate.get('evidence', {})))
    return dict(schema_version=1, issues=sorted(issues.values(), key=lambda i: i['id']))
