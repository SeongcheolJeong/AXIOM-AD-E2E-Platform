"""Optional native engineering adapter. Qualcomm never falls back to a reference model."""
import json
from pathlib import Path
import time
from fastapi import HTTPException

from ade2e.experiment_runtime import ExperimentHandlerMetadata, ExperimentExecution, ExperimentOutputFile
from ade2e.service import canonical_bytes
from .runtime import readiness, identity

ADAPTER='native-engineering-lab'
FIDELITY='native_engineering_reference'


class EngineeringHandler:
    def __init__(self,database=None):
        self.database=database

    def readiness(self):
        value=readiness()
        if self.database is None or getattr(self.database,'team_mode',True):
            value['engines']['rtl'].update(available=False,local_operator_enabled=False,
                reason='User RTL is disabled for shared/team workers; use a local trusted-operator workspace')
        value['available']=any(engine['available'] for engine in value['engines'].values())
        return value

    def metadata(self):
        # Discovery is cheap; real executable/version checks occur in readiness/prepare.
        import shutil
        return ExperimentHandlerMetadata(id=ADAPTER,version='0.1.0',kinds=['engineering_lab'],
            status='available' if shutil.which('verilator') or shutil.which('ngspice') else 'not_configured',
            fidelity=FIDELITY,cancellation_supported=True,
            description='Local supplied Stream32 RTL and native ngspice transient experiments; vendor runtime unavailable')

    def prepare(self,spec):
        from ade2e.engineering_lab import resolve_test,hydrate_test
        if set(spec.input)!={'test_ref'} or spec.comparison_kind!='single':
            raise ValueError('Engineering Lab requires an immutable test reference')
        if any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):
            raise ValueError('External executable paths, commands and arbitrary engine models are unsupported')
        with self.database.read_session() as session:
            snapshot=hydrate_test(resolve_test(session,spec.project_id,spec.input['test_ref']))
        test=snapshot['test'];engine=test['config']['engine']
        if engine in {'isp_rtl', 'adas_module_rtl', 'adas_validation_campaign', 'adas_preboard_checks'}:
            probe = self.readiness()['engines'][engine]
            if not probe['available']:
                raise ValueError(probe['reason'])
            try:
                from axiom_isp_rtl.handler import prepare_simulation
            except ImportError as exc:
                raise ValueError('Open ISP optional adapter is unavailable') from exc
            return prepare_simulation(self.database, spec)
        if test['seed']!=spec.seed:raise ValueError('Seed must match the saved revision')
        probe=self.readiness()['engines'][engine]
        if not probe['available']:raise ValueError(probe['reason'])
        snapshot['tool_identity']=identity(engine)
        return snapshot

    def execute(self,snapshot,work_dir:Path,should_cancel):
        if snapshot['test']['config']['engine'] in {'isp_rtl', 'adas_module_rtl', 'adas_validation_campaign', 'adas_preboard_checks'}:
            try:
                from axiom_isp_rtl.handler import execute_simulation
            except ImportError as exc:
                raise HTTPException(422, 'Open ISP optional adapter is unavailable on this worker') from exc
            return execute_simulation(self.database, snapshot, work_dir, should_cancel)
        from ade2e.adapters import AdapterCancelled
        from ade2e.engineering_lab import hydrate_test,source_identity
        def check():
            if should_cancel():raise AdapterCancelled('Engineering Lab acknowledged cancellation')
        check();started=time.perf_counter();test=hydrate_test(snapshot['test'])['test'];config=test['config'];engine=config['engine']
        ready=self.readiness()['engines'][engine]
        if not ready['available']:raise HTTPException(422, ready['reason'])
        current=identity(engine)
        if current!=snapshot['tool_identity']:raise HTTPException(422, 'Tool or adapter source identity changed after preparation; create a new execution')
        work_dir.mkdir(parents=True,exist_ok=True)
        try:
            if engine=='rtl':
                from . import rtl
                result=rtl.run(config,work_dir,check,current,test['seed'])
            elif engine=='spice':
                from . import spice
                result=spice.run(config,work_dir,check,current)
            else:
                raise ValueError('Qualcomm runtime is not configured; CPU reference fallback is forbidden')
        except AdapterCancelled:
            raise
        except (RuntimeError, ValueError) as error:
            # Known native process/parser failures are actionable source diagnostics.
            # Keep the job failed and expose a bounded plain-text message through the
            # normal worker contract; cancellation exceptions pass through unchanged.
            detail=''.join(c for c in str(error) if c in '\n\t' or ord(c)>=32)[-6000:]
            raise HTTPException(422, f'{engine} execution failed: {detail}') from error
        provenance=dict(adapter_id=ADAPTER,adapter_version='0.1.0',fidelity=FIDELITY,
            test_ref={k:test[k] for k in ('id','revision','sha256')},seed=test['seed'],
            **source_identity(config),tool_identity=current,
            simulation_scope='two_state_stream32_rtl' if engine=='rtl' else 'analog_device_model_transient',
            clock_hz=100000000 if engine=='rtl' else None,
            sampling='pre-edge signals, rising edge at cycle*10+5ns' if engine=='rtl' else 'native adaptive time points; no resampling')
        summary=result['summary']|dict(cpu_wall_seconds=time.perf_counter()-started,engine=engine,
                    fidelity=FIDELITY,adapter_version='0.1.0',test_ref=provenance['test_ref'],config_hash=provenance['config_hash'])
        trace=dict(schema_version='engineering-trace/1',engine='verilator' if engine=='rtl' else 'ngspice',
                   axis=result['axis'],signals=result['signals'],samples=result['samples'],summary=summary,
                   provenance=provenance,test=test)
        for name,value in [('trace.json',trace),('summary.json',summary),('provenance.json',provenance),('test.json',test)]:
            (work_dir/name).write_bytes(canonical_bytes(value))
        artifacts=[]
        for path in sorted(work_dir.iterdir()):
            if not path.is_file() or path.is_symlink():continue
            mime='application/json' if path.suffix=='.json' else 'text/csv' if path.suffix=='.csv' else 'text/plain'
            artifacts.append(ExperimentOutputFile(path,path.name,mime))
        check()
        return ExperimentExecution(summary=summary,artifacts=artifacts,fidelity=FIDELITY)
