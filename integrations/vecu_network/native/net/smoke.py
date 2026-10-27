"""Real SIL Kit smoke; run with SDK directory and participant executable."""
import json,select,socket,subprocess,sys,time
from pathlib import Path
sdk=Path(sys.argv[1]);binary=sys.argv[2]
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
uri=f'silkit://127.0.0.1:{port}'
children=[]
def spawn(args):
    p=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,text=True,bufsize=1);children.append(p);return p
def receive(p):
    if not select.select([p.stdout],[],[],15)[0]:raise TimeoutError('participant timeout')
    line=p.stdout.readline()
    if not line:raise RuntimeError('participant exited')
    return json.loads(line)
try:
    reg=spawn([str(sdk/'bin/sil-kit-registry'),'--listen-uri',uri,'--log','off'])
    for _ in range(100):
        try:
            with socket.create_connection(('127.0.0.1',port),.1):break
        except OSError:time.sleep(.03)
    roles=['perception','adas','brake','restbus']
    nodes={role:spawn([binary,role,uri]) for role in roles}
    print('created', [receive(p) for p in nodes.values()])
    coordinator=spawn([str(sdk/'bin/sil-kit-system-controller'),'--connect-uri',uri,'--log','off',*roles])
    for tick in range(3):
        for role,p in nodes.items():
            frames=[{'bus':'can','can_id':256,'data':[1,2,3,4,5,6,7,8]}] if role=='adas' and tick==0 else []
            p.stdin.write(json.dumps({'op':'tick','time_ms':tick,'frames':frames})+'\n');p.stdin.flush()
        print(tick,{role:receive(p) for role,p in nodes.items()})
    for p in nodes.values():p.stdin.write('{"op":"close"}\n');p.stdin.flush()
    for p in nodes.values():print(receive(p))
finally:
    for p in children:
        if p.poll() is None:p.terminate()
    for p in children:
        try:p.wait(timeout=3)
        except subprocess.TimeoutExpired:p.kill();p.wait()
