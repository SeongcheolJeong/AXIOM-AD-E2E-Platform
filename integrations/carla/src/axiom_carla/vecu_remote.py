"""Operator-installed native build/launch helper. No user-selected commands."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tarfile
try:
    from . import hil_policy
except ImportError:
    import hil_policy

ROOT=Path(__file__).resolve().parent

def build_dir(key):
    if not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('Invalid build identity')
    return ROOT/'vecu-builds'/key

def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['probe','build','check','launch','cancel'])
    parser.add_argument('--key');args=parser.parse_args()
    if args.operation=='probe':
        if platform.system()!='Linux' or platform.machine() not in ('x86_64','amd64'):raise ValueError('First compiled ECU target requires Linux x86_64')
        compiler=subprocess.run(['c++','--version'],capture_output=True,text=True,check=True,timeout=10).stdout
        print(json.dumps({'target_platform':{'system':platform.system(),'machine':platform.machine()},'compiler':compiler.splitlines()[0],
            'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}));return
    if args.operation=='cancel':
        path=build_dir(args.key)/'process.json'
        if path.exists():
            pid=json.loads(path.read_text())['pid']
            try:os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        print(json.dumps({'cancel_requested':True}));return
    raw=bytearray()
    # Do not read ahead across exec into the native program's init message.
    while len(raw)<=12*1024*1024:
        byte=os.read(0,1)
        if not byte:break
        raw.extend(byte)
        if byte==b'\n':break
    if len(raw)>12*1024*1024:raise ValueError('Native request too large')
    request=json.loads(raw)
    if args.operation in ('check','launch'):
        runtime=hil_policy.validate_runtime(request);path=hil_policy.executable(runtime,ROOT)
        if args.operation=='check':print(json.dumps({'binary_sha256':runtime['binary_sha256'],'available':True}));return
        # exec preserves PID. Pipe already positioned after single runtime line.
        os.execv(str(path),[str(path)])
    key=request['build_key'];directory=build_dir(key);directory.mkdir(parents=True,exist_ok=False)
    source=directory/'source';source.mkdir()
    bundle=base64.b64decode(request['bundle'],validate=True)
    if hashlib.sha256(bundle).hexdigest()!=request['bundle_sha256']:raise ValueError('Build source archive changed')
    with tarfile.open(fileobj=io.BytesIO(bundle),mode='r:gz') as tar:
        for item in tar.getmembers():
            if not item.isfile() or item.name.startswith('/') or '..' in Path(item.name).parts:raise ValueError('Invalid source archive entry')
            data=tar.extractfile(item).read()
            if hashlib.sha256(data).hexdigest()!=request['files'].get(item.name):raise ValueError('Build source file hash mismatch')
            path=source/item.name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    if {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}!=set(request['files']):raise ValueError('Incomplete source archive')
    policy={'cpp-immediate':'immediate','cpp-ramp':'ramp'}[request['source_id']]
    command=[sys.executable,str(source/'build.py'),'--policy',policy,'--output-dir',str(directory)]
    with (directory/'driver.stdout').open('wb') as out,(directory/'driver.stderr').open('wb') as err:
        child=subprocess.Popen(command,stdout=out,stderr=err,start_new_session=True)
        (directory/'process.json').write_text(json.dumps({'pid':child.pid}))
        try:code=child.wait(timeout=180)
        except BaseException:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait();raise
        finally:(directory/'process.json').unlink(missing_ok=True)
    log=(directory/'driver.stderr').read_text(errors='replace')
    if code:raise RuntimeError('Native compiler failed: '+log[-16000:])
    manifest=json.loads((directory/'driver.stdout').read_text())
    (directory/'process.json').unlink(missing_ok=True)
    print(json.dumps({'manifest':manifest,'log':log},allow_nan=False))

if __name__=='__main__':main()
