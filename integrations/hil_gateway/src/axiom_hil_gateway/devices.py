"""Operator-owned device driver registry. Public requests never choose Python modules."""
import hashlib,importlib,json,socket
from pathlib import Path

def load(root,target):
    path=Path(root)/'bridge-devices.json'
    data=json.loads(path.read_bytes());value=data.get(target)
    if value is None:raise ValueError('Physical ADAS/Brake device profile is not configured' if target=='hardware' else 'vcan bench is not configured')
    if value['target']!=target or value['can_a']==value['can_b']:raise ValueError('Two isolated CAN interfaces required')
    if value.get('driver_file') or value['codec_id']!='axiom-example/1':
        module=Path(root)/value['driver_file']
        if module.parent!=Path(root) or module.is_symlink() or hashlib.sha256(module.read_bytes()).hexdigest()!=value['driver_sha256']:
            raise ValueError('Operator device driver identity failed')
    return value

def initialize_devices(config,session,initialization,telemetry):
    if config['codec_id']!='axiom-example/1':
        module=importlib.import_module(Path(config['driver_file']).stem)
        return module.initialize(config,session,initialization,telemetry)
    for role,endpoint in config['endpoints'].items():
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.sendto(json.dumps(dict(op='initialize',schema_version='hil-example-device/1',session=session,
                role=role,initialization=initialization,telemetry=telemetry)).encode(),tuple(endpoint['control']))
            raw,peer=s.recvfrom(65535);reply=json.loads(raw)
            expected=config['identities'][role]
            if (peer!=tuple(endpoint['control']) or reply.get('session')!=session or reply.get('identity')!=expected
                or reply.get('status')!='ready'):raise ValueError('Physical ECU initialization/identity not confirmed')

def probe_devices(config):
    for key in ('can_a','can_b'):
        interface=config[key];socket.if_nametoindex(interface)
        virtual='/virtual/net/' in str((Path('/sys/class/net')/interface).resolve())
        if config['target']=='hardware' and virtual:raise ValueError('Virtual interface cannot establish physical HIL readiness')
    if config['target']=='hardware':
        if not config.get('identities') or set(config['identities'])!={'adas','brake'}:raise ValueError('Both physical identities are required')
        # A registered driver must perform a read-only device probe before any initialization.
        if not config.get('driver_file'):raise ValueError('Physical ECU probe driver has not been registered')
        module=importlib.import_module(Path(config['driver_file']).stem)
        observed=module.probe(config)
        if observed.get('identities')!=config['identities'] or not observed.get('available'):raise ValueError('Physical device identity probe failed')
    return dict(available=True,device=config,physical_hardware_connected=config['target']=='hardware',hard_realtime=False)
