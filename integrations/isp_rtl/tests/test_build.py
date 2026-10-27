"""FPGA results must never imply pixel/board validation or hide timing misses."""
import json
from pathlib import Path
import pytest

from axiom_isp_rtl import build


def test_report_distinguishes_missing_clock_timing_failure_and_resource_use():
    missing = build._report_summary({'utilization': {'DP16KD': {'used': 12}}})
    assert missing['timing_status'] == 'not_evaluated'
    assert missing['timing_met'] is None
    assert missing['resources']['bram'] == 12
    failed = build._report_summary({'fmax': {'clk': {'achieved': 99.99, 'constraint': 100}}})
    assert failed['timing_status'] == 'failed'
    weakened = build._report_summary({'fmax': {'clk': {'achieved': 90, 'constraint': 80}}})
    assert weakened['timing_status'] == 'failed'
    passed = build._report_summary({'fmax': {'clk': {'achieved': 121.2, 'constraint': 100}}})
    assert passed['timing_status'] == 'passed'


def test_other_families_are_explicit_export_templates(tmp_path):
    profiles = build.build_profiles()
    assert profiles[0]['device'] == 'LFE5U-85F'
    assert profiles[0]['mode'] == 'out_of_context'
    for p in profiles[1:]:
        assert build.readiness(p['id'])['available'] is False
        with pytest.raises(ValueError):
            build.identity(p['id'])
    build.write_vendor_templates(tmp_path, ['axiom_isp_top.sv'])
    assert json.loads((tmp_path/'vendor-template-status.json').read_text())['status'] == 'not_executed'
    for name in ('gw2a-project-template.tcl', 'cyclonev-project-template.tcl'):
        assert 'AXIOM_EXACT_FPGA_PART' in (tmp_path/name).read_text()
        assert 'error' in (tmp_path/name).read_text()


def test_missing_tool_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv('AXIOM_OSS_CAD_SUITE', str(tmp_path))
    assert not build.readiness()['available']
    with pytest.raises(ValueError, match='unavailable'):
        build.identity()


def test_build_config_rejects_unknown_profile():
    with pytest.raises(ValueError, match='Unknown'):
        build.readiness('untrusted-command')


@pytest.mark.parametrize('kind', ['AdapterCancelled', 'AdapterOwnershipLost'])
def test_build_does_not_swallow_a_single_control_exception(tmp_path, monkeypatch, kind):
    from ade2e import adapters
    from axiom_rtl_lab import runner
    exception = getattr(adapters, kind)
    def stop(*args, **kwargs):
        raise exception('one-shot worker control event')
    monkeypatch.setattr(runner, 'run_process', stop)
    tools = dict(profile=build.build_profiles()[0], tools={
        'yosys': {'path': '/not-executed'}, 'nextpnr-ecp5': {'path': '/not-executed'}})
    with pytest.raises(exception, match='one-shot'):
        build.run({}, tmp_path, lambda: None, tools)
    assert not (tmp_path/'summary.json').exists()
