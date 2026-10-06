import pytest

from pipeline.dictionary_investigations import refresh

ENTRIES = [dict(id='word', headword='流行')]
ROWS = [dict(entry_id='word', investigation_questions=[
    dict(component='行', question='What does 行 contribute?')],
    research=dict(gaps=['Evidence does not resolve the historical derivation.']))]


def test_issues_are_structured_idempotent_and_preserve_triage():
    doc = refresh(ENTRIES, ROWS)
    assert len(doc['issues']) == 2
    assert all(i['status'] == 'open' and i['current'] for i in doc['issues'])
    doc['issues'][0].update(status='resolved', resolution='Checked independently')
    assert refresh(ENTRIES, ROWS, doc) == doc
    retired = refresh(ENTRIES, [], doc)
    assert all(not i['current'] for i in retired['issues'])
    assert retired['issues'][0]['resolution'] == 'Checked independently'


def test_reader_notes_do_not_need_a_research_dossier():
    doc = refresh(ENTRIES, [], notes=[dict(entry_id='word', component='行', question='Explain 行')])
    assert doc['issues'][0]['origin'] == 'reader'
    assert doc['issues'][0]['evidence'] == {}


def test_inactive_registry_rows_do_not_create_dangling_issues():
    assert refresh(ENTRIES, [dict(entry_id='retired',
        research=dict(gaps=['Unresolved old fragment']))])['issues'] == []


@pytest.mark.parametrize('note', [dict(entry_id='missing', question='Why?'),
    dict(entry_id='word', question=' '), dict(entry_id='word', component='相', question='Why?')])
def test_invalid_issues_fail_closed(note):
    with pytest.raises(ValueError):
        refresh(ENTRIES, [], notes=[note])
