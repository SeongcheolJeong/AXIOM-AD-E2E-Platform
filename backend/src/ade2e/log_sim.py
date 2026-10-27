"""Log investigation → bounded native variation → retained comparison.

No engine imports or new queue. The CARLA source is an explicitly bound retained
execution; selected events are focus windows, never simulator state checkpoints.
"""
import base64
import hashlib
import io
import json
import math
import tempfile
from pathlib import Path
from threading import RLock

from fastapi import Header, HTTPException, Request, Response
from sqlalchemy import select
from . import cases, experiment_jobs, object_demo
from .case_schemas import CaseCreate, RecordingSelection
from .experiment_models import ExperimentArtifactRow
from .experiment_schemas import ExperimentCreate
from .log_sim_schemas import LogCaseCreate, LogVariantPreview, LogVariantSubmit
from .object_demo_schemas import DemoPreview
from .object_story import _read_scene, StoryUnavailable
from .project_recordings import authorize_recording_source, recording_source_scope
from .recording_models import RecordingChannelRow, RecordingMessageRow, RecordingRow, RecordingSchemaRow
from .recording_schemas import RecordingImportMetadata
from .recordings import import_recording, read_message_bytes
from .scenario_v2 import save_scenario
from .scenario_v2_schemas import ScenarioV2Create
from .scene_sequence_schemas import PlanningEgo, ObjectTracks
from .service import canonical_bytes, digest, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Catalog
from .team_access import project_authorize

SOURCE_KIND = 'log-demo-source'
VARIANT_KIND = 'log-demo-variant'
CASE_KIND = 'log-demo-case'
NATIVE_ID = 'carla-urban-right-turn'
PRESET = 'urban_right_turn'
_IMPORT_LOCK = RLock()


def _catalog(session, project, kind, identity):
    row = session.scalar(select(Catalog).where(Catalog.project_id == project, Catalog.kind == kind,
        Catalog.id == identity).order_by(Catalog.revision.desc()).limit(1))
    if row is None:
        return None
    try:
        value = json.loads(row.manifest)
        if digest(value) != row.manifest_hash or (value['project_id'], value['kind'], value['id'], value['revision']) != (project, kind, identity, row.revision):
            raise ValueError('identity')
        return value | {'manifest_hash': row.manifest_hash}
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, 'Log Sim catalog integrity check failed') from exc


def _store(db, project, kind, identity, definition):
    with db.session.begin() as session:
        previous = _catalog(session, project, kind, identity)
        if previous and previous['definition'] == definition:
            return previous
        revision = previous['revision'] + 1 if previous else 1
        value = dict(schema_version={SOURCE_KIND:'log-sim-source/1',VARIANT_KIND:'log-sim-variant/1',CASE_KIND:'log-sim-case-link/1'}[kind],
            project_id=project, kind=kind, id=identity, revision=revision, definition=definition)
        raw = canonical_bytes(value)
        session.add(Catalog(project_id=project, kind=kind, id=identity, revision=revision,
            manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest()))
        return value | {'manifest_hash': hashlib.sha256(raw).hexdigest()}


def _native_source(session, project):
    item = object_demo._item(session, project, PRESET)
    ready = item['ready'] and bool(item['saved_execution_id'])
    value = dict(id=NATIVE_ID, title='도심 우회전 · 앞차 정지', kind='carla', ready=ready,
        status='ready' if ready else ('unavailable' if item['status']=='ready' else item['status']), reason=item['reason'] if not item['ready'] else
        (None if ready else '완료된 원본 CARLA 실행을 프리셋에 연결해야 합니다.'),
        baseline_execution_id=item['saved_execution_id'], saved_comparison_execution_id=item['saved_comparison_execution_id'],
        scenario_ref=item['scenario_ref'], binding_revision=item['binding_revision'], defaults=item['defaults'],
        duration_s=0, capabilities={'variant': ready, 'case_save': ready}, source_scope='simulation',
        replay_semantics='full_scenario_from_start', physical_sensor_validation=False)
    if ready:
        side, _ = object_demo._execution(session, project, item['saved_execution_id'])
        value['duration_s'] = side['scenario']['duration_s']
    value['source_hash'] = digest({key: value[key] for key in ('id','scenario_ref','binding_revision','baseline_execution_id')})
    stored = _catalog(session, project, SOURCE_KIND, NATIVE_ID)
    if stored and stored['definition'].get('source_hash') == value['source_hash']:
        value['recording_ref'] = stored['definition']['recording_ref']
    return value


def _road_source(session, project, identity):
    value = _catalog(session, project, SOURCE_KIND, identity)
    if value is None or value['definition'].get('kind') not in {'nuscenes', 'recorded'}:
        raise HTTPException(404, 'Log Sim source not found')
    definition = value['definition']; recording = session.get(RecordingRow, definition['recording_ref']['id'])
    if recording is None or recording.sha256 != definition['recording_ref']['sha256']:
        raise HTTPException(409, 'Road recording differs from its pinned source')
    return dict(id=identity, title=definition['title'], kind=definition['kind'], ready=True, status='ready', reason=None,
        defaults={}, capabilities={'variant': False, 'case_save': True}, duration_s=definition['duration_s'],
        recording_ref=definition['recording_ref'], source_hash=value['manifest_hash'], source_scope=recording_source_scope(recording),
        annotation_source=definition.get('annotation_source', 'original_dataset_annotations'), physical_sensor_validation=False,
        frame_count=definition.get('frame_count'), library_metadata=definition.get('library_metadata'))


def _source(session, project, identity):
    return _native_source(session, project) if identity == NATIVE_ID else _road_source(session, project, identity)


def _scene(db, session, project, source):
    if not source['ready']:
        raise HTTPException(409, source['reason'])
    side, snapshot = object_demo._execution(session, project, source['baseline_execution_id'])
    try:
        events, receipt = _read_scene(db, session, side, snapshot)
    except StoryUnavailable as exc:
        raise HTTPException(409, 'Original scene unavailable: ' + str(exc)) from exc
    artifact = session.get(ExperimentArtifactRow, receipt['artifact_id'])
    scene = json.loads(experiment_jobs.artifact_path(db, artifact).read_bytes())
    return scene, events, receipt


def _events_metrics(scene, selected):
    ego = next(actor['id'] for actor in scene['actors'] if actor['role'] == 'ego')
    events = []
    for item in scene['planner_events']:
        label = ('앞차 정지 명령' if item.get('action_id') == 'lead_wait' and item['type'] == 'action_started'
                 else '자차 대기 요청' if item['actor_id'] == ego and item['reason'] == 'follow_lead_stop' and item['type'] == 'decision_changed'
                 else '앞차 대기 종료' if item.get('action_id') == 'lead_wait' and item['type'] == 'action_completed' else None)
        if label:
            events.append(dict(id='event-'+str(len(events)), label=label, kind=item['reason'], time_s=item['time_s'],
                end_time_s=min(scene['frames'][-1]['time_s'], item['time_s']+4)))
    for item in selected:
        if item['actor_id'] == ego and (item['category']=='arrived' or item['category']=='resume' and item.get('previous_decision',{}).get('reason')=='follow_lead_stop'):
            events.append(dict(id='event-'+str(len(events)), label='자차 재출발 요청' if item['category']=='resume' else '목적지 도착 판단',
                kind=item['category'], time_s=item['time_s'], end_time_s=min(scene['frames'][-1]['time_s'], item['time_s']+3)))
    arrival = next((item['observation_time_s'] for item in selected if item['actor_id']==ego and item['category']=='arrived'), None)
    gaps = []; stop_s = 0.; previous = 0.
    for frame in scene['frames']:
        state = next(x for x in frame['actors'] if x['id'] == ego)
        lead = state.get('plan', {}).get('reactive', {}).get('diagnostics', {}).get('lead', {})
        if type(lead.get('gap_m')) in (int,float): gaps.append(lead['gap_m'])
        if state['speed_mps'] < .2 and (arrival is None or frame['time_s'] <= arrival):
            stop_s += frame['time_s'] - previous
        previous = frame['time_s']
    contact = scene.get('contact_capture', {})
    count = None
    if contact.get('method') == 'native_vehicle_collision_sensor_callbacks':
        count = len([x for x in scene.get('events',[]) if x.get('kind') == 'native_contact'])
    return sorted(events,key=lambda x:x['time_s']), dict(minimum_lead_gap_m=min(gaps) if gaps else None,
        ego_stop_duration_s=round(stop_s,3), arrival_time_s=arrival, native_contact_count=count,
        metric_scope='recorded_simulator_state_stop_speed_below_0.2mps_before_arrival')


def carla_planning_frames(scene):
    """Rotate native bbox centers, then reflect CARLA Y/yaw into right handed XY."""
    if scene.get('coordinate_system') != 'carla_world_m_x_forward_y_right_z_up':
        raise HTTPException(422, 'Unsupported native coordinate convention')
    actors = {a['id']: a for a in scene['actors']}
    ego_id = next(a['id'] for a in scene['actors'] if a['role']=='ego')
    if not 1 <= len(scene['frames']) <= 512:
        raise HTTPException(413, 'Select a native source with 1..512 frames')
    result=[]
    for frame in scene['frames']:
        timestamp=str(round(frame['time_s']*1e9)); transformed={}
        for state in frame['actors']:
            actor=actors[state['id']]; yaw=state['heading_rad']; center=actor['bbox_center']; dims=actor['dimensions_m']
            x=state['x']+math.cos(yaw)*center['x']-math.sin(yaw)*center['y']
            y=state['y']+math.sin(yaw)*center['x']+math.cos(yaw)*center['y']
            item=dict(id=state['id'],x_m=float(x),y_m=float(-y),yaw_rad=float(math.atan2(-math.sin(yaw),math.cos(yaw))),
                length_m=float(dims['length_m']),width_m=float(dims['width_m']))
            if isinstance(state.get('velocity'),dict):
                item.update(vx_mps=float(state['velocity']['x']),vy_mps=float(-state['velocity']['y']))
            transformed[state['id']]=item
        ego=transformed.pop(ego_id); speed=math.hypot(ego.pop('vx_mps',0.),ego.pop('vy_mps',0.)); ego.pop('id')
        e=PlanningEgo(timestamp_ns=timestamp,frame_id='map',speed_mps=speed,source='native_carla_bbox_trace',**ego)
        o=ObjectTracks(timestamp_ns=timestamp,frame_id='map',coordinate_frame='world',velocity_semantics='absolute_ground_velocity',
            source='native_carla_bbox_trace',objects=list(transformed.values()))
        result.append((e.model_dump(mode='json'),o.model_dump(mode='json')))
    return result


def _native_recording(db, project, source, scene, receipt):
    from mcap.writer import Writer, CompressionType
    from .scene_sequences import EGO_SCHEMA, OBJECT_SCHEMA, EGO_SCHEMA_NAME, OBJECT_SCHEMA_NAME
    frames=carla_planning_frames(scene); output=io.BytesIO()
    writer=Writer(output,compression=CompressionType.NONE,enable_crcs=True,enable_data_crcs=True)
    writer.start(profile='axiom-log-sim-native/1',library='axiom-log-sim/1')
    es=writer.register_schema(EGO_SCHEMA_NAME,'jsonschema',canonical_bytes(EGO_SCHEMA))
    os=writer.register_schema(OBJECT_SCHEMA_NAME,'jsonschema',canonical_bytes(OBJECT_SCHEMA))
    ec=writer.register_channel('/planning/ego','json',es); oc=writer.register_channel('/planning/objects','json',os)
    writer.add_metadata('axiom.log_sim_source',{'schema_version':'log-sim-native-conversion/1',
        'lineage_json':canonical_bytes(receipt).decode(),'coordinate_conversion':'native_bbox_center_then_y_yaw_reflection',
        'source_hash':source['source_hash'],'physical_sensor_validation':'false'})
    for index,(ego,objects) in enumerate(frames):
        at=int(ego['timestamp_ns'])
        writer.add_message(ec,at,canonical_bytes(ego),at,sequence=index)
        writer.add_message(oc,at,canonical_bytes(objects),at,sequence=index)
    writer.finish(); raw=output.getvalue()
    with tempfile.TemporaryDirectory(prefix='axiom-log-sim-') as temp:
        path=Path(temp)/'trace.mcap'; path.write_bytes(raw)
        recording,_=import_recording(db,_IMPORT_LOCK,path,RecordingImportMetadata(name='CARLA 우회전 원본 궤적',
            source_kind='simulation',source='Native CARLA execution '+source['baseline_execution_id']),hashlib.sha256(raw).hexdigest(),len(raw))
    ref=dict(id=recording.id,sha256=recording.sha256,ego_channel_id=ec,objects_channel_id=oc,
        start_time_ns=recording.start_time_ns,end_time_ns=recording.end_time_ns,time_origin_ns='0')
    _store(db,project,SOURCE_KIND,NATIVE_ID,dict(kind='carla',source_hash=source['source_hash'],recording_ref=ref,receipt=receipt))
    return ref


def register_road_source(db, project_id, recording_id, title='nuScenes · 실도로 조사', *, library_metadata=None, **channels):
    """Operator preparation helper for an already imported original MCAP selection."""
    with db.read_session() as session:
        recording=session.get(RecordingRow,recording_id)
        if recording is None: raise HTTPException(404,'Imported road recording missing')
        scope=recording_source_scope(recording)
        if scope.get('source_dataset') != 'nuScenes mini': raise HTTPException(422,'Expected original nuScenes provenance')
        schemas={row.schema_id:row.payload['name'] for row in session.scalars(select(RecordingSchemaRow).where(RecordingSchemaRow.recording_id==recording_id))}
        available=list(session.scalars(select(RecordingChannelRow).where(RecordingChannelRow.recording_id==recording_id)))
        ref=dict(id=recording_id,sha256=recording.sha256,start_time_ns=recording.payload['start_time_ns'],
            end_time_ns=recording.payload['end_time_ns'])
        for key,schema in [('ego_channel_id','axiom.PlanningEgo.v1'),('objects_channel_id','axiom.ObjectTracks.v1'),('camera_channel_id','foxglove.CompressedImage')]:
            matching=[row for row in available if schemas.get(row.schema_ref)==schema and (channels.get(key) is None or row.channel_id==channels[key])]
            if len(matching)!=1: raise HTTPException(422,'Road source requires one explicit '+schema+' channel')
            ref[key]=matching[0].channel_id
        ego_messages=list(session.scalars(select(RecordingMessageRow).where(RecordingMessageRow.recording_id==recording_id,
            RecordingMessageRow.channel_id==ref['ego_channel_id']).order_by(RecordingMessageRow.log_time_key)))
        if not 2<=len(ego_messages)<=64: raise HTTPException(422,'Road demo requires 2..64 selected original ego frames')
        ref['time_origin_ns']=str(int(ego_messages[0].log_time_key)); ref['end_time_ns']=str(int(ego_messages[-1].log_time_key))
        duration=(int(ref['end_time_ns'])-int(ref['time_origin_ns']))/1e9
    definition=dict(kind='nuscenes',title=title,recording_ref=ref,duration_s=duration,frame_count=len(ego_messages))
    if library_metadata is not None:
        definition['library_metadata']=library_metadata
    return _store(db,project_id,SOURCE_KIND,'nuscenes-'+recording_id,definition)


def _road_frames(db,session,project,source):
    ref=source['recording_ref']; recording=session.get(RecordingRow,ref['id']); rows=list(session.scalars(select(RecordingMessageRow).where(
        RecordingMessageRow.recording_id==ref['id']).order_by(RecordingMessageRow.log_time_key,RecordingMessageRow.ordinal)))
    egos=[]; objects={}; cameras=[]
    for row in rows:
        at=int(row.log_time_key)
        if row.channel_id==ref['ego_channel_id'] and ref.get('ego_ordinals') is not None and row.ordinal not in ref['ego_ordinals']: continue
        if row.channel_id==ref['ego_channel_id']:
            egos.append((at,PlanningEgo.model_validate_json(read_message_bytes(db,recording,row))))
        elif row.channel_id==ref['objects_channel_id']:
            objects[at]=ObjectTracks.model_validate_json(read_message_bytes(db,recording,row))
        elif row.channel_id==ref.get('camera_channel_id'): cameras.append((at,row.ordinal))
    frames=[]
    for at,ego in egos:
        tracks=objects.get(at)
        if tracks is None or tracks.coordinate_frame!='world': raise HTTPException(409,'Road object frame missing or unsupported')
        prior=[(t,ordinal) for t,ordinal in cameras if t<=at and at-t<=500_000_000]
        camera=max(prior,default=None)
        frame=dict(time_s=(at-int(ref['time_origin_ns']))/1e9,timestamp_ns=str(at),
            ego=dict(x=ego.x_m,y=ego.y_m,yaw_rad=ego.yaw_rad,speed_mps=ego.speed_mps,length_m=ego.length_m,width_m=ego.width_m),
            objects=[dict(id=obj.id,x=obj.x_m,y=obj.y_m,yaw_rad=obj.yaw_rad,length_m=obj.length_m,width_m=obj.width_m,category=source.get('annotation_source', 'recorded object')) for obj in tracks.objects])
        if camera:
            frame.update(image_url=f'/api/v1/log-sim/sources/{source["id"]}/images/{camera[1]}?project_id={project}',
                image_timestamp_ns=str(camera[0]),image_age_ns=str(at-camera[0]))
        frames.append(frame)
    return frames


def _case_pin(session,source,body):
    if body.case_id is None:return None
    owned=_catalog(session,body.project_id,CASE_KIND,body.case_id)
    if owned is None or owned['definition']['source_hash']!=source['source_hash']:
        raise HTTPException(404,'Case is not linked to this project source')
    case=cases._get(session,body.case_id,body.case_revision); ref=source.get('recording_ref')
    if ref is None or case['segment']['recording_id']!=ref['id'] or case['segment']['channel_id']!=ref['ego_channel_id'] or case['segment']['recording_sha256']!=ref['sha256']:
        raise HTTPException(422,'Case must select this source ego recording')
    return dict(id=case['id'],revision=case['revision'],manifest_hash=case['manifest_hash'],segment_hash=case['segment_hash'])


def _preview(db,body):
    with db.read_session() as session:
        source=_source(session,body.project_id,body.source_id)
        if not source['capabilities']['variant']:raise HTTPException(422,'This source supports investigation only')
        pin=_case_pin(session,source,body)
        draft=object_demo._preview(session,PRESET,DemoPreview(project_id=body.project_id,parameters={'lead_wait_s':body.lead_wait_s}))
        original=object_demo._bound_scenario(session,body.project_id,PRESET,object_demo._binding(session,body.project_id,PRESET)['definition'])[0]['scenario']
        request=body.model_dump(mode='json',exclude={'preview_hash'})
        value=dict(schema_version='log-sim-preview/1',request=request,source_hash=source['source_hash'],case_ref=pin,
            baseline_execution_id=source['baseline_execution_id'],scenario=draft['scenario'],parameters=draft['parameters'],
            changes=object_demo._diff(original,draft['scenario']),execution_status='not_executed',
            replay_semantics='full_scenario_from_start',native_preflight_required=True)
        return value|{'preview_hash':digest(value)}


def register_log_sim_routes(api,db,registry):
    @api.get('/log-sim/sources',tags=['Log Sim'])
    def sources(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            items=[_native_source(session,project_id)]
            ids=set(session.scalars(select(Catalog.id).where(Catalog.project_id==project_id,Catalog.kind==SOURCE_KIND)))
            for identity in sorted(ids-{NATIVE_ID}):
                source=_road_source(session,project_id,identity)
                authorize_recording_source(request,db,project_id,source['recording_ref']['id']);items.append(source)
        return dict(schema_version='log-sim-sources/1',project_id=project_id,items=items)

    @api.get('/log-sim/sources/{identity}',tags=['Log Sim'])
    def source_detail(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            source=_source(session,project_id,identity)
            if source['kind']=='carla':
                scene,selected,receipt=_scene(db,session,project_id,source);events,metrics=_events_metrics(scene,selected)
                return source|dict(events=events,metrics=metrics,source_receipt=receipt)
            authorize_recording_source(request,db,project_id,source['recording_ref']['id'])
            return source|dict(road_frames=_road_frames(db,session,project_id,source),events=[],metrics={})

    @api.get('/log-sim/sources/{identity}/images/{ordinal}',tags=['Log Sim'])
    def road_image(identity:str,ordinal:int,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            source=_road_source(session,project_id,identity);ref=source['recording_ref']
            authorize_recording_source(request,db,project_id,ref['id']);row=session.get(RecordingMessageRow,(ref['id'],ordinal))
            if row is None or row.channel_id!=ref.get('camera_channel_id'):raise HTTPException(404,'Original front camera sample missing')
            message=json.loads(read_message_bytes(db,session.get(RecordingRow,ref['id']),row))
            if message.get('format') not in {'jpeg','png'}:raise HTTPException(422,'Unsupported original compressed image')
            try: raw=base64.b64decode(message['data'],validate=True)
            except (KeyError,ValueError) as exc:raise HTTPException(409,'Original image encoding invalid') from exc
            return Response(raw,media_type='image/'+message['format'],headers={'X-Content-SHA256':hashlib.sha256(raw).hexdigest()})

    @api.post('/log-sim/sources/{identity}/cases',status_code=201,tags=['Log Sim'],
        openapi_extra={'x-axiom-min-role':'Engineer'})
    def create_case(identity:str,body:LogCaseCreate,request:Request,idempotency_key:str=Header(alias='Idempotency-Key',min_length=1,max_length=180)):
        project_authorize(request,db,body.project_id,'Engineer')
        with db.read_session() as session:
            source=_source(session,body.project_id,identity)
            if body.end_time_s>source['duration_s']:raise HTTPException(422,'Case interval extends beyond source duration')
            ref=source.get('recording_ref')
            if source['kind']=='carla' and ref is None:scene,_,receipt=_scene(db,session,body.project_id,source)
            elif source['kind'] in {'nuscenes', 'recorded'}:authorize_recording_source(request,db,body.project_id,ref['id'])
        if ref is None:ref=_native_recording(db,body.project_id,source,scene,receipt)
        origin=int(ref['time_origin_ns']);start=origin+round(body.start_time_s*1e9);end=origin+round(body.end_time_s*1e9)
        selection=RecordingSelection(recording_id=ref['id'],channel_id=ref['ego_channel_id'],start_time_ns=str(start),end_time_ns=str(end))
        result=cases.create_case(db,CaseCreate(title=body.title,note=body.note,source=selection),
            'log-sim:'+digest({'project':body.project_id,'key':idempotency_key}))
        _store(db,body.project_id,CASE_KIND,result['id'],dict(source_id=identity,source_hash=source['source_hash'],
            case_id=result['id'],case_revision=result['revision'],case_manifest_hash=result['manifest_hash']))
        return result

    @api.get('/log-sim/sources/{identity}/cases/{case_id}',tags=['Log Sim'])
    def source_case(identity:str,case_id:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            source=_source(session,project_id,identity);owned=_catalog(session,project_id,CASE_KIND,case_id)
            if owned is None or owned['definition']['source_hash']!=source['source_hash']:
                raise HTTPException(404,'Case is not linked to this project source')
            if source['kind'] in {'nuscenes', 'recorded'}:authorize_recording_source(request,db,project_id,source['recording_ref']['id'])
            return cases._get(session,case_id,owned['definition']['case_revision'])

    @api.post('/log-sim/variants/preview',tags=['Log Sim'],openapi_extra={'x-axiom-read-only':True})
    def preview(body:LogVariantPreview,request:Request):
        project_authorize(request,db,body.project_id)
        return _preview(db,body)

    @api.post('/log-sim/variants',status_code=202,tags=['Log Sim'],openapi_extra={'x-axiom-min-role':'Engineer'})
    def submit(body:LogVariantSubmit,request:Request,idempotency_key:str=Header(alias='Idempotency-Key',min_length=1,max_length=180)):
        project_authorize(request,db,body.project_id,'Engineer')
        identity='variant_'+digest({'project':body.project_id,'key':idempotency_key})[:32]
        with db.read_session() as session:
            previous=_catalog(session,body.project_id,VARIANT_KIND,identity)
            if previous:
                if previous['definition']['request']!=body.model_dump(mode='json'):raise HTTPException(409,'Idempotency key identifies another Log Sim variant')
                return previous['definition']
        preview=_preview(db,body)
        if preview['preview_hash']!=body.preview_hash:raise HTTPException(409,'Source, Case or parameters changed; preview again')
        scenario=save_scenario(db,ScenarioV2Create(project_id=body.project_id,scenario=preview['scenario']),identity)
        with db.read_session() as session:
            baseline,_=object_demo._execution(session,body.project_id,preview['baseline_execution_id'])
        spec=ExperimentCreate(project_id=body.project_id,name=f'Log Sim · 앞차 {body.lead_wait_s:g}초',kind='object_sim',
            adapter_id=baseline['execution']['adapter_id'],adapter_version=baseline['execution']['adapter_version'],
            input=dict(scenario_id=scenario['id'],scenario_revision=scenario['revision'],configuration_hash=scenario['configuration_hash']),
            seed=scenario['scenario']['seed'])
        experiment=experiment_jobs.create(db,registry,spec,identity)
        execution=experiment_jobs.submit(db,registry,experiment['id'],experiment['preview_hash'],identity).model_dump(mode='json')
        value=dict(schema_version='log-sim-variant/1',id=identity,project_id=body.project_id,source_id=body.source_id,
            source_hash=preview['source_hash'],request=body.model_dump(mode='json'),case_ref=preview['case_ref'],parameters=preview['parameters'],
            baseline_execution_id=preview['baseline_execution_id'],scenario_ref={k:scenario[k] for k in ('id','revision','configuration_hash','manifest_hash')},
            experiment_id=experiment['id'],execution_id=execution['id'],execution=execution,created_at=utcnow(),
            replay_semantics='full_scenario_from_start')
        _store(db,body.project_id,VARIANT_KIND,identity,value)
        return value

    @api.get('/log-sim/variants',tags=['Log Sim'])
    def variants(request:Request,project_id:str='default',source_id:str|None=None):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            ids=set(session.scalars(select(Catalog.id).where(Catalog.project_id==project_id,Catalog.kind==VARIANT_KIND)))
            items=[_catalog(session,project_id,VARIANT_KIND,identity)['definition'] for identity in ids]
            items=[item for item in items if source_id is None or item['source_id']==source_id]
            for item in items:
                side,_=object_demo._execution(session,project_id,item['execution_id'])
                item['execution']=side['execution']
                if side['execution']['status']=='completed':
                    try:
                        scene,selected,_=_scene(db,session,project_id,dict(ready=True,baseline_execution_id=item['execution_id']))
                        _,item['metrics']=_events_metrics(scene,selected)
                    except HTTPException:
                        item['metrics_unavailable']=True
        return dict(items=sorted(items,key=lambda x:x['created_at'],reverse=True)[:50])
