"""Native tool capability and source identity, isolated from the host environment."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).parent
VENDOR = ROOT / 'vendor' / 'mini_isp'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def vendor_manifest():
    result = json.loads((VENDOR / 'manifest.json').read_text())
    for item in result['files']:
        if digest(VENDOR / item['path']) != item['sha256']:
            raise ValueError('Pinned ISP vendor source failed integrity check: ' + item['path'])
    return result


def clean_env():
    return {'PATH': os.defpath + ':/opt/homebrew/bin', 'LC_ALL': 'C', 'LANG': 'C'}


def readiness():
    v = shutil.which('verilator')
    c = shutil.which('clang++') or shutil.which('g++')
    result = dict(available=False, verilator_path=v, compiler_path=c,
                  verilator_version=None, compiler_version=None,
                  reason='Verilator and a C++ compiler are required')
    if not v or not c:
        return result
    try:
        vendor_manifest()
        for key, executable in [('verilator_version', v), ('compiler_version', c)]:
            result[key] = subprocess.check_output([executable, '--version'], text=True,
                timeout=5, stderr=subprocess.STDOUT, env=clean_env()).splitlines()[0]
        result.update(available=True, reason='Native tool versions and pinned RTL sources verified; compilation occurs during execution')
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result['reason'] = str(exc)
    return result


def identity():
    tools = readiness()
    if not tools['available']:
        raise ValueError(tools['reason'])
    return dict(adapter_version='0.1.0', tools=tools, vendor=vendor_manifest(),
        source_sha256={str(p.relative_to(ROOT)): digest(p) for p in sorted(ROOT.rglob('*'))
                       if p.is_file() and p.suffix in {'.py', '.cpp', '.sv', '.json', '.md', '.toml'}},
        host_os=platform.system(), host_arch=platform.machine(), two_state=True)
