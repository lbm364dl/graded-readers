import json
from pathlib import Path

import pytest

from pipeline.korean_source_context import REFERENCE, load

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
