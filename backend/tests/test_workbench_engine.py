"""Real Verilator generated graphs and independent per-node/end-to-end scoreboards."""
import copy,csv
from pathlib import Path
import pytest
from axiom_workbench.model import reference,scoreboard,compare,decorate,FIELDS
from axiom_workbench.runner import readiness,identity,compile_graph,run_process
from axiom_workbench.generator import generate

@pytest.fixture(scope='module')
def cache(tmp_path_factory):
    if not readiness()['available']:pytest.skip('Actual Verilator required')
    return tmp_path_factory.mktemp('workbench cache spaces')

def compiled(depths,words=512,stalls=None,resets=None,max_cycles=5000):
    return dict(fifo_order=[f'q{i}' for i in range(len(depths))],depths=depths,source_id='s',sink_id='t',ready_id='r',
      stream_edges=[f'e{i}' for i in range(len(depths)+1)],ready_edge_id='enable',execution_hash='test',
      config=dict(depths=depths,words=words,max_cycles=max_cycles,stalls=stalls or [],resets=resets or [0,1]))

def native(c,tmp,cache):
    e,stim=reference(c);b,build,hit,g=compile_graph(c['depths'],cache,identity(),lambda:None)
    with (tmp/'in.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['cycle','reset','in_valid','in_data','out_ready']);w.writeheader();w.writerows(stim)
    run_process([str(b),str(tmp/'in.csv'),str(tmp/'out.csv'),str(tmp/'test.vcd')],tmp,tmp/'run.log',lambda:None,30,'simulation')
    rows=[]
    with (tmp/'out.csv').open() as f:
        for raw in csv.DictReader(f):
            row={k:int(v) for k,v in raw.items()};nodes={n:{k:row.pop(f'f{i}_{k}') for k in FIELDS} for i,n in enumerate(c['fifo_order'])}
            rows.append(decorate(dict(**row,nodes=nodes),c))
    return e,rows,build

@pytest.mark.parametrize('depths',[[64],[64,64],[2,3,7],[2,3,4,7]])
def test_normal_native_pipeline_arithmetic(depths,tmp_path,cache):
    c=compiled(depths);e,a,b=native(c,tmp_path,cache)
    assert compare(e,a,c)['matched']
    board,s,t=scoreboard(a,c)
    assert board['passed'] and s['drained']
    assert s['max_latency_cycles']==len(depths)
    assert s['cycles']==512+2+len(depths)
    assert s['output_transfers']==512 and s['reset_discarded']==0
    assert s['peak_occupancy']==len(depths)
    assert '$timescale 1ns $end' in (tmp_path/'test.vcd').read_text()
    top=generate(depths)['generated_top.sv']
    assert top.count('reference_fifo #')==len(depths)
    assert b['depths']==depths

@pytest.mark.parametrize('reset',[False,True])
def test_all_fifo_full_propagation_recovery_and_reset_accounting(reset,tmp_path,cache):
    c=compiled([2,3,4,7],stalls=[dict(start_cycle=32,end_cycle=132)],resets=[0,1,64] if reset else [0,1])
    e,a,_=native(c,tmp_path,cache);result=compare(e,a,c);assert result['matched'],result
    _,s,t=scoreboard(a,c)
    assert s['input_stall_cycles']>0 and s['peak_occupancy']==16
    assert s['output_transfers']+s['reset_discarded']==512
    assert s['reset_discarded']==(16 if reset else 0)
    assert s['reset_discarded']==sum(v['reset_discarded'] for v in s['per_node'].values())
    assert any(all(r['nodes'][n]['full'] and r['nodes'][n]['accepted_in'] and r['nodes'][n]['accepted_out'] for n in c['fifo_order']) for r in a)
    assert len({r['index'] for r in t})==512
    assert [r['data'] for r in t if r['status']=='delivered']==[r['out_data'] for r in a if r['accepted_out']]


def test_mismatch_localizes_block_and_independent_connection_scoreboard(tmp_path,cache):
    c=compiled([64,64]);e,a,_=native(c,tmp_path,cache)
    a[12]['nodes']['q1']['out_data']^=1
    # A real final FIFO output fault is visible at both the node and global port.
    a[12]['out_data']^=1
    a[12]=decorate(a[12],c)
    mismatch=compare(e,a,c)
    assert not mismatch['matched']
    assert mismatch['first_mismatch']['node_id']=='q1' and mismatch['first_mismatch']['cycle']==12
    assert not mismatch['scoreboards']['rtl']['passed']

def test_global_only_mismatch_retains_boundary_location():
    c=compiled([64,64]);e,_=reference(c);a=copy.deepcopy(e)
    a[12]['in_ready']^=1
    first=compare(e,a,c)['first_mismatch']
    assert (first['cycle'],first['node_id'],first['edge_id'])==(12,'s','e0')


def test_bounded_native_timeout_preserves_pending_all_nodes(tmp_path,cache):
    c=compiled([2,3,4,7],stalls=[dict(start_cycle=0,end_cycle=128)],max_cycles=128)
    e,a,_=native(c,tmp_path,cache);assert compare(e,a,c)['matched']
    _,s,t=scoreboard(a,c)
    assert s['timeout'] and s['pending_words']==16 and s['unsubmitted_words']==496
    assert len(t)==16 and all(r['status']=='pending' for r in t)


def test_layout_neutral_compilation_and_cache_integrity(tmp_path,cache):
    c=compiled([64,64]);e,a,b=native(c,tmp_path,cache)
    binary,m,hit,g=compile_graph([64,64],cache,identity(),lambda:None)
    assert hit and m['cache_key']==b['cache_key']
    # Pinned compiler receipt uses the actual selected compiler command.
    log=(binary.parent.parent/'compile.log').read_text()
    assert identity()['tools']['compiler_path'] in log
