"""Read-only access to the immutable exported evidence (standard library only)."""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile
import hashlib, json

ROOT = Path(__file__).resolve().parents[1]
def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p, value):
    p=Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
def sha(data): return hashlib.sha256(data).hexdigest()
def batches(): return read(ROOT/'data/manifest.json')['batches']
def batch(name): return next(b for b in batches() if b['id']==name)
def archive(b): return ZipFile(ROOT/b['archive'])
def rows(b):
    with archive(b) as z:
        return [json.loads(z.read(n)) for n in sorted(z.namelist()) if n.startswith('runs/') and n.endswith('/result.json')]
def verify(b):
    p=ROOT/b['archive']
    if sha(p.read_bytes())!=b['archive_sha256']: raise ValueError('archive_hash_mismatch: '+b['id'])
    expected=read(ROOT/'data/batches'/b['id']/'archive_members.sha256.json')
    with archive(b) as z:
        if set(z.namelist())!=set(expected): raise ValueError('archive_member_set_mismatch')
        for n,h in expected.items():
            if sha(z.read(n))!=h: raise ValueError('archive_member_hash_mismatch: '+n)
        f=json.loads(z.read('freeze.json'))
        canonical=json.dumps({k:v for k,v in f.items() if k!='freeze_id'},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        if sha(canonical)!=f['freeze_id']: raise ValueError('freeze_id_mismatch')
        for n,h in f['files'].items():
            if sha(z.read('source_snapshot/'+n.replace('\\','/')))!=h: raise ValueError('frozen_source_hash_mismatch')
        for n,h in f['artifacts'].items():
            if sha(z.read(n))!=h: raise ValueError('frozen_artifact_hash_mismatch')
        metadata=ROOT/'data/batches'/b['id']
        for name in ('freeze.json','tasks.json','prompts.json','schedule.json','summary.json','results.csv'):
            if (metadata/name).exists() and (metadata/name).read_bytes()!=z.read(name): raise ValueError('metadata_copy_mismatch')
    return {'batch':b['id'],'status':'passed','files':len(expected),'runs':len(rows(b)),'source_and_protocol_bytes_unchanged':True}
def unpack(b, target):
    verify(b); target=Path(target).resolve()
    if target.exists(): raise ValueError('output_already_exists')
    target.mkdir(parents=True)
    with archive(b) as z:
        for n in z.namelist():
            path=PurePosixPath(n)
            if path.is_absolute() or '..' in path.parts or ':' in n: raise ValueError('unsafe_archive_path')
            p=target.joinpath(*path.parts); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(z.read(n))
    return target
