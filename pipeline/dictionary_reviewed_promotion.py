"""Exact accepted-record promotion proposal. No worker or registry write API."""
import base64,copy,hashlib,json,os
from pathlib import Path
from pipeline.annotation_adjudication import digest
from pipeline import dictionary_research_revision as revision
from pipeline.worker_paths import checked_regular_file,checked_directory
from pipeline.receipt_replay_session import invocation
ROOT=Path('/home/catalin/graded-readers')
FIELD='authenticated_dictionary_promotion'

def _file(relative):
    p=Path(relative)
    if p.is_absolute() or '..' in p.parts:raise ValueError('Foreign promotion path')
    return checked_regular_file(ROOT/p)

def _sha(data):return hashlib.sha256(data).hexdigest()

@invocation
def build_promotion(run_dir,evidence_path,registry_path):
    """Return a staged registry and immutable receipt, without modifying files."""
    evidence_path=Path(evidence_path).resolve();run_dir=Path(run_dir).resolve()
    if not evidence_path.is_relative_to(ROOT) or not run_dir.is_relative_to(ROOT):raise ValueError('Foreign evidence root')
    evidence=json.loads(_file(str(evidence_path.relative_to(ROOT))).read_text())
    checked=revision.promotion_preflight(run_dir,evidence)
    registry_path=checked_regular_file(Path(registry_path).absolute())
    if registry_path.resolve()!=Path(revision.editorial.dictionary.GRAMMAR).resolve() or not registry_path.is_relative_to(ROOT):raise ValueError('Foreign target registry')
    before_bytes=registry_path.read_bytes();before=json.loads(before_bytes)
    _bind_before_snapshot(before_bytes,evidence)
    record=copy.deepcopy(checked['record']);entries=record['proposal']['entries'];ids={x['id'] for x in entries}
    actual={x['id']:x for x in before['entries'] if x['id'] in ids}
    if actual!={x['id']:x for x in record['before_entries']}:raise ValueError('Partial before mismatch')
    after=copy.deepcopy(before);byid={x['id']:x for x in entries}
    after['entries']=[copy.deepcopy(byid.get(x['id'],x)) for x in before['entries']]
    after.setdefault('grammar_revision_reviews',[]).append(record)
    receipt={'version':3,'binding_policy':'accepted-full-registry-snapshot-v3','provider':'exact-reviewed-dictionary-promotion','language':'ko',
             'registry_relpath':str(registry_path.relative_to(ROOT)),'run_relpath':str(run_dir.relative_to(ROOT)),'evidence_relpath':str(evidence_path.relative_to(ROOT)),
             'evidence_sha256':_sha(evidence_path.read_bytes()),'evidence_digest':evidence['digest'],
             'before_registry_sha256':_sha(before_bytes),'before_registry_bytes':base64.b64encode(before_bytes).decode(),'before_document':before,'after_document':after,
             'record_digest':digest(record),'coverage_digest':digest(record['coverage']),'annotation_approval':False}
    receipt['digest']=digest(receipt)
    return after,receipt

@invocation
def verify_history(record,descriptor,*,historical_staged=False):
    """Historical auth; never replaces current freshness checks at application."""
    if set(descriptor)!={'version','receipt_relpath','receipt_sha256'} or type(descriptor['version']) is not int or descriptor['version'] not in ((1,3) if historical_staged else (3,)):raise ValueError('Unknown promotion marker')
    path=_file(descriptor['receipt_relpath']);raw=path.read_bytes()
    if _sha(raw)!=descriptor['receipt_sha256']:raise ValueError('Promotion receipt changed')
    receipt=json.loads(raw)
    if type(receipt.get('version')) is not int or receipt['version']!=descriptor['version'] or receipt.get('provider')!='exact-reviewed-dictionary-promotion' or receipt.get('language')!='ko' or receipt.get('annotation_approval') is not False:raise ValueError('Foreign promotion receipt')
    if receipt['version']==3 and receipt.get('binding_policy')!='accepted-full-registry-snapshot-v3':raise ValueError('Missing full snapshot policy')
    if receipt['version']==1 and not historical_staged:raise ValueError('Historical staged receipt cannot authorize current promotion')
    if receipt['digest']!=digest({k:v for k,v in receipt.items() if k!='digest'}):raise ValueError('Promotion digest changed')
    evidence_path=_file(receipt['evidence_relpath'])
    if _sha(evidence_path.read_bytes())!=receipt['evidence_sha256']:raise ValueError('Accepted evidence changed')
    run=Path(receipt['run_relpath'])
    if run.is_absolute() or '..' in run.parts:raise ValueError('Foreign source run')
    registry=_file(receipt['registry_relpath'])
    if registry.resolve()!=Path(revision.editorial.dictionary.GRAMMAR).resolve():raise ValueError('Foreign registry receipt')
    evidence=json.loads(evidence_path.read_text());revision.replay(ROOT/run,evidence)
    clean=copy.deepcopy(record);clean.pop(FIELD,None)
    if evidence['digest']!=receipt['evidence_digest'] or evidence['editorial_record']!=clean or digest(clean)!=receipt['record_digest'] or digest(clean['coverage'])!=receipt['coverage_digest']:raise ValueError('Foreign accepted record')
    raw_before=base64.b64decode(receipt['before_registry_bytes'],validate=True)
    if _sha(raw_before)!=receipt['before_registry_sha256'] or json.loads(raw_before)!=receipt['before_document']:raise ValueError('Before snapshot changed')
    _bind_before_snapshot(raw_before,evidence)
    run=Path(receipt['run_relpath'])
    if run.is_absolute() or '..' in run.parts:raise ValueError('Foreign source run')
    before=receipt['before_document'];after=receipt['after_document'];expected=copy.deepcopy(before)
    replacements={x['id']:x for x in clean['proposal']['entries']}
    if {x['id']:x for x in before['entries'] if x['id'] in replacements}!={x['id']:x for x in clean['before_entries']}:raise ValueError('Before identity mismatch')
    expected['entries']=[copy.deepcopy(replacements.get(x['id'],x)) for x in before['entries']]
    expected.setdefault('grammar_revision_reviews',[]).append(clean)
    if expected!=after:raise ValueError('Unreviewed sibling or registry mutation')
    return True

def verify_affected_coverage(record, *, word_registry, grammar_registry):
    """Run actual asset gates on every source chapter; never write app assets."""
    from pipeline import korean_dictionary, korean_dictionary_revision
    korean_dictionary_revision._verify_coverage_fresh(record['coverage'])
    chapters=[];seen=set()
    for name in record['coverage']['files']:
        document=json.loads(Path(name).read_text())
        for chapter in document.get('chapters',[document]):
            key=(name,chapter.get('number',1))
            if key in seen:raise ValueError('Duplicate source chapter')
            seen.add(key)
            korean_dictionary.build_assets(chapter,Path('/unused-readonly-promotion'),word_registry=word_registry,grammar_registry=grammar_registry,write=False)
            chapters.append({'file':name,'chapter':chapter.get('number',1),'digest':digest(chapter)})
    if not set((x['file'],x['chapter']) for x in record['coverage']['chapter_digests']).issubset(seen):raise ValueError('Missing covered chapter')
    return {'checked_chapters':chapters,'occurrence_layers':len(record['coverage']['occurrences']),'source_positions_preserved':True,'assets_written':False,'annotation_approval':False}

def promote_exact_reviewed(run_dir,evidence_path,registry_path,*,word_registry,grammar_registry):
    """Explicit future write entrypoint; no regeneration, requires versioned adapter.

    Caller must install/audit the paired registry history adapter before invoking.
    """
    from pipeline import korean_dictionary
    run_dir=checked_directory(Path(run_dir).absolute());registry_path=checked_regular_file(Path(registry_path).absolute())
    after,receipt=build_promotion(run_dir,evidence_path,registry_path)
    original=registry_path.read_bytes()
    merged=dict(grammar_registry);merged.update({x['id']:x for x in after['grammar_revision_reviews'][-1]['proposal']['entries']})
    affected=verify_affected_coverage(after['grammar_revision_reviews'][-1],word_registry=word_registry,grammar_registry=merged)
    raw=(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode()
    receipt_path=run_dir/('dictionary-promotion-'+receipt['digest']+'.json')
    _immutable_artifact(receipt_path,raw)
    descriptor={'version':3,'receipt_relpath':str(receipt_path.relative_to(ROOT)),'receipt_sha256':_sha(raw)}
    after['grammar_revision_reviews'][-1][FIELD]=descriptor
    temp=run_dir/('dictionary-registry-proposal-'+receipt['digest']+'.json')
    _immutable_artifact(temp,(json.dumps(after,ensure_ascii=False,indent=2)+'\n').encode())
    korean_dictionary._registry(temp)
    # Revalidate current freshness immediately before replacement, not historical mode.
    evidence=json.loads(Path(evidence_path).read_text());revision.promotion_preflight(run_dir,evidence)
    if registry_path.read_bytes()!=original or _sha(original)!=receipt['before_registry_sha256']:raise ValueError('Registry changed before promotion')
    # Stage in destination directory so replacement is atomic on its filesystem.
    destination=registry_path.with_name('.'+registry_path.name+'.promotion-'+receipt['digest'])
    created=False;identity=None
    try:
        checked_directory(destination.parent)
        with destination.open('xb') as f:
            created=True;st=os.fstat(f.fileno());identity=(st.st_dev,st.st_ino)
            f.write(temp.read_bytes());f.flush();os.fsync(f.fileno())
        # No memo survives staging writes; fresh checks precede the actual replace.
        revision.promotion_preflight(run_dir,evidence)
        if registry_path.read_bytes()!=original:raise ValueError('Registry changed during staging')
        checked_regular_file(destination)
        if destination.read_bytes()!=temp.read_bytes():raise ValueError('Staging bytes changed')
        os.replace(destination,registry_path)
    finally:
        if created:
            try:
                st=destination.lstat()
                if (st.st_dev,st.st_ino)==identity:destination.unlink()
            except FileNotFoundError:pass
    return {'promoted':True,'receipt':descriptor,'affected_coverage':affected,'annotation_approval':False}


def _immutable_artifact(path,raw):
    """Never overwrite preexisting aliases or a different coordinator artifact."""
    path=Path(path).absolute();checked_directory(path.parent)
    try:
        with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if checked_regular_file(path).read_bytes()!=raw:raise ValueError('Managed artifact collision')
    return path


def _bind_before_snapshot(raw,evidence):
    handoff=evidence['handoff'];accepted=handoff['registry_before_text'].encode('utf-8')
    if _sha(accepted)!=handoff['registry_sha256'] or raw!=accepted or _sha(raw)!=handoff['registry_sha256']:
        raise ValueError('Before registry differs from authenticated accepted snapshot')
