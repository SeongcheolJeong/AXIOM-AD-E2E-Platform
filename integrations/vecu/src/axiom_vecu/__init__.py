"""Optional vECU adapter: deterministic fixed replay and explicit CARLA delegation."""
import copy
import json
import math
from pathlib import Path
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.service import canonical_bytes,digest
from . import runtime,builds

VERSION='0.2.0'
FIDELITY='host_reference_vecu_replay'

def identity():
    from axiom_carla import hil_policy,hil_dut,city_control,hil_native,vecu_remote
    return {p.name:runtime.sha(p) for p in [Path(__file__),Path(runtime.__file__),Path(builds.__file__),Path(hil_policy.__file__),Path(hil_dut.__file__),Path(city_control.__file__),Path(hil_native.__file__),Path(vecu_remote.__file__)]}

def profile(params,project):
    value=dict(schema_version='hil-dut-profile/1',project_id=project,id='vecu_profile_'+digest(params)[:24],
        revision=1,name='Frozen vECU profile',note='Reference application',parameters=params,
        created_at='frozen-in-vecu-test',author=dict(user_label='vecu-runtime',role='Engineer'))
    return value|{'sha256':digest(value)}

class Oracle:
    """Independent contract monitor; does not reimplement traffic control."""
    def __init__(self,params):self.params=params;self.degraded=False;self.fresh=0;self.last=-1
    def check(self,request,reply):
        p=request['object_packet'];age=request['time_s']-p['time_s']
        new=p['sequence']>self.last;valid=age<=self.params['max_object_age_ms']/1000+1e-8
        if not valid:self.degraded=True;self.fresh=0
        elif self.degraded:
            self.fresh=self.fresh+1 if new else 0
            if self.fresh>=self.params['recovery_fresh_steps']:self.degraded=False
        expected='degraded' if not valid else 'recovering' if self.degraded else 'tracking'
        control=reply.get('control',{})
        if (reply.get('object_sequence')!=p['sequence'] or reply.get('state')!=expected
            or not isinstance(reply.get('object_age_s'),(int,float)) or not math.isfinite(reply['object_age_s'])
            or abs(reply['object_age_s']-age)>1e-7 or reply.get('fresh_recovery_steps')!=self.fresh):
            raise ValueError('Independent freshness/recovery contract mismatch')
        if set(control)!={'throttle','brake','steer'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in control.values()):
            raise ValueError('Invalid control output')
        if not 0<=control['throttle']<=1 or not 0<=control['brake']<=1 or not -1<=control['steer']<=1:raise ValueError('Control output out of range')
        if self.degraded and (control['throttle']!=0 or control['brake']!=1):raise ValueError('Degraded reference policy was not applied')
        self.last=p['sequence']

class VecuHandler:
    def __init__(self,database=None):
        self.db=database
        from axiom_carla.hil import HilExperimentHandler
        self.carla=HilExperimentHandler(database=database)
    def catalog(self):return runtime.catalog()
    def build_sources(self):return builds.SOURCES
    def built_runtime(self,build_execution_id,project_id,session=None):
        return builds.built_runtime(self.db,build_execution_id,project_id,session)
    def check_package(self,package,project):
        if package.get('build_execution_id'):
            current=self.built_runtime(package['build_execution_id'],project)
            if current!=package['runtime']:raise ValueError('Frozen build package differs')
            builds.call(self.carla.transport,'check',current,timeout=15)
        elif self.catalog().get(package['runtime_id'])!=package['runtime']:
            raise ValueError('Registered software changed; save a new package revision')
    def metadata(self):
        return ExperimentHandlerMetadata(id='host-vecu',version=VERSION,kinds=['virtual_ecu'],status='available',
            fidelity=FIDELITY,resource_id=self.carla.metadata().resource_id,cancellation_supported=True,
            description='Reference application in registered local process. Fixed replay or existing CARLA closed loop. No production/physical ECU validation.')
    def prepare(self,spec):
        from ade2e.vecu import resolve
        if spec.kind=='virtual_ecu' and set(spec.input)=={'build_source_id'}:
            if spec.comparison_kind!='single' or any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):
                raise ValueError('Build accepts only a registered source')
            return builds.prepare(self.carla.transport,spec.input['build_source_id'])
        if spec.kind!='virtual_ecu' or set(spec.input)!={'test_ref'} or spec.comparison_kind!='single' or any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):
            raise ValueError('vECU requires only a saved test reference')
        with self.db.read_session() as session:
            test=resolve(session,spec.project_id,'vecu-test',spec.input['test_ref'])
            package=resolve(session,spec.project_id,'vecu-package',test['package_ref'])
            variant=resolve(session,spec.project_id,'vecu-package',test['variant_package_ref']) if test.get('variant_package_ref') else package
            source=resolve(session,spec.project_id,'vecu-input',test['input_ref']) if test['mode']=='fixed_replay' else None
            if test['mode']=='carla_closed_loop':
                from ade2e.hil_beta import _test
                h=_test(session,spec.project_id,test['hil_test_ref'])
                if h['experiment']!=test['hil_experiment']:raise ValueError('HIL experiment changed')
        if spec.seed!=test['seed']:raise ValueError('Saved seed differs')
        for p in (package,variant):self.check_package(p,spec.project_id)
        snapshot=dict(schema_version='vecu-execution-input/1',test=test,package=package,variant_package=variant,source=source,
            adapter_sources=identity(),execution_fidelity=FIDELITY)
        if test['mode']=='carla_closed_loop':
            if test['comparison']=='package_ab' and not all(p['runtime'].get('kind')=='compiled_reference' for p in (package,variant)):
                raise ValueError('CARLA package A/B requires two compiled packages')
            if not package.get('build_execution_id') and package['runtime_id']!='reference-traffic-dut':raise ValueError('CARLA runtime unsupported')
            snapshot['carla']=self.carla.prepare(ExperimentCreate.model_validate(test['hil_experiment']))
            if package.get('build_execution_id'):
                snapshot['carla'].update(comparison_kind='package_ab',
                    dut_profiles={side:profile(test[key],spec.project_id) for side,key in [('baseline','baseline'),('faulted','variant')]},
                    runtimes={'baseline':package['runtime'],'faulted':variant['runtime']},
                    fault_enabled=test.get('fault_enabled',True),
                    side_fault_enabled={'baseline':test['comparison']!='fault_ab' and test.get('fault_enabled',True),'faulted':test.get('fault_enabled',True)})
            snapshot['execution_fidelity']='actual_carla_software_dut'
        return snapshot
    def execute(self,snapshot,work_dir,should_cancel):
        if snapshot.get('schema_version')=='vecu-build-input/1':
            return builds.execute(self.carla.transport,snapshot,work_dir,should_cancel)
        if snapshot['adapter_sources']!=identity():raise ValueError('vECU adapter source changed after preview')
        test=snapshot['test'];package=snapshot['package'];work=Path(work_dir);work.mkdir(parents=True,exist_ok=True)
        variant=snapshot.get('variant_package',package)
        for p in (package,variant):self.check_package(p,test['project_id'])
        def check():
            if should_cancel():raise AdapterCancelled('vECU cancellation acknowledged; child process closed')
        check();outputs=[];traces={};summary={}
        if test['mode']=='carla_closed_loop':
            result=self.carla.execute(snapshot['carla'],work/'carla',should_cancel)
            outputs.extend(result.artifacts)
            for side in ('baseline','faulted'):
                path=next(a.path for a in result.artifacts if a.name==side+'-hil-trace.json')
                trace=json.loads(path.read_bytes())
                traces[side]=[dict(step=r['sequence'],time_ns=r['sequence']*50000000,
                    request=r['request'],reply=r['reply'],wall_latency_ms=r['ipc_wall_latency_ms'],
                    next_ego=r.get('next_ego'),injection=r['injection']) for r in trace['rows']]
            summary.update(carla_result=result.summary,verdict='not_evaluated',checks={'transport_and_control_readback':'passed'},
                scope='Actual CARLA closed loop with reference software; control readback verified, driving safety not evaluated')
        else:
            source=snapshot['source'];init=dict(op='init',schema_version='hil-dut-init/1',route=source['route'],
                target_speed_mps=source['target_speed_mps'],physics={k:source[k] for k in ('wheelbase_m','max_steer_angle_rad')})
            for side in ('baseline','faulted'):
                from axiom_carla import hil_native,hil_policy
                check();params=test['baseline' if side=='baseline' else 'variant']
                selected=package if side=='baseline' else variant
                compiled=selected['runtime'].get('kind')=='compiled_reference'
                initialization=init|{'dut_profile':profile(params,test['project_id'])}
                monitor=hil_policy.PolicyOracle(initialization,selected['runtime']['policy_id']) if compiled else Oracle(params)
                faults=test['faults'] if test.get('fault_enabled',True) and (side=='faulted' or test['comparison'] in ('settings_ab','package_ab')) else []
                kwargs={'command':builds.command(self.carla.transport,'launch'),'preamble':selected['runtime']} if compiled else {}
                proc=runtime.Process(selected['runtime'],initialization,work/(side+'-stderr.log'),check,**kwargs)
                rows=[];injector=hil_native.ObjectFault()
                try:
                    for i,frame in enumerate(source['frames']):
                        check();fault=next((f for f in faults if f['start_step']<=i<f['end_step']),None)
                        injector.fault=dict(kind='drop' if fault['kind']=='dropout' else fault['kind'],start_s=fault['start_step']*.05,
                            duration_s=(fault['end_step']-fault['start_step'])*.05,delay_ms=fault['delay_steps']*50) if fault else None
                        packet,injection=injector.step(i,frame['objects'])
                        request=dict(op='step',schema_version='hil-dut-step/1',sequence=i,time_s=i*.05,ego=frame['ego'],object_packet=packet,
                            object_delivery_present=injection['delivery_present'])
                        reply,elapsed=proc.exchange(request);monitor.check(request,reply)
                        rows.append(dict(step=i,time_ns=i*50000000,request=request,reply=reply,wall_latency_ms=elapsed,injection=injection))
                finally:proc.close()
                traces[side]=rows
                outputs.append(ExperimentOutputFile(work/(side+'-stderr.log'),side+'-stderr.log','text/plain'))
            summary.update(verdict='passed',checks={'protocol':'passed','freshness_recovery':'passed','control_bounds':'passed'},
                scope='Fixed synthetic/provided input replay; output commands do not alter ego or object input. Not closed-loop vehicle or production ECU validation')
        check()
        for p in (package,variant):self.check_package(p,test['project_id'])
        if snapshot['adapter_sources']!=identity():
            raise ValueError('vECU sources changed during execution; result not published')
        sides={}
        for side,rows in traces.items():
            logical=[{k:v for k,v in row.items() if k!='wall_latency_ms'} for row in rows]
            states=[r['reply']['state'] for r in rows]
            sides[side]=dict(steps=len(rows),logical_sha256=digest(logical),input_sha256=digest([r['request'] for r in rows]),
                degraded_steps=states.count('degraded'),recovering_steps=states.count('recovering'),tracking_steps=states.count('tracking'),
                first_degraded_step=next((i for i,s in enumerate(states) if s=='degraded'),None),
                last_state=states[-1],wall_latency_max_ms=max(r['wall_latency_ms'] for r in rows))
        summary.update(schema_version='vecu-result/1',mode=test['mode'],classification='reference_application',production_software=False,
            physical_hardware_connected=False,seed=test['seed'],sides=sides,period_ns=50000000,
            test_ref={k:test[k] for k in ('id','revision','sha256')},package_ref=test['package_ref'],input_ref=test['input_ref'],
            comparison=test['comparison'],variant_package_ref=test.get('variant_package_ref'),
            packages={side:{'ref':{k:p[k] for k in ('id','revision','sha256')},'runtime':p['runtime']} for side,p in [('baseline',package),('faulted',variant)]},
            runtime_sha256=package['runtime']['runtime_sha256'],trace_artifact='vecu-trace.json',
            virtual_time_basis='integer 50ms steps',wall_time_basis='monotonic host IPC duration',
            adapter_sources=snapshot['adapter_sources'])
        for name,value in [('vecu-trace.json',dict(schema_version='vecu-trace/1',mode=test['mode'],sides=traces)),('vecu-summary.json',summary),('vecu-provenance.json',snapshot)]:
            path=work/name;path.write_bytes(canonical_bytes(value));outputs.append(ExperimentOutputFile(path,name,'application/json'))
        return ExperimentExecution(summary=summary,artifacts=outputs,fidelity=snapshot['execution_fidelity'])
