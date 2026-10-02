"""Reviewed, source-bound guidance for historical narrative interpretation."""
import hashlib
import json
from pathlib import Path

from jsonschema import validate
from pipeline.agent_harness import digest

REFERENCE = Path(__file__).resolve().parents[1] / 'data/korean/source-context.json'


def load(source, path=REFERENCE):
    records = json.loads(path.read_text())['records']
    paragraphs = source.rstrip('\n').split('\n\n')
    source_hash = hashlib.sha256(source.rstrip('\n').encode()).hexdigest()
    findings = []
    for record in records:
        draft, meta, review = (record[k] for k in ('draft', 'meta', 'review'))
        validate(review, json.loads(record['schema']))
        fingerprint = digest(record['prompt'], record['schema'], meta['model'], meta['effort'])
        fingerprint = digest(fingerprint, 'offline', 'tool-profile-v1')
        reviewed_input = json.loads(record['prompt'].split('\nINPUT:\n', 1)[1])
        if (reviewed_input != draft or meta['fingerprint'] != fingerprint
                or meta.get('return_code') != 0 or meta.get('tool_profile') != 'offline'
                or not review['approved'] or review['issues']
                or draft['source_sha256'] != source_hash):
            raise ValueError('Unverified Korean source guidance')
        for paragraph in draft['paragraphs']:
            if paragraphs[paragraph['index']] != paragraph['text']:
                raise ValueError('Korean source guidance quotation changed')
        findings.append(draft)
    return findings
