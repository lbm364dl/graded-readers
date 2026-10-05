"""Replayable entry reuse from independent run lessons, without registry promotion."""
from copy import deepcopy
from pathlib import Path
from pipeline.annotation_adjudication import digest
from pipeline.annotation_run_lessons import _authenticate

FIELD = 'reviewed_dictionary_handoff'
GUIDANCE = ('The reviewed_run_grammar entries have independently approved standalone writer/critic '
    'provenance, but are not published registry entries or annotation approvals. Preserve their exact '
    'identities and content. Evaluate all required chapter uses and the complete dictionary delta. '
    'A defect in one retained entry requires an explicit independently reviewed source revision; '
    'do not replace its definition while drafting another entry.')
class DictionaryHandoffError(ValueError): pass


def replay_handoff(run_dir, handoff, *, language, chapter_text, required_ids, published):
    if handoff is None: return []
    if (not isinstance(handoff, dict) or set(handoff) != {'version','language','chapter_digest','sources','digest'}
        or type(handoff['version']) is not int or handoff['version'] != 1
        or handoff['language'] != language or handoff['chapter_digest'] != digest(chapter_text)
        or handoff['digest'] != digest({k:v for k,v in handoff.items() if k != 'digest'})
        or not isinstance(handoff['sources'],list) or not 1 <= len(handoff['sources']) <= 3):
        raise DictionaryHandoffError('Reviewed dictionary handoff scope or digest changed')
    result=[];seen=set()
    for row in handoff['sources']:
        try: expected, proof = _authenticate(row, Path(run_dir))
        except ValueError as error: raise DictionaryHandoffError('Imported dictionary source does not authenticate') from error
        if expected.get('language') != language or expected.get('chapter',{}).get('text') != chapter_text:
            raise DictionaryHandoffError('Imported dictionary source belongs to another chapter or language')
        lesson=next(iter(proof['references'].values()))['content']
        entry={k:deepcopy(v) for k,v in lesson.items() if k not in {'_annotation_research_fact','issue_ids'}}
        identity=entry.get('id')
        if identity not in required_ids or identity in published or identity in seen:
            raise DictionaryHandoffError('Imported dictionary identity conflicts, repeats or is not requested')
        seen.add(identity);result.append(entry)
    return result


def make_handoff(run_dir, packets, *, language, chapter_text, required_ids, published):
    rows=[];seen=set()
    for packet in packets:
        for row in packet.get('lessons',[]):
            identity=row['import_receipt']['lesson_id']
            if identity not in required_ids: continue
            if identity in published:
                _, proof = _authenticate(row, Path(run_dir))
                lesson = next(iter(proof['references'].values()))['content']
                entry = {k:deepcopy(v) for k,v in lesson.items() if k not in {'_annotation_research_fact','issue_ids'}}
                if published[identity] != entry:
                    raise DictionaryHandoffError('Reviewed run lesson conflicts with published entry; explicit registry revision required')
                continue
            # Exactly the same receipt may be supplied by multiple authenticated inputs.
            key=digest(row)
            if key not in seen: rows.append(deepcopy(row));seen.add(key)
    if not rows:return None
    body={'version':1,'language':language,'chapter_digest':digest(chapter_text),'sources':rows}
    handoff={**body,'digest':digest(body)}
    replay_handoff(run_dir,handoff,language=language,chapter_text=chapter_text,
                   required_ids=required_ids,published=published)
    return handoff


def collect_korean_handoff(run_dir, chunks, texts, chapter_text, published):
    """Select authenticated current run sources at exact assembled chunk positions."""
    from pipeline.annotation_run_lesson_callers import resolve_run_lesson_context
    if len(chunks) != len(texts) or ''.join(texts) != chapter_text:
        raise DictionaryHandoffError('Dictionary handoff chunk source differs from chapter')
    packets=[];offset=0
    for index,(candidate,text) in enumerate(zip(chunks,texts)):
        position={'chunk_index':index,'source_text_digest':digest(text),
                  'parent_text_digest':digest(chapter_text),'source_start':offset}
        bound=resolve_run_lesson_context(run_dir,candidate=candidate,source_text=text,language='ko',
            representation='korean-flat',context={'annotation_source_position':position})
        if bound['packet']:packets.append(bound['packet'])
        offset+=len(text)
    required={row['entry_id'] for chunk in chunks for row in chunk['grammar_links']}
    return make_handoff(run_dir,packets,language='ko',chapter_text=chapter_text,
                        required_ids=required,published=published)


def verify_review_handoff(meta, context):
    """Bind assembly source lineage to the exact independent review input."""
    saved=meta.get(FIELD)
    if saved is None and FIELD not in context:return
    if saved is None or context.get(FIELD) != saved:
        raise DictionaryHandoffError('Dictionary handoff differs from independently reviewed context')
    scope=meta['dictionary_import_scope']
    reviewed_published = {entry['id']:entry for entry in context.get('approved_grammar', [])}
    if any(scope['published'].get(identity) != entry for identity,entry in reviewed_published.items()):
        raise DictionaryHandoffError('Dictionary handoff published entries changed')
    if scope['chapter_text'] != context.get('chapter',{}).get('text') or set(scope['required_ids']) != set(context.get('grammar_requests', [])):
        raise DictionaryHandoffError('Dictionary handoff requested identities or chapter changed')


def validate_retained_bindings(bindings, imported):
    identities={entry['id'] for entry in imported}
    if any(row['draft_id'] in identities and row['entry_id'] != row['draft_id'] for row in bindings['bindings']):
        raise DictionaryHandoffError('Reviewed run lesson identity requires exact self-binding')


def handoff_after_annotation_stage(run_dir, evidence, annotation, *, chapter_text, published):
    """Derive current knowledge after an already verified stage, preserving its receipts.

    This is not a stage approval verifier: callers must first complete the normal
    stage cache/fresh proof checks. Saved lineage is checked again before using
    its exact source positions; final coordinated chunk views bind the sources.
    """
    from pipeline.korean_agent_harness import read, read_annotation_chunk, digest as worker_digest
    from pipeline import korean_contracts as contracts
    from pipeline.agent_harness import CodexRunner
    from pipeline.annotation_run_lesson_callers import resolve_run_lesson_context
    job=evidence.get('proposal_job')
    if job is None:return None
    if Path(job).name != job or evidence.get('approved') is not True or evidence.get('output_digest') != worker_digest(annotation):
        raise DictionaryHandoffError('Dictionary handoff requires the exact verified annotation stage')
    directory=Path(run_dir)/'agents'/job
    meta=read(directory/'meta.json')
    if meta.get('kind') != 'annotation_assembly':
        if meta.get(FIELD) is not None:raise DictionaryHandoffError('Dictionary handoff lacks source chunk lineage')
        return None
    from pipeline.annotation_research import _reviewed_lesson_source_manifest_path
    if meta.get(FIELD) is None and not _reviewed_lesson_source_manifest_path(Path(run_dir),create=False).exists():
        return None
    if meta.get('return_code') != 0 or read(directory/'result.json') != annotation:
        raise DictionaryHandoffError('Accepted annotation assembly changed')
    values=[];texts=[]
    for record in meta['chunks']:
        child=record['job']
        if Path(child).name != child:raise DictionaryHandoffError('Invalid dictionary source chunk job')
        value=read_annotation_chunk(Path(run_dir)/'agents'/child/'result.json',record)
        child_meta=read(Path(run_dir)/'agents'/child/'meta.json')
        if child_meta.get('return_code') != 0 or worker_digest(value) != record['digest']:
            raise DictionaryHandoffError('Dictionary source chunk lineage changed')
        CodexRunner._check_tool_profile(Path(run_dir)/'agents'/child,'offline',child_meta)
        values.append(value);texts.append(record['text'])
    if ''.join(texts) != chapter_text:raise DictionaryHandoffError('Accepted annotation parent changed')
    assembled=contracts.combine_annotations(values,texts)
    if 'grammar_binding_job' in meta:
        binding_job=meta['grammar_binding_job']
        if Path(binding_job).name != binding_job:raise DictionaryHandoffError('Invalid dictionary grammar binding job')
        binding_meta=read(Path(run_dir)/'agents'/binding_job/'meta.json')
        bindings=read(Path(run_dir)/'agents'/binding_job/'result.json')
        if binding_meta.get('return_code') != 0 or worker_digest(bindings) != meta['grammar_binding_digest']:
            raise DictionaryHandoffError('Dictionary grammar binding lineage changed')
        CodexRunner._check_tool_profile(Path(run_dir)/'agents'/binding_job,'offline',binding_meta)
        assembled=contracts.bind_grammar_identities(assembled,bindings,set(meta['approved_grammar_ids']))
    if assembled != annotation:raise DictionaryHandoffError('Accepted annotation differs from source-position lineage')
    saved=meta.get(FIELD)
    required={row['entry_id'] for row in annotation['grammar_links']}
    # First authenticate the original imported source independently of later
    # registry membership. make_handoff then reconciles exact published content.
    if saved is not None:replay_handoff(run_dir,saved,language='ko',chapter_text=chapter_text,required_ids=required,published={})
    current_chunks=contracts.slice_annotations(annotation,texts)
    packets=[];offset=0
    for index,(candidate,text) in enumerate(zip(current_chunks,texts)):
        position={'chunk_index':index,'source_text_digest':digest(text),'parent_text_digest':digest(chapter_text),'source_start':offset}
        explicit=[]
        for row in saved['sources'] if saved else []:
            target=row['expected_context']['target_occurrence']
            if target['source_start']==offset and target['source_text']==text:
                explicit.append(row)
        old_packet={'version':1,'language':'ko','representation':'korean-flat','position':position,'lessons':explicit} if explicit else None
        bound=resolve_run_lesson_context(run_dir,candidate=candidate,source_text=text,language='ko',representation='korean-flat',
            context={'annotation_source_position':position},envelope=old_packet)
        if bound['packet']:packets.append(bound['packet'])
        offset+=len(text)
    return make_handoff(run_dir,packets,language='ko',chapter_text=chapter_text,required_ids=required,published=published)
