"""Read sealed native-city contracts without importing an optional engine SDK."""
import json
import math
from fastapi import HTTPException
from sqlalchemy import select
from . import experiment_jobs
from .experiment_models import ExperimentArtifactRow
from .city_motion import scenario_fidelity,REPLAY

PROFILE_VERSION='native-city-observers/1'
REACTIVE_EVALUATOR_VERSION='native-city-reactive-destination/1'
SIGNAL_SCHEDULE_EVALUATOR_VERSION='native-city-command-schedule/1'
SIGNAL_COMMAND_TOLERANCE_S=1e-9
METRICS={'native_contact':'boolean','lateral_error_p95':'m','goal_error':'m','final_speed':'m/s','maximum_speed':'m/s','distance_travelled':'m'}
ADAPTER_VERSION='carla-city-object/1.0.0'


def _finite(value):return type(value) in (int,float) and math.isfinite(value)


def _close(a,b):return _finite(a) and _finite(b) and abs(a-b)<=1e-5


def _same_metrics(actual,expected):
    if not isinstance(actual,list) or len(actual)!=len(expected):return False
    for a,b in zip(actual,expected):
        if set(a)!=set(b):return False
        for key,value in b.items():
            if type(value) in (int,float):
                if not _close(a[key],value):return False
            elif a[key]!=value or type(a[key]) is not type(value):return False
    return True


def observations(db,session,row,receipt,criteria):
    """Validate source joins and recompute aggregates over every retained tick."""
    empty=lambda reason:{(c.metric,c.actor_id):{'status':'unevaluated','reason':reason,'denominator':1,'evaluated':0,
                                               'source_evaluator':PROFILE_VERSION} for c in criteria}
    if receipt.adapter_id!='carla-city-object' or receipt.adapter_version!=ADAPTER_VERSION:
        return empty('native_city_adapter_version_not_accepted')
    summary=receipt.summary or {}
    if (summary.get('schema_version')!='carla-city-result/1' or summary.get('cleanup_status')!='confirmed'
        or summary.get('physical_sensor_validation') is not False or summary.get('execution_status')!='completed'):
        return empty('native_city_complete_receipt_required')
    artifacts=list(session.scalars(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==row.id)))
    selected={}
    for name in ('city-scene.json','city-evaluation.json','map.xodr'):
        matches=[artifact for artifact in artifacts if artifact.name==name]
        if len(matches)!=1:return empty('native_city_original_artifacts_missing_or_ambiguous')
        selected[name]=matches[0]
    try:
        for name in ('city-scene.json','city-evaluation.json'):
            if selected[name].size_bytes>(128*1024**2 if name=='city-scene.json' else experiment_jobs.MANIFEST_LIMIT):
                raise ValueError('City evidence document exceeds the accepted bound')
        scene=json.loads(experiment_jobs.artifact_path(db,selected['city-scene.json']).read_bytes())
        evaluation=json.loads(experiment_jobs.artifact_path(db,selected['city-evaluation.json']).read_bytes())
        experiment_jobs.artifact_path(db,selected['map.xodr'])
        pinned=row.prepared['input'];scenario=pinned['scenario'];source=pinned['source']
        if (pinned.get('schema_version')!='carla-city-input/1' or scene.get('schema_version')!='carla-city-scene/1'
            or evaluation.get('schema_version')!='carla-city-evaluation/1' or scene.get('physical_sensor_validation') is not False
            or evaluation.get('physical_sensor_validation') is not False or scene.get('fidelity')!=scenario_fidelity(scenario)
            or receipt.fidelity!=scene.get('fidelity')
            or pinned.get('execution_fidelity',scenario_fidelity(scenario))!=scenario_fidelity(scenario)
            or scene.get('coordinate_system')!=scenario['coordinate_system']):raise ValueError('City evidence contract differs')
        if (summary.get('source')!=source or any(scene.get('source',{}).get(k)!=v for k,v in source.items())
            or summary.get('scene_sha256')!=selected['city-scene.json'].sha256
            or evaluation.get('source_scene_sha256')!=selected['city-scene.json'].sha256
            or summary.get('map_sha256')!=selected['map.xodr'].sha256
            or scene['source'].get('xodr_sha256')!=selected['map.xodr'].sha256
            or pinned['map']['opendrive_sha256']!=selected['map.xodr'].sha256):raise ValueError('City original source hash join differs')
        for field,pinned_field in [('runner_sha256','native_script_sha256'),('control_sha256','control_script_sha256')]:
            if scene['source'].get(field)!=pinned[pinned_field]:raise ValueError('City controller version differs from frozen input')
        if pinned.get('maneuver_script_sha256') and scene['source'].get('maneuver_sha256')!=pinned['maneuver_script_sha256']:
            raise ValueError('City maneuver version differs from frozen input')
        reactive=any(a['controller']['kind']=='reactive' for a in scenario['actors'])
        if reactive and (not pinned.get('planner_script_sha256') or scene['source'].get('planner_sha256')!=pinned['planner_script_sha256']):
            raise ValueError('City reactive planner version differs from frozen input')
        if scene['source'].get('engine_version')!=pinned['server_version'] or scene['source'].get('adapter_version')!=ADAPTER_VERSION:
            raise ValueError('City engine or adapter build differs')
        if (evaluation.get('criteria')!=scenario['acceptance'] or summary.get('evaluation')!=evaluation
            or scene.get('routes')!={a['id']:a['route'] for a in scenario['actors']}):raise ValueError('City frozen criteria or route differs')
        frames=scene['frames'];expected=round(scenario['duration_s']/scenario['timestep_s']);dt=scenario['timestep_s']
        ids=[a['id'] for a in scenario['actors']]
        if len(frames)!=expected or summary.get('frame_count')!=expected or [a['id'] for a in scene['actors']]!=ids:
            raise ValueError('City retained frame or actor denominator differs')
        for spec,actual in zip(scenario['actors'],scene['actors']):
            if any(actual.get(k)!=v for k,v in spec.items()):raise ValueError('City actor/controller intent differs')
        for i,frame in enumerate(frames):
            if (frame.get('index')!=i or not _close(frame.get('time_s'),(i+1)*dt)
                or not _finite(frame.get('simulation_time_s')) or type(frame.get('frame')) is not int
                or type(frame.get('input_frame')) is not int or frame['frame']!=frame['input_frame']+1
                or i and (frame['input_frame']!=frames[i-1]['frame'] or not _close(frame['simulation_time_s']-frames[i-1]['simulation_time_s'],dt))
                or [a.get('id') for a in frame['actors']]!=ids):raise ValueError('City response clock or identity differs')
            if scenario.get('signals'):
                observations=frame.get('signals',[])
                if not isinstance(observations,list) or len({s['id'] for s in observations})!=len(observations):
                    raise ValueError('Native signal observation denominator is missing or ambiguous')
                actual_signals={s['id']:s.get('state') for s in observations}
                for schedule in scenario['signals']:
                    wanted=schedule['initial_state']
                    for change in schedule['changes']:
                        # Match native city_maneuvers.scheduled_state command
                        # timing; a change after this tick belongs to the next.
                        if change['time_s']<=i*dt+SIGNAL_COMMAND_TOLERANCE_S:wanted=change['state']
                    if actual_signals.get(schedule['id'])!=wanted:
                        raise ValueError('Native signal observation differs from frozen input schedule')
        events=scene['events']
        for event in events:
            if (event.get('kind')!='native_contact' or event.get('provenance')!='native_carla_collision_sensor'
                or not event.get('actor_ids') or any(a not in ids for a in event['actor_ids'])
                or type(event.get('frame')) is not int or not frames[0]['frame']<=event['frame']<=frames[-1]['frame']
                or not _close(event.get('time_s'),(event['frame']-frames[0]['input_frame'])*dt)):
                raise ValueError('City native contact provenance differs')
        measured=[]
        for index,actor in enumerate(scenario['actors']):
            states=[frame['actors'][index] for frame in frames];route=actor['route']
            destination=(actor.get('destination') or route[-1]) if route else None
            if any(not all(_finite(s.get(k)) for k in ('x','y','speed_mps')) or s['speed_mps']<0 for s in states):
                raise ValueError('City retained metric inputs are invalid')
            errors=[s.get('route_error_m') for s in states] if route else []
            if any(not _finite(error) or error<0 for error in errors):raise ValueError('City route error denominator is incomplete')
            errors.sort()
            measured.append({'actor_id':actor['id'],
                'distance_travelled_m':sum(math.hypot(a['x']-b['x'],a['y']-b['y']) for a,b in zip(states,states[1:])),
                'maximum_speed_mps':max(a['speed_mps'] for a in states),
                'native_contact':any(actor['id'] in e['actor_ids'] for e in events),
                'lateral_error_p95_m':errors[max(0,math.ceil(.95*len(errors))-1)] if errors else None,
                'goal_error_m':math.hypot(states[-1]['x']-destination['x'],states[-1]['y']-destination['y']) if destination else None,
                'final_speed_mps':states[-1]['speed_mps']})
        if (not _same_metrics(scene['metrics'],measured) or not _same_metrics(summary['metrics'],measured)
            or not _same_metrics(evaluation.get('measured_metrics'),measured)):
            raise ValueError('City metric aggregates differ from retained responses')
    except (ValueError,TypeError,KeyError,IndexError,AttributeError) as error:
        raise HTTPException(409,'Native city verification evidence failed its source/metric contract: '+str(error)) from error
    result={};by_actor={value['actor_id']:value for value in measured}
    replay_ids=[a['id'] for a in scenario['actors'] if a['controller']['kind']=='timed_replay']
    contact_sensor_ids=[a['id'] for a in scenario['actors'] if a['kind']=='vehicle' and a['id'] not in replay_ids]
    names={'lateral_error_p95':'lateral_error_p95_m','goal_error':'goal_error_m','final_speed':'final_speed_mps','maximum_speed':'maximum_speed_mps',
           'distance_travelled':'distance_travelled_m'}
    for criterion in criteria:
        identity=(criterion.metric,criterion.actor_id)
        base={'denominator':expected,'evaluated':expected,'source_evaluator':PROFILE_VERSION,'source_scene_sha256':selected['city-scene.json'].sha256,
              'source_evaluation_sha256':selected['city-evaluation.json'].sha256,'source_configuration_hash':source['configuration_hash'],
              'scope':'native_simulator_ground_truth_route_control_only','physical_sensor_validation':False}
        if reactive:
            base.update(reactive_evaluator_version=REACTIVE_EVALUATOR_VERSION,
                        source_planner_sha256=pinned['planner_script_sha256'],goal_reference='pinned_destination_or_original_route_end')
        if scenario.get('signals'):
            base['signal_input_scope']='native_observed_fixed_schedule; no stop-line crossing or traffic-law verdict'
            base['signal_schedule_evaluator_version']=SIGNAL_SCHEDULE_EVALUATOR_VERSION
            base['signal_command_tolerance_s']=SIGNAL_COMMAND_TOLERANCE_S
        if criterion.metric=='native_contact':
            base['contact_observation_scope']={'sensor_actor_ids':contact_sensor_ids,
                'coverage':'union_of_vehicle_collision_callbacks; pedestrian/static-only interactions are not fully instrumented'}
        if criterion.metric not in METRICS:
            result[identity]={**base,'status':'unevaluated','evaluated':0,'reason':'metric_not_in_native_city_profile'};continue
        if criterion.actor_id not in by_actor and not (criterion.actor_id=='all' and criterion.metric=='native_contact'):
            result[identity]={**base,'status':'unevaluated','evaluated':0,'reason':'actor_not_in_native_city_evidence'};continue
        if replay_ids:
            base.update(scope='timed_position_replay_diagnostic_only' if receipt.fidelity==REPLAY else 'physical_actor_route_control_with_replay_context',diagnostic_only_actor_ids=replay_ids)
            if criterion.actor_id in replay_ids or criterion.metric=='native_contact':
                result[identity]={**base,'status':'unevaluated','evaluated':0,
                    'reason':'timed_position_replay_is_diagnostic_only' if criterion.actor_id in replay_ids else 'mixed_timed_replay_contact_is_diagnostic_only'};continue
        value=(bool(events) if criterion.actor_id=='all' else by_actor[criterion.actor_id]['native_contact']) if criterion.metric=='native_contact' else by_actor[criterion.actor_id][names[criterion.metric]]
        if criterion.metric=='native_contact' and (not contact_sensor_ids or (criterion.actor_id!='all' and criterion.actor_id not in contact_sensor_ids and not value)):
            result[identity]={**base,'status':'unevaluated','evaluated':0,'reason':'actor_contact_absence_is_not_fully_instrumented'};continue
        if value is None:
            result[identity]={**base,'status':'unevaluated','evaluated':0,'reason':'required_native_route_metric_unavailable'};continue
        result[identity]={**base,'status':'observed','value':value,'unit':METRICS[criterion.metric],
                          'provenance':'native_collision_sensor_events' if criterion.metric=='native_contact' else 'retained_native_response_metrics'}
    return result
