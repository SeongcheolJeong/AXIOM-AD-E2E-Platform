"""Optional registered SocketCAN gateway runtime boundary; no hardware fallback."""
import copy,hashlib,json,subprocess,sys
from pathlib import Path

# Matches the registered remote gateway and its host process transport.
MAX_MESSAGE = 8 * 1024 * 1024

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def validate_runtime(value):
    if (value.get('schema_version')!='hil-gateway-runtime/1' or value.get('kind')!='hil_gateway'
        or value.get('protocol')!='hil-dut/1' or value.get('dut_version')!='hil-gateway/1'
        or value.get('runtime_sha256')!=hashlib.sha256(canonical({k:v for k,v in value.items() if k!='runtime_sha256'})).hexdigest()):
        raise ValueError('Gateway runtime identity mismatch')
    return value

def command(runtime,root):
    validate_runtime(runtime);root=Path(root)
    path=root/'bridge-install.json'
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=runtime['installation_sha256']:raise ValueError('Gateway installation unavailable/changed')
    install=json.loads(path.read_bytes())
    for name,sha in install['files'].items():
        p=root/name
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('Gateway executable identity changed')
    return [sys.executable,str(root/'bridge_remote.py'),'launch',runtime['device']['target']]

class Audit:
    def __init__(self,init):self.runtime=init['gateway_runtime']
    def check(self,request,reply):
        value=reply.get('bridge',{});frame=value.get('brake_feedback',{});control=reply.get('control',{})
        if (reply.get('sequence')!=request['sequence'] or value.get('schema_version')!='hil-gateway-observation/1'
            or value.get('target')!=self.runtime['device']['target'] or frame.get('control')!=control
            or not frame.get('data_hex') or set(control)!={'throttle','brake','steer'}):
            raise ValueError('Gateway control lacks received Brake output evidence')
        # The built-in codec can be checked without executing any ECU policy.
        if self.runtime['device']['codec_id']=='axiom-example/1':
            import struct
            t,b,s,_,_=struct.unpack('<HHhBB',bytes.fromhex(frame['data_hex']))
            if control!={'throttle':t/10000,'brake':b/10000,'steer':s/32767}:raise ValueError('Brake bytes differ from selected control')
        if not value.get('diagnostic_observed') and reply.get('state')!='unobserved':raise ValueError('Unobserved ECU state was fabricated')
