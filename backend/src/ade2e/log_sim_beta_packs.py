"""Small task packs; original executions and authored input examples stay distinct."""
import io
from fastapi import HTTPException, Request, Response
from pydantic import Field
from mcap.writer import Writer
from . import log_sim
from .base_schemas import StrictModel
from .log_sim_replays import _put, _get
from .project_recordings import authorize_recording_source
from .scene_sequences import EGO_SCHEMA, OBJECT_SCHEMA
from .service import canonical_bytes, utcnow
from .team_access import project_authorize

PACKS = [
    dict(id='stop-resume', title='정지·재출발', question='자차가 멈추고 다시 움직일 때 요청은 어떻게 달라지는가?', edit='차간 시간', metric='정지 구간·가속도 요청', case='재출발 직전과 직후를 포함해 Case로 저장'),
    dict(id='lead-brake', title='앞차 감속', question='앞 객체와 거리가 줄 때 감속 요청은 언제 달라지는가?', edit='정지 여유 거리', metric='감속 요청·최초 차이 시점', case='차이가 시작된 시점과 원본을 기록'),
    dict(id='following-gap', title='차간 거리', question='같은 로그에서 차간 시간 변경은 어떤 요청 차이를 만드는가?', edit='차간 시간', metric='판단 차이·가감속 방향 반대', case='방향이 반대인 후보 또는 차이 없음 관찰 저장'),
    dict(id='cut-in', title='끼어들기', question='옆 객체가 앞으로 이동하는 구간에서 제어 요청은 어떻게 달라지는가?', edit='차간 시간', metric='객체 위치·요청 변화', case='객체 진입 전후와 검토 메모 저장'),
    dict(id='intersection', title='교차로 상호작용', question='교차 객체가 있는 원본에서 설정 변경 효과와 한계는 무엇인가?', edit='목표 속도', metric='기록 궤적·요청 차이', case='효과 또는 변화 없음과 제어기 한계를 기록'),
    dict(id='input-errors', title='입력 누락·시각 불일치', question='누락된 객체 관측을 빈 공간으로 처리하지 않는가?', edit='지원 예제와 오류 예제 업로드', metric='입력 점검 오류', case='안내에 따라 정상 입력을 다시 등록'),
]


def example_mcap(invalid=False, camera=False):
    out=io.BytesIO();w=Writer(out);w.start(profile='axiom-authored-log-beta-example/1')
    es=w.register_schema('axiom.PlanningEgo.v1','jsonschema',canonical_bytes(EGO_SCHEMA))
    os=w.register_schema('axiom.ObjectTracks.v1','jsonschema',canonical_bytes(OBJECT_SCHEMA))
    ec=w.register_channel('/planning/ego','json',es);oc=w.register_channel('/planning/objects','json',os)
    w.add_metadata('axiom.authored_example',{'scope':'synthetic software input example; not real road or sensor validation'})
    for i in range(12):
        at=1_000_000_000+i*500_000_000
        ego=dict(timestamp_ns=str(at),frame_id='map',x_m=float(i),y_m=0.,yaw_rad=0.,speed_mps=4.,length_m=4.5,width_m=1.9,source='authored_beta_example')
        objects=dict(timestamp_ns=str(at),frame_id='map',coordinate_frame='world',velocity_semantics='absolute_ground_velocity',source='authored_beta_example',objects=[dict(id='lead',x_m=14.,y_m=0.,yaw_rad=0.,length_m=4.,width_m=2.,vx_mps=0.,vy_mps=0.)])
        w.add_message(ec,at,canonical_bytes(ego),at,i)
        if not invalid or i!=4:w.add_message(oc,at,canonical_bytes(objects),at,i)
    w.finish();return out.getvalue()


class PackBind(StrictModel):
    project_id:str=Field(default='default',min_length=1,max_length=64)
    source_id:str=Field(min_length=1,max_length=100)
    source_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    note:str=Field(min_length=1,max_length=2000)


def register_log_sim_pack_routes(api,db):
    @api.get('/log-sim/packs',tags=['Log Sim beta'])
    def packs(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        result=[]
        with db.read_session() as session:
            for pack in PACKS:
                binding=log_sim._catalog(session,project_id,'log-beta-pack',pack['id'])
                value=pack|{'binding':None,'status':'input_example' if pack['id']=='input-errors' else 'unbound'}
                if binding:
                    b=binding['definition'];source=log_sim._source(session,project_id,b['source_id'])
                    if source['source_hash']!=b['source_hash']:raise HTTPException(409,'Pack source changed')
                    if source['kind']!='carla':authorize_recording_source(request,db,project_id,source['recording_ref']['id'])
                    value.update(binding=b,status='ready')
                result.append(value)
        return {'schema_version':'log-beta-packs/1','items':result}

    @api.post('/log-sim/packs/{identity}/binding',tags=['Log Sim beta'])
    def bind(identity:str,body:PackBind,request:Request):
        principal=project_authorize(request,db,body.project_id,'Admin')
        if principal.mode=='project':raise HTTPException(403,'Only the operator prepares representative beta packs')
        if identity not in {p['id'] for p in PACKS if p['id']!='input-errors'}:raise HTTPException(404,'Pack not found')
        with db.session.begin() as session:
            source=log_sim._source(session,body.project_id,body.source_id)
            if source['kind']!='carla':authorize_recording_source(request,db,body.project_id,source['recording_ref']['id'])
            if source['source_hash']!=body.source_hash:raise HTTPException(409,'Pack must pin the exact source hash')
            value=body.model_dump()|{'id':identity,'created_at':utcnow(),'source_kind':source['kind'],'annotation_source':source.get('annotation_source')}
            _put(session,body.project_id,'log-beta-pack',identity,value)
            return value

    @api.get('/log-sim/example.mcap',tags=['Log Sim beta'])
    def example(request:Request,project_id:str='default',invalid:bool=False):
        project_authorize(request,db,project_id)
        return Response(example_mcap(invalid),media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="authored-log-example.mcap"'})
