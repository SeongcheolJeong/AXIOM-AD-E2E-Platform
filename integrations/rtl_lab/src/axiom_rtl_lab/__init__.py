"""Native reference RTL + independent SimPy comparison; no chip/PHY timing claims."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import time
from ade2e.experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from ade2e.service import canonical_bytes
from .model import reference,scoreboard,compare
from .runner import readiness,identity,compile_fifo,run_process,digest

ADAPTER='verilator-fifo-ab'
FIDELITY='rtl_reference_fifo'

class RtlHandler:
    def __init__(self,database=None):self.database=database
    def readiness(self):return readiness()
    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER,version='0.1.0',kinds=['fpga_rtl'],
          status='available' if shutil.which('verilator') and (shutil.which('clang++') or shutil.which('g++')) else 'not_configured',
          fidelity=FIDELITY,cancellation_supported=True,description='Bundled 32-bit FIFO RTL versus independent SimPy; compilation verified per execution')
    def prepare(self,spec):
        from ade2e.rtl_lab import resolve_test,hydrate_test
        if set(spec.input)!={'test_ref'} or spec.comparison_kind!='single':raise ValueError('RTL Lab requires one immutable test reference')
        if any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):raise ValueError('External code and execution models are unsupported')
        with self.database.read_session() as s:snapshot=hydrate_test(resolve_test(s,spec.project_id,spec.input['test_ref']))
        if spec.seed!=snapshot['test']['seed']:raise ValueError('Seed must match saved revision')
        snapshot['tool_identity']=identity()
        return snapshot
    def execute(self,snapshot,work_dir:Path,should_cancel):
        from ade2e.adapters import AdapterCancelledBeforeDispatch
        from ade2e.rtl_lab_schemas import RtlConfig
        def check():
            if should_cancel():raise AdapterCancelledBeforeDispatch('RTL Lab cancelled')
        check();started=time.perf_counter();test=snapshot['test'];config=RtlConfig.model_validate(test['config']).model_dump();seed=test['seed']
        current=identity()
        if current!=snapshot['tool_identity']:raise ValueError('RTL sources or tools changed after preparation; create a new execution')
        work_dir.mkdir(parents=True,exist_ok=True)
        binary,build,hit=compile_fifo(config['depth'],self.database.experiment_dir/'rtl-build-cache',current,check)
        shutil.copyfile(binary.parent.parent/'compile.log',work_dir/'compile.log')
        expected,stimulus=reference(config,seed,check)
        with (work_dir/'stimulus.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['cycle','reset','in_valid','in_data','out_ready']);w.writeheader();w.writerows(stimulus)
        run_process([str(binary.resolve()),str((work_dir/'stimulus.csv').resolve()),str((work_dir/'rtl-cycles.csv').resolve()),str((work_dir/'fifo.vcd').resolve())],work_dir,work_dir/'run.log',check,30,'simulation')
        with (work_dir/'rtl-cycles.csv').open() as f:actual=[{k:int(v) for k,v in r.items()} for r in csv.DictReader(f)]
        if len(actual)>config['max_cycles']:raise ValueError('RTL trace exceeds configured cycle budget')
        comparison=compare(expected,actual,config);_,ref_summary,ref_transfers=scoreboard(expected,config);_,rtl_summary,rtl_transfers=scoreboard(actual,config)
        provenance=dict(adapter_version='0.1.0',fidelity=FIDELITY,test_ref={k:test[k] for k in ('id','revision','sha256')},
          tool_identity=current,build=build,cache_hit=hit,stimulus_sha256=digest(work_dir/'stimulus.csv'),
          sampling='pre-edge signals, rising edge at cycle*10+5 ns; occupancy_after is post-edge',clock_hz=100000000,data_width=32)
        verdict='timeout' if rtl_summary['timeout'] or ref_summary['timeout'] else 'passed' if comparison['matched'] else 'mismatch'
        summary=dict(**rtl_summary,verdict=verdict,matched=comparison['matched'],first_mismatch=comparison['first_mismatch'],
          mismatch_count=comparison['mismatch_count'],reference_summary=ref_summary,cpu_wall_seconds=time.perf_counter()-started,
          adapter_version='0.1.0',fidelity=FIDELITY,cache_hit=hit,test_ref=provenance['test_ref'])
        comparison.update(schema_version='rtl-comparison/1',verdict=verdict,provenance=provenance)
        documents={'rtl-trace.json':dict(schema_version='rtl-trace/1',engine='verilator',config=config,seed=seed,cycles=actual,summary=rtl_summary,provenance=provenance),
          'simpy-trace.json':dict(schema_version='rtl-trace/1',engine='simpy',config=config,seed=seed,cycles=expected,summary=ref_summary,provenance=provenance),
          'comparison.json':comparison,'summary.json':summary,'build.json':build}
        artifacts=[]
        for name,doc in documents.items():
            path=work_dir/name;path.write_bytes(canonical_bytes(doc));artifacts.append(ExperimentOutputFile(path,name,'application/json'))
        with (work_dir/'transfers.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['engine','index','data','input_cycle','output_cycle','reset_cycle','latency_cycles','status']);w.writeheader()
            for engine,rows in [('simpy',ref_transfers),('verilator',rtl_transfers)]:
                w.writerows(dict(engine=engine,**r) for r in rows)
        for name,mime in [('fifo.vcd','text/plain'),('stimulus.csv','text/csv'),('rtl-cycles.csv','text/csv'),('transfers.csv','text/csv'),('compile.log','text/plain'),('run.log','text/plain')]:
            artifacts.append(ExperimentOutputFile(work_dir/name,name,mime))
        check()
        return ExperimentExecution(summary=summary,artifacts=artifacts,fidelity=FIDELITY)
