from pathlib import Path
import json

import numpy as np
import pytest

from axiom_isp_rtl import generated_design, identity, readiness, run
from axiom_isp_rtl import model


@pytest.fixture(scope='module')
def tools():
    if not readiness()['available']:pytest.skip('Native Verilator toolchain unavailable')
    return identity()


def config(**updates):
    return dict(width=16,height=16,frames=2,max_cycles=3000,
                black_level=64,gains=[172,141,113,207],source_pattern='gradient',**updates)


@pytest.mark.parametrize('pattern',['RGGB','BGGR','GRBG','GBRG'])
def test_native_four_patterns_with_real_stalls(tmp_path,tools,pattern):
    c=config(bayer_pattern=pattern,stalls=[dict(start_cycle=380,end_cycle=445)])
    r=run(c,tmp_path,lambda:None,tools,42)['summary']
    assert r['verdict']=='passed',r
    assert r['completed_frames']==2 and r['mismatch_count']==0
    assert r['output_sha256']==r['reference_sha256']
    assert r['output_width']==12 and r['output_height']==12
    assert r['input_transfers']==512
    assert r['protocol_error_count']==0


@pytest.mark.parametrize('cycle',[100,400])
def test_native_reset_replays_unfinished_frame(tmp_path,tools,cycle):
    c=config(reset_at_cycle=cycle)
    r=run(c,tmp_path,lambda:None,tools,23)['summary']
    assert r['verdict']=='passed',r
    assert r['reset_count']==1 and r['input_transfers']>512
    assert r['output_sha256']==r['reference_sha256']
    if cycle==400:assert r['discarded_output_pixels']>0


def test_native_raw_asset_extremes_and_gain_saturation(tmp_path,tools):
    c=dict(width=8,height=8,frames=1,max_cycles=1000,black_level=300,
           gains=[65535,0,128,256],bayer_pattern='GBRG',source_kind='raw_asset')
    raw=np.arange(64,dtype='<u2').reshape(1,8,8)*65
    raw[0,0,:4]=[0,299,300,4095]
    path=tmp_path/'asset.raw';raw.tofile(path);c['_input_raw_path']=str(path)
    r=run(c,tmp_path,lambda:None,tools,9)['summary']
    assert r['verdict']=='passed',r
    assert (tmp_path/'input.raw').read_bytes()==path.read_bytes()


def test_native_timeout_not_passed(tmp_path,tools):
    c=config();c['max_cycles']=40
    r=run(c,tmp_path,lambda:None,tools,42)['summary']
    assert r['verdict']=='timeout' and r['completed_frames']==0
    assert r['max_abs_error']==0 and r['unreceived_pixels']==288


def test_one_lsb_independent_oracle_corruption_is_detected(tmp_path,tools,monkeypatch):
    original=model.golden
    def corrupted(raw,c):
        result=original(raw,c)
        result['rgb'][0,1,2,1]^=1
        return result
    monkeypatch.setattr(model,'golden',corrupted)
    c=config();c['frames']=1
    r=run(c,tmp_path,lambda:None,tools,42)['summary']
    assert r['verdict']=='failed' and r['mismatch_count']==1 and r['max_abs_error']==1
    assert {k:r['first_mismatch'][k] for k in ['frame','x','y','channel']}==dict(frame=0,x=2,y=1,channel='G')
    assert abs(r['first_mismatch']['actual']-r['first_mismatch']['expected'])==1


def test_maximum_frames_and_full_execution_top_port_vcd(tmp_path,tools):
    c=dict(width=320,height=240,frames=2,max_cycles=1000000,
           black_level=65,gains=[173,131,115,211],bayer_pattern='BGGR',source_pattern='gradient',
           stalls=[dict(start_cycle=100000,end_cycle=110000)])
    result=run(c,tmp_path,lambda:None,tools,9);r=result['summary']
    assert r['verdict']=='passed',r
    assert r['completed_frames']==2 and r['input_transfers']==153600
    assert r['vcd_cycles']==r['cycles']>4096
    assert any(s['accepted_out'] for s in result['samples'])
    vcd=tmp_path/'waveform.vcd'
    assert vcd.stat().st_size<96*1024*1024
    times=[int(line[1:]) for line in vcd.read_text().splitlines() if line.startswith('#')]
    assert times[0]==0 and times[-1]==(r['cycles']-1)*10+5
    assert len(times)==r['cycles']*2


def test_same_raw_a_b_independent_of_processing_config(tmp_path,tools):
    a=config();a['frames']=1
    b={**a,'black_level':700,'gains':[256,128,128,64]}
    ra=run(a,tmp_path/'a',lambda:None,tools,42)['summary']
    rb=run(b,tmp_path/'b',lambda:None,tools,42)['summary']
    assert ra['verdict']==rb['verdict']=='passed'
    assert ra['input_sha256']==rb['input_sha256']
    assert ra['output_sha256']!=rb['output_sha256']
    assert ra['design_hash']!=rb['design_hash']


def test_constant_oracle_and_identity(tmp_path):
    c=dict(width=8,height=8,frames=1,black_level=64,gains=[128]*4)
    raw=np.full((1,8,8),1064,dtype='<u2')
    for pattern in model.PATTERNS:
        r=model.golden(raw,{**c,'bayer_pattern':pattern})['rgb']
        assert r.shape==(1,4,4,3) and np.all(r==1000)
    a=generated_design(c,tmp_path/'a');b=generated_design({**c,'stalls':[{'start_cycle':10,'end_cycle':20}]},tmp_path/'b')
    assert a['design_hash']==b['design_hash']
    assert all(Path(s).exists() for s in a['sources'])
    assert 'MIT' in (tmp_path/'a/mini-isp-LICENSE.md').read_text()


def test_cancel_before_compile(tmp_path,tools):
    def cancel():raise RuntimeError('cancelled-test')
    with pytest.raises(RuntimeError,match='cancelled-test'):
        run(config(),tmp_path,cancel,tools,42)
    assert not (tmp_path/'build.json').exists()


@pytest.mark.parametrize('changes',[{'width':9},{'height':4},{'gains':[128]},
    {'black_level':4096},{'stalls':[{'start_cycle':2,'end_cycle':1}]},
    {'stalls':[{'start_cycle':20,'end_cycle':30},{'start_cycle':10,'end_cycle':15}]},
    {'stalls':[{'start_cycle':10,'end_cycle':30},{'start_cycle':20,'end_cycle':40}]},
    {'bayer_pattern':'RGB'},{'reset_at_cycle':1}])
def test_reject_unsupported_config(changes):
    with pytest.raises(ValueError):model.validate(changes)
