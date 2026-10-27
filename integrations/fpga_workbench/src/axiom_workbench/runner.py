"""Compile generated, project-authored topology with bounded native process execution."""
import fcntl
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import tempfile
import time
import simpy
from axiom_rtl_lab.runner import readiness,run_process,digest,SOURCES
from .generator import generate

FLAGS=['--cc','--exe','--build','--trace','--assert','--top-module','workbench_top','-j','2']
def identity():
    tools=readiness()
    if not tools['available']:raise ValueError(tools['detail'])
    folder=Path(__file__).parent
    return dict(tools=tools,source_sha256={n:digest(folder/n) for n in ['model.py','generator.py','runner.py','__init__.py']},
      reference_fifo_sha256=digest(SOURCES/'reference_fifo.sv'),native_runner_sha256=digest(SOURCES.parent/'runner.py'),
      oracle_sha256=digest(SOURCES.parent/'model.py'),python_version=platform.python_version(),simpy_version=simpy.__version__,compile_flags=FLAGS)

def compile_graph(depths,cache,tool_identity,check):
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    generated=generate(depths);hashes={k:hashlib.sha256(v.encode()).hexdigest() for k,v in generated.items()}
    key=hashlib.sha256(json.dumps(dict(identity=tool_identity,depths=depths,generated=hashes),sort_keys=True).encode()).hexdigest()
    target=cache/key
    with (cache/(key+'.lock')).open('a') as lock:
        start=time.monotonic()
        while True:
            check()
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()-start>125:raise RuntimeError('compile_lock_timeout')
                time.sleep(.1)
        binary=target/'obj'/'Vworkbench_top';receipt=target/'build.json'
        if receipt.is_file() and binary.is_file():
            m=json.loads(receipt.read_text())
            if m['binary_sha256']!=digest(binary):raise ValueError('Workbench cached binary integrity check failed')
            return binary,m,True,generated
        stage=Path(tempfile.mkdtemp(prefix='axiom-workbench-',dir='/tmp'));publish=None
        try:
            for name,text in generated.items():(stage/name).write_text(text)
            shutil.copyfile(SOURCES/'reference_fifo.sv',stage/'reference_fifo.sv')
            args=[tool_identity['tools']['verilator_path'],*FLAGS,'-MAKEFLAGS','CXX='+tool_identity['tools']['compiler_path'],'--Mdir','obj','reference_fifo.sv','generated_top.sv','harness.cpp']
            run_process(args,stage,stage/'compile.log',check,120,'compile')
            built=stage/'obj'/'Vworkbench_top'
            m=dict(cache_key=key,binary_sha256=digest(built),identity=tool_identity,depths=depths,command=args,generated_sha256=hashes)
            (stage/'build.json').write_text(json.dumps(m,indent=2))
            publish=Path(tempfile.mkdtemp(prefix=key+'-',dir=cache));(publish/'obj').mkdir()
            shutil.copy2(built,publish/'obj'/'Vworkbench_top')
            for name in ['compile.log','build.json']:shutil.copyfile(stage/name,publish/name)
            publish.rename(target);publish=None
            return target/'obj'/'Vworkbench_top',m,False,generated
        finally:
            shutil.rmtree(stage)
            if publish is not None:shutil.rmtree(publish)
