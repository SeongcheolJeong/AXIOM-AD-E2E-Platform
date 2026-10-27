"""Sequential native CARLA fault or same-fault DUT profile experiments."""
from __future__ import annotations
import copy
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.scenario_v2 import get_scenario
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from . import city, hil_native, hil_dut, hil_policy, hil_network

VERSION = '1.0.0'
FIDELITY = 'actual_carla_software_dut'


def validate_dut_profiles(profiles):
    if not isinstance(profiles, dict) or set(profiles) != {'baseline', 'faulted'}:
        raise ValueError('DUT comparison requires two frozen profiles')
    result = {side: hil_dut.validate_dut_profile(value) for side, value in profiles.items()}
    if result['baseline']['project_id'] != result['faulted']['project_id']:
        raise ValueError('DUT profiles belong to different projects')
    return result


def resolve_dut_profiles(session, project_id, profiles):
    result = validate_dut_profiles(profiles)
    for profile in result.values():
        if profile['project_id'] != project_id:
            raise ValueError('DUT profile belongs to another project')
        row = session.get(SimulationCatalogRevisionRow,
            (project_id, 'hil-dut-profile', profile['id'], profile['revision']))
        if row is None or city.sha(row.manifest) != row.manifest_hash:
            raise ValueError('Pinned DUT profile catalog revision is absent or corrupt')
        stored = hil_dut.validate_dut_profile({**json.loads(row.manifest), 'sha256': row.manifest_hash})
        if city.canonical(stored) != city.canonical(profile):
            raise ValueError('Frozen DUT profile differs from pinned catalog revision')
    return result


def validate_profile(scenario, snapshot=None):
    actors=scenario['actors']
    if (len(actors)!=2 or any(a['kind']!='vehicle' for a in actors) or scenario['timestep_s']!=.05
            or scenario['duration_s']!=18 or scenario.get('environment')!={'lighting':'day','weather':'dry'}):
        raise ValueError('HIL demo requires two vehicles, 18 seconds, 20 Hz and clear day')
    ego=next((a for a in actors if a['role']=='ego'),None)
    lead=next((a for a in actors if a['role']!='ego'),None)
    if (not ego or not lead or ego['controller']['kind']!='traffic' or lead['controller']['kind']!='route_follow'
            or ego['speed_mps']!=3 or lead['speed_mps']!=2 or lead['controller'].get('stop_time_s')!=4
            or ego['controller'].get('stop_time_s') is not None
            or any(a['controller'].get('wait_s',0) or a['controller']['end_behavior']!='stop' for a in actors)
            or any(a.get('route_mode')=='authored_lane_change' or a.get('behavior',{}).get('actions') for a in actors)):
        raise ValueError('HIL profile is fixed ego traffic 3m/s and lead 2m/s stopping at 4 seconds')
    if any(s['initial_state']!='green' or s['changes'] for s in scenario.get('signals',[])):
        raise ValueError('HIL lead-stop demo requires explicitly fixed green signals')
    if not ego['route'] or not lead['route']:
        raise ValueError('HIL requires fixed routes')
    length=city.city_control.lengths(ego['route'])[-1]
    if not 30<=length<=60:
        raise ValueError('HIL demonstration requires a bounded 30..60m straight route')
    heading=ego['route'][0]['heading_rad']
    if any(abs(math.atan2(math.sin(p['heading_rad']-heading),math.cos(p['heading_rad']-heading)))>.15 for p in ego['route']):
        raise ValueError('HIL first profile requires the native straight route')
    projection=city.city_control.project(lead['pose'],ego['route'])
    if not 12-1e-5<=projection['progress_m']<=14.1 or projection['error_m']>.25:
        raise ValueError('HIL lead must begin approximately12m ahead on the same native route')
    cameras=scenario.get('sensors',[])
    if (len(cameras)!=2 or {s['id'] for s in cameras}!={'front','chase'}
            or any(s['actor_id']!=ego['id'] or s['width']!=640 or s['height']!=360 or s['period_s']!=.1 for s in cameras)):
        raise ValueError('HIL demo requires front/chase640x360 RGB at10Hz')
    if snapshot and (snapshot['map']['native_name'].split('/')[-1]!='Town10HD_Opt'
            or any(snapshot['assets'][a['id']]['blueprint_id']!='vehicle.toyota.prius' for a in actors)):
        raise ValueError('HIL demo requires native Town10HD_Opt and two Prius assets')
    return ego,lead


class HilCityTransport(city.CityExperimentHandler):
    def __init__(self, *, database=None):
        super().__init__(database=database)
        city_script=self.script
        for attr,suffix in [('target','SSH'),('python','PYTHON'),('script','SCRIPT'),('directory','DIR'),
                            ('spool','SPOOL'),('owner','OWNER')]:
            value=os.environ.get('AXIOM_CARLA_HIL_'+suffix)
            if value is not None:setattr(self,attr,value)
        if not os.environ.get('AXIOM_CARLA_HIL_SCRIPT') and city_script:
            self.script=str(Path(city_script).with_name('hil_native.py'))
        self.port=int(os.environ.get('AXIOM_CARLA_HIL_PORT',str(self.port)))
        self.autoware=''

    def native_probe(self):
        result=super().native_probe()
        if result.get('hil_source_hashes')!=hil_native.source_hashes():
            raise ValueError('Remote software-DUT sources differ from installed sources')
        return result


def same_dut_response(actual, expected):
    """Allow only float roundoff when replaying Linux math on another host.

    Archived evidence is never rewritten. Protocol structure and discrete values
    remain exact; this is a numerical recomputation check, not a byte identity.
    """
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys()==expected.keys() and all(same_dut_response(actual[k],expected[k]) for k in expected)
    if isinstance(expected, list):
        return len(actual)==len(expected) and all(same_dut_response(a,e) for a,e in zip(actual,expected))
    if type(expected) is float:
        return math.isfinite(actual) and math.isfinite(expected) and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12)
    return actual==expected


def validate_trace(trace, scene, snapshot, side, fault):
    rows=trace.get('rows');frames=trace.get('frames');source=snapshot['source']
    config=snapshot.get('hil',{'side':side,'fault':fault})
    injected,profile=hil_native.side_contract(config)
    scene_profile=hil_native.hil_scene_profiles.validate_scene_profile(config['scene_profile']) if 'scene_profile' in config else None
    if scene_profile is not None:
        if (not same_dut_response(trace.get('scene_profile'),scene_profile)
                or not same_dut_response(scene.get('hil',{}).get('scene_profile'),scene_profile)):
            raise ValueError('HIL scene overlay differs from frozen profile')
    elif 'scene_profile' in trace or 'scene_profile' in scene.get('hil',{}):
        raise ValueError('Legacy HIL trace unexpectedly contains a scene overlay')
    if config['side']!=side or config['fault']!=fault:
        raise ValueError('HIL trace side differs from frozen side configuration')
    if profile is not None:
        if (trace.get('comparison_kind')!=config.get('comparison_kind','same_fault_dut_ab')
                or not same_dut_response(trace.get('dut_profile'),profile)
                or not same_dut_response(trace.get('dut_initialization',{}).get('dut_profile'),profile)
                or trace.get('max_object_age_s')!=profile['parameters']['max_object_age_ms']/1000
                or trace.get('recovery_fresh_steps')!=profile['parameters']['recovery_fresh_steps']):
            raise ValueError('HIL trace or initialization differs from frozen DUT profile')
    elif ('dut_profile' in trace or 'dut_profile' in trace.get('dut_initialization',{})
            or trace.get('comparison_kind','same_scene_fault_ab')!='same_scene_fault_ab'):
        raise ValueError('Legacy HIL trace unexpectedly contains a DUT profile')
    if (trace.get('schema_version')!='hil-demo-trace/1' or trace.get('execution_target')!='software_dut'
            or trace.get('side')!=side or trace.get('status')!='completed'
            or trace.get('source_hashes')!=hil_native.source_hashes()
            or trace.get('fault')!=injected
            or trace.get('fault_time_basis')!='simulation_time' or trace.get('ipc_time_basis')!='parent_monotonic_wall_clock'
            or not isinstance(rows,list) or len(rows)!=len(scene['frames']) or not isinstance(frames,list) or len(frames)!=len(rows)):
        raise ValueError('HIL trace contract or complete frame denominator mismatch')
    if scene.get('source',{}).get('hil_source_hashes')!=trace['source_hashes']:
        raise ValueError('HIL scene source does not match the DUT trace')
    ego=next(a for a in snapshot['scenario']['actors'] if a['role']=='ego')
    injector=hil_native.ObjectFault(injected)
    initialization={'op':'init','schema_version':'hil-dut-init/1','route':ego['route'],
        'target_speed_mps':ego['speed_mps'],'physics':scene['vehicle_physics'][ego['id']]}
    if profile is not None:initialization['dut_profile']=profile
    compiled=config.get('runtime')
    if compiled:
        hil_policy.validate_runtime(compiled)
        if trace.get('runtime')!=compiled or trace.get('fault_enabled')!=config['fault_enabled']:
            raise ValueError('Compiled package trace identity differs')
        if compiled['kind']=='network_reference':
            dut=hil_network.NetworkAudit({**initialization,'network_runtime':compiled})
        else:dut=hil_policy.PolicyOracle(initialization,compiled['policy_id'])
    else:dut=hil_dut.ReferenceDut(initialization)
    lead_spec=next((a for a in snapshot['scenario']['actors'] if a['role']!='ego'),None)
    lead_events=[];lead_previous=None;lead_progress=None;lead_steering=None
    for index,(row,native,compact) in enumerate(zip(rows,scene['frames'],frames)):
        request=row.get('request',{});response=row.get('reply',{})
        observed=next(a for a in native['actors'] if a['id']==ego['id'])
        if (row.get('sequence')!=index or request.get('sequence')!=index or row.get('input_frame')!=native['input_frame']
                or not hil_dut.finite(row.get('time_s')) or abs(row['time_s']-index*.05)>1e-8
                or row.get('response_frame')!=native['frame'] or row.get('next_ego')!=observed
                or set(row.get('applied_control',{}))!={'throttle','brake','steer'}
                or any(not hil_dut.finite(row['applied_control'][k]) or abs(row['applied_control'][k]-observed['control'][k])>1e-5 for k in ('throttle','brake','steer'))
                or row.get('requested_control')!=observed['control']
                or not hil_dut.finite(row.get('ipc_wall_latency_ms')) or row['ipc_wall_latency_ms']<0):
            raise ValueError('HIL request/control/response frame relationship mismatch')
        actor_ids={a['id']:a['native_actor_id'] for a in scene['actors']}
        expected_batch=[{'actor_id':actor_ids[a['id']],'id':a['id'],'requested_control':a['control']} for a in native['actors']]
        if (row.get('command_batch')!=expected_batch or row.get('command_acknowledgements')!=[
                {'actor_id':entry['actor_id'],'error':None} for entry in expected_batch]):
            raise ValueError('HIL command batch does not acknowledge every native vehicle before the tick')
        packet,injection=injector.step(index,row['generated_objects'])
        if request.get('object_packet')!=packet or row.get('injection')!=injection or request.get('object_delivery_present') is not injection['delivery_present']:
            raise ValueError('HIL delivered objects do not match the declared fault')
        if compiled:dut.check(request,response)
        elif not same_dut_response(response,dut.step(request)):
            raise ValueError('HIL saved response differs from the source-pinned reference DUT')
        if row['requested_control']!=response['control']:
            raise ValueError('HIL command differs from executed ECU response')
        lead=response['plan'].get('lead')
        expected={'time_s':(index+1)*.05,'input_time_s':index*.05,'sequence':index,'input_age_ms':response['object_age_s']*1000,
            'dut_state':response['state'],'ego_speed_mps':observed['speed_mps'],
            'lead_gap_m':lead['origin_gap_m'] if lead else None,'command':row['applied_control'],
            'ipc_wall_latency_ms':row['ipc_wall_latency_ms'],'object_sequence':response['object_sequence'] if compiled and compiled['kind']=='network_reference' else packet['sequence'],'response_frame':native['frame']}
        if scene_profile is not None:
            if lead_spec is None:raise ValueError('Scene overlay has no source-pinned lead')
            lead=next(a for a in native['actors'] if a['id']==lead_spec['id'])
            if row.get('next_lead')!=lead:
                raise ValueError('HIL lead response differs from native scene')
            motion=hil_native.lead_motion(scene_profile,index*.05)
            state=next(a for a in row['generated_objects'] if a['id']==lead_spec['id'])
            physics=scene['vehicle_physics'][lead_spec['id']]
            control,plan=city.city_control.feedback(state,lead_spec['route'],motion['target_speed_mps'],
                wheelbase_m=physics['wheelbase_m'],max_steer_rad=physics['max_steer_angle_rad'],
                rear_axle_offset_m=physics.get('rear_axle_offset_m',0.),previous=lead_progress,
                previous_steering=lead_steering,end_behavior='stop',stop=motion['target_speed_mps']==0)
            if not same_dut_response(lead['control'],control):
                raise ValueError('Lead command differs from the frozen scene schedule')
            lead_progress,lead_steering=plan['progress_m'],plan['steering_progress_m']
            fields,events=hil_native.lead_presentation(scene_profile,row,lead,lead_previous,lead_events)
            expected.update(fields);lead_events.extend(events);lead_previous=fields
        if compact!=expected:
            raise ValueError('HIL presentation trace differs from original observations')
    if scene_profile is not None and [e for e in trace.get('events',[]) if e.get('type','').startswith('lead_')]!=lead_events:
        raise ValueError('HIL lead events differ from commands or observed movement')
    return trace


class HilExperimentHandler:
    def __init__(self, *, database=None):
        self.db=database
        self.transport=HilCityTransport(database=database)

    def metadata(self):
        native=self.transport.metadata()
        return ExperimentHandlerMetadata(id='carla-software-dut',version=VERSION,kinds=['hil_demo'],
            status=native.status,resource_id=native.resource_id,fidelity=FIDELITY,cancellation_supported=True,
            description='Separate-process software DUT, CARLA physical vehicle control and simulated object delay/drop A/B. No physical hardware or hard-real-time claim.')

    def prepare(self,spec):
        profile_mode=spec.comparison_kind=='same_fault_dut_ab'
        required={'scenario_id','scenario_revision','fault'}|({'dut_profiles'} if profile_mode else set())
        if 'scene_profile' in spec.input:required.add('scene_profile')
        if (spec.kind!='hil_demo' or spec.comparison_kind not in ('same_scene_fault_ab','same_fault_dut_ab')
                or any(x is not None for x in (spec.model,spec.sensor,spec.evaluator))
                or set(spec.input)!=required):
            raise ValueError('HIL requires one pinned scenario and object fault comparison')
        fault=hil_native.validate_fault(spec.input['fault'])
        scene_profile=hil_native.hil_scene_profiles.validate_scene_profile(spec.input['scene_profile']) if 'scene_profile' in spec.input else None
        with self.db.read_session() as session:
            original=get_scenario(session,spec.input['scenario_id'],spec.input['scenario_revision'])
            if original['project_id']!=spec.project_id:raise ValueError('HIL scenario belongs to another project')
            profiles=resolve_dut_profiles(session,spec.project_id,spec.input['dut_profiles']) if profile_mode else None
        proxy=SimpleNamespace(kind='object_sim',comparison_kind='single',model=None,sensor=None,evaluator=None,
            project_id=spec.project_id,seed=spec.seed,input={'scenario_id':original['id'],
            'scenario_revision':original['revision'],'configuration_hash':original['configuration_hash']})
        frozen=self.transport.prepare(proxy)
        validate_profile(frozen['scenario'],frozen)
        result={'schema_version':'carla-software-dut-experiment/1','comparison_kind':spec.comparison_kind,
            'execution_target':'software_dut','execution_fidelity':FIDELITY,'scenario_source':frozen['source'],
            'city_adapter_version':city.VERSION,'city_snapshot':frozen,'fault':fault,
            'hil_source_hashes':hil_native.source_hashes(),'hil_adapter_sha256':city.file_sha(__file__),
            'workload':{**frozen['workload'],'side_count':2,'total_ticks':2*frozen['workload']['ticks'],
                        'total_camera_bytes':2*frozen['workload']['camera_bytes']}}
        if profiles is not None:result['dut_profiles']=profiles
        if scene_profile is not None:result['scene_profile']=scene_profile
        return result

    def execute(self,snapshot,work_dir,should_cancel):
        if (snapshot.get('schema_version')!='carla-software-dut-experiment/1'
                or snapshot.get('hil_source_hashes')!=hil_native.source_hashes()
                or snapshot.get('hil_adapter_sha256')!=city.file_sha(__file__)
                or snapshot.get('city_adapter_version')!=city.VERSION):
            raise ValueError('HIL implementation changed after preview')
        work=Path(work_dir);work.mkdir(parents=True,exist_ok=True)
        fault=hil_native.validate_fault(snapshot['fault'])
        scene_profile=hil_native.hil_scene_profiles.validate_scene_profile(snapshot['scene_profile']) if 'scene_profile' in snapshot else None
        mode=snapshot.get('comparison_kind','same_scene_fault_ab')
        if mode not in ('same_scene_fault_ab','same_fault_dut_ab','package_ab'):
            raise ValueError('Unsupported HIL comparison')
        profiles=validate_dut_profiles(snapshot.get('dut_profiles')) if mode in ('same_fault_dut_ab','package_ab') else None
        if profiles is None and 'dut_profiles' in snapshot:
            raise ValueError('Legacy HIL comparison does not accept DUT profiles')
        validate_profile(snapshot['city_snapshot']['scenario'],snapshot['city_snapshot'])
        outputs=[];sides={}
        for side in ('baseline','faulted'):
            if should_cancel():raise AdapterCancelled('HIL cancelled before '+side+' dispatch')
            frozen=copy.deepcopy(snapshot['city_snapshot'])
            frozen['hil']={'side':side,'fault':fault,'source_hashes':snapshot['hil_source_hashes']}
            if scene_profile is not None:frozen['hil']['scene_profile']=copy.deepcopy(scene_profile)
            if profiles is not None:
                frozen['hil'].update(comparison_kind=mode,dut_profile=profiles[side])
            if mode=='package_ab':
                frozen['hil'].update(runtime=hil_policy.validate_runtime(snapshot['runtimes'][side]),fault_enabled=snapshot.get('side_fault_enabled',{}).get(side,snapshot['fault_enabled']))
            result=self.transport.execute(frozen,work/side,should_cancel)
            trace_path=work/side/'hil-trace.json';scene_path=work/side/'city-scene.json'
            trace=validate_trace(json.loads(trace_path.read_bytes()),json.loads(scene_path.read_bytes()),frozen,side,fault)
            prefix=side+'-'
            summary=copy.deepcopy(result.summary)
            for field in ('scene_artifact','map_artifact','evaluation_artifact'):
                summary[field]=prefix+summary[field]
            summary.update(artifact_prefix=prefix,hil_trace_artifact=prefix+'hil-trace.json',
                hil_trace_sha256=city.file_sha(trace_path),execution_target='software_dut',
                evaluation_scope='native_city_motion_diagnostics_only',
                dut_states=sorted({r['dut_state'] for r in trace['frames']}))
            if profiles is not None:summary['dut_profile']=copy.deepcopy(profiles[side])
            if scene_profile is not None:summary['scene_profile']=copy.deepcopy(scene_profile)
            sides[side]=summary
            outputs.extend(ExperimentOutputFile(item.path,prefix+item.name,item.media_type) for item in result.artifacts)
        summary={'schema_version':'hil-demo-result/1','execution_target':'software_dut','comparison_kind':mode,
            'sides':sides,'fault':fault,'scenario_source':snapshot['scenario_source'],'hil_source_hashes':snapshot['hil_source_hashes'],
            'physical_hardware_connected':False,'fault_time_basis':'simulation_time','ipc_time_basis':'parent_monotonic_wall_clock',
            'execution_status':'completed','cleanup_status':'confirmed','physical_sensor_validation':False,
            'evaluation_status':'not_evaluated','verification_verdict':'not_evaluated',
            'limitations':['Separate-process reference software DUT; no ECU or physical HIL hardware connected.',
                'Faults are scheduled in simulation time. IPC wall latency is measured separately; hard-real-time operation is not validated.',
                'CARLA actor ground truth feeds the DUT; RGB is contextual rendered observation.',
                'A/B are sequential native executions with the same scenario; byte-identical physics and imagery are not guaranteed.']}
        if profiles is not None:
            summary['dut_profiles']=copy.deepcopy(profiles)
            summary['limitations'].append('Both DUT profiles receive the same fault schedule; closed-loop vehicle responses can change subsequent object observations.')
        if scene_profile is not None:
            summary['scene_profile']=copy.deepcopy(scene_profile)
            summary['limitations'].append('The original scenario source is preserved; the explicit scene profile overrides only lead speed and stop timing before physical feedback control.')
        path=work/'hil-summary.json';path.write_bytes(city.canonical(summary))
        outputs.append(ExperimentOutputFile(path,path.name,'application/json'))
        return ExperimentExecution(summary=summary,artifacts=outputs,fidelity=FIDELITY)


def create_hil_handler(*,database=None):
    return HilExperimentHandler(database=database)
