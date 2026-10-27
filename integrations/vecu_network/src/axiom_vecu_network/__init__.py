"""Optional native CAN/Ethernet, public BSW and OS execution adapter.

Authoring lives in the core API. Engine imports and deployment remain optional.
No software-reference fallback exists when a registered native engine is missing.
"""
import copy
import json
import subprocess
from pathlib import Path
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.service import canonical_bytes,digest
from axiom_vecu import profile
from axiom_vecu import runtime as process_runtime

VERSION='0.1.0'
PROFILE_ID='actual_silkit_autoas_trampoline'
FIDELITY='native_functional_can_ethernet_bsw_os'


def identity():
    from . import native_runtime,wire,remote
    from axiom_carla import hil_network,hil_policy,hil_dut,city_control,hil_native,hil
    from ade2e import vecu_network_schemas
    modules=[__file__,native_runtime.__file__,wire.__file__,remote.__file__,process_runtime.__file__,
        hil_network.__file__,hil_policy.__file__,hil_dut.__file__,city_control.__file__,hil_native.__file__,hil.__file__,vecu_network_schemas.__file__]
    return {str(Path(path).parent.name)+'/'+Path(path).name:process_runtime.sha(path) for path in modules}


def _injection(test,side,time_ms,reply):
    recorded=reply.get('network',{}).get('injection')
    if isinstance(recorded,dict):return recorded
    faults=[copy.deepcopy(f) for f in test['faults'] if f['start_ms']<=time_ms<f['end_ms']] if side=='faulted' else []
    kinds=[fault['kind'] for fault in faults]
    return dict(active=bool(faults),kind=' + '.join(kinds) if kinds else 'none',kinds=kinds,faults=faults,boundary='virtual_network')


class NetworkHandler:
    def __init__(self,database=None):
        self.db=database
        from axiom_carla.hil import HilExperimentHandler
        self.carla=HilExperimentHandler(database=database)

    def metadata(self):
        return ExperimentHandlerMetadata(id='vecu-network',version=VERSION,kinds=['virtual_ecu'],status='available',
            fidelity=FIDELITY,resource_id=self.carla.metadata().resource_id,cancellation_supported=True,
            description='Registered native SIL Kit frames with public autoas BSW and Trampoline OS. Functional host simulation, not production AUTOSAR or physical bus validation.')

    def catalog(self):
        return dict(profiles=[dict(id=PROFILE_ID,name='SIL Kit / autoas BSW / Trampoline OS',
            fidelity=FIDELITY,license_scope='internal_verification',production_software=False)],
            capabilities=dict(can='classic_11bit_8byte_functional_frames',ethernet='ethernet_ipv4_udp',
                restbus='vehicle_state_and_heartbeat',ecu_roles=['perception','adas','brake','restbus'],
                bsw=['Com','PduR','CanIf'],os=['Task','Alarm','Event','priority_scheduling'],
                physical_bus=False,autosar_full_stack=False,mcal=False,arxml_import=False,someip=False,
                can_fd=False,hardware_hil=False,silver_compatibility=False))

    def readiness(self,profile_id=PROFILE_ID):
        if profile_id!=PROFILE_ID:raise ValueError('Unknown registered network profile')
        try:
            probe=self._probe()
            return dict(available=True,status='available',profile_id=PROFILE_ID,runtime=probe)
        except (OSError,ValueError,RuntimeError,TimeoutError,subprocess.SubprocessError) as exc:
            return dict(available=False,status='not_configured',profile_id=PROFILE_ID,reason=str(exc))

    def _probe(self):
        from . import remote
        result=remote.call(self.carla.transport,'probe')
        if not result.get('available') or not isinstance(result.get('manifest'),dict):
            raise ValueError(result.get('reason','Registered native engines are unavailable'))
        sha=result.get('manifest_sha256')
        if not isinstance(sha,str) or len(sha)!=64 or sha!=digest(result['manifest']):
            raise ValueError('Native engine manifest identity failed')
        return dict(schema_version='vecu-network-installation/1',profile_id=PROFILE_ID,
            manifest_sha256=sha,manifest=result['manifest'])

    def prepare(self,spec):
        from ade2e.vecu_network import resolve
        from ade2e.vecu import resolve as vecu_resolve
        if spec.kind!='virtual_ecu' or set(spec.input)!={'test_ref'} or spec.comparison_kind!='single' or any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):
            raise ValueError('Native network execution requires only a saved network test reference')
        if self.db is None:raise ValueError('Network execution requires its project database')
        with self.db.read_session() as session:
            test=resolve(session,spec.project_id,'vecu-network-test',spec.input['test_ref'])
            network=resolve(session,spec.project_id,'vecu-network-config',test['network_ref'])
            source=vecu_resolve(session,spec.project_id,'vecu-input',test['input_ref']) if test['mode']=='fixed_replay' else None
            if test['mode']=='carla_closed_loop':
                from ade2e.hil_beta import _test
                existing=_test(session,spec.project_id,test['hil_test_ref'])
                if existing['experiment']!=test['hil_experiment']:raise ValueError('Saved HIL scenario differs')
        if spec.seed!=test['seed']:raise ValueError('Network test seed differs')
        installation=self._probe()
        runtimes={}
        for side in ('baseline','faulted'):
            runtime=dict(schema_version='vecu-network-runtime/1',kind='network_reference',profile_id=PROFILE_ID,
                manifest_sha256=installation['manifest_sha256'],network=copy.deepcopy(network),
                faults=copy.deepcopy(test['faults']) if side=='faulted' else [],side=side,
                dut_version='network-ecu/1',protocol='hil-dut/1')
            runtime['runtime_sha256']=digest(runtime);runtimes[side]=runtime
        snapshot=dict(schema_version='vecu-network-execution-input/1',test=test,network=network,source=source,
            runtime=installation,runtimes=runtimes,adapter_sources=identity(),execution_fidelity=FIDELITY)
        if test['mode']=='carla_closed_loop':
            carla=self.carla.prepare(ExperimentCreate.model_validate(test['hil_experiment']))
            parameters=dict(max_object_age_ms=250,recovery_fresh_steps=3)
            carla.update(comparison_kind='package_ab',dut_profiles={side:profile(parameters,spec.project_id) for side in runtimes},
                runtimes=copy.deepcopy(runtimes),fault_enabled=False,side_fault_enabled={'baseline':False,'faulted':False})
            snapshot['carla']=carla;snapshot['execution_fidelity']='actual_carla_native_network_bsw_os'
        return snapshot

    def execute(self,snapshot,work_dir,should_cancel):
        from . import remote
        from axiom_carla.hil_network import NetworkAudit
        if snapshot['adapter_sources']!=identity():raise ValueError('Network adapter sources changed after preview')
        if snapshot['runtime']!=self._probe():raise ValueError('Native engine installation changed after preview')
        work=Path(work_dir);work.mkdir(parents=True,exist_ok=True);test=snapshot['test'];traces={};outputs=[];carla_result=None
        def check():
            if should_cancel():raise AdapterCancelled('Native network cancellation acknowledged')
        check()
        if test['mode']=='carla_closed_loop':
            result=self.carla.execute(snapshot['carla'],work/'carla',should_cancel);carla_result=result.summary;outputs.extend(result.artifacts)
            for side in ('baseline','faulted'):
                path=next(artifact.path for artifact in result.artifacts if artifact.name==side+'-hil-trace.json')
                native=json.loads(path.read_bytes())
                traces[side]=[dict(step=row['sequence'],time_ns=row['sequence']*50000000,request=row['request'],reply=row['reply'],
                    next_ego=row.get('next_ego'),wall_latency_ms=row['ipc_wall_latency_ms'],
                    injection=_injection(test,side,row['sequence']*50,row['reply'])) for row in native['rows']]
        else:
            source=snapshot['source'];parameters=dict(max_object_age_ms=250,recovery_fresh_steps=3)
            common=dict(op='init',schema_version='hil-dut-init/1',route=source['route'],target_speed_mps=source['target_speed_mps'],
                physics={key:source[key] for key in ('wheelbase_m','max_steer_angle_rad')},dut_profile=profile(parameters,test['project_id']))
            for side in ('baseline','faulted'):
                check();selected=snapshot['runtimes'][side];init=common|{'network_runtime':copy.deepcopy(selected)}
                monitor=NetworkAudit(init);rows=[]
                proc=process_runtime.Process(selected,init,work/(side+'-stderr.log'),check,timeout_s=30,
                    command=remote.command(self.carla.transport,'launch'),preamble=selected)
                try:
                    for index,frame in enumerate(source['frames']):
                        check();request=dict(op='step',schema_version='hil-dut-step/1',sequence=index,time_s=index*.05,
                            ego=frame['ego'],object_packet=dict(sequence=index,time_s=index*.05,objects=frame['objects']),object_delivery_present=True)
                        reply,elapsed=proc.exchange(request);monitor.check(request,reply)
                        rows.append(dict(step=index,time_ns=index*50000000,request=request,reply=reply,wall_latency_ms=elapsed,
                            injection=_injection(test,side,index*50,reply)))
                finally:proc.close()
                traces[side]=rows;outputs.append(ExperimentOutputFile(work/(side+'-stderr.log'),side+'-stderr.log','text/plain'))
        check()
        if snapshot['adapter_sources']!=identity():raise ValueError('Network adapter sources changed during execution')
        if snapshot['runtime']!=self._probe():raise ValueError('Native engine installation changed during execution')
        sides={};network_sides={};events={}
        for side,rows in traces.items():
            if not rows:raise ValueError('Native network produced no output')
            logical=[{key:value for key,value in row.items() if key!='wall_latency_ms'} for row in rows]
            states=[row['reply']['state'] for row in rows]
            network_sides[side]=copy.deepcopy(rows[-1]['reply']['network']['stats'])
            events[side]=[event for row in rows for event in row['reply']['network'].get('events',[])]
            sides[side]=dict(steps=len(rows),logical_sha256=digest(logical),input_sha256=digest([row['request'] for row in rows]),
                degraded_steps=states.count('degraded'),recovering_steps=states.count('recovering'),tracking_steps=states.count('tracking'),
                first_degraded_step=next((index for index,state in enumerate(states) if state=='degraded'),None),last_state=states[-1],
                wall_latency_max_ms=max(row['wall_latency_ms'] for row in rows),network=network_sides[side])
        network_summary=dict(schema_version='vecu-network-summary/1',sides=network_sides,
            logical_sha256=digest(events),timing='functional_virtual_1ms; not physical bus timing or CPU WCET',
            manifest_sha256=snapshot['runtime']['manifest_sha256'])
        summary=dict(schema_version='vecu-result/1',mode=test['mode'],comparison='network_fault_ab',classification='native_reference_network_application',
            production_software=False,physical_hardware_connected=False,seed=test['seed'],test_ref={key:test[key] for key in ('id','revision','sha256')},
            network_ref=test['network_ref'],input_ref=test['input_ref'],hil_test_ref=test['hil_test_ref'],period_ns=50000000,sides=sides,
            verdict='passed',checks=dict(protocol='passed',native_network_audit='passed',transport_and_control_readback='passed' if carla_result else 'not_applicable',driving_safety='not_evaluated'),
            runtime_sha256=snapshot['runtime']['manifest_sha256'],adapter_sources=snapshot['adapter_sources'],network=network_summary,
            trace_artifact='vecu-trace.json',network_trace_artifact='network-trace.json',carla_result=carla_result,
            scope='Actual native SIL Kit, public BSW/OS and registered C++ reference controller; functional software contract verified. Not production AUTOSAR, physical bus, or vehicle safety validation.',
            virtual_time_basis='integer 1ms network and 50ms control steps',wall_time_basis='monotonic host IPC duration')
        artifacts=[('vecu-trace.json',dict(schema_version='vecu-trace/1',mode=test['mode'],sides=traces)),
            ('network-trace.json',dict(schema_version='vecu-network-trace/1',sides=events)),('network-summary.json',network_summary),
            ('vecu-summary.json',summary),('vecu-provenance.json',snapshot)]
        for name,value in artifacts:
            path=work/name;path.write_bytes(canonical_bytes(value));outputs.append(ExperimentOutputFile(path,name,'application/json'))
        return ExperimentExecution(summary=summary,artifacts=outputs,fidelity=snapshot['execution_fidelity'])
