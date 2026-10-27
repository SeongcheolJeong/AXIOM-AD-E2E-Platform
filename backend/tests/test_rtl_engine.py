"""Actual Verilator FIFO runs plus independent queue scoreboard and failure handling."""
import copy
import csv
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from axiom_rtl_lab.model import reference,scoreboard,compare
from axiom_rtl_lab.runner import identity,compile_fifo,run_process,readiness
from ade2e.adapters import AdapterCancelledBeforeDispatch

@pytest.fixture(scope='module')
def build_cache(tmp_path_factory):
    if not readiness()['available']:pytest.skip('Actual Verilator and C++ compiler required')
    return tmp_path_factory.mktemp('rtl native build')

def native(config,tmp,cache):
    rows,stimulus=reference(config)
    b,_,_=compile_fifo(config['depth'],cache,identity(),lambda:None)
    with (tmp/'in.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['cycle','reset','in_valid','in_data','out_ready']);w.writeheader();w.writerows(stimulus)
    run_process([str(b),str(tmp/'in.csv'),str(tmp/'out.csv'),str(tmp/'fifo.vcd')],tmp,tmp/'run.log',lambda:None,30,'simulation')
    with (tmp/'out.csv').open() as f:actual=[{k:int(v) for k,v in r.items()} for r in csv.DictReader(f)]
    return rows,actual

@pytest.mark.parametrize('depth',[2,3,64])
@pytest.mark.parametrize('resets',[[0,1],[0,1,35]])
def test_native_wrap_full_pop_push_stall_reset(depth,resets,tmp_path,build_cache):
    c=dict(depth=depth,words=257,max_cycles=1000,stalls=[dict(start_cycle=10,end_cycle=60),dict(start_cycle=80,end_cycle=91)],resets=resets)
    expected,actual=native(c,tmp_path,build_cache)
    result=compare(expected,actual,c)
    assert result['matched'],result
    board,s,transfers=scoreboard(actual,c)
    assert board['passed'] and s['drained']
    assert s['input_transfers']==s['output_transfers']+s['reset_discarded']==257
    assert s['peak_occupancy']<=depth
    assert any(r['accepted_in'] and r['accepted_out'] for r in actual)
    if depth<64:assert any(r['full'] and r['accepted_in'] and r['accepted_out'] for r in actual)
    if len(resets)>2:assert s['reset_discarded']>0
    assert '$timescale 1ns $end' in (tmp_path/'fifo.vcd').read_text()
    assert all(t['latency_cycles']>=1 for t in transfers if t['status']=='delivered')


def test_normal_arithmetic_oracle_and_corruption(tmp_path,build_cache):
    c=dict(depth=64,words=4096,max_cycles=20000,stalls=[],resets=[0,1])
    expected,actual=native(c,tmp_path,build_cache)
    _,s,_=scoreboard(actual,c)
    assert s['cycles']==4099 and s['max_latency_cycles']==1 and s['output_transfers']==4096
    assert s['input_stall_cycles']==0 and s['peak_occupancy']==1
    corrupt=copy.deepcopy(actual);corrupt[15]['out_data']^=1
    mismatch=compare(expected,corrupt,c)
    assert not mismatch['matched'] and mismatch['first_mismatch']['cycle']==15
    assert mismatch['first_mismatch']['signal']=='out_data'
    assert not mismatch['scoreboards']['rtl']['passed']


def test_native_cycle_timeout_retains_pending(tmp_path,build_cache):
    c=dict(depth=3,words=128,max_cycles=32,stalls=[dict(start_cycle=0,end_cycle=32)],resets=[0])
    e,a=native(c,tmp_path,build_cache);board,s,transfers=scoreboard(a,c)
    assert compare(e,a,c)['matched']
    assert s['timeout'] and not s['drained'] and board['pending']==3
    assert sum(t['status']=='pending' for t in transfers)==3


def test_cache_integrity_and_identity(tmp_path,build_cache):
    ident=identity();b,m,hit=compile_fifo(3,build_cache,ident,lambda:None)
    assert hit
    # Use an isolated cache receipt to test rejection without damaging shared successful builds.
    cache=tmp_path/'cache';target=cache/m['cache_key'];(target/'obj').mkdir(parents=True)
    (target/'obj'/'Vreference_fifo').write_bytes(b'tampered')
    import json
    (target/'build.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='integrity'):compile_fifo(3,cache,ident,lambda:None)


def test_process_failure_timeout_cancel_and_missing(tmp_path,monkeypatch):
    with pytest.raises(RuntimeError,match='compile_failed'):
        run_process([sys.executable,'-c','raise SystemExit(3)'],tmp_path,tmp_path/'failed.log',lambda:None,1,'compile')
    with pytest.raises(RuntimeError,match='simulation_timeout'):
        run_process([sys.executable,'-c','import time;time.sleep(10)'],tmp_path,tmp_path/'timeout.log',lambda:None,.1,'simulation')
    start=time.monotonic()
    def cancel():
        if time.monotonic()-start>.1:raise AdapterCancelledBeforeDispatch('test cancellation')
    with pytest.raises(AdapterCancelledBeforeDispatch):
        run_process([sys.executable,'-c','import time;time.sleep(10)'],tmp_path,tmp_path/'cancel.log',cancel,2,'simulation')
    monkeypatch.setenv('PATH','/definitely/missing')
    assert not readiness()['available']
    with pytest.raises(ValueError):identity()
