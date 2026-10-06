"""Normal-import checks to run after the reviewed proposal is installed."""
from __future__ import annotations

import pytest

from pipeline import annotation_adjudication as adjudication
from pipeline import annotation_missing_layer as missing


def _fixture(tmp_path):
    candidate = {'segments': [
        {'text': '가', 'lemma': '가', 'lexical_id': 'lex-a'},
        {'text': '나', 'lemma': '나', 'lexical_id': 'lex-b'}], 'grammar_links': []}
    paths = ['/segments/0/lexical_id', '/segments/1/lexical_id']
    entry = {'id': 'grammar.test', 'pattern': 'V + particle',
             'meaning_en': 'a reviewed grammar pattern'}
    knowledge = {'catalog_status': 'reviewed', 'approved_entries': [entry]}
    binding = missing.identity_bindings(knowledge)['grammar.test']
    declaration = {'version': 1, 'anchor_paths': paths,
        'collection_path': '/grammar_links', 'identity': 'grammar.test',
        'identity_status': binding['status'], 'identity_digest': binding['digest'],
        'source_interval': {'start': 0, 'end': 2, 'surface': '가나'}}
    issue = {'explanation': 'The required occurrence row is absent at this exact position.',
        'candidate_paths': paths, 'supporting_paths': [], 'missing_layer': declaration}
    review = {'verdict': 'revise', 'approved': False, 'issues': [issue],
              'prose_revision_reason': ''}
    context = {'missing_layer_authority_policy_version': 1,
        'grammar_knowledge': knowledge, 'chunk_text': '가나'}
    references = {'approved-grammar': {'kind': 'approved_lesson', 'content': entry}}
    gate = {'passed': True, 'issues': [], 'candidate_digest': adjudication.digest(candidate),
        'source_text_digest': adjudication.digest('가나')}
    receipt = {'kind': 'normal', 'review_digest': adjudication.digest(review)}
    inputs = adjudication._build_inputs(language='ko', representation='korean-flat',
        candidate=candidate, current_review=review, prior_history=[], context=context,
        known_reference_input=references, deterministic_gate_evidence=gate,
        normal_review_receipt=receipt, source_text='가나',
        host_binding_policy_version=8, run_dir=tmp_path)
    issue_id = inputs['normalized_review']['issues'][0]['issue_id']
    refs = [{'reference_id': 'approved-grammar', 'path': '/meaning_en'}]
    rows = [{'path': path, 'disposition': 'actionable', 'target_binding': 'exact',
        'evidence_refs': refs, 'reason': 'The entry supports the target disposition.',
        'diagnosis': 'Add the missing occurrence row.'} for path in paths]
    output = {'classifications': [{'issue_id': issue_id, 'disposition': 'actionable',
        'target_binding': 'exact', 'candidate_paths': paths, 'evidence_refs': refs,
        'reason': 'The entry supports the missing occurrence finding.',
        'diagnosis': 'Use the validated append declaration.', 'target_dispositions': rows}],
        'new_issues': [], 'prose_revision_reason': ''}
    return inputs, output


def test_installed_v8_requires_all_missing_layer_anchors_actionable(tmp_path):
    inputs, output = _fixture(tmp_path)
    result = adjudication.validate_adjudication_output(output, inputs, run_dir=tmp_path)
    assert result['status'] == 'actionable'
    assert result['repair_diagnoses'][0]['missing_layer'] == \
        inputs['current_review']['issues'][0]['missing_layer']

    output['classifications'][0]['target_dispositions'][1]['disposition'] = 'unsupported'
    output['classifications'][0]['target_dispositions'][1]['diagnosis'] = ''
    result = adjudication.validate_adjudication_output(output, inputs, run_dir=tmp_path)
    assert result['status'] == 'uncertain'
    assert result['classifications'][0]['disposition'] == 'uncertain'
    assert not result['repair_diagnoses']


def test_adjudicated_missing_layer_reaches_repair_authority(tmp_path):
    from pipeline.annotation_repair_authority import build_authority_packet
    from pipeline.annotation_repair_policy_context import missing_layer_repair_context

    inputs, output = _fixture(tmp_path)
    result = adjudication.validate_adjudication_output(output, inputs, run_dir=tmp_path)
    issues = result['repair_diagnoses']
    context = missing_layer_repair_context(inputs['context'], issues)
    packet = build_authority_packet(issues, inputs['candidate'], 'korean-flat',
        adjudication.digest(inputs['candidate']),
        missing_layer_policy_version=context['missing_layer_authority_policy_version'],
        source_text=inputs['source_text'], grammar_knowledge=context['grammar_knowledge'],
        run_dir=tmp_path)
    assert packet['issues'][0]['missing_layer_authority']['declaration'] == \
        inputs['current_review']['issues'][0]['missing_layer']
    assert ['append_row', '/grammar_links'] in packet['issues'][0]['allowed_targets']

    # An unchanged typed declaration cannot acquire append authority without
    # the explicit host policy selected by the caller.
    with pytest.raises(ValueError, match='explicit authority policy'):
        build_authority_packet(issues, inputs['candidate'], 'korean-flat',
            adjudication.digest(inputs['candidate']), source_text=inputs['source_text'],
            grammar_knowledge=context['grammar_knowledge'], run_dir=tmp_path)
