"""Pinned, board-independent ECP5 implementation of the bundled Open ISP design.

This is an internal-clock OOC experiment. It does not emit a board bitstream.
Vendor project templates are explicitly incomplete until an exact part is supplied.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

PROFILE_ID = 'ecp5-85k-cabga381-6-ooc/1'
TOOL_RELEASE = '2026-10-16'
ARCHIVE_SHA256 = 'ac8765e94c5122c3e3b48b18f55cea306d08aa37ffc12cd9a938f3c485322326'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def build_profiles():
    profiles = [
        dict(id=PROFILE_ID, revision=1, name='ECP5-85F · OOC 100 MHz',
             family='ECP5', device='LFE5U-85F', package='CABGA381', speed=6,
             target_clock_hz=100_000_000, mode='out_of_context', executable=True,
             scope='Internal clock only; evaluation package/speed, no board pins or bitstream'),
        dict(id='gw2a-gowin-template/1', revision=1, name='GW2A · Gowin template',
             family='GW2A', device=None, executable=False, mode='export_template',
             reason='Exact device/package, Standard license and verified gw_sh worker required'),
        dict(id='cyclonev-quartus-template/1', revision=1, name='Cyclone V · Quartus template',
             family='Cyclone V', device=None, executable=False, mode='export_template',
             reason='Exact device/package and verified Windows/Linux Quartus worker required'),
    ]
    return [p | {'sha256': _hash(p)} for p in profiles]


def _profile(profile_id):
    for profile in build_profiles():
        if profile['id'] == profile_id:
            return profile
    raise ValueError('Unknown versioned FPGA build profile')


def _root():
    default = Path.home()/'.cache/axiom/tools/oss-cad-suite-20261016/oss-cad-suite'
    return Path(os.environ.get('AXIOM_OSS_CAD_SUITE', str(default))).expanduser().resolve()


def _env():
    # Tool-local libraries are located by the official executable wrappers.
    return {'PATH': str(_root()/'bin') + ':' + os.defpath, 'LC_ALL': 'C',
            'HOME': str(Path.home())}


def readiness(profile_id=PROFILE_ID):
    profile = _profile(profile_id)
    result = dict(available=False, profile=profile, reason=profile.get('reason', ''), tools={})
    if not profile['executable']:
        return result
    for name, flag in [('yosys', '-V'), ('nextpnr-ecp5', '--version')]:
        path = _root()/'bin'/name
        try:
            output = subprocess.check_output([str(path), flag], env=_env(), text=True,
                                             stderr=subprocess.STDOUT, timeout=15)
            banner = next((line.strip() for line in output.splitlines()
                           if line.startswith('Yosys ') or 'Next Generation Place and Route' in line), None)
            if not banner:
                raise ValueError(f'{name} returned no recognizable version banner')
            result['tools'][name] = dict(path=str(path), version=banner)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result['reason'] = f'{name} is unavailable: {error}'
            return result
    result.update(available=True, reason='Tool versions verified; actual synthesis and OOC routing run separately')
    return result


def identity(profile_id=PROFILE_ID):
    ready = readiness(profile_id)
    if not ready['available']:
        raise ValueError(ready['reason'])
    package = Path(__file__).parent
    files = {str(p.relative_to(package)): _digest(p) for p in sorted(package.rglob('*'))
             if p.is_file() and p.suffix in {'.py', '.sv', '.v', '.cpp', '.json'} and '__pycache__' not in p.parts}
    # Executable paths can be wrapper scripts: include the actual binaries and
    # synthesis plugin/database, not just their launcher text/version banner.
    tool_files = {}
    for pattern in ('bin/yosys', 'bin/nextpnr-ecp5', 'libexec/yosys', 'libexec/nextpnr-ecp5',
                    'share/yosys/plugins/slang*', 'share/trellis/database/devices.json',
                    'share/nextpnr/ecp5/chipdb-85k.bin'):
        for path in _root().glob(pattern):
            if path.is_file():
                tool_files[str(path.relative_to(_root()))] = _digest(path)
    return dict(profile=ready['profile'], tools=ready['tools'], adapter_version='0.1.0',
                reference_distribution=TOOL_RELEASE, expected_archive_sha256=ARCHIVE_SHA256,
                configured_tool_root=str(_root()),
                source_sha256=files, tool_sha256=tool_files,
                host=dict(os=platform.system(), arch=platform.machine()),
                fidelity='fpga_ooc_implementation')


def write_vendor_templates(work_dir, sources):
    """Export reviewable scripts, never claim these unconfigured targets ran."""
    work_dir = Path(work_dir)
    names = [Path(s).name for s in sources]
    common = '# Generated Open ISP source template. NOT EXECUTED; no board constraints.\n'
    quartus = common + 'package require ::quartus::project\n'
    quartus += 'if {![info exists ::env(AXIOM_EXACT_FPGA_PART)]} {error "Set AXIOM_EXACT_FPGA_PART to your exact Cyclone V device"}\n'
    quartus += 'project_new open_isp -overwrite\nset_global_assignment -name FAMILY "Cyclone V"\n'
    quartus += 'set_global_assignment -name DEVICE $::env(AXIOM_EXACT_FPGA_PART)\nset_global_assignment -name TOP_LEVEL_ENTITY axiom_isp_top\n'
    quartus += ''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE {{{name}}}\n' for name in names)
    quartus += 'export_assignments\nproject_close\n'
    gowin = common + 'if {![info exists ::env(AXIOM_EXACT_FPGA_PART)]} {error "Set AXIOM_EXACT_FPGA_PART to your exact GW2A device"}\n'
    gowin += 'set_device $::env(AXIOM_EXACT_FPGA_PART)\n'
    gowin += ''.join(f'add_file -type verilog {{{name}}}\n' for name in names)
    gowin += 'set_option -top_module axiom_isp_top\n# Add verified board timing/pin constraints before run all.\n'
    (work_dir/'cyclonev-project-template.tcl').write_text(quartus)
    (work_dir/'gw2a-project-template.tcl').write_text(gowin)
    (work_dir/'vendor-template-status.json').write_text(json.dumps(dict(
        status='not_executed', profiles=build_profiles()[1:],
        required=['Exact device', 'Verified tool/license/worker', 'Board clock and pin constraints']), indent=2))


def _report_summary(report):
    clocks = report.get('fmax', {})
    entries = [v for v in clocks.values() if isinstance(v, dict) and 'achieved' in v and 'constraint' in v]
    fmax = min((float(v['achieved']) for v in entries), default=None)
    met = all(float(v['achieved']) >= float(v['constraint']) >= 100.0 for v in entries) if entries else None
    utilization = report.get('utilization', {})
    def used(*keys):
        return sum(int(utilization.get(key, {}).get('used', 0)) for key in keys)
    return dict(fmax_mhz=fmax, timing_met=met,
                timing_status='passed' if met else 'failed' if met is False else 'not_evaluated',
                clocks=clocks, resources=dict(lut=used('TRELLIS_COMB'), ff=used('TRELLIS_FF'),
                    bram=used('DP16KD'), dsp=used('MULT18X18D')))


def run(config, work_dir, check, tool_identity, seed=42):
    from axiom_rtl_lab.runner import run_process
    from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost
    from .rtl import generated_design
    profile = tool_identity['profile']
    if profile != _profile(PROFILE_ID):
        raise ValueError('Only the pinned ECP5 OOC evaluation profile is executable')
    work_dir = Path(work_dir); work_dir.mkdir(parents=True, exist_ok=True)
    check()
    design = generated_design(config, work_dir)
    write_vendor_templates(work_dir, design['sources'])
    summary = dict(operation='build', validation_domain='fpga_ooc', verdict='failed',
        design_hash=design['design_hash'], build_profile_id=PROFILE_ID,
        synthesis_status='not_evaluated', place_route_status='not_evaluated', timing_status='not_evaluated',
        target_clock_hz=100_000_000, fmax_mhz=None, timing_met=None, resources={},
        pixel_validation='not_evaluated', board_validation='not_evaluated', bitstream_generated=False,
        scope='Internal OOC clock only; no pins, PHY, DDR controller, board or Qualcomm validation',
        seed=seed)
    stage = Path(tempfile.mkdtemp(prefix='axiom-isp-ecp5-', dir='/tmp'))
    phase = 'synthesis'
    commands = []
    try:
        names = []
        for path in design['sources']:
            path = Path(path); names.append(path.name); shutil.copyfile(path, stage/path.name)
        # Sources are bundled and locally generated, never user supplied script text.
        script = f'read_slang --top {design["top"]} ' + ' '.join(names)
        script += f'; synth_ecp5 -top {design["top"]} -json netlist.json; check -assert; tee -o synthesis-stat.json stat -json'
        (stage/'synthesis.ys').write_text(script + '\n')
        args = [tool_identity['tools']['yosys']['path'], '-m', 'slang', '-s', 'synthesis.ys']
        commands.append(args)
        run_process(args, stage, stage/'synthesis.log', check, 300, phase, _env())
        summary['synthesis_status'] = 'passed'
        phase = 'place_route'
        # Finish/report a routed design even if timing misses. The independent
        # 100 MHz report check below still marks the build verdict failed.
        args = [tool_identity['tools']['nextpnr-ecp5']['path'], '--85k', '--package', 'CABGA381',
                '--speed', '6', '--out-of-context', '--freq', '100', '--seed', str(seed),
                '--timing-allow-fail',
                '--json', 'netlist.json', '--write', 'routed.json', '--report', 'timing-report.json']
        commands.append(args)
        run_process(args, stage, stage/'place-route.log', check, 600, phase, _env())
        summary['place_route_status'] = 'passed'
    except (AdapterCancelled, AdapterOwnershipLost):
        raise
    except RuntimeError as error:
        summary[phase + '_status'] = 'failed'
        summary['error'] = str(error)[-6000:]
    finally:
        for name in ('synthesis.ys', 'synthesis.log', 'synthesis-stat.json', 'netlist.json',
                     'place-route.log', 'routed.json', 'timing-report.json'):
            path = stage/name
            if path.is_file():
                shutil.copyfile(path, work_dir/name)
        shutil.rmtree(stage)
    if (work_dir/'timing-report.json').is_file():
        summary.update(_report_summary(json.loads((work_dir/'timing-report.json').read_text())))
    summary['verdict'] = 'passed' if all(summary[k] == 'passed' for k in
        ('synthesis_status', 'place_route_status', 'timing_status')) else 'failed'
    (work_dir/'build-manifest.json').write_text(json.dumps(dict(design=design['manifest'],
        tool_identity=tool_identity, profile=profile, commands=commands, seed=seed), indent=2))
    (work_dir/'summary.json').write_text(json.dumps(summary, indent=2))
    check()
    return dict(summary=summary, axis=dict(kind='cycles', unit='cycle'), signals=[], samples=[])
