"""Rehearse a source overlay in a NEW directory. No app import, DB or network."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(data):
    return hashlib.sha256(data).hexdigest()


def local(root, name):
    relative=Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe manifest path')
    result=(root/relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('Manifest escapes root')
    return result


def apply(baseline, target):
    package=Path(__file__).resolve().parents[2]
    here=Path(__file__).resolve().parent
    manifest=json.loads((here/'SHARED_INTEGRATION.json').read_text(encoding='utf-8'))
    delivery=json.loads((here/'DELIVERY_FILES.json').read_text(encoding='utf-8'))
    patch=here/'chat-shared-integration.patch'
    data=patch.read_bytes()
    assert sha(data)==manifest['patch_sha256'], 'Patch fingerprint mismatch'
    headers=re.findall(rb'^--- a/(.+)\n\+\+\+ b/(.+)\n',data,re.M)
    assert all(a==b for a,b in headers)
    assert {a.decode() for a,_ in headers}=={row['path'] for row in manifest['files']}
    baseline=baseline.resolve();target=target.resolve()
    assert not target.exists(), 'Destination must be new'
    assert not target.is_relative_to(baseline), 'Destination must be isolated from baseline'
    prepared=[]
    for row in manifest['files']:
        raw=local(baseline,row['path']).read_bytes()
        assert sha(raw)==row['base_sha256'], 'Baseline changed: '+row['path']
        prepared.append((row,raw.replace(b'\r\n',b'\n')))
    target.mkdir(parents=True,exist_ok=False)
    for row,raw in prepared:
        path=local(target,row['path']);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    command=['git','-c','core.autocrlf=false','apply']
    subprocess.run(command+['--check',str(patch)],cwd=target,check=True,capture_output=True)
    subprocess.run(command+['--whitespace=nowarn',str(patch)],cwd=target,check=True,capture_output=True)
    for row,_ in prepared:
        path=local(target,row['path']);raw=path.read_bytes()
        assert sha(raw)==row['integration_normalized_lf_sha256'], row['path']
        parts=raw.split(b'\n');exceptions=set(row['newline_exceptions'])
        assert all(type(i) is int and 0<=i<len(parts)-1 for i in exceptions)
        default=row['newline_default']=='crlf'
        rebuilt=b''.join(part+(b'\r\n' if default!=(i in exceptions) else b'\n') for i,part in enumerate(parts[:-1]))+parts[-1]
        assert sha(rebuilt)==row['integration_result_sha256'], row['path']
        path.write_bytes(rebuilt)
    for row in delivery['direct']:
        raw=local(package,row['path']).read_bytes()
        assert sha(raw.replace(b'\r\n',b'\n'))==row['normalized_lf_sha256'],row['path']
        path=local(target,row['path']);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print(json.dumps({'shared_byte_exact':len(prepared),'direct_normalized':len(delivery['direct']),'app_started':False,'database_writes':0}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('new_target',type=Path)
    args=parser.parse_args()
    apply(args.baseline,args.new_target)
