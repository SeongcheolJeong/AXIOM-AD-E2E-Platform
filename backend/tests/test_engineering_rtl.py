"""Real local Verilator execution of supplied RTL and independent transfer contracts."""
import copy
from pathlib import Path
import pytest
from axiom_engineering.rtl import run, scoreboard
from axiom_engineering.runtime import identity, readiness
from ade2e.engineering_lab import templates_payload
from axiom_rtl_lab.model import input_word

@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setenv('ADE2E_ENABLE_LOCAL_RTL','1');monkeypatch.setenv('ADE2E_TEAM_MODE','0')
    if not readiness()['engines']['rtl']['available']:pytest.skip('Native Verilator required')
    return identity('rtl')


def config(preset='passthrough',**changes):
    data=next(t['config'] for t in templates_payload()['items'] if t['id']==preset)
    return copy.deepcopy(data)|dict(stalls=[])|changes


def execute(c,tmp_path,tools):
    return run(c,tmp_path,lambda:None,tools,42)

@pytest.mark.parametrize('preset',['passthrough','xor-transform'])
def test_native_supplied_source_contract_and_backpressure(preset,tmp_path,tools):
    c=config(preset,words=64,max_cycles=256,stalls=[dict(start_cycle=16,end_cycle=96)])
    result=execute(c,tmp_path,tools);s=result['summary'];rows=result['samples']
    assert s['verdict']=='passed' and s['input_transfers']==s['output_transfers']==64
    assert s['mismatch_count']==s['protocol_error_count']==0
    assert [r['in_data'] for r in rows if r['accepted_in']]==[input_word(i,42) for i in range(64)]
    mask=c['xor_mask'] if c['expectation']=='xor' else 0
    assert [r['out_data'] for r in rows if r['accepted_out']]==[input_word(i,42)^mask for i in range(64)]
    assert len(rows)==256  # includes tail, not an early successful stop
    assert all(rows[i]['in_data']==rows[16]['in_data'] for i in range(16,96))
    assert (tmp_path/'waveform.vcd').stat().st_size>0


def test_buggy_transform_localizes_actual_output(tmp_path,tools):
    result=execute(config('buggy-xor',words=16,max_cycles=64),tmp_path,tools)
    s=result['summary'];assert s['verdict']=='failed' and s['mismatch_count']==16
    assert s['first_mismatch']['cycle']==2 and s['first_mismatch']['signal']=='out_data'
    assert s['first_mismatch']['expected']!=s['first_mismatch']['actual']

REGISTER='''module user_top(input logic clk,reset,in_valid,out_ready,input logic[31:0] in_data,
 output logic in_ready,out_valid,output logic[31:0] out_data);
 assign in_ready=!out_valid || out_ready;
 always_ff @(posedge clk) begin
  if(reset)begin out_valid<=0;out_data<=0;end
  else if(in_ready)begin out_valid<=in_valid;if(in_valid)out_data<=in_data;end
 end
endmodule
'''

def test_registered_rtl_reset_tracks_loss_and_recovery(tmp_path,tools):
    c=config(words=64,max_cycles=256,sources=[dict(name='register.sv',content=REGISTER)],
             stalls=[dict(start_cycle=16,end_cycle=96)],resets=[0,1,40])
    result=execute(c,tmp_path,tools);s=result['summary']
    assert s['verdict']=='passed' and s['reset_discarded']==1
    assert s['input_transfers']==64 and s['output_transfers']==63 and s['max_latency_cycles']>1
    assert s['pending_words']==0


def test_multiple_source_files_are_compiled_and_hashed(tmp_path,tools):
    c=config(words=8,max_cycles=32)
    c['sources'][0]['content']=c['sources'][0]['content'].replace('assign out_data = in_data;', 'helper h(.a(in_data),.b(out_data));')
    c['sources'].append(dict(name='helper.v',content='module helper(input [31:0] a, output [31:0] b); assign b=a; endmodule'))
    assert execute(c,tmp_path,tools)['summary']['verdict']=='passed'
    assert (tmp_path/'source_helper.v').is_file()


def test_late_spurious_output_is_not_hidden_by_early_drain(tmp_path,tools):
    c=config(words=4,max_cycles=32)
    c['sources'][0]['content']=c['sources'][0]['content'].replace('assign out_valid = in_valid && !reset;',
        'logic [7:0] count; always_ff @(posedge clk) if(reset)count<=0;else count<=count+1;\nassign out_valid = (in_valid || count==10) && !reset;')
    s=execute(c,tmp_path,tools)['summary']
    assert s['verdict']=='failed' and s['protocol_error_count']==1 and s['output_transfers']==5
    assert s['first_mismatch']['signal']=='unexpected_output'


def test_observation_is_not_algorithm_validation(tmp_path,tools):
    s=execute(config('buggy-xor',words=8,max_cycles=32,expectation='observe'),tmp_path,tools)['summary']
    assert s['verdict']=='observed' and not s['data_checked']


def test_incomplete_transfer_contract_is_timeout(tmp_path,tools):
    s=execute(config(words=64,max_cycles=16),tmp_path,tools)['summary']
    assert s['verdict']=='timeout' and s['unsubmitted_words']==50

@pytest.mark.parametrize('mutation',[lambda s:s.replace('endmodule','syntax ??? endmodule'),lambda s:s.replace('input logic [31:0] in_data','input logic [15:0] in_data')])
def test_native_compiler_rejects_bad_source_and_incompatible_ports(mutation,tmp_path,tools):
    c=config();c['sources'][0]['content']=mutation(c['sources'][0]['content'])
    with pytest.raises(RuntimeError,match='compile_failed'):execute(c,tmp_path,tools)
    assert (tmp_path/'compile.log').is_file()


def test_shared_or_nonoptin_host_disables_rtl(monkeypatch):
    monkeypatch.delenv('ADE2E_ENABLE_LOCAL_RTL',raising=False)
    assert not readiness()['engines']['rtl']['available']
    monkeypatch.setenv('ADE2E_ENABLE_LOCAL_RTL','1')
    for value in ['1','true','YES']:
        monkeypatch.setenv('ADE2E_TEAM_MODE',value)
        assert not readiness()['engines']['rtl']['available']
