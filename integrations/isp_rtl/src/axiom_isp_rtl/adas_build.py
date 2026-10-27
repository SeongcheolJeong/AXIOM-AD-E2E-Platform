"""Separate ECP5 OOC implementation of the generated four-channel ISP DUT."""
import json
from pathlib import Path
import shutil
import tempfile

from . import build as common
from .adas_design import generated_design

PROFILE_ID = common.PROFILE_ID
IMPLEMENTATION_OPTIONS = {'placer_heap_timingweight': 30}
build_profiles = common.build_profiles
readiness = common.readiness


def identity(profile_id=PROFILE_ID):
    from axiom_rtl_lab import runner
    result = common.identity(profile_id)
    result['shared_runner_sha256'] = common._digest(runner.__file__)
    result['implementation_options'] = dict(IMPLEMENTATION_OPTIONS)
    return result


def write_vendor_templates(work_dir, sources):
    common.write_vendor_templates(work_dir, sources)
    for name in ('cyclonev-project-template.tcl', 'gw2a-project-template.tcl'):
        path = Path(work_dir)/name
        path.write_text(path.read_text().replace('axiom_isp_top', 'axiom_adas_top').replace('project_new open_isp', 'project_new adas_isp'))


def run(config, work_dir, check, tool_identity, seed=42):
    from axiom_rtl_lab.runner import run_process
    from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost
    profile = tool_identity['profile']
    if profile != common._profile(PROFILE_ID):
        raise ValueError('Only the pinned ECP5 OOC evaluation profile is executable')
    if tool_identity.get('implementation_options') != IMPLEMENTATION_OPTIONS:
        raise ValueError('ADAS implementation options changed after preparation')
    work_dir = Path(work_dir); work_dir.mkdir(parents=True, exist_ok=True)
    check()
    design = generated_design(config, work_dir)
    write_vendor_templates(work_dir, design['sources'])
    summary = dict(operation='build', validation_domain='fpga_ooc', verdict='failed',
        design_hash=design['design_hash'], pipeline_id=design['manifest']['pipeline_id'],
        build_profile_id=PROFILE_ID, synthesis_status='not_evaluated',
        place_route_status='not_evaluated', timing_status='not_evaluated',
        target_clock_hz=100_000_000, fmax_mhz=None, timing_met=None, resources={},
        pixel_validation='not_evaluated', board_validation='not_evaluated',
        bitstream_generated=False, seed=seed,
        scope='Four streaming ISP cores; internal OOC timing only; no pins, PHY, CDC, DDR, board or Qualcomm validation')
    stage = Path(tempfile.mkdtemp(prefix='axiom-adas-ecp5-', dir='/tmp'))
    phase = 'synthesis'; commands = []
    try:
        names = []
        for source in design['sources']:
            path = Path(source); names.append(path.name); shutil.copyfile(path, stage/path.name)
        script = f'read_slang --top {design["top"]} ' + ' '.join(names)
        script += f'; synth_ecp5 -top {design["top"]} -json netlist.json; check -assert; tee -o synthesis-stat.json stat -json'
        (stage/'synthesis.ys').write_text(script+'\n')
        args = [tool_identity['tools']['yosys']['path'], '-m', 'slang', '-s', 'synthesis.ys']
        commands.append(args)
        run_process(args, stage, stage/'synthesis.log', check, 600, phase, common._env())
        summary['synthesis_status'] = 'passed'
        phase = 'place_route'
        args = [tool_identity['tools']['nextpnr-ecp5']['path'], '--85k', '--package', 'CABGA381',
                '--speed', '6', '--out-of-context', '--freq', '100', '--seed', str(seed),
                '--placer-heap-timingweight', str(IMPLEMENTATION_OPTIONS['placer_heap_timingweight']),
                '--timing-allow-fail', '--json', 'netlist.json', '--write', 'routed.json',
                '--report', 'timing-report.json']
        commands.append(args)
        run_process(args, stage, stage/'place-route.log', check, 900, phase, common._env())
        summary['place_route_status'] = 'passed'
    except (AdapterCancelled, AdapterOwnershipLost):
        raise
    except RuntimeError as error:
        summary[phase+'_status'] = 'failed'; summary['error'] = str(error)[-6000:]
    finally:
        for name in ('synthesis.ys', 'synthesis.log', 'synthesis-stat.json', 'netlist.json',
                     'place-route.log', 'routed.json', 'timing-report.json'):
            path = stage/name
            if path.is_file(): shutil.copyfile(path, work_dir/name)
        shutil.rmtree(stage)
    if (work_dir/'timing-report.json').is_file():
        summary.update(common._report_summary(json.loads((work_dir/'timing-report.json').read_text())))
    summary['verdict'] = 'passed' if all(summary[k] == 'passed' for k in
        ('synthesis_status', 'place_route_status', 'timing_status')) else 'failed'
    (work_dir/'build-manifest.json').write_text(json.dumps(dict(design=design['manifest'],
        tool_identity=tool_identity, profile=profile, implementation_options=IMPLEMENTATION_OPTIONS,
        commands=commands, seed=seed), indent=2))
    (work_dir/'summary.json').write_text(json.dumps(summary, indent=2))
    check()
    return dict(summary=summary, axis=dict(kind='cycles', unit='cycle'), signals=[], samples=[])
