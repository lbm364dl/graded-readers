"""Versioned shared editorial guidance; independent reviewers judge semantics."""
import copy
from pipeline.annotation_adjudication import digest
FIELD='dictionary_editorial_criteria'
CRITERIA='''Explain this entry's own reusable meaning, formation and essential usage constraints. Evidence coverage and comparison inventories are checks on the explanation, not a list of topics to teach. A prerequisite or contrast belongs here only when it answers a natural question about this entry; state the distinction briefly and link the other approved lesson when the schema supports a link. Do not teach the other pattern's formation, meaning and variants in this entry merely because it appears among the contrasts. Preserve supported functions of this entry and essential formation; do not shorten away evidence-backed content just to avoid a contrast. Keep contextual translations and full example sentences in the dedicated occurrence/example presentation. A minimal schematic form may clarify this entry's own formation; that does not authorize an inline example-sentence catalog. If the permitted revision schema has no example field, leave dedicated example cards unchanged instead of inventing fields or adding a For Example paragraph. Research approval establishes cited claims, not approval of every possible lesson sentence. On repair, account for every critic objection: remove or revise the implicated sentence, or retain it only with concrete evidence explaining why the objection is unsupported for this entry. Do not paraphrase a rejected off-topic lesson into the same explanation. The independent critic must check scope and unresolved prior objections against the actual new artifact.'''
def marker():return {'version':1,'policy_digest':digest(CRITERIA)}
def validate(value):
 if not isinstance(value,dict) or type(value.get('version')) is not int or value!=marker():raise ValueError('Unknown dictionary editorial criteria')
 return value

def guidance(value=None):
 if value is None:return ''
 validate(value);return CRITERIA


def append(prompt,value="current"):
 if value=="current":value=marker()
 if value is None:return prompt
 validate(value)
 import json
 return prompt+'\nSHARED EDITORIAL CRITERIA:\n'+json.dumps({FIELD:value})+'\n'+CRITERIA
