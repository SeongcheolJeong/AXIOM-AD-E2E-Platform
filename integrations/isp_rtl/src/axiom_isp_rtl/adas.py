"""Four-camera native RTL execution with bounded evidence and a pixel oracle.

Only the simulator executes the DUT. The C++ harness checks every accepted pixel
against independent spatial MHC equations, never against a second RTL instance.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import time

from PIL import Image

from .runtime import ROOT, clean_env, digest, identity as base_identity, readiness


def identity():
    from axiom_rtl_lab import runner
    result = base_identity()
    result['shared_runner_sha256'] = digest(runner.__file__)
    paths = [Path(result['tools'][key]).resolve() for key in ('verilator_path', 'compiler_path')]
    paths.append(paths[0].with_name('verilator_bin'))
    result['tool_sha256'] = {str(path): digest(path) for path in paths if path.is_file()}
    return result


def generated_design(config, outdir):
    from .adas_design import generated_design as generate
    return generate(config, outdir)


def validate(config):
    from ade2e.adas_module_schemas import AdasModuleConfig
    return AdasModuleConfig.model_validate({k: v for k, v in config.items() if not k.startswith('_')}).model_dump()


def preview_indices(config):
    """Bound previews independently from pixel checking and frame metadata."""
    if config.get('preview_frames') is not None:
        return sorted(config['preview_frames'])
    frames = config['frames']
    return list(range(frames)) if frames <= 30 else sorted({0, 3, 29, frames-1} & set(range(frames)))


def harness(config, seed):
    c = validate(config)
    channels = c['channels']
    faults = ',\n'.join('{%d,%d,%dULL,%dULL}' % (
        f['channel'], {'output_stall': 0, 'input_gap': 1, 'reset': 2}[f['kind']],
        f['at_cycle'], f['duration_cycles']) for f in c['faults'])
    updates = []
    for u in c['csr_updates']:
        gains = u.get('gains')
        updates.append('{%d,%dULL,%d,{%s},%s}' % (u['channel'], u['at_cycle'],
            u['black_level'] if u.get('black_level') is not None else -1,
            ','.join(map(str, gains or [0]*4)), 'true' if gains is not None else 'false'))
    replacements = dict(WIDTH=c['width'], HEIGHT=c['height'], FRAMES=c['frames'],
        MAX_CYCLES=c['max_cycles'], PIXEL_RATE=c['pixel_rate_hz'],
        RASTER_WIDTH=c['raster_width'], RASTER_HEIGHT=c['raster_height'], SEED=seed,
        CFA=','.join(str({'RGGB': 0, 'BGGR': 1, 'GRBG': 2, 'GBRG': 3}[v['bayer_pattern']]) for v in channels),
        PATTERN=','.join(str({'gradient': 0, 'color_bars': 1, 'impulse': 2}[v['source_pattern']]) for v in channels),
        BLACK=','.join(str(v['black_level']) for v in channels),
        GAINS=','.join('{'+','.join(map(str, v['gains']))+'}' for v in channels),
        RAW_REPEAT=','.join('true' if v.get('raw_repeat') else 'false' for v in channels),
        PREVIEW_FRAMES=','.join(map(str, preview_indices(c))),
        INIT_RESET=2 if c['init_mode'] == 'randomized' else 0, INIT_SEED=c['init_seed'],
        FAULTS=faults, UPDATES=',\n'.join(updates),
        WINDOWS=','.join('{%dULL,%dULL}' % (w['start_cycle'], w['end_cycle']) for w in c['trace_windows']),
        TRACE_STRIDE=max(1, (c['max_cycles']+3999)//4000))
    text = (ROOT/'templates/adas_harness.cpp').read_text()
    for key, value in replacements.items():
        text = text.replace('@'+key+'@', str(value))
    return text


def run(config, work_dir, check, tool_identity, seed=42):
    from axiom_rtl_lab.runner import run_process
    c = validate(config)
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    check()
    started = time.perf_counter()
    design = generated_design(c, work_dir)
    text = harness(c, seed)
    (work_dir/'harness.cpp').write_text(text)
    tools = tool_identity.get('tools', tool_identity)
    stage = Path(tempfile.mkdtemp(prefix='axiom-adas-', dir='/tmp'))
    try:
        for source in design['sources']:
            shutil.copyfile(source, stage/Path(source).name)
        (stage/'harness.cpp').write_text(text)
        args = [tools['verilator_path'], '--cc', '--exe', '--build', '--assert', '-O3',
                '--top-module', design['top'], '--prefix', 'Vaxiom_adas_top', '-j', '2',
                '--x-initial', 'unique' if c['init_mode'] == 'randomized' else '0',
                '--x-assign', 'unique' if c['init_mode'] == 'randomized' else '0', '-CFLAGS', '-O3 -std=c++17',
                '-MAKEFLAGS', 'CXX='+tools['compiler_path'], '--Mdir', 'obj',
                *[Path(s).name for s in design['sources']], 'harness.cpp']
        try:
            run_process(args, stage, stage/'compile.log', check, 300, 'adas_compile', clean_env())
        finally:
            if (stage/'compile.log').exists():
                shutil.copyfile(stage/'compile.log', work_dir/'compile.log')
        binary = stage/'obj/Vaxiom_adas_top'
        (work_dir/'build.json').write_text(json.dumps(dict(command=args, design_hash=design['design_hash'],
            binary_sha256=digest(binary), harness_sha256=digest(stage/'harness.cpp'),
            tool_identity=tool_identity, two_state=True, init_mode=c['init_mode'], init_seed=c['init_seed'],
            initialization_scope='Verilator two-state initialization; randomized values are not four-state X propagation'), indent=2))
        paths = config.get('_input_raw_paths', {})
        source_paths = []
        for channel in c['channels']:
            path = paths.get(str(channel['channel']))
            if channel['source_kind'] == 'raw_asset':
                source_frames = 1 if channel.get('raw_repeat') else c['frames']
                if not path or not Path(path).is_file() or Path(path).stat().st_size != c['width']*c['height']*source_frames*2:
                    raise ValueError('ADAS decoded RAW asset missing or has wrong byte count')
                source_paths.append(str(Path(path).resolve()))
            else:
                source_paths.append('-')
        def bounded_check():
            check()
            for name in ('native.csv', 'waveform.vcd', 'run.log'):
                path = work_dir/name
                if path.exists() and path.stat().st_size > 128*1024*1024:
                    raise ValueError('ADAS bounded trace exceeded 128 MiB: '+name)
        run_process([str(binary), str(work_dir), *source_paths], stage, work_dir/'run.log',
                    bounded_check, max(1800, math.ceil(c['max_cycles']/100000000)*180), 'adas_simulation', clean_env())
        stats = json.loads((work_dir/'native-summary.json').read_text())
        manifest = json.loads((work_dir/'frames.json').read_text())
        committed_errors = discarded_errors = expected_discarded_errors = unexpected_dispositions = 0
        for frame in manifest['frames']:
            errors = frame['mismatch_count']
            if frame['status'] == 'committed':
                committed_errors += errors
            else:
                discarded_errors += errors
            # A removed input beat can shift a partially emitted line before EOL
            # detects malformed framing. Only explicitly faulted, aborted frames
            # may contain those differences; no committed image may hide errors.
            start = 256 + frame['frame']*c['raster_width']*c['raster_height']*c['clock_hz']/c['pixel_rate_hz']
            end = 256 + (frame['frame']+1)*c['raster_width']*c['raster_height']*c['clock_hz']/c['pixel_rate_hz']
            reason = frame['loss_reason']
            kind = {'fifo_overflow': 'output_stall', 'channel_reset': 'reset',
                    'frame_boundary': 'input_gap', 'input_sof_gap': 'input_gap'}.get(reason)
            faulted_frame = any(f['channel'] == frame['channel'] and f['kind'] == kind and
                f['at_cycle'] < end and f['at_cycle']+f['duration_cycles'] > start for f in c['faults'])
            if reason == 'input_sof_gap':
                faulted_frame = any(f['channel'] == frame['channel'] and f['kind'] == 'input_gap' and
                    f['at_cycle'] <= frame['first_input_ns']//10 < f['at_cycle']+f['duration_cycles'] for f in c['faults'])
            if reason in {'overlapping_frame', 'admission_rejected'}:
                # Only a still-active output stall at this boundary explains an
                # overlap/rejected admission; an unrelated fault is not a waiver.
                boundary = frame['terminal_ns']//10 if reason == 'overlapping_frame' else frame['first_input_ns']//10
                faulted_frame = any(f['channel'] == frame['channel'] and f['kind'] == 'output_stall' and
                    f['at_cycle'] <= boundary < f['at_cycle']+f['duration_cycles'] for f in c['faults'])
            if frame['status'] in {'aborted', 'unadmitted'} and not faulted_frame:
                unexpected_dispositions += 1
            expected_corruption = frame['status'] == 'aborted' and frame['loss_reason'] == 'frame_boundary' and any(
                f['channel'] == frame['channel'] and f['kind'] == 'input_gap' and
                f['at_cycle'] < end and f['at_cycle']+f['duration_cycles'] > start for f in c['faults'])
            frame['mismatch_disposition'] = 'invalidated_input_gap' if errors and expected_corruption else 'unexpected' if errors else 'none'
            if expected_corruption:
                expected_discarded_errors += errors
            for field in ('input_preview_artifact', 'output_preview_artifact'):
                old = frame.get(field)
                if old:
                    path = work_dir/old
                    if path.exists():
                        image = Image.open(path)
                        image.save(path.with_suffix('.png'))
                        path.unlink()
                        frame[field] = str(Path(old).with_suffix('.png'))
        manifest.update(schema_version='adas-module-frames/1',
            preview_scope='Spatially sampled actual RAW/RGB; full-resolution pixels checked before sampling',
            source_start_cycle=256, clock_hz=c['clock_hz'], config=c,
            preview_frames=preview_indices(c),
            source_repeat_policy=[dict(channel=v['channel'], raw_repeat=v.get('raw_repeat', False)) for v in c['channels']])
        source_window_ns = round(c['frames']*c['raster_width']*c['raster_height']*1e9/c['pixel_rate_hz'])
        observed_window_ns = source_window_ns + stats['drain_ns']
        for channel in manifest['channels']:
            channel.update(input_accepted_gbps=round(channel['input_pixels']*12/observed_window_ns, 6),
                output_rgb_gbps=round(channel['output_pixels']*36/observed_window_ns, 6))
        (work_dir/'frames.json').write_text(json.dumps(manifest, indent=2))
        with (work_dir/'native.csv').open() as stream:
            samples = [{k: int(v) for k, v in row.items()} for row in csv.DictReader(stream)]
        if len(samples) > 22000:
            raise ValueError('ADAS trace sample budget exceeded')
        full_target = (c['width'], c['height'], c['frames'], c['pixel_rate_hz'], c['raster_width'], c['raster_height']) == (1920, 1080, 30, 74250000, 2200, 1125)
        stats.update(committed_mismatch_count=committed_errors, discarded_mismatch_count=discarded_errors,
            expected_discarded_mismatch_count=expected_discarded_errors,
            unexpected_mismatch_count=stats['mismatch_count']-expected_discarded_errors,
            unexpected_disposition_count=unexpected_dispositions)
        unexpected = stats['unexpected_mismatch_count'] or stats['protocol_error_count'] or stats['missing_frames'] or unexpected_dispositions or stats['consumer_protocol_error_count'] or stats['consumer_aborted_visible_frames']
        verdict = 'timeout' if stats['timed_out'] else 'failed' if unexpected else 'passed'
        summary = stats | dict(verdict=verdict, design_hash=design['design_hash'],
            width=c['width'], height=c['height'], output_width=c['width']-4, output_height=c['height']-4,
            channels=4, requested_frames=4*c['frames'], acceptance_1080p30=full_target,
            acceptance_1080p30_passed=full_target and verdict=='passed' and stats['completed_frames']==120,
            pixel_checked_scope='Every accepted RGB12 component, including aborted partial frames',
            preview_scope=manifest['preview_scope'], source_start_cycle=256,
            source_window_ns=source_window_ns,
            offered_raw_gbps=round(4*c['width']*c['height']*c['frames']*12/source_window_ns, 6),
            throughput_scope='Accepted payload bits over source window plus drain; output includes explicitly aborted partial pixels',
            cpu_wall_seconds=time.perf_counter()-started, frame_manifest='frames.json',
            validation_scope='Four native open RTL camera paths; post-CSI digital boundary, no PHY, board or Qualcomm implementation',
            consumer_artifact='consumer.json', progress_artifact='progress.json',
            consumer_scope='Transactional hash/count receiver of actual RGB and terminal handshakes; only complete commits become visible',
            preview_frames=preview_indices(c), init_mode=c['init_mode'], init_seed=c['init_seed'], two_state=True,
            initialization_scope='Verilator two-state initialization; randomized values are not four-state X propagation',
            source_repeat_policy=manifest['source_repeat_policy'],
            clock_hz=c['clock_hz'], sampling='Pre-edge actual top-port samples; bounded dense windows plus periodic samples',
            trace_windows=c['trace_windows'], full_run_vcd=False)
        (work_dir/'summary.json').write_text(json.dumps(summary, indent=2))
        signals = [dict(id=k, unit='pixels' if 'fifo' in k else 'count' if 'config_version' in k else 'bit') for k in samples[0] if k not in {'cycle', 'time'}] if samples else []
        check()
        return dict(summary=summary, axis=dict(unit='ns', label='Simulated 100 MHz clock'),
                    samples=samples, signals=signals, frames=manifest)
    finally:
        shutil.rmtree(stage)
