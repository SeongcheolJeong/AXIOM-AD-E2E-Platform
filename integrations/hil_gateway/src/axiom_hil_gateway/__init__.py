"""Optional native SocketCAN/UDP adapter on the existing immutable execution lifecycle."""
import copy,hashlib,json,os,shlex,subprocess
from pathlib import Path
from ade2e.experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.adapters import AdapterCancelled
from ade2e.service import canonical_bytes,digest
from ade2e.hil_bridge_schemas import get_profile

VERSION='0.4.0'

def identity():
    from . import runtime,socketcan,remote,devices,evidence,codecs,process,hooks,model_monitor
    from axiom_carla import hil_gateway
    from . import carla_native as hil_native, carla_handler as hil,policy
    return {Path(p).name:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
        [__file__,runtime.__file__,socketcan.__file__,remote.__file__,devices.__file__,process.__file__,codecs.__file__,evidence.__file__,hooks.__file__,model_monitor.__file__,hil_gateway.__file__,hil_native.__file__,hil.__file__,policy.__file__]}

class GatewayHandler:
    def __init__(self,database=None):
        self.db=database
        from .carla_handler import HilExperimentHandler
        self.carla=HilExperimentHandler(database=database)
        directory=os.environ.get('AXIOM_HIL_GATEWAY_DIR','')
        self.configured=bool(directory)
        if directory:self.carla.transport.directory=directory;self.carla.transport.script=directory+'/hil_native.py'
    def metadata(self):
        return ExperimentHandlerMetadata(id='hil-gateway',version=VERSION,kinds=['virtual_ecu'],status='available' if self.configured else 'not_configured',
            fidelity='socketcan_udp_functional_bench',resource_id=self.carla.metadata().resource_id,
            cancellation_supported=True,description='Two ECU SocketCAN/UDP bridge. vcan evidence is not physical HIL validation.')
    def command(self,operation,target):
        t=self.carla.transport
        if not self.configured or not t.target or not t.python:raise ValueError('Registered Linux HIL gateway is not configured')
        return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',t.target,shlex.join([t.python,t.directory+'/bridge_remote.py',operation,target])]
    def probe(self,profile_id):
        profile=get_profile(profile_id)
        if not profile:raise ValueError('Unknown HIL profile')
        result=subprocess.run(self.command('probe',profile['target']),capture_output=True,timeout=20)
        if result.returncode:raise ValueError(result.stderr.decode(errors='replace')[-1800:])
        probe=json.loads(result.stdout)
        if probe.get('device',{}).get('target')!=profile['target'] or not probe.get('available'):raise ValueError('Bench identity/readiness failed')
        return probe
    def readiness(self,profile_id):
        try:return self.probe(profile_id)|{'status':'available','profile_id':profile_id}
        except (ValueError,OSError,subprocess.SubprocessError) as exc:return dict(available=False,status='not_configured',profile_id=profile_id,reason=str(exc))
    def prepare(self,spec):
        from ade2e.hil_bridge import resolve,source_snapshot
        if set(spec.input)!={'binding_ref'} or spec.kind!='virtual_ecu' or spec.comparison_kind!='single' or any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):
            raise ValueError('HIL gateway requires only an immutable binding reference')
        with self.db.read_session() as session:
            binding=resolve(session,spec.project_id,spec.input['binding_ref'])
            if source_snapshot(session,spec.project_id,binding['source_kind'],binding['source_ref'])!=binding['source']:raise ValueError('SIL source changed')
            coupling=None
            if binding.get('model_coupling') or binding.get('model_couplings'):
                from ade2e.hil_model_coupling import compile_coupling,compile_model_set
                coupling=(compile_model_set(self.db,session,spec.project_id,binding['model_couplings'],binding['source']['duration_ms']) if binding.get('model_couplings') else
                    compile_coupling(self.db,session,spec.project_id,binding['model_coupling'],binding['source']['duration_ms']))
                if coupling['summary']!=binding['model_link']:raise ValueError('Native model evidence changed after binding save')
        if spec.seed!=binding['source']['seed']:raise ValueError('Seed differs from SIL test')
        if any(not f['supported'] for f in binding['injections']):raise ValueError('ECU reset/OS event injection requires a registered device driver; no software substitution')
        if binding['profile']!=get_profile(binding['profile_id']):raise ValueError('Profile changed; save a new binding revision')
        installation=self.probe(binding['profile_id']);runtimes={}
        for side in ('baseline','faulted'):
            runtime=dict(schema_version='hil-gateway-runtime/1',kind='hil_gateway',protocol='hil-dut/1',dut_version='hil-gateway/1',
                installation_sha256=installation['installation_sha256'],device=installation['device'],
                max_lateness_ms=binding['max_lateness_ms'],faults=copy.deepcopy(binding['injections']) if side=='faulted' else [],side=side)
            if coupling:
                runtime['model_input']=dict(samples=coupling['samples'][side],policy=coupling['policy'],source=coupling['summary']['runs'][side],
                    coupling_sha256=coupling['summary']['sha256'])
            runtimes[side]=runtime|{'runtime_sha256':digest(runtime)}
        snapshot=dict(schema_version='hil-bridge-execution-input/1',binding=binding,source=binding['source'],
            installation=installation,runtimes=runtimes,adapter_sources=identity())
        if binding['source']['mode']=='carla_closed_loop':
            from axiom_vecu import profile
            carla=self.carla.prepare(ExperimentCreate.model_validate(binding['source']['carla_experiment']))
            carla.update(comparison_kind='package_ab',dut_profiles={side:profile(dict(max_object_age_ms=250,recovery_fresh_steps=3),spec.project_id) for side in runtimes},
                runtimes=runtimes,fault_enabled=False,side_fault_enabled={'baseline':False,'faulted':False})
            snapshot['carla']=carla
        return snapshot
    def execute(self,snapshot,work_dir,should_cancel):
        try:return self._execute(snapshot,work_dir,should_cancel)
        except BaseException as exc:
            # The common worker retains these bounded files on failed/cancelled runs.
            # They are diagnostics, never replacement ECU output or success evidence.
            work=Path(work_dir);work.mkdir(parents=True,exist_ok=True)
            sources=sorted(p for p in work.rglob('*') if p.is_file() and not p.is_symlink()
                and (p.name.endswith('stderr.log') or p.name=='external-dispatch.json'))[:12]
            for index,path in enumerate(sources):
                if path.stat().st_size<=1024*1024:
                    suffix='external-dispatch.json' if path.name.endswith('.json') else 'native-stderr.log'
                    (work/f'gateway-{index}-{suffix}').write_bytes(path.read_bytes())
            (work/'hil-native-diagnostics.json').write_bytes(canonical_bytes(dict(
                schema_version='hil-bridge-failure/1',error=str(exc),exception=type(exc).__name__,
                physical_validation=False,cleanup='requires separate process/bus observation',
                installation_sha256=snapshot.get('installation',{}).get('installation_sha256'))))
            raise
    def _execute(self,snapshot,work_dir,should_cancel):
        from .process import Process
        from axiom_carla.hil_gateway import Audit
        from .evidence import derive
        if snapshot['adapter_sources']!=identity():raise ValueError('Gateway adapter changed after preview')
        if self.probe(snapshot['binding']['profile_id'])!=snapshot['installation']:raise ValueError('Gateway devices changed after preview')
        work=Path(work_dir);work.mkdir(parents=True,exist_ok=True);source=snapshot['source'];rows={};outputs=[];carla=None
        def check():
            if should_cancel():raise AdapterCancelled('HIL gateway cancellation requested; transport/children released')
        if source['mode']=='carla_closed_loop':
            result=self.carla.execute(snapshot['carla'],work/'carla',should_cancel);carla=result.summary;outputs+=result.artifacts
            for side in ('baseline','faulted'):
                path=next(a.path for a in result.artifacts if a.name==side+'-hil-trace.json')
                trace=json.loads(path.read_bytes())
                rows[side]=[dict(step=r['sequence'],time_ns=r['sequence']*50_000_000,request=r['request'],reply=r['reply'],
                    next_ego=r['next_ego'],vehicle_observed_monotonic_ns=r.get('vehicle_observed_monotonic_ns')) for r in trace['rows']]
        else:
            from axiom_vecu import profile
            inp=source['source'];init=dict(op='init',schema_version='hil-dut-init/1',route=inp['route'],target_speed_mps=inp['target_speed_mps'],
                physics={k:inp[k] for k in ('wheelbase_m','max_steer_angle_rad')},dut_profile=profile(dict(max_object_age_ms=250,recovery_fresh_steps=3),snapshot['binding']['project_id']),replay_frames=inp['frames'])
            for side in ('baseline','faulted'):
                check();runtime=snapshot['runtimes'][side];audit=Audit({'gateway_runtime':runtime});rows[side]=[]
                proc=Process(runtime,init,work/(side+'-native-stderr.log'),check,timeout_s=10,command=self.command('launch',runtime['device']['target']),preamble=runtime)
                try:
                    for i,frame in enumerate(inp['frames']):
                        check();request=dict(op='step',schema_version='hil-dut-step/1',sequence=i,time_s=i*.05,ego=frame['ego'],
                            object_packet=dict(sequence=i,time_s=i*.05,objects=frame['objects']),object_delivery_present=True)
                        reply,_=proc.exchange(request);audit.check(request,reply)
                        rows[side].append(dict(step=i,time_ns=i*50_000_000,request=request,reply=reply))
                finally:proc.close()
                outputs.append(ExperimentOutputFile(work/(side+'-native-stderr.log'),side+'-native-stderr.log','text/plain'))
        check();report=derive(rows,snapshot);errors={side:records[-1]['reply']['bridge']['validity_errors'] for side,records in rows.items()}
        target=snapshot['binding']['profile']['target']
        summary=dict(schema_version='hil-bridge-result/1',mode=source['mode'],target=target,comparison='normal_fault_ab',
            binding_ref={k:snapshot['binding'][k] for k in ('id','revision','sha256')},source_ref=snapshot['binding']['source_ref'],
            seed=source['seed'],verdict='timing_invalid' if any(errors.values()) else 'passed',validity_errors=errors,
            physical_hardware_connected=target=='hardware',physical_validation=False,hard_realtime=False,
            scope='SocketCAN/UDP connection and reference-process ECU behavior; not physical ECU validation' if target=='vcan' else 'Registered physical ECU I/O observation; driving safety not evaluated',
            carla_result=carla,clock_basis='Linux monotonic wall clock; separate CARLA simulation time',
            adapter_sources=identity(),installation_sha256=snapshot['installation']['installation_sha256'],
            sides={side:{'steps':len(records),'trace_sha256':digest(records)} for side,records in rows.items()})
        summary['model_link']=snapshot['binding'].get('model_link')
        for name,value in [('vecu-trace.json',dict(schema_version='vecu-trace/1',mode=source['mode'],sides=rows)),
            ('hil-causality.json',report),('hil-bridge-provenance.json',snapshot),('hil-bridge-summary.json',summary)]:
            path=work/name;path.write_bytes(canonical_bytes(value));outputs.append(ExperimentOutputFile(path,name,'application/json'))
        return ExperimentExecution(summary=summary,artifacts=outputs,fidelity='actual_carla_socketcan_vcan_reference' if carla and target=='vcan' else 'socketcan_'+target+'_functional_bench')
