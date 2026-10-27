"""Operator-pinned remote helper; executes only the registered gateway/container."""
import hashlib,json,os,subprocess,sys
from pathlib import Path

def manifest(root):
    value=json.loads((root/'bridge-install.json').read_bytes())
    for name,sha in value['files'].items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise ValueError('Gateway installation changed: '+name)
    return value

def main():
    root=Path(__file__).resolve().parent;op=sys.argv[1];target=sys.argv[2] if len(sys.argv)>2 else 'vcan';installation=manifest(root)
    from bridge_devices import load,probe_devices
    config=load(root,target)
    if op=='probe':
        if target=='vcan':
            # Container has a dedicated network namespace, not the host CAN namespace.
            command=['docker','exec',config['container'],'python3',str(root/'bridge_remote.py'),'probe-local','vcan']
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            if result.returncode:raise ValueError(result.stderr[-2000:])
            observed=json.loads(result.stdout)
        else:observed=probe_devices(config)
        observed.update(installation=installation,installation_sha256=hashlib.sha256((root/'bridge-install.json').read_bytes()).hexdigest())
        print(json.dumps(observed));return
    if op=='probe-local':print(json.dumps(probe_devices(config)));return
    if op!='launch':raise ValueError('Unsupported registered gateway operation')
    raw=bytearray()
    while True:
        ch=os.read(0,1)
        if not ch or len(raw)>8*1024*1024:raise ValueError('Gateway launch request incomplete/too large')
        if ch==b'\n':break
        raw+=ch
    request=json.loads(raw)
    if 'gateway_runtime' in request:runtime=request['gateway_runtime']
    else:
        runtime=request;raw=bytearray()
        while True:
            ch=os.read(0,1)
            if not ch or len(raw)>8*1024*1024:raise ValueError('Gateway init incomplete')
            if ch==b'\n':break
            raw+=ch
        request=json.loads(raw);request['gateway_runtime']=runtime;raw=json.dumps(request).encode()
    if runtime['installation_sha256']!=hashlib.sha256((root/'bridge-install.json').read_bytes()).hexdigest() or runtime['device']!=config:
        raise ValueError('Frozen gateway installation/device differs')
    command=['python3',str(root/'bridge_runtime.py')]
    if target=='vcan':command=['docker','exec','-i',config['container'],*command]
    child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,bufsize=0)
    try:
        # A separate pump retains actual child PID in init; outer CARLA does not pretend it is local.
        child.stdin.write(raw+b'\n');child.stdin.flush()
        import threading
        def pump():
            try:
                while True:
                    block=os.read(0,65536)
                    if not block:break
                    child.stdin.write(block);child.stdin.flush()
            except (OSError,BrokenPipeError):pass
            finally:
                try:child.stdin.close()
                except OSError:pass
        threading.Thread(target=pump,daemon=True).start()
        while True:
            block=os.read(child.stdout.fileno(),65536)
            if not block:break
            os.write(1,block)
        if child.wait()!=0:raise RuntimeError('Gateway process failed')
    finally:
        if child.poll() is None:child.terminate();child.wait(timeout=3)
if __name__=='__main__':main()
