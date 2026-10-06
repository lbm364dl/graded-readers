"""Synchronous invocation-local exact-byte replay memo; no worker-stage cache."""
import contextvars,copy,functools,hashlib,io,json,locale,threading,os,builtins,stat
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch
from pipeline.worker_paths import checked_regular_file
ACTIVE=contextvars.ContextVar('receipt_replay_session',default=None)
ROOT=Path(__file__).resolve().parents[1]
# Proposal bootstrap sets canonical ROOT explicitly; production package uses same.
ORIGINAL_OPEN=Path.open
ORIGINAL_GLOB=Path.glob
ORIGINAL_RGLOB=Path.rglob
ORIGINAL_GET_CODE=SourceFileLoader.get_code
ORIGINAL_IO_OPEN=io.open
ORIGINAL_BUILTIN_OPEN=builtins.open
ORIGINAL_OS_OPEN=os.open
ORIGINAL_STAT=os.stat
ORIGINAL_LSTAT=os.lstat
ORIGINAL_LISTDIR=os.listdir
LAST_STATS={}
INVOCATION_LOCK=threading.RLock()
class ReplayChanged(ValueError):pass
def _stat_fingerprint(value):
 # Parent directory membership churn is not identity/path safety evidence.
 # Membership is tracked separately only when actually enumerated.
 identity=(value.st_mode,value.st_ino,value.st_dev,value.st_uid,value.st_gid)
 if stat.S_ISDIR(value.st_mode):return identity
 return identity+(value.st_nlink,value.st_size,value.st_mtime_ns,value.st_ctime_ns)

class Session:
 def __init__(self):self.snapshots={};self.nodes={};self.hits=0;self.misses=0;self.logical_reads=0;self.physical_reads=0;self.inventories={};self.stats_seen={};self.directory_sets={};self.operations={}
 def read(self,path):
  path=Path(path).absolute();checked_regular_file(path);self.logical_reads+=1
  if path not in self.snapshots:
   with ORIGINAL_IO_OPEN(path,'rb') as f:self.snapshots[path]=f.read()
   self.physical_reads+=1
  return self.snapshots[path]
 def glob(self,path,pattern,recursive=False):
  method=ORIGINAL_RGLOB if recursive else ORIGINAL_GLOB;key=(str(Path(path).absolute()),pattern,recursive)
  result=list(method(path,pattern));names=sorted(map(str,result))
  if key in self.inventories and self.inventories[key]!=names:raise ReplayChanged("Receipt directory inventory changed")
  self.inventories[key]=names;return iter(result)
 def stat(self,path,follow=True):
  if isinstance(path,int):raise ReplayChanged("Untracked descriptor stat")
  path=Path(path).absolute();key=(str(path),follow)
  try:
   value=ORIGINAL_STAT(path,follow_symlinks=follow);fingerprint=_stat_fingerprint(value)
  except FileNotFoundError:
   self.stats_seen.setdefault(key,None);raise
  previous=self.stats_seen.setdefault(key,fingerprint)
  if previous!=fingerprint:raise ReplayChanged("Dependency presence/stat changed during replay")
  return value
 def listdir(self,path):
  if isinstance(path,int):raise ReplayChanged("Untracked directory descriptor")
  key=str(Path(path).absolute());result=ORIGINAL_LISTDIR(path);names=sorted(result)
  if self.directory_sets.setdefault(key,names)!=names:raise ReplayChanged("Directory contents changed")
  return result
 def finish(self):
  for (path,follow),expected in self.stats_seen.items():
   try:
    v=ORIGINAL_STAT(path,follow_symlinks=follow);current=_stat_fingerprint(v)
   except FileNotFoundError:current=None
   if current!=expected:raise ReplayChanged("Negative/stat dependency changed during replay")
  for path,names in self.directory_sets.items():
   if sorted(ORIGINAL_LISTDIR(path))!=names:raise ReplayChanged("Directory contents changed during replay")
  for (path,pattern,recursive),expected in self.inventories.items():
   method=ORIGINAL_RGLOB if recursive else ORIGINAL_GLOB
   if sorted(map(str,method(Path(path),pattern)))!=expected:raise ReplayChanged("Receipt dependency set changed during replay")
  for path,raw in self.snapshots.items():
   checked_regular_file(path)
   with ORIGINAL_IO_OPEN(path,'rb') as f:current=f.read()
   self.physical_reads+=1
   if hashlib.sha256(current).digest()!=hashlib.sha256(raw).digest():raise ReplayChanged('Receipt/code/source dependency changed during replay')
 def stats(self):return {'logical_reads':self.logical_reads,'physical_reads':self.physical_reads,'files':len(self.snapshots),'memo_hits':self.hits,'verified_nodes':self.misses,'snapshot_bytes':sum(map(len,self.snapshots.values())),'stat_dependencies':len(self.stats_seen),'negative_dependencies':sum(v is None for v in self.stats_seen.values()),'directory_dependencies':len(self.directory_sets)}
def _open(path,mode='r',buffering=-1,encoding=None,errors=None,newline=None):
 session=ACTIVE.get()
 if session is None:return ORIGINAL_OPEN(path,mode,buffering,encoding,errors,newline)
 if mode not in ('r','rb','rt'):raise ReplayChanged('Replay invocation may not mutate files')
 raw=session.read(path)
 if 'b' in mode:return io.BytesIO(raw)
 return io.StringIO(raw.decode(locale.getencoding() if encoding=='locale' else (encoding or locale.getencoding()),errors or 'strict'),newline=newline)
def _glob(path,pattern):
 session=ACTIVE.get();return session.glob(path,pattern) if session else ORIGINAL_GLOB(path,pattern)
def _rglob(path,pattern):
 session=ACTIVE.get();return session.glob(path,pattern,True) if session else ORIGINAL_RGLOB(path,pattern)
def _io_open(file,mode='r',buffering=-1,encoding=None,errors=None,newline=None,closefd=True,opener=None):
 if ACTIVE.get() is None:return ORIGINAL_IO_OPEN(file,mode,buffering,encoding,errors,newline,closefd,opener)
 if isinstance(file,int) or opener is not None:raise ReplayChanged('Untracked descriptor/custom opener')
 return _open(Path(file),mode,buffering,encoding,errors,newline)
def _builtin_open(file,mode='r',buffering=-1,encoding=None,errors=None,newline=None,closefd=True,opener=None):
 if ACTIVE.get() is None:return ORIGINAL_BUILTIN_OPEN(file,mode,buffering,encoding,errors,newline,closefd,opener)
 return _io_open(file,mode,buffering,encoding,errors,newline,closefd,opener)
def _os_open(*args,**kwargs):
 if ACTIVE.get() is not None:raise ReplayChanged('Raw descriptor I/O not supported by pinned replay adapter')
 return ORIGINAL_OS_OPEN(*args,**kwargs)
def _stat(path,*,dir_fd=None,follow_symlinks=True):
 session=ACTIVE.get()
 if session is None:return ORIGINAL_STAT(path,dir_fd=dir_fd,follow_symlinks=follow_symlinks)
 if dir_fd is not None:raise ReplayChanged('Relative descriptor stat unsupported')
 return session.stat(path,follow_symlinks)
def _lstat(path,*,dir_fd=None):return _stat(path,dir_fd=dir_fd,follow_symlinks=False)
def _listdir(path='.'):
 session=ACTIVE.get();return session.listdir(path) if session else ORIGINAL_LISTDIR(path)
def _get_code(loader,fullname):
 session=ACTIVE.get()
 if session is None:return ORIGINAL_GET_CODE(loader,fullname)
 filename=loader.get_filename(fullname)
 return compile(session.read(filename),filename,'exec',dont_inherit=True)
def invocation(fn):
 @functools.wraps(fn)
 def wrapped(*args,**kwargs):
  if ACTIVE.get() is not None:return fn(*args,**kwargs)
  with INVOCATION_LOCK:
   session=Session();token=ACTIVE.set(session)
   try:
    # Freeze code/schema used by dynamic source verifiers, not just data reads.
    for p in sorted(session.glob(ROOT/'pipeline','*.py',True)):session.read(p)
    for p in sorted(session.glob(ROOT/'pipeline/schemas','*.json',True)):session.read(p)
    session.read(Path(fn.__code__.co_filename));session.read(Path(__file__))
    with patch.object(Path,'open',_open),patch.object(Path,'glob',_glob),patch.object(Path,'rglob',_rglob),patch.object(SourceFileLoader,'get_code',_get_code),patch.object(io,'open',_io_open),patch.object(builtins,'open',_builtin_open),patch.object(os,'open',_os_open),patch.object(os,'stat',_stat),patch.object(os,'lstat',_lstat),patch.object(os,'listdir',_listdir):result=fn(*args,**kwargs)
    session.finish();LAST_STATS.clear();LAST_STATS.update(session.stats());return result
   finally:ACTIVE.reset(token)
 return wrapped
def memoized_provenance(fn):
 @functools.wraps(fn)
 def wrapped(*args,**kwargs):
  session=ACTIVE.get()
  if session is None:return fn(*args,**kwargs)
  key=hashlib.sha256(json.dumps([fn.__module__,fn.__qualname__,args,kwargs],ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
  if key in session.nodes:session.hits+=1;return copy.deepcopy(session.nodes[key])
  # All reads during the first actual gate replay join the exact snapshot.
  session.read(Path(fn.__code__.co_filename))
  result=fn(*args,**kwargs);session.misses+=1;session.nodes[key]=copy.deepcopy(result);return result
 return wrapped
