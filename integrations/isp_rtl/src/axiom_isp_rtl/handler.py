"""Immutable pixel runs and independent FPGA OOC builds on the shared worker."""
import hashlib
import mimetypes
from pathlib import Path
import tempfile

from fastapi import HTTPException
from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost
from ade2e.engineering_lab import hydrate_test, resolve_test, resolve_isp_asset, resolve_adas_assets, source_identity
from ade2e.experiment_runtime import ExperimentHandlerMetadata, ExperimentExecution, ExperimentOutputFile
from ade2e.service import canonical_bytes

SIM_FIDELITY = 'open_isp_rtl_pixel_reference'
BUILD_FIDELITY = 'fpga_ooc_implementation'
BUILD_ADAPTER = 'open-isp-ecp5-build'
ADAS_FIDELITY = 'adas_module_rtl_reference'


def _simulation_module(config):
    if config['engine'] == 'adas_validation_campaign':
        from . import campaign
        return campaign
    if config['engine'] == 'adas_preboard_checks':
        from . import preboard
        return preboard
    if config['engine'] == 'adas_module_rtl':
        from . import adas
        return adas
    import axiom_isp_rtl
    return axiom_isp_rtl


def _build_module(config):
    if config['engine'] == 'adas_module_rtl':
        from . import adas_build
        return adas_build
    from . import build
    return build


def _design_hash(config):
    if config['engine'] == 'adas_preboard_checks':
        from .adas_design import generated_design
        config = config['core_config']
    else:
        generated_design = _simulation_module(config).generated_design
    with tempfile.TemporaryDirectory(prefix='axiom-isp-design-') as directory:
        return generated_design(config, Path(directory))['design_hash']


def _test(database, spec, build=False):
    keys = {'test_ref', 'build_profile_id'} if build else {'test_ref'}
    if set(spec.input) != keys or spec.comparison_kind != 'single':
        raise ValueError('Open ISP requires one immutable test reference and a build profile only for builds')
    if any(v is not None for v in (spec.model, spec.sensor, spec.evaluator)):
        raise ValueError('Open ISP accepts no external models, executable paths or commands')
    with database.read_session() as session:
        test = resolve_test(session, spec.project_id, spec.input['test_ref'])
        if test['config']['engine'] not in {'isp_rtl', 'adas_module_rtl', 'adas_validation_campaign', 'adas_preboard_checks'}:
            raise ValueError('Open ISP requires a bundled ISP or ADAS module RTL configuration')
        if build and test['config']['engine'] not in {'isp_rtl','adas_module_rtl'}:
            raise ValueError('Build requires the actual bundled pixel core configuration')
        asset = None if build else resolve_isp_asset(session, test)
        assets = {} if build else resolve_adas_assets(session, test)
    if test['seed'] != spec.seed:
        raise ValueError('Seed must match the saved ISP revision')
    snapshot = hydrate_test(test)
    snapshot.update(raw_asset=asset, design_hash=_design_hash(test['config']))
    if test['config']['engine'] == 'adas_module_rtl':
        snapshot['raw_assets'] = assets
    return snapshot


def _verify_raw(database, asset, check=lambda: None):
    from ade2e.apollo_lab import asset_path
    path = asset_path(database, asset)
    if not path.is_file() or path.stat().st_size != asset['size_bytes']:
        raise ValueError('ISP RAW original is missing or its size changed')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            check()
            checksum.update(chunk)
    if checksum.hexdigest() != asset['content_sha256']:
        raise ValueError('ISP RAW original hash changed')
    return path


def prepare_simulation(database, spec):
    snapshot = _test(database, spec)
    config = snapshot['test']['config']
    if config['engine']=='adas_validation_campaign' and config['include_preboard']:
        from .preboard import readiness
        if not readiness()['available']:
            raise ValueError('Requested pre-board native tools are unavailable; no substitute execution')
    snapshot.update(tool_identity=_simulation_module(config).identity(), operation='simulate',
                    execution_fidelity='adas_preboard_reference' if config['engine'] in {'adas_validation_campaign', 'adas_preboard_checks'} else ADAS_FIDELITY if config['engine'] == 'adas_module_rtl' else SIM_FIDELITY)
    if snapshot['raw_asset']:
        _verify_raw(database, snapshot['raw_asset'])
    for asset in snapshot.get('raw_assets', {}).values():
        _verify_raw(database, asset)
    return snapshot


def _write_input(database, snapshot, work_dir, check=lambda: None):
    """Reuse the tested Apollo RAW10/12 decoder; no vendor pixel algorithm is reused."""
    import numpy as np
    from axiom_apollo_lab.pixels import decode
    asset = snapshot['raw_asset']
    data = np.memmap(_verify_raw(database, asset, check), dtype=np.uint8, mode='r')
    config = snapshot['test']['config']
    path = work_dir / 'input.raw'
    with path.open('wb') as stream:
        for index in range(config['raw_frame_index'], config['raw_frame_index'] + config['frames']):
            check()
            stream.write(decode(data, asset['metadata'], index).astype('<u2').tobytes())
    del data
    return str(path.resolve())


def _write_adas_inputs(database, snapshot, work_dir, check):
    """Decode to private worker inputs; four originals remain immutable asset refs."""
    import numpy as np
    from axiom_apollo_lab.pixels import decode
    config = snapshot['test']['config']
    folder = work_dir / '.inputs'
    folder.mkdir(exist_ok=True)
    paths = {}
    for channel in config['channels']:
        key = str(channel['channel'])
        asset = snapshot.get('raw_assets', {}).get(key)
        if asset is None:
            continue
        data = np.memmap(_verify_raw(database, asset, check), dtype=np.uint8, mode='r')
        path = folder / ('channel-' + key + '.raw')
        with path.open('wb') as stream:
            for index in range(channel['raw_frame_index'], channel['raw_frame_index'] + (1 if channel.get('raw_repeat') else config['frames'])):
                check()
                stream.write(decode(data, asset['metadata'], index).astype('<u2').tobytes())
        del data
        paths[key] = str(path.resolve())
    return paths


def _publish(snapshot, work_dir, result, operation):
    test = snapshot['test']
    preboard = test['config']['engine'] in {'adas_validation_campaign', 'adas_preboard_checks'}
    adas = test['config']['engine'] == 'adas_module_rtl'
    fidelity = ('adas_preboard_reference' if preboard else ADAS_FIDELITY if adas else SIM_FIDELITY) if operation == 'simulate' else BUILD_FIDELITY
    provenance = dict(adapter_id='native-engineering-lab' if operation == 'simulate' else BUILD_ADAPTER,
        adapter_version='0.1.0', operation=operation, fidelity=fidelity,
        test_ref={k:test[k] for k in ('id','revision','sha256')}, seed=test['seed'],
        design_hash=snapshot['design_hash'], **source_identity(test['config']),
        tool_identity=snapshot['tool_identity'], raw_asset=snapshot.get('raw_asset'))
    if adas:
        provenance['raw_assets'] = snapshot.get('raw_assets', {})
        provenance['artifact_policy'] = dict(max_files=512 if operation == 'simulate' else 256,
            preview_scope='Bounded selected frame previews; every accepted RGB pixel is checked independently',
            max_trace_file_bytes=128*1024*1024)
    if operation == 'build':
        provenance['build_profile_id'] = snapshot['build_profile_id']
    summary = result['summary'] | dict(operation=operation,
        validation_domain=('adas_preboard' if preboard else 'adas_module' if adas else 'pixels') if operation == 'simulate' else 'fpga_ooc',
        fidelity=fidelity, design_hash=snapshot['design_hash'], test_ref=provenance['test_ref'],
        config_hash=provenance['config_hash'], engine=test['config']['engine'], adapter_version='0.1.0')
    if operation == 'build':
        summary.update(build_profile_id=snapshot['build_profile_id'], pixel_validation='not_evaluated')
    trace = dict(schema_version='engineering-trace/1', engine=('adas-native-campaign' if preboard else 'verilator-adas' if adas else 'verilator-isp') if operation == 'simulate' else 'fpga-build',
        axis=result.get('axis',dict(unit='cycles',label='Build stages')), signals=result.get('signals',[]),
        samples=result.get('samples',[]), summary=summary, provenance=provenance, test=test)
    for name, value in [('trace.json',trace),('summary.json',summary),('provenance.json',provenance),('test.json',test)]:
        (work_dir/name).write_bytes(canonical_bytes(value))
    files = []
    for path in sorted(work_dir.rglob('*')):
        if path.is_file() and not path.is_symlink():
            name = path.relative_to(work_dir).as_posix()
            if any(part.startswith('.') for part in path.relative_to(work_dir).parts):
                continue
            mime = mimetypes.guess_type(name)[0] or ('text/plain' if path.suffix in {'.sv','.v','.log','.tcl','.qsf','.ys','.vcd'} else 'application/octet-stream')
            files.append(ExperimentOutputFile(path,name,mime))
    return ExperimentExecution(summary=summary, artifacts=files, fidelity=fidelity)


def _execute(database, snapshot, work_dir, should_cancel, operation):
    def check():
        if should_cancel():
            raise AdapterCancelled('Open ISP acknowledged cancellation')
    check()
    test = hydrate_test(snapshot['test'])['test']
    config = dict(test['config'])
    try:
        if operation == 'build':
            build = _build_module(config)
            current = build.identity(snapshot['build_profile_id'])
        else:
            simulation = _simulation_module(config)
            current = simulation.identity()
        if current != snapshot['tool_identity'] or _design_hash(config) != snapshot['design_hash']:
            raise ValueError('ISP tool/source/design identity changed after preparation; create a new execution')
        work_dir.mkdir(parents=True, exist_ok=True)
        if operation == 'build':
            result = build.run(config, work_dir, check, current, test['seed'])
        else:
            if snapshot.get('raw_asset'):
                config['_input_raw_path'] = _write_input(database, snapshot, work_dir, check)
            if config['engine'] == 'adas_module_rtl':
                config['_input_raw_paths'] = _write_adas_inputs(database, snapshot, work_dir, check)
            result = simulation.run(config, work_dir, check, current, test['seed'])
        if result['summary'].get('design_hash') != snapshot['design_hash']:
            raise ValueError('Executed RTL design hash differs from the prepared design')
        check()
    except (AdapterCancelled, AdapterOwnershipLost):
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        detail = ''.join(c for c in str(exc) if c in '\n\t' or ord(c)>=32)[-6000:]
        raise HTTPException(422, 'Open ISP '+operation+' failed: '+detail) from exc
    return _publish(snapshot,work_dir,result,operation)


def execute_simulation(database, snapshot, work_dir, should_cancel):
    return _execute(database,snapshot,work_dir,should_cancel,'simulate')


class BuildHandler:
    def __init__(self, database=None):
        self.database = database

    def metadata(self):
        return ExperimentHandlerMetadata(id=BUILD_ADAPTER,version='0.1.0',kinds=['fpga_build'],status='available',
            fidelity=BUILD_FIDELITY,cancellation_supported=True,
            description='Pinned Open ISP ECP5 out-of-context synthesis and place/route; no pixel or board validation')

    def profiles(self):
        from . import build
        profiles = [profile | build.readiness(profile['id']) | {'engines':['isp_rtl']}
                    for profile in build.build_profiles()]
        try:
            from . import adas_build
        except ImportError:
            return profiles
        for profile in profiles:
            if any(p['id'] == profile['id'] for p in adas_build.build_profiles()):
                profile['engines'].append('adas_module_rtl')
        return profiles

    def prepare(self, spec):
        snapshot = _test(self.database,spec,build=True)
        build = _build_module(snapshot['test']['config'])
        profile = spec.input['build_profile_id']
        ready = build.readiness(profile)
        if not ready['available']:
            raise ValueError(ready['reason'])
        snapshot.update(tool_identity=build.identity(profile),build_profile_id=profile,
            operation='build',execution_fidelity=BUILD_FIDELITY)
        return snapshot

    def execute(self, snapshot, work_dir, should_cancel):
        return _execute(self.database,snapshot,work_dir,should_cancel,'build')
