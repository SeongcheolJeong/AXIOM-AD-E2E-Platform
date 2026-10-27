"""Registered native network worker operations. No public commands or paths."""
import hashlib,json,os,shlex,subprocess,sys
from pathlib import Path

def command(transport,operation):
    if operation not in ('probe','launch'):raise ValueError('Unsupported network operation')
    if not transport.target or not transport.python or not transport.directory:raise ValueError('Network Linux worker is not configured')
    return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',transport.target,
        shlex.join([transport.python,transport.directory+'/network_remote.py',operation])]

def call(transport,operation,timeout=30):
    p=subprocess.run(command(transport,operation),capture_output=True,timeout=timeout)
    if p.returncode:raise ValueError('Native network worker unavailable: '+p.stderr.decode(errors='replace')[-3000:])
    return json.loads(p.stdout)

def main():
    root=Path(__file__).resolve().parent
    from hil_network import validate_install
    path=root/'network-install.json';sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if len(sys.argv)!=2:raise ValueError('One registered operation required')
    if sys.argv[1]=='probe':
        manifest=validate_install({'manifest_sha256':sha},root)
        print(json.dumps({'available':True,'manifest_sha256':sha,'manifest':manifest},separators=(',',':')))
    elif sys.argv[1]=='launch':
        # Consume exactly one preamble without buffering any subsequent init bytes.
        raw=bytearray()
        while True:
            ch=os.read(0,1)
            if not ch or len(raw)>131072:raise ValueError('Invalid network launch preamble')
            if ch==b'\n':break
            raw+=ch
        runtime=json.loads(raw)
        from hil_network import validate_runtime
        validate_runtime(runtime);validate_install(runtime,root)
        # Network runner's init consumes the pinned runtime supplied by this helper.
        import network_runtime
        original=network_runtime.Network.__init__
        def initialize(self,init,root):return original(self,dict(init,network_runtime=runtime),root)
        network_runtime.Network.__init__=initialize
        # The runner also validates before construction, so supply runtime at line boundary.
        class Input:
            def __iter__(self):
                for line in sys.__stdin__:
                    value=json.loads(line)
                    if value.get('op')=='init':value['network_runtime']=runtime
                    yield json.dumps(value)
        sys.stdin=Input();network_runtime.main()
    else:raise ValueError('Unsupported network operation')
if __name__=='__main__':main()
