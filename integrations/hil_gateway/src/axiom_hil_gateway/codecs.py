"""Versioned codec lookup from operator-pinned registration, not public Python paths."""
import hashlib,importlib,struct
from pathlib import Path
try:from axiom_vecu_network import wire
except ImportError:import network_wire as wire

class ExampleCodec:
    id='axiom-example/1'
    can_ids={'command':256,'status':257,'vehicle_state':512,'heartbeat':513}
    object_payload=staticmethod(wire.object_payload)
    parse_objects=staticmethod(wire.parse_objects)
    decode_command=staticmethod(wire.decode_command)
    @staticmethod
    def vehicle_state(ego):return list(struct.pack('<H6x',max(0,min(65535,round(ego['speed_mps']*100)))))
    @staticmethod
    def heartbeat(tick):return list(struct.pack('<IB3x',tick,1))

def load_codec(config,root):
    if config['codec_id']=='axiom-example/1':return ExampleCodec()
    path=Path(root)/config['driver_file']
    if (path.parent!=Path(root) or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=config['driver_sha256']):
        raise ValueError('Operator codec source hash mismatch')
    codec=importlib.import_module(path.stem).codec
    if codec.id!=config['codec_id'] or set(codec.can_ids)!=set(ExampleCodec.can_ids) or len(set(codec.can_ids.values()))!=4:
        raise ValueError('Codec identity or CAN ID mapping invalid')
    if any(type(i) is not int or not 0<=i<=0x7ff for i in codec.can_ids.values()):raise ValueError('Unsupported codec CAN ID')
    if any(not callable(getattr(codec,k,None)) for k in ('object_payload','parse_objects','decode_command','vehicle_state','heartbeat')):
        raise ValueError('Codec contract incomplete')
    return codec
