"""Bounded subprocess runner and content-addressed build cache for trusted bundled RTL only."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import platform
import simpy

SOURCES=Path(__file__).parent/'rtl'
FLAGS=['--cc','--exe','--build','--trace','--assert','--top-module','reference_fifo','-j','2']
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def readiness():
    v=shutil.which('verilator');c=shutil.which('clang++') or shutil.which('g++')
    result=dict(available=False,verilator_path=v,compiler_path=c,verilator_version=None,compiler_version=None,detail='Verilator and C++ compiler are required')
    if not v or not c:return result
    try:
        for key,path in [('verilator_version',v),('compiler_version',c)]:
            result[key]=subprocess.check_output([path,'--version'],text=True,timeout=5,stderr=subprocess.STDOUT).splitlines()[0]
        result.update(available=True,detail='Tool versions verified; compilation checked during execution')
    except (OSError,subprocess.SubprocessError) as exc:result['detail']=str(exc)
    return result

def identity():
    r=readiness()
    if not r['available']:raise ValueError(r['detail'])
    return dict(tools=r,source_sha256={n:digest(SOURCES/n) for n in ['reference_fifo.sv','harness.cpp']},
      reference_sha256={n:digest(SOURCES.parent/n) for n in ['model.py','runner.py','__init__.py']},
      python_version=platform.python_version(),simpy_version=simpy.__version__,compile_flags=FLAGS)

def run_process(args,cwd,log,check,timeout,phase,env=None):
    check()
    with Path(log).open('w') as out:
        process=subprocess.Popen(args,cwd=cwd,stdout=out,stderr=subprocess.STDOUT,start_new_session=True,env=env)
        started=time.monotonic()
        try:
            while process.poll() is None:
                check()
                if time.monotonic()-started>timeout:raise RuntimeError(f'{phase}_timeout: exceeded {timeout}s')
                time.sleep(.05)
        except BaseException:
            if process.poll() is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait()
            raise
    if process.returncode:
        tail=Path(log).read_text(errors='replace')[-5000:]
        raise RuntimeError(f'{phase}_failed: exit {process.returncode}\n{tail}')
    check()

def compile_fifo(depth,cache_dir,tool_identity,check):
    cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(json.dumps(dict(depth=depth,identity=tool_identity),sort_keys=True).encode()).hexdigest()
    target=cache/key
    with (cache/(key+'.lock')).open('a') as lock:
        start=time.monotonic()
        while True:
            check()
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()-start>125:raise RuntimeError('compile_lock_timeout')
                time.sleep(.1)
        binary=target/'obj'/'Vreference_fifo';receipt=target/'build.json'
        if receipt.is_file() and binary.is_file():
            manifest=json.loads(receipt.read_text())
            if manifest['binary_sha256']!=digest(binary):raise ValueError('Cached RTL binary failed integrity check')
            return binary,manifest,True
        # Verilator GNU Make rejects whitespace in its build directory.
        # Compile in our private /tmp directory; publish only owned native outputs to the cache.
        stage=Path(tempfile.mkdtemp(prefix='axiom-rtl-build-',dir='/tmp'))
        publish=None
        try:
            for name in tool_identity['source_sha256']:shutil.copyfile(SOURCES/name,stage/name)
            args=[tool_identity['tools']['verilator_path'],*FLAGS,f'-GDEPTH={depth}','-MAKEFLAGS','CXX='+tool_identity['tools']['compiler_path'],'--Mdir','obj','reference_fifo.sv','harness.cpp']
            env=os.environ.copy();env['CXX']=tool_identity['tools']['compiler_path']
            run_process(args,stage,stage/'compile.log',check,120,'compile',env)
            built=stage/'obj'/'Vreference_fifo'
            manifest=dict(cache_key=key,binary_sha256=digest(built),identity=tool_identity,depth=depth,command=args)
            (stage/'build.json').write_text(json.dumps(manifest,indent=2))
            publish=Path(tempfile.mkdtemp(prefix=key+'-',dir=cache))
            (publish/'obj').mkdir()
            shutil.copy2(built,publish/'obj'/'Vreference_fifo')
            for name in ['build.json','compile.log']:shutil.copyfile(stage/name,publish/name)
            publish.rename(target);publish=None
            return target/'obj'/'Vreference_fifo',manifest,False
        finally:
            shutil.rmtree(stage)
            if publish is not None:shutil.rmtree(publish)
