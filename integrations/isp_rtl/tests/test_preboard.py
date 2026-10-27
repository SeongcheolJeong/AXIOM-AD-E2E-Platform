"""Native pre-board checks; no mocked simulator results or physical-board claims."""
import json
from pathlib import Path

import pytest

from ade2e.adas_module_schemas import AdasModuleConfig
from ade2e.adapters import AdapterCancelled
from axiom_isp_rtl import preboard
from axiom_isp_rtl.runtime import vendor_manifest


def config(**changes):
    return dict(engine='adas_preboard_checks',core_config=AdasModuleConfig(engine='adas_module_rtl').model_dump(),
                boundary_words=32,**changes)


@pytest.fixture(scope='module')
def native_identity():
    if not preboard.readiness()['available']:pytest.skip('Native Verilator/Icarus tools unavailable')
    return preboard.identity()


@pytest.mark.parametrize('periods',[(14,10),(4,40),(40,4)])
def test_actual_lint_core_four_state_and_reset_cdc_controls(native_identity,tmp_path,periods):
    vendor_before=vendor_manifest()
    result=preboard.run(config(source_period_ns=periods[0],sink_period_ns=periods[1]),tmp_path,lambda:None,native_identity,seed=73)
    assert result['summary']['verdict']=='passed'
    checks={c['id']:c for c in result['summary']['checks']}
    assert len(checks)==8 and all(c['status']=='passed' for c in checks.values())
    assert checks['boundary_4state']['recovered_words']==32
    assert checks['boundary_4state']['pending_words_discarded_on_lock_loss']>0
    assert checks['core_valid_pixels_4state']['engine']=='Icarus Verilog'
    assert 'CORE_PIXELS_RESULT errors=0 pixels=144,144,144,144' in (tmp_path/'core-pixels.log').read_text()
    assert 'BLC_VALID_DATA_X' in (tmp_path/'blc_original_x_control.log').read_text()
    assert 'BLC_RESULT errors=0 pixels=3' in (tmp_path/'blc_fixed_4state.log').read_text()
    assert 'BAD_RESET_DETECTED' in (tmp_path/'bad_reset_control.log').read_text()
    assert 'BAD_CONSUMER_DETECTED' in (tmp_path/'bad_consumer_control.log').read_text()
    assert result['summary']['board_ready'] is False
    assert all(v=='not_configured' for v in result['summary']['board_requirements'].values())
    assert vendor_manifest()==vendor_before
    assert (tmp_path/'boundary.vcd').stat().st_size>1000
    assert 0<len(result['samples'])<=2048 and result['axis']['unit']=='ns'
    manifest=json.loads((tmp_path/'boundary-manifest.json').read_text())
    assert manifest['reference_only'] and not manifest['integrated_with_isp']
    assert manifest['core_design_hash']==result['summary']['design_hash']
    assert 'Explicit zero extension' in json.loads((tmp_path/'design.json').read_text())['blc_derivative']['change']


def test_tools_fail_closed(monkeypatch):
    monkeypatch.setattr(preboard.shutil,'which',lambda _:None)
    assert preboard.readiness()['available'] is False
    with pytest.raises(ValueError,match='required'):preboard.identity()


def test_cancellation_is_not_converted_to_failed_check(native_identity,tmp_path):
    calls=0
    def cancel():
        nonlocal calls
        calls+=1
        if calls==2:raise AdapterCancelled('test cancellation')
    with pytest.raises(AdapterCancelled,match='test cancellation'):
        preboard.run(config(),tmp_path,cancel,native_identity)
    assert not (tmp_path/'summary.json').exists()


@pytest.mark.parametrize('change',[{'boundary_words':15},{'source_period_ns':5},{'sink_period_ns':True},
                                  {'reset_hold_cycles':1},{'lock_delay_cycles':33},{'hardware_target':'fake-board'}])
def test_boundary_configuration_is_bounded(change):
    value=config();value.update(change)
    with pytest.raises(ValueError):preboard.validate(value)
