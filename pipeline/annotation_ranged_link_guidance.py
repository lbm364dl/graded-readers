"""Explicit contextual ranged-analysis policy; absence preserves old requests."""
import copy,hashlib,json
FIELD='ranged_link_review_policy'
REPRESENTATIONS={'korean-flat','chinese-annotation','japanese-annotation'}
COMMON='''RANGED_LINK_REVIEW_POLICY=1. Before judging a ranged grammar analysis, inspect its entire recorded source form and recorded range, the exact source occurrence, and this representation's attachment rule. Do not silently shorten a recorded phrase to its ending and then reject the recorded attachment. A range attachment is an access point, not a claim that its tap alone contains the whole grammatical construction. Evaluate the linked analysis against the complete recorded span and relevant reviewed lesson. A valid attachment does not establish that the grammatical analysis or meaning is correct. Preserve concrete findings for truly out-of-range attachments, a recorded form that does not reconstruct its source span, or a wrong grammatical analysis. Deterministic source/tap checks remain authoritative: report a concrete inconsistency for host investigation rather than inventing new offsets or authorizing a source/boundary edit. Diagnostic and supporting source spans do not enlarge candidate_paths repair authority. Distinct source positions remain distinct even when their text matches.'''
RULES={
'korean-flat':'''Korean flat grammar_links attach at segment_index; display_end_segment_index, when nonnegative, ends an inclusive displayed span. Inspect the complete recorded display_form and the covered source words. The documented ranged-construction representation stores a first included word as its access index; that word need not itself bear the grammatical ending. A first-range access index is not a defective analysis merely because the ending occurs on a later included tap. An attachment outside the actual represented range, a nonreconstructing recorded display, or an unsupported construction remains a defect. Unranged links retain their own representation contract; do not invent a range for them.''',
'chinese-annotation':'''Chinese grammar_overlays use zero-based Python character start/end offsets into the exact source, end exclusive, and text records the entire source[start:end]. An overlay may span several primary segments; its first character or first covered segment need not bear the grammatical contribution. Inspect the complete overlay text, not only a mentioned component. There is no requirement to move the overlay start to a particle or ending. Nonreconstructing text, out-of-source ranges and wrong analysis remain defects.''',
'japanese-annotation':'''Japanese grammar_overlays use absolute zero-based character start/end offsets into the exact source, end exclusive, and surface records the entire covered source. Component start/end offsets are relative to that overlay surface. The overlay's first character or first covered tap need not be the tap bearing the grammatical ending. Inspect the full overlay and exact component ranges without treating relative offsets as absolute ones. Nonreconstructing surfaces, out-of-overlay component ranges, out-of-source overlays and wrong analysis remain defects.'''}
def guidance(representation):
 if representation not in REPRESENTATIONS:raise ValueError('Unknown ranged-link representation')
 return COMMON+'\n\n'+RULES[representation]
def marker(representation):
 return {'version':1,'representation':representation,'policy_digest':hashlib.sha256(guidance(representation).encode()).hexdigest()}
def with_policy(context,representation):
 result=copy.deepcopy(context);existing=result.get(FIELD)
 if FIELD in result:
  contextual_guidance(result,representation)
  if existing!=marker(representation):raise ValueError('Conflicting ranged-link policy')
 result[FIELD]=marker(representation);return result
def contextual_guidance(context,representation=None):
 if not isinstance(context,dict):return ''
 found=[]
 for owner in (context,context.get('chunk_review_context')):
  if isinstance(owner,dict) and FIELD in owner:
   value=owner[FIELD]
   if not isinstance(value,dict) or type(value.get('version')) is not int or value!=marker(value.get('representation')):raise ValueError('Invalid ranged-link policy')
   found.append(value)
 if not found:return ''
 if any(v!=found[0] for v in found):raise ValueError('Conflicting ranged-link policies')
 if representation is not None and found[0]['representation']!=representation:raise ValueError('Foreign ranged-link representation')
 return guidance(found[0]['representation'])
