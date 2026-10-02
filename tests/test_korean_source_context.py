import json
from pathlib import Path

import pytest

from pipeline.korean_source_context import REFERENCE, load
from pipeline import korean_source_context as context

SOURCE = Path('books/korean/honggildong/original.txt').read_text()


def test_reviewed_context_preserves_exact_source_and_finding():
    findings = load(SOURCE)
    assert findings
    assert findings[0]['paragraphs']
    assert findings[0]['finding_en']


@pytest.mark.parametrize('change', ['finding', 'review', 'completion', 'source', 'quotation'])
def test_unreviewed_or_stale_context_is_rejected(tmp_path, change):
    data = json.loads(REFERENCE.read_text())
    record = data['records'][0]
    if change == 'finding':
        record['draft']['finding_en'] = 'An unsupported replacement'
    elif change == 'review':
        record['review'] = {'approved': False, 'issues': ['Unsupported identity']}
    elif change == 'completion':
        record['meta']['return_code'] = 1
    elif change == 'quotation':
        record['draft']['paragraphs'][0]['text'] = 'Changed quotation'
    path = tmp_path / 'context.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load(SOURCE + ('Changed source' if change == 'source' else ''), path)


def test_exact_historical_context_remains_verifiable_but_tampering_fails(tmp_path, monkeypatch):
    import hashlib
    original = REFERENCE.read_bytes()
    expected = hashlib.sha256(original).hexdigest()
    current = tmp_path / 'current.json'
    current.write_text('Changed current guidance')
    snapshot = tmp_path / (expected + '.json')
    snapshot.write_bytes(original)
    monkeypatch.setattr(context, 'REFERENCE', current)
    monkeypatch.setattr(context, 'HISTORY', tmp_path)
    assert context.matches(expected)
    assert load(SOURCE, snapshot)
    snapshot.write_text('Altered snapshot')
    assert not context.matches(expected)
    assert not context.matches('../unapproved')
