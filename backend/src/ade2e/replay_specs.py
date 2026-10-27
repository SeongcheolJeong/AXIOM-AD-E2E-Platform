"""Normal project-scoped authoring and preflight for temporal replay experiments."""
from fastapi import HTTPException, Query, Request
from sqlalchemy import select

from .replay_spec_models import ReplaySpecRow
from .replay_spec_schemas import ReplaySpecCreate, ReplaySpecDetail
from .scene_sequence_models import SceneSequenceRow
from .scene_sequence_schemas import SceneSequenceCreate, SceneSequenceDetail, SceneSequenceFreeze
from .scene_sequences import (EGO_SCHEMA, EGO_SCHEMA_NAME, OBJECT_SCHEMA, OBJECT_SCHEMA_NAME,
    create_scene_sequence, discover_sequence_sources, get_scene_sequence, sequence_preflight, validate_retained_sequence)
from .project_recordings import authorize_recording_source
from .service import digest, uid, utcnow
from .team_access import project_authorize


def capabilities():
    return {'schema_version':'replay-capabilities/1','adapter_id':'reference-planning-replay','adapter_version':'1',
        'modes':['original_replay','open_loop','planning_closed_loop'],
        'source_types':['recording','authored_reference'],'ego_schema_name':EGO_SCHEMA_NAME,
        'capture_clock_contract':'v1_payload_timestamp_equals_native_mcap_log_timestamp',
        'objects_schema_name':OBJECT_SCHEMA_NAME,'ego_schema':EGO_SCHEMA,'objects_schema':OBJECT_SCHEMA,
        'max_frames':512,'max_objects_per_frame':128,'max_step_ns':'1000000000',
        'controller':{'id':'geometric-headway','version':'1','learned_model':False},
        'dynamics':{'id':'planar-kinematic-bicycle','version':'1','vehicle_dynamics_validation':False},
        'camera_e2e_closed_loop':False,'surrounding_actors_reactive':False,
        'limitations':['Only explicit supported ego/object schemas establish planning inputs.',
            'Legacy recording sources require an explicit immutable operator grant for project users.',
            'No camera reconstruction, lane geometry inference or commercial driving safety verdict.']}


def get_replay_spec(session, identifier):
    row = session.get(ReplaySpecRow,identifier)
    if row is None:
        raise HTTPException(404,'Replay spec not found')
    detail = ReplaySpecDetail.model_validate(row.payload)
    snapshot = {key:value for key,value in row.payload.items() if key not in {'sha256'}}
    if (digest(snapshot) != row.sha256 or detail.sha256 != row.sha256 or detail.id != row.id
            or detail.project_id != row.project_id or detail.request.sequence_id != row.sequence_id):
        raise HTTPException(409,'Replay spec integrity check failed')
    return detail


def create_replay_spec(session, db, body):
    sequence = get_scene_sequence(session,body.sequence_id)
    if sequence.project_id != body.project_id:
        raise HTTPException(404,'Project scene sequence not found')
    if sequence.sha256 != body.sequence_sha256:
        raise HTTPException(409,'Pinned scene sequence hash differs from the selected immutable source')
    detail = {'id':uid('replay'),'project_id':body.project_id,'name':body.name,'created_at':utcnow(),
        'request':body.model_dump(mode='json'),'sequence_snapshot':sequence.snapshot,
        'fidelity':'authored_planar_reference' if sequence.source_type=='authored_reference' else 'recorded_objects_reference_vehicle_feedback',
        'limitations':sequence.limitations+['Camera pixels are retained nonreactive source references; camera E2E closed loop is unsupported.']}
    detail['sha256'] = digest(detail)
    result = ReplaySpecDetail.model_validate(detail)
    session.add(ReplaySpecRow(id=result.id,sequence_id=sequence.id,project_id=result.project_id,
        created_at=result.created_at,sha256=result.sha256,payload=detail))
    session.flush()
    return result


def replay_preflight(session,db,detail):
    sequence = get_scene_sequence(session,detail.request.sequence_id)
    reasons,warnings = [],[]
    if sequence.project_id != detail.project_id or sequence.sha256 != detail.request.sequence_sha256 or sequence.snapshot != detail.sequence_snapshot:
        reasons.append('Pinned temporal scene sequence differs from replay spec')
    try:
        validate_retained_sequence(session,db,sequence)
    except (ValueError,TypeError,HTTPException) as error:
        reasons.append('Retained source integrity: '+ str(getattr(error,'detail',error)))
    frames = detail.sequence_snapshot['frames']
    missing = sum(frame['object_drop'] is not None for frame in frames)
    if detail.request.mode == 'planning_closed_loop':
        if len(frames)<2:
            reasons.append('Closed-loop vehicle feedback requires at least two retained input times')
        if missing:
            reasons.append('Closed loop requires complete causal object observations; missing/stale steps are not free space')
    elif missing:
        warnings.append('Missing object steps remain unevaluated; the reference planner emits a diagnostic stop request')
    if 'lane_departure' in detail.request.observers:
        warnings.append('Lane departure remains unevaluated because no retained map lane geometry is supported')
    if 'goal_arrival' in detail.request.observers and detail.request.goal is None:
        warnings.append('Goal arrival remains unevaluated because no goal was configured')
    return {'schema_version':'replay-preflight/1','replay_spec_id':detail.id,'replay_spec_sha256':detail.sha256,
        'sequence_sha256':sequence.sha256,'executable':not reasons,'blocking_reasons':reasons,'warnings':warnings,
        'mode':detail.request.mode,'source_type':sequence.source_type,'source_kind':sequence.source_kind,
        'frame_count':len(frames),'object_steps_available':len(frames)-missing,'object_steps_unevaluated':missing,
        'sensor_message_drops':sum(sensor['drop'] is not None for frame in frames for sensor in frame['sensors']),
        'camera_e2e_closed_loop':False,'surrounding_actors_reactive':False,
        'source_use_scope':sequence.snapshot.get('source_use_scope',{'use_scope':'unknown','commercial_use':None}),
        'execution_fidelity':detail.fidelity,'estimated_reference_steps':len(frames)}


def register_replay_spec_routes(api,db):
    @api.get('/replay-specs/capabilities',tags=['replay'])
    def replay_capabilities():
        return capabilities()

    @api.get('/scene-sequences/sources/{recording_id}',tags=['replay'])
    def source_discovery(recording_id:str,request:Request,project_id:str='default'):
        access = authorize_recording_source(request,db,project_id,recording_id)
        with db.read_session() as session:
            return discover_sequence_sources(session,recording_id)|{'project_id':project_id,
                'access_scope':access['access_scope'],'source_use_scope':access['source_use_scope'],
                'capture_clock_contract':'v1_payload_timestamp_equals_native_mcap_log_timestamp'}

    @api.post('/scene-sequences/preview',tags=['replay'],openapi_extra={'x-axiom-min-role':'Viewer'})
    def sequence_preview(body:SceneSequenceCreate,request:Request):
        project_authorize(request,db,body.project_id,'Viewer')
        if body.source_type=='recording':
            authorize_recording_source(request,db,body.project_id,body.recording_id)
        with db.read_session() as session:
            return sequence_preflight(session,db,body)

    @api.post('/scene-sequences/freeze',response_model=SceneSequenceDetail,status_code=201,tags=['replay'])
    def sequence_freeze(body:SceneSequenceFreeze,request:Request):
        project_authorize(request,db,body.definition.project_id,'Engineer')
        if body.definition.source_type=='recording':
            authorize_recording_source(request,db,body.definition.project_id,body.definition.recording_id)
        with db.session.begin() as session:
            return create_scene_sequence(session,db,body.definition,expected_snapshot_sha256=body.preview_sha256)

    @api.post('/scene-sequences',response_model=SceneSequenceDetail,status_code=201,tags=['replay'])
    def sequence_create(body:SceneSequenceCreate,request:Request):
        project_authorize(request,db,body.project_id,'Engineer')
        if body.source_type=='recording':
            authorize_recording_source(request,db,body.project_id,body.recording_id)
        with db.session.begin() as session:
            return create_scene_sequence(session,db,body)

    @api.get('/scene-sequences',tags=['replay'])
    def sequence_list(request:Request,project_id:str='default',limit:int=Query(50,ge=1,le=100)):
        project_authorize(request,db,project_id,'Viewer')
        with db.read_session() as session:
            rows = session.scalars(select(SceneSequenceRow).where(SceneSequenceRow.project_id==project_id)
                .order_by(SceneSequenceRow.created_at.desc(),SceneSequenceRow.id).limit(limit))
            return {'items':[{'id':row.id,'project_id':row.project_id,'name':row.payload['name'],
                'created_at':row.created_at,'sha256':row.sha256,'source_type':row.payload['source_type'],
                'source_kind':row.payload['source_kind'],'frame_count':row.payload['frame_count']} for row in rows]}

    @api.get('/scene-sequences/{sequence_id}',response_model=SceneSequenceDetail,tags=['replay'])
    def sequence_get(sequence_id:str,request:Request):
        with db.read_session() as session:
            detail = get_scene_sequence(session,sequence_id)
        project_authorize(request,db,detail.project_id,'Viewer')
        return detail

    @api.post('/replay-specs',response_model=ReplaySpecDetail,status_code=201,tags=['replay'])
    def replay_create(body:ReplaySpecCreate,request:Request):
        project_authorize(request,db,body.project_id,'Engineer')
        with db.session.begin() as session:
            return create_replay_spec(session,db,body)

    @api.get('/replay-specs',tags=['replay'])
    def replay_list(request:Request,project_id:str='default',limit:int=Query(50,ge=1,le=100)):
        project_authorize(request,db,project_id,'Viewer')
        with db.read_session() as session:
            rows = session.scalars(select(ReplaySpecRow).where(ReplaySpecRow.project_id==project_id)
                .order_by(ReplaySpecRow.created_at.desc(),ReplaySpecRow.id).limit(limit))
            return {'items':[{'id':row.id,'project_id':row.project_id,'name':row.payload['name'],
                'sha256':row.sha256,'created_at':row.created_at,'sequence_id':row.sequence_id,
                'mode':row.payload['request']['mode'],'fidelity':row.payload['fidelity']} for row in rows]}

    @api.get('/replay-specs/{replay_spec_id}',response_model=ReplaySpecDetail,tags=['replay'])
    def replay_get(replay_spec_id:str,request:Request):
        with db.read_session() as session:
            detail = get_replay_spec(session,replay_spec_id)
        project_authorize(request,db,detail.project_id,'Viewer')
        return detail

    @api.post('/replay-specs/{replay_spec_id}/preflight',tags=['replay'])
    def replay_check(replay_spec_id:str,request:Request):
        with db.read_session() as session:
            detail = get_replay_spec(session,replay_spec_id)
        project_authorize(request,db,detail.project_id,'Viewer')
        with db.read_session() as session:
            return replay_preflight(session,db,detail)
