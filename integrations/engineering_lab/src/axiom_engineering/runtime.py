"""Native tool identity and explicit local-operator execution boundary."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def version(path, flag='--version'):
    if not path:
        return None
    try:
        output = subprocess.check_output([path, flag], text=True, stderr=subprocess.STDOUT, timeout=8)
        return next((line.strip() for line in output.splitlines() if line.strip() and '******' not in line), '')
    except (OSError, subprocess.SubprocessError):
        return None


def readiness():
    v, c, n = shutil.which('verilator'), shutil.which('clang++') or shutil.which('g++'), shutil.which('ngspice')
    vv, cv, nv = version(v), version(c), version(n, '-v')
    enabled = os.environ.get('ADE2E_ENABLE_LOCAL_RTL') == '1' and os.environ.get('ADE2E_TEAM_MODE', '0').lower() not in {'1','true','yes'}
    rtl = dict(available=bool(vv and cv and enabled), verilator_path=v, compiler_path=c,
               verilator_version=vv, compiler_version=cv, local_operator_enabled=enabled,
               reason='Local trusted RTL only; unsupported for shared/untrusted execution' if enabled else
               'Set ADE2E_ENABLE_LOCAL_RTL=1 in a local non-team worker to enable trusted RTL')
    spice = dict(available=bool(nv), ngspice_path=n, ngspice_version=nv,
                 reason='Self-contained RLC/source/diode transient model' if nv else 'ngspice executable is unavailable')
    qualcomm = dict(available=False, status='not_configured', runtime_path=None, runtime_version=None,
                    host_os=platform.system(), host_arch=platform.machine(), supported_target=None,
                    license_status='not_verified',
                    reason='No verified target-matched Qualcomm runtime or official execution interface is configured. Architecture PDFs and Software Center are not an ISP runtime.',
                    required=['Exact target and runtime package/version', 'Supported execution host',
                              'Activated package entitlement', 'Official CLI/API and RAW input/output format'])
    try:
        from axiom_isp_rtl import readiness as isp_readiness
        isp = isp_readiness()
    except (ImportError, OSError, ValueError) as exc:
        isp = dict(available=False, reason='Open ISP optional adapter unavailable: ' + str(exc))
    try:
        from axiom_isp_rtl.adas import readiness as adas_readiness
        adas = adas_readiness()
    except (ImportError, OSError, ValueError) as exc:
        adas = dict(available=False, reason='ADAS module RTL optional adapter unavailable: ' + str(exc))
    try:
        from axiom_isp_rtl.preboard import readiness as preboard_readiness
        preboard = preboard_readiness()
    except (ImportError, OSError, ValueError) as exc:
        preboard = dict(available=False, reason='Pre-board checks unavailable: '+str(exc))
    campaign = dict(adas)
    return dict(available=any(x['available'] for x in (rtl, spice, isp, adas)), worker_readiness='not_verified',
                engines=dict(rtl=rtl, spice=spice, qualcomm=qualcomm, isp_rtl=isp, adas_module_rtl=adas, adas_validation_campaign=campaign, adas_preboard_checks=preboard))


def identity(engine):
    from axiom_rtl_lab import runner
    ready = readiness()['engines'][engine]
    if not ready['available']:
        raise ValueError(ready['reason'])
    package = Path(__file__).parent
    return dict(engine=engine, tools=ready, python_version=platform.python_version(),
                adapter_version='0.1.0', source_sha256={p.name:digest(p) for p in sorted(package.glob('*.py'))},
                shared_runner_sha256=digest(runner.__file__),
                host=dict(os=platform.system(), arch=platform.machine()),
                execution_boundary='trusted_local_restricted_hdl' if engine == 'rtl' else 'restricted_self_contained_netlist')


def clean_env():
    """Do not inherit credentials, compiler injections or simulator initialization variables."""
    return {'PATH': os.defpath + ':/opt/homebrew/bin', 'LC_ALL':'C'}
