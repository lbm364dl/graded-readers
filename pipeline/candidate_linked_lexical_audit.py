"""Proposal helper: explicit candidate-to-record indexing without semantic inference."""
from __future__ import annotations
import hashlib,json

def digest(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

LEXICAL_AUDIT_VERSION = 1
LEXICAL_AUDIT_GUIDANCE = '''CANDIDATE-LINKED LEXICAL IDENTITY AUDIT (version 1): The workspace index connects exact candidate lexical identity fields and source intervals to complete caller-supplied reference records. Use it only to inspect identity and sense evidence. A reviewed dictionary record does not approve the occurrence gloss or candidate. Do not select a homograph automatically, infer a missing ID, or treat same-headword alternatives as equivalent.'''

def run_lesson_source_descriptors(context):
 """Project only source descriptors; the host replay authenticates these before use."""
 packet=context.get('reviewed_run_lessons') if isinstance(context,dict) else None
 if not isinstance(packet,dict): return []
 keys=('source_run_relpath','import_receipt','expected_context')
 rows=packet.get('lessons',[])
 descriptors=[]
 for row in rows if isinstance(rows,list) else []:
  if not isinstance(row,dict) or any(key not in row for key in keys):
   raise ValueError('run-reviewed lesson lacks its original source descriptor')
  descriptors.append({key:row[key] for key in keys})
 return descriptors

def _source_hash(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def korean_caller_audit(annotation, text, context):
 """Index the exact approved/candidate records already supplied by the KO reviewer."""
 # Review contexts carry materialized record arrays rather than live registries.
 approved=context.get('approved_words',[]); candidates=context.get('lexical_candidates',[])
 if isinstance(approved,dict): approved=list(approved.values())
 if isinstance(candidates,dict): candidates=list(candidates.values())
 approved=[r for r in approved if isinstance(r,dict) and isinstance(r.get('id'),str)]
 candidates=[r for r in candidates if isinstance(r,dict) and isinstance(r.get('id'),str)]
 def group(role, source, records):
  return {'role':role,'source_id':source,'source_sha256':_source_hash(context),'records':records}
 sets=[group('approved_context_records','review-input.context.approved_words',approved),
       group('candidate_context_records','review-input.context.lexical_candidates',candidates)]
 eligible={str(i):[r['id'] for r in candidates if r.get('headword')==seg.get('lemma')]
           for i,seg in enumerate(annotation.get('segments',[])) if seg.get('lemma')}
 return candidate_linked_identity_audit(language='ko',representation='korean-flat',annotation=annotation,
  source_text=text,identity_field='lexical_id',headword_field='lemma',record_sets=sets,
  eligible_ids_by_segment=eligible,unavailable_reason=None if sets else 'No caller-supplied lexical records.')

def japanese_caller_audit(annotation, text, dictionary, dictionary_sha256):
 """Project only canonical candidates returned by the Japanese link policy."""
 from pipeline.japanese_dictionary_links import candidate_for_lemma
 keys=set(); eligible={}
 for i,seg in enumerate(annotation.get('segments',[])):
  lemma=seg.get('lemma'); reading=seg.get('lemma_kana')
  if not isinstance(lemma,str) or not lemma or not isinstance(reading,str) or not reading: continue
  choice=candidate_for_lemma(lemma,reading)
  options=(choice or {}).get('canonical_candidates')
  if options is None and choice and choice.get('dictionary_key'): options=[choice]
  if options:
   ids=[]
   for option in options:
    key=option['dictionary_key']; ids.append(key); keys.add(key)
   eligible[str(i)]=ids
 records=[]
 for key in sorted(keys):
  raw=dictionary.get(key)
  if not isinstance(raw,dict) or raw.get('w',key)!=key or raw.get('a') is True:
   raise ValueError(f'caller candidate is absent from canonical Japanese dictionary: {key}')
  records.append({'id':key,'headword':raw.get('w',key),'dictionary_key':key,'entry':raw})
 return candidate_linked_identity_audit(language='ja',representation='japanese-segments',annotation=annotation,
  source_text=text,identity_field='dictionary_key',headword_field='lemma',
  record_sets=[{'role':'canonical_local_dictionary_entries','source_id':'app/assets/dictionary_ja.json',
                'source_sha256':dictionary_sha256,'records':records}],eligible_ids_by_segment=eligible)

def chinese_caller_audit(annotation, text):
 """Chinese's present annotation contract has no stable lexical identity field."""
 return candidate_linked_identity_audit(language='zh',representation='chinese-annotation',annotation=annotation,
  source_text=text,identity_field=None,headword_field=None,record_sets=[],
  unavailable_reason='Chinese review contract supplies no stable lexical identity field or approved word-reference registry.')

def candidate_linked_identity_audit(*, language, representation, annotation, source_text,
                                    identity_field, headword_field, record_sets, eligible_ids_by_segment=None,
                                    record_source_sha256=None, unavailable_reason=None):
 """Build a deterministic audit index and one lossless record-reference table.

    `record_sets` is a list of {role, source_id, source_sha256, records}; every
    record is copied exactly. The helper never creates a lexical identity or
    chooses between alternatives. Same-headword grouping compares only the
    explicit headword field to the candidate's explicit lemma field.
    """
 if not isinstance(annotation,dict) or not isinstance(annotation.get('segments'),list):
  raise ValueError('candidate annotation has no segments')
 if not isinstance(source_text,str): raise ValueError('source text must be exact text')
 if ''.join(str(row.get('text',row.get('surface',''))) for row in annotation['segments'])!=source_text:
  raise ValueError('candidate segments do not reconstruct exact source')
 records_by_key={}; logical_ids={}
 for group in record_sets:
  for original in group.get('records',[]):
   if not isinstance(original,dict) or not isinstance(original.get('id'),str) or not isinstance(original.get('headword'),str):
    raise ValueError('reference record lacks an explicit stable id or headword')
   logical_id=original['id']
   # Per-file workspace IDs must be unique. Preserve the logical dictionary ID
   # separately and group byte-identical records while retaining every origin.
   record_digest=digest(original)
   row_id='lexref:'+hashlib.sha256((logical_id+'\0'+record_digest).encode()).hexdigest()
   key=(logical_id,record_digest)
   envelope=records_by_key.get(key)
   provenance={'source_role':group['role'],'source_id':group['source_id'],
               'source_sha256':group['source_sha256']}
   if envelope is None:
    envelope={'id':row_id,'lexical_id':logical_id,'headword':original['headword'],
      'source_provenance':[provenance],'record_sha256':record_digest,'record':original}
    records_by_key[key]=envelope
   elif provenance not in envelope['source_provenance']:
    envelope['source_provenance'].append(provenance)
   logical_ids.setdefault(logical_id,[]).append(row_id)
 records=list(records_by_key.values())
 by_id={}
 for row in records: by_id.setdefault(row['lexical_id'],[]).append(row)
 offsets=[]; pos=0
 for idx,row in enumerate(annotation['segments']):
  surface=row.get('text',row.get('surface','')); start=pos; end=start+len(surface); pos=end
  lemma=row.get(headword_field,'') if headword_field else ''
  identity=row.get(identity_field,'') if identity_field else ''
  same_headword=[r['id'] for r in records if r['headword']==lemma] if lemma else []
  used=[r['id'] for r in by_id.get(identity,[])] if identity else []
  eligible=list((eligible_ids_by_segment or {}).get(str(idx), []))
  unknown=set(eligible)-set(by_id)
  if unknown: raise ValueError(f'caller supplied unknown lexical record ids: {sorted(unknown)}')
  eligible_rows=[r['id'] for logical in eligible for r in by_id.get(logical,[])]
  offsets.append({'candidate_path':f'/segments/{idx}/{identity_field}' if identity_field and identity else None,
   'segment_path':f'/segments/{idx}','source_local_interval':[start,end],'surface':surface,
   'explicit_lemma':lemma or None,'explicit_identity':identity or None,
   'used_reference_ids':list(dict.fromkeys(used)),
   'eligible_reference_ids':list(dict.fromkeys([*used,*eligible_rows])),
   'same_headword_reference_ids':list(dict.fromkeys(same_headword)),
   'identity_status':('recorded_identity_has_reference' if used else 'recorded_identity_unmatched' if identity else 'no_identity_field_in_representation')})
 if pos!=len(source_text): raise ValueError('source offsets disagree')
 index={'format':'candidate_linked_lexical_identity_audit_v1','language':language,'representation':representation,
  'candidate_digest':digest(annotation),'source_text_digest':hashlib.sha256(source_text.encode()).hexdigest(),
  'identity_field':identity_field,'headword_field':headword_field,
  'alternative_ids_are_caller_supplied':True,
  'reference_sources':[{'source_id':g['source_id'],'source_role':g['role'],'source_sha256':g['source_sha256'],'record_count':len(g.get('records',[]))} for g in record_sets],
  'segments':offsets,
  'non_authority_notice':'This is a navigational index over exact candidate fields and supplied records. It does not approve a candidate occurrence or choose an identity. Same-headword rows are grouped only by exact stored headword equality; no lemma or identity is inferred.',
  'unavailable_reason':unavailable_reason}
 columns=['id','lexical_id','headword','source_provenance','record_sha256','record']
 view={'format':'lossless_reference_rows','columns':columns,'rows':[[r[c] for c in columns] for r in records]}
 return {'candidate_linked_lexical_identity_index':index,
         'candidate_linked_lexical_identity_records':view}
