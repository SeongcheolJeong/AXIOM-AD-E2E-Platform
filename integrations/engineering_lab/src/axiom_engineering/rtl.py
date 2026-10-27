"""Fixed Stream32 harness for locally supplied synthesizable SystemVerilog.

The selected transfer contract is an oracle, not an inferred model of arbitrary RTL.
"""
from collections import deque
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from axiom_rtl_lab.runner import run_process
from .runtime import clean_env, digest

SIGNALS = ['reset', 'in_valid', 'in_ready', 'in_data', 'out_valid', 'out_ready', 'out_data', 'accepted_in', 'accepted_out']


def generate(config, seed):
    wrapper = '''`timescale 1ns/1ns
module axiom_engineering_top (
 input logic clk, reset, in_valid, out_ready,
 input logic [31:0] in_data,
 output logic in_ready, out_valid,
 output logic [31:0] out_data
);
 user_top dut(.clk(clk), .reset(reset), .in_valid(in_valid), .in_ready(in_ready),
 .in_data(in_data), .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data));
endmodule
'''
    resets = ' || '.join(f'cycle=={c}u' for c in config['resets'])
    stalls = ' || '.join(f'(cycle>={s["start_cycle"]}u && cycle<{s["end_cycle"]}u)' for s in config['stalls']) or 'false'
    harness = r'''#include "Vaxiom_engineering_top.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
#include <fstream>
#include <memory>
#include <cstdint>
int main(int argc, char** argv) {
 if(argc!=3) return 2;
 auto ctx=std::make_unique<VerilatedContext>(); ctx->commandArgs(argc,argv); ctx->traceEverOn(true);
 auto top=std::make_unique<Vaxiom_engineering_top>(ctx.get());
 auto vcd=std::make_unique<VerilatedVcdC>(); top->trace(vcd.get(),6); vcd->open(argv[2]);
 std::ofstream out(argv[1]); if(!out || !vcd->isOpen()) return 3;
 out << "cycle,time,reset,in_valid,in_ready,in_data,out_valid,out_ready,out_data,accepted_in,accepted_out\n";
 uint32_t sent=0, received=0, discarded=0; int64_t pending=0;
 for(uint32_t cycle=0;cycle<MAX_CYCLESu;++cycle) {
  bool reset=(RESETS), valid=!reset && sent<WORDS_COUNTu, ready=!(STALLS);
  uint32_t data=valid ? uint32_t((sent ^ SEEDu)*2654435761u) : 0u;
  ctx->time(cycle*10ULL); top->clk=0; top->reset=reset; top->in_valid=valid;
  top->in_data=data; top->out_ready=ready; top->eval(); vcd->dump(ctx->time());
  uint32_t ir=top->in_ready, ov=top->out_valid, od=top->out_data;
  uint32_t ai=!reset && valid && ir, ao=!reset && ov && ready;
  ctx->time(cycle*10ULL+5); top->clk=1; top->eval(); vcd->dump(ctx->time());
  out<<cycle<<','<<ctx->time()<<','<<reset<<','<<valid<<','<<ir<<','<<data<<','<<ov<<','<<ready<<','<<od<<','<<ai<<','<<ao<<'\n';
  if(reset){discarded+=pending>0?pending:0;pending=0;}
  else{sent+=ai;received+=ao;pending+=int64_t(ai)-int64_t(ao);}
  if(ctx->gotFinish())return 4;
  // Observe the entire configured window to catch outputs after apparent drain.
 }
 top->final(); vcd->close(); return out.good()?0:5;
}
'''
    for key,value in dict(MAX_CYCLES=config['max_cycles'],WORDS_COUNT=config['words'],RESETS=resets,STALLS=stalls,SEED=seed).items():
        harness=harness.replace(key,str(value))
    return wrapper,harness


def scoreboard(rows, config):
    pending=deque(); records=[]; errors=[]; mismatches=0; protocols=0; sent=received=discarded=0; latencies=[]
    previous=None
    def error(row,signal,expected,actual,protocol=False):
        nonlocal mismatches,protocols
        if protocol:protocols+=1
        else:mismatches+=1
        if len(errors)<100:errors.append(dict(cycle=row['cycle'],time=row['time'],signal=signal,expected=expected,actual=actual))
    for row in rows:
        if row['reset']:
            for item in pending:
                records.append(dict(**item,output_cycle=None,output_data=None,status='reset_discarded',reset_cycle=row['cycle'],latency_cycles=None))
            discarded+=len(pending);pending.clear();previous=row;continue
        if previous and not previous['reset'] and previous['out_valid'] and not previous['out_ready']:
            if not row['out_valid'] or previous['out_data']!=row['out_data']:
                error(row,'output_stability',previous['out_data'],row['out_data'],True)
        if row['accepted_in']:
            expected=row['in_data'] ^ (config['xor_mask'] if config['expectation']=='xor' else 0)
            pending.append(dict(index=sent,input_data=row['in_data'],expected_data=expected,input_cycle=row['cycle']))
            sent+=1
        if row['accepted_out']:
            received+=1
            if not pending:
                error(row,'unexpected_output',None,row['out_data'],True)
            else:
                item=pending.popleft();latency=row['cycle']-item['input_cycle'];latencies.append(latency)
                if config['expectation']!='observe' and row['out_data']!=item['expected_data']:
                    error(row,'out_data',item['expected_data'],row['out_data'])
                records.append(dict(**item,output_cycle=row['cycle'],output_data=row['out_data'],status='delivered',reset_cycle=None,latency_cycles=latency))
        previous=row
    for item in pending:
        records.append(dict(**item,output_cycle=None,output_data=None,status='pending',reset_cycle=None,latency_cycles=None))
    incomplete=bool(pending) or sent<config['words']
    verdict='failed' if mismatches or protocols else 'observed' if config['expectation']=='observe' else 'timeout' if incomplete else 'passed'
    summary=dict(verdict=verdict,input_transfers=sent,output_transfers=received,reset_discarded=discarded,
                 pending_words=len(pending),unsubmitted_words=config['words']-sent,mismatch_count=mismatches,
                 protocol_error_count=protocols,first_mismatch=errors[0] if errors else None,
                 max_latency_cycles=max(latencies,default=0),cycles=len(rows),
                 expectation=config['expectation'],data_checked=config['expectation']!='observe',
                 data_loss_free=not incomplete and not protocols and sent==received+discarded,
                 validation_scope='Selected Stream32 transfer contract only; no inferred RTL equivalence or hardware timing',
                 errors=errors)
    return summary,records


def run(config, work_dir, check, tool_identity, seed=42):
    from ade2e.engineering_lab_schemas import RtlEngineeringConfig
    config=RtlEngineeringConfig.model_validate(config).model_dump()
    work_dir=Path(work_dir);work_dir.mkdir(parents=True,exist_ok=True)
    wrapper,harness=generate(config,seed)
    (work_dir/'wrapper.sv').write_text(wrapper);(work_dir/'harness.cpp').write_text(harness)
    # GNU Make cannot compile from workspace paths containing spaces. Private build only.
    stage=Path(tempfile.mkdtemp(prefix='axiom-user-rtl-',dir='/tmp'))
    def bounded_check():
        check()
        for path in [stage/'compile.log',work_dir/'native.csv',work_dir/'waveform.vcd',work_dir/'run.log']:
            if path.exists() and path.stat().st_size>128*1024*1024:
                raise ValueError('Native compilation or simulation output exceeds 128 MiB budget')
    try:
        (stage/'src').mkdir()
        for source in config['sources']:
            (stage/'src'/source['name']).write_text(source['content'])
            (work_dir/('source_'+source['name'])).write_text(source['content'])
        (stage/'wrapper.sv').write_text(wrapper);(stage/'harness.cpp').write_text(harness)
        args=[tool_identity['tools']['verilator_path'],'--cc','--exe','--build','--trace','--assert',
              '--top-module','axiom_engineering_top','--prefix','Vaxiom_engineering_top','-j','2',
              '--x-initial','0','--x-assign','0','-MAKEFLAGS','CXX='+tool_identity['tools']['compiler_path'],
              '--Mdir','obj','wrapper.sv',*[str(Path('src')/s['name']) for s in config['sources']],'harness.cpp']
        try:
            run_process(args,stage,stage/'compile.log',bounded_check,120,'compile',clean_env())
        finally:
            if (stage/'compile.log').exists():shutil.copyfile(stage/'compile.log',work_dir/'compile.log')
        binary=stage/'obj'/'Vaxiom_engineering_top'
        (work_dir/'build.json').write_text(json.dumps(dict(command=args,binary_sha256=digest(binary),tool_identity=tool_identity,
            wrapper_sha256=digest(stage/'wrapper.sv'),harness_sha256=digest(stage/'harness.cpp'),
            source_sha256={s['name']:digest(stage/'src'/s['name']) for s in config['sources']},two_state=True),indent=2))
        run_process([str(binary),str((work_dir/'native.csv').resolve()),str((work_dir/'waveform.vcd').resolve())],
                    stage,work_dir/'run.log',bounded_check,30,'simulation',clean_env())
        if (work_dir/'native.csv').stat().st_size>32*1024*1024 or (work_dir/'waveform.vcd').stat().st_size>128*1024*1024:
            raise ValueError('Native result exceeds bounded output size')
        with (work_dir/'native.csv').open() as f:rows=[{k:int(v) for k,v in row.items()} for row in csv.DictReader(f)]
        if not rows or len(rows)>config['max_cycles']:raise ValueError('Native trace is empty or exceeds the cycle budget')
        if any(row['cycle']!=i or row['time']!=i*10+5 for i,row in enumerate(rows)):raise ValueError('Native trace clock identity differs')
        summary,transfers=scoreboard(rows,config)
        fields=['index','input_data','expected_data','input_cycle','output_cycle','output_data','status','reset_cycle','latency_cycles']
        with (work_dir/'transfers.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(transfers)
        return dict(axis=dict(unit='ns',label='Virtual clock time'),
                    signals=[dict(id=s,unit='word' if s.endswith('data') else 'bit') for s in SIGNALS],samples=rows,summary=summary)
    finally:
        shutil.rmtree(stage)
