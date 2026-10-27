"""Actual ngspice transients and defensive normalization checks."""
import math
import shutil

import pytest

from axiom_engineering.spice import run, _read_ascii_raw, MAX_SAMPLES


@pytest.fixture
def tools():
    binary = shutil.which('ngspice')
    if not binary:
        pytest.skip('Actual ngspice runtime unavailable')
    return {'tools': {'ngspice_path': binary, 'ngspice_version': 'native-test'}}


def rc(resistance='1k'):
    return dict(engine='spice', netlist=f'''* RC native transient
V1 in 0 PULSE(0 1 0 1n 1n 10m 20m)
R1 in out {resistance}
C1 out 0 1u
.end
''', step_s=1e-5, stop_s=.005, probes=['v(in)', 'v(out)', 'i(v1)'])


def nearest(result, time, probe='v(out)'):
    return min(result['samples'], key=lambda row: abs(row['time'] - time))[probe]


def test_actual_rc_tau_and_changed_resistance(tools, tmp_path):
    fast = run(rc(), tmp_path / 'fast', lambda: None, tools)
    slow = run(rc('2k'), tmp_path / 'slow', lambda: None, tools)
    assert fast['axis'] == {'unit': 's', 'label': 'Time'}
    assert fast['signals'][-1] == {'id': 'i(v1)', 'unit': 'A'}
    assert fast['summary']['verdict'] == 'observed'
    assert nearest(fast, .001) == pytest.approx(1 - math.exp(-1), abs=.004)
    assert nearest(slow, .001) == pytest.approx(1 - math.exp(-.5), abs=.004)
    assert fast['summary']['final_values']['v(out)'] == pytest.approx(1 - math.exp(-5), abs=.001)
    assert nearest(fast, .001) > nearest(slow, .001) + .2
    assert fast['summary']['sample_count'] == len(fast['samples'])
    assert fast['samples'][0]['time'] == 0
    assert fast['samples'][-1]['time'] == pytest.approx(.005)
    assert all(a['time'] < b['time'] for a, b in zip(fast['samples'], fast['samples'][1:]))
    assert fast['summary']['min_values']['i(v1)'] < -.0009
    assert (tmp_path / 'fast' / 'native.csv').read_text().startswith('time,v(in),v(out),i(v1)')
    circuit = (tmp_path / 'fast' / 'generated.cir').read_text()
    assert circuit.count('.end') == 1 and '.control' not in circuit
    assert '.tran ' in circuit and '.save time ' in circuit
    assert (tmp_path / 'fast' / 'spice.log').stat().st_size > 0


def test_actual_rlc_transient_and_diode_current(tools, tmp_path):
    config = dict(engine='spice', netlist='''* RLC step
V1 in 0 PULSE(0 1 0 1n 1n 10m 20m)
R1 in mid 10
L1 mid out 10m
C1 out 0 1u
.end
''', step_s=1e-6, stop_s=.005, probes=['v(out)', 'i(v1)'])
    result = run(config, tmp_path / 'rlc', lambda: None, tools)
    # Underdamped second-order response must overshoot, unlike the RC circuit.
    assert result['summary']['max_values']['v(out)'] > 1.5
    assert result['summary']['min_values']['i(v1)'] < 0
    config['netlist'] = '''* Diode operating point retained through transient
V1 in 0 DC 1
R1 in out 1k
D1 out 0 diode_model
.model diode_model D(IS=1e-14 N=1)
.end
'''
    diode = run(config, tmp_path / 'diode', lambda: None, tools)
    voltage = diode['summary']['final_values']['v(out)']
    current = diode['summary']['final_values']['i(v1)']
    assert .5 < voltage < .8
    assert current == pytest.approx(-(1 - voltage) / 1000, rel=1e-5)


def test_native_error_and_missing_probe_fail_closed(tools, tmp_path):
    bad = rc()
    bad['netlist'] = '* Missing model\nV1 in 0 DC 1\nD1 in 0 nonexistent\n.end\n'
    with pytest.raises(RuntimeError, match='spice_'):
        run(bad, tmp_path / 'bad', lambda: None, tools)
    assert (tmp_path / 'bad' / 'spice.log').stat().st_size
    bad = rc()
    bad['probes'] = ['v(does_not_exist)']
    with pytest.raises(RuntimeError, match='spice_'):
        run(bad, tmp_path / 'missing', lambda: None, tools)


def test_control_and_file_access_rejected_before_dispatch(tools, tmp_path):
    for directive in ['.control\nshell touch owned\n.endc', '.include external.lib', '.param r=2']:
        bad = rc()
        bad['netlist'] = '* Unsafe netlist\nR1 in out 1k\n' + directive + '\n.end\n'
        with pytest.raises(ValueError):
            run(bad, tmp_path, lambda: None, tools)
    assert not (tmp_path / 'generated.cir').exists()


def test_cancel_before_dispatch(tools, tmp_path):
    from ade2e.adapters import AdapterCancelledBeforeDispatch
    def cancel():
        raise AdapterCancelledBeforeDispatch('cancelled')
    with pytest.raises(AdapterCancelledBeforeDispatch):
        run(rc(), tmp_path, cancel, tools)
    assert not (tmp_path / 'generated.cir').exists()


def raw_header(points):
    return f'''Title: bounded parser
Plotname: Transient Analysis
Flags: real
No. Variables: 2
No. Points: {points}
Variables:
0 time time
1 v(out) voltage
Values:
'''


@pytest.mark.parametrize('values', ['0 0\n0.1\n1 0\n0.2\n', '0 0\nnan\n1 1\n0.2\n', '0 0\n0.1\n'])
def test_invalid_waveform_is_never_normalized(values, tmp_path):
    raw = tmp_path / 'bad.raw'
    raw.write_text(raw_header(2) + values)
    with pytest.raises(RuntimeError, match='spice_output_invalid'):
        _read_ascii_raw(raw, ['v(out)'], lambda: None)


def test_sample_budget_checked_before_parse(tmp_path):
    raw = tmp_path / 'large.raw'
    raw.write_text(raw_header(MAX_SAMPLES + 1))
    with pytest.raises(RuntimeError, match='spice_sample_budget'):
        _read_ascii_raw(raw, ['v(out)'], lambda: None)


def test_late_power_sag_native_points_above_old_budget_and_full_recovery(tools,tmp_path):
    from ade2e.hil_boundary_models import ModelInjectionSave,compile_plan
    settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=4000,step_us=20,voltage_v=6,
        repeats=[dict(start_ms=1350,end_ms=1650),dict(start_ms=2850,end_ms=3150)])
    config=compile_plan(ModelInjectionSave(settings=settings))['sides']['faulted']
    result=run(config,tmp_path/'late',lambda:None,tools)
    assert 200000<len(result['samples'])<=MAX_SAMPLES
    assert result['samples'][-1]['time']==4
    assert nearest(result,2.84)>11 and nearest(result,2.86)<8
    assert nearest(result,3.14)<8 and nearest(result,3.16)>11
    assert max(b['time']-a['time'] for a,b in zip(result['samples'],result['samples'][1:]))<=2.000001e-5
