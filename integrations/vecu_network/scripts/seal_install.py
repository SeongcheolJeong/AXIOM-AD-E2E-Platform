"""Operator-only seal for a prepared, isolated Linux installation.

Run after copying all native builds and runner sources. Never writes old services.
"""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args();root=a.root.resolve()
binaries={'participant':'net/build/participant','registry':'net/build/bin/sil-kit-registry','system_controller':'net/build/bin/sil-kit-system-controller',
          'bsw':'bsw/build-2/axiom-autoas-bsw','os':'os/build-5/axiom-network-os','controller':'controller/build/reference-traffic-cpp'}
required=list(binaries.values())+['network_runtime.py','network_wire.py','network_remote.py','hil_network.py','hil_dut.py','city_control.py']
required += [str(p.relative_to(root)) for p in sorted((root/'net/build/lib').glob('*')) if p.is_file() and not p.is_symlink()]
required += ['bsw/build-2/manifest.json','os/build-5/build-manifest.json','controller/build/manifest.json','net/build/manifest.json']
files={}
for name in required:
    path=root/name
    if path.is_symlink() or not path.is_file():raise ValueError('Missing regular installed file '+name)
    files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
manifest={'schema_version':'vecu-network-install/1','profile_id':'actual_silkit_autoas_trampoline','binaries':binaries,'files':files,
          'engines':{'silkit':'5.0.8','autoas':'cc3709675ac30f7539c0710dfec6d579a5235841','trampoline':'ff287023252bf73273067f00d2dbc8bbc9a20401'},
          'fidelity':'functional virtual frames; host BSW; OSEK Task/Alarm/Event; externally clocked 1ms; independent processes',
          'excluded':['physical CAN arbitration/PHY','MCU WCET','complete AUTOSAR','OEM production ECU','Silver compatibility']}
raw=json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
(root/'network-install.json').write_bytes(raw);print(hashlib.sha256(raw).hexdigest())
