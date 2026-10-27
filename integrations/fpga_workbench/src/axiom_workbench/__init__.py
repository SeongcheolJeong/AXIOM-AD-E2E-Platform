"""Workbench optional experiment adapter: graph → actual RTL + independent model."""
import csv
from pathlib import Path
import shutil
import time
from ade2e.experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from ade2e.service import canonical_bytes
from .model import reference,scoreboard,compare,decorate,FIELDS
from .runner import identity,compile_graph,readiness,run_process,digest,SOURCES

ADAPTER='verilator-workbench-ab';FIDELITY='rtl_reference_graph'
class WorkbenchHandler:
    def __init__(self,database=None):self.database=database
    def readiness(self):return readiness()
    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER,version='0.1.0',kinds=['fpga_workbench'],fidelity=FIDELITY,
          status='available' if shutil.which('verilator') and (shutil.which('clang++') or shutil.which('g++')) else 'not_configured',cancellation_supported=True,
          description='Executable bounded FIFO graph: generated RTL versus independent SimPy, not physical circuit timing')
    def prepare(self,spec):
        from ade2e.fpga_workbench import resolve_bench,hydrate_bench
        if set(spec.input)!={'bench_ref'} or spec.comparison_kind!='single':raise ValueError('Workbench requires one immutable bench reference')
        if any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):raise ValueError('External code is unsupported')
        with self.database.read_session() as s:snapshot=hydrate_bench(resolve_bench(s,spec.project_id,spec.input['bench_ref']))
        if spec.seed!=snapshot['bench']['seed']:raise ValueError('Seed must match saved bench')
        snapshot['tool_identity']=identity();return snapshot
    def execute(self,snapshot,work_dir:Path,should_cancel):
        from ade2e.adapters import AdapterCancelledBeforeDispatch
        from ade2e.fpga_workbench import hydrate_bench
        def check():
            if should_cancel():raise AdapterCancelledBeforeDispatch('Workbench execution cancelled')
        check();started=time.perf_counter();bench=snapshot['bench'];compiled=hydrate_bench(bench)['compiled']
        if compiled!=snapshot['compiled']:raise ValueError('Compiled topology differs from frozen graph')
        tools=identity()
        if tools!=snapshot['tool_identity']:raise ValueError('Workbench sources/tools changed after preparation')
        work_dir.mkdir(parents=True,exist_ok=True)
        binary,build,hit,generated=compile_graph(compiled['depths'],self.database.experiment_dir/'workbench-build-cache',tools,check)
        shutil.copyfile(binary.parent.parent/'compile.log',work_dir/'compile.log')
        for name,text in generated.items():(work_dir/name).write_text(text)
        shutil.copyfile(SOURCES/'reference_fifo.sv',work_dir/'reference_fifo.sv')
        expected,stimulus=reference(compiled,bench['seed'],check)
        with (work_dir/'stimulus.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['cycle','reset','in_valid','in_data','out_ready']);w.writeheader();w.writerows(stimulus)
        run_process([str(binary.resolve()),str((work_dir/'stimulus.csv').resolve()),str((work_dir/'rtl-cycles.csv').resolve()),str((work_dir/'workbench.vcd').resolve())],work_dir,work_dir/'run.log',check,30,'simulation')
        actual=[]
        with (work_dir/'rtl-cycles.csv').open() as f:
            for raw in csv.DictReader(f):
                data={k:int(v) for k,v in raw.items()};nodes={node:{k:data.pop(f'f{i}_{k}') for k in FIELDS} for i,node in enumerate(compiled['fifo_order'])}
                actual.append(decorate(dict(**data,nodes=nodes),compiled))
        if len(actual)>compiled['config']['max_cycles']:raise ValueError('Native cycle budget exceeded')
        result=compare(expected,actual,compiled);_,rs,rt=scoreboard(actual,compiled);_,ss,st=scoreboard(expected,compiled)
        verdict='timeout' if rs['timeout'] or ss['timeout'] else 'passed' if result['matched'] else 'mismatch'
        provenance=dict(bench_ref={k:bench[k] for k in ['id','revision','sha256']},execution_hash=compiled['execution_hash'],adapter_version='0.1.0',
          fidelity=FIDELITY,tool_identity=tools,build=build,cache_hit=hit,stimulus_sha256=digest(work_dir/'stimulus.csv'),
          clock_hz=100000000,data_width=32,sampling='pre-edge signals; rising edge cycle*10+5 ns; occupancy_after post-edge')
        summary=dict(**rs,verdict=verdict,matched=result['matched'],first_mismatch=result['first_mismatch'],mismatch_count=result['mismatch_count'],
          cpu_wall_seconds=time.perf_counter()-started,execution_hash=compiled['execution_hash'],bench_ref=provenance['bench_ref'],adapter_version='0.1.0',fidelity=FIDELITY,cache_hit=hit)
        result.update(schema_version='workbench-comparison/1',verdict=verdict,provenance=provenance)
        def trace(engine,cycles,s):return dict(schema_version='workbench-trace/1',engine=engine,config=compiled['config'],seed=bench['seed'],graph=bench['graph'],bench=bench,compiled=compiled,cycles=cycles,summary=s,provenance=provenance)
        docs={'rtl-trace.json':trace('verilator',actual,rs),'simpy-trace.json':trace('simpy',expected,ss),'comparison.json':result,'summary.json':summary,'build.json':build}
        artifacts=[]
        for name,value in docs.items():
            path=work_dir/name;path.write_bytes(canonical_bytes(value));artifacts.append(ExperimentOutputFile(path,name,'application/json'))
        with (work_dir/'transfers.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['engine','index','data','input_cycle','output_cycle','reset_cycle','latency_cycles','status']);w.writeheader()
            for engine,rows in [('verilator',rt),('simpy',st)]:w.writerows(dict(engine=engine,**r) for r in rows)
        for name,mime in [('workbench.vcd','text/plain'),('stimulus.csv','text/csv'),('rtl-cycles.csv','text/csv'),('transfers.csv','text/csv'),('compile.log','text/plain'),('run.log','text/plain'),('generated_top.sv','text/plain'),('harness.cpp','text/plain'),('reference_fifo.sv','text/plain')]:
            artifacts.append(ExperimentOutputFile(work_dir/name,name,mime))
        check();return ExperimentExecution(summary=summary,artifacts=artifacts,fidelity=FIDELITY)
