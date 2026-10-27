"""Bounded ngspice transient execution for an already validated passive/device netlist.

Only generated .save/.tran directives run. No user control script, external model,
or shell is involved. Samples retain ngspice's adaptive time points verbatim.
"""
import csv
import math
import os
from pathlib import Path

from axiom_rtl_lab.runner import run_process

MAX_SAMPLES = 250_000
MAX_NATIVE_BYTES = 64 * 1024 * 1024


def _generated_netlist(config):
    # Revalidate inside the engine as well as in prepare: a direct invocation must
    # not turn the fixed batch interface into a control-language execution path.
    from ade2e.engineering_lab_schemas import SpiceEngineeringConfig
    config = SpiceEngineeringConfig.model_validate(config).model_dump()
    lines = [line for line in config['netlist'].splitlines() if line.strip().lower() != '.end']
    lines.extend([
        '.save time ' + ' '.join(config['probes']),
        f'.tran {config["step_s"]:.17g} {config["stop_s"]:.17g} 0 {config["step_s"]:.17g}',
        '.end',
    ])
    return config, '\n'.join(lines) + '\n'


def _canonical_vector(name):
    name = name.lower()
    if name.endswith('#branch'):
        return 'i(' + name[:-7] + ')'
    return name


def _read_ascii_raw(path: Path, probes, check):
    """Read the single real transient plot emitted by ngspice -b -r."""
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError('spice_output_missing: ngspice produced no transient waveform')
    if path.stat().st_size > MAX_NATIVE_BYTES:
        raise RuntimeError('spice_output_budget: native waveform exceeds 64 MiB')
    with path.open(encoding='ascii') as source:
        header = {}
        while True:
            line = source.readline()
            if not line:
                raise RuntimeError('spice_output_invalid: missing Variables header')
            if line.strip() == 'Variables:':
                break
            if ':' in line:
                key, value = line.split(':', 1)
                header[key.strip().lower()] = value.strip()
        try:
            variable_count = int(header['no. variables'])
            point_count = int(header['no. points'])
        except (KeyError, ValueError) as exc:
            raise RuntimeError('spice_output_invalid: invalid waveform dimensions') from exc
        if header.get('plotname', '').lower() != 'transient analysis' or header.get('flags', '') != 'real':
            raise RuntimeError('spice_output_invalid: expected one real transient plot')
        if not 1 <= point_count <= MAX_SAMPLES:
            raise RuntimeError(f'spice_sample_budget: {point_count} points; maximum {MAX_SAMPLES}')
        if not 2 <= variable_count <= len(probes) + 1:
            raise RuntimeError('spice_output_invalid: unexpected vector count')
        vectors = []
        for index in range(variable_count):
            parts = source.readline().split()
            if len(parts) != 3 or parts[0] != str(index):
                raise RuntimeError('spice_output_invalid: invalid vector declaration')
            vectors.append(_canonical_vector(parts[1]))
        if len(set(vectors)) != len(vectors) or vectors[0] != 'time':
            raise RuntimeError('spice_output_invalid: ambiguous time/vector declarations')
        for probe in probes:
            if probe.lower() not in vectors:
                raise RuntimeError(f'spice_probe_unavailable: requested vector {probe} is absent')
        while True:
            line = source.readline()
            if not line:
                raise RuntimeError('spice_output_invalid: missing Values header')
            if line.strip():
                if line.strip() != 'Values:':
                    raise RuntimeError('spice_output_invalid: expected ASCII waveform values')
                break
        tokens = (token for line in source for token in line.split())
        indices = [(probe, vectors.index(probe.lower())) for probe in probes]
        samples = []
        previous_time = -math.inf
        try:
            for index in range(point_count):
                if index % 256 == 0:
                    check()
                if next(tokens) != str(index):
                    raise RuntimeError('spice_output_invalid: nonsequential sample index')
                values = [float(next(tokens)) for _ in range(variable_count)]
                if not all(math.isfinite(value) for value in values):
                    raise RuntimeError('spice_output_invalid: nonfinite sample')
                if values[0] < 0 or values[0] <= previous_time:
                    raise RuntimeError('spice_output_invalid: time points must increase')
                previous_time = values[0]
                samples.append(dict(time=values[0], **{probe: values[i] for probe, i in indices}))
        except (StopIteration, ValueError) as exc:
            raise RuntimeError('spice_output_invalid: truncated or nonnumeric waveform') from exc
        if next(tokens, None) is not None:
            raise RuntimeError('spice_output_invalid: unexpected trailing waveform content')
    return samples


def run(config, work_dir: Path, check, tool_identity: dict):
    config, generated = _generated_netlist(config)
    check()
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    circuit = work_dir / 'generated.cir'
    raw = work_dir / 'native.raw'
    log = work_dir / 'spice.log'
    circuit.write_text(generated, encoding='ascii')
    runtime_dir = work_dir / 'spice-runtime'
    runtime_dir.mkdir(exist_ok=True)
    binary = tool_identity['tools']['ngspice_path']
    if not binary:
        raise ValueError('ngspice runtime is not configured')
    # -n disables personal init files; an empty SPICE_SCRIPTS prevents global
    # spinit code-model loading. Do not inherit user credentials or config paths.
    env = {'PATH': os.pathsep.join([str(Path(binary).parent), '/usr/bin', '/bin']),
           'LC_ALL': 'C', 'SPICE_ASCIIRAWFILE': '1',
           'SPICE_SCRIPTS': str(runtime_dir.resolve()),
           'SPICE_USERINIT_DIR': str(runtime_dir.resolve()),
           'TMPDIR': str(runtime_dir.resolve())}

    def bounded_check():
        check()
        for path in (raw, log):
            if path.exists() and path.stat().st_size > MAX_NATIVE_BYTES:
                raise RuntimeError('spice_output_budget: native waveform or log exceeds 64 MiB')

    run_process([str(binary), '-n', '-b', '-r', str(raw.resolve()), str(circuit.resolve())],
                work_dir, log, bounded_check, 30, 'spice', env=env)
    bounded_check()
    samples = _read_ascii_raw(raw, config['probes'], bounded_check)
    tolerance = max(1e-15, config['stop_s'] * 1e-9)
    if abs(samples[0]['time']) > tolerance or abs(samples[-1]['time'] - config['stop_s']) > tolerance:
        raise RuntimeError('spice_transient_incomplete: waveform does not span the requested transient interval')
    with (work_dir / 'native.csv').open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=['time', *config['probes']])
        writer.writeheader()
        writer.writerows(samples)
    bounded_check()
    return dict(
        axis=dict(unit='s', label='Time'),
        signals=[dict(id=probe, unit='V' if probe.lower().startswith('v(') else 'A') for probe in config['probes']],
        samples=samples,
        summary=dict(verdict='observed', sample_count=len(samples), duration_s=samples[-1]['time'] - samples[0]['time'],
                     requested_stop_s=config['stop_s'], maximum_step_s=config['step_s'],
                     sampling='native_adaptive_points', peak_definition='maximum_absolute_value',
                     final_values={p: samples[-1][p] for p in config['probes']},
                     peak_values={p: max(abs(s[p]) for s in samples) for p in config['probes']},
                     min_values={p: min(s[p] for s in samples) for p in config['probes']},
                     max_values={p: max(s[p] for s in samples) for p in config['probes']}),
    )
