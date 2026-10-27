"""Native boundary injection plans. Independent RTL/SPICE tests, not CARLA or hardware I/O."""
import json
from typing import Annotated, Literal, Union
from fastapi import Header, HTTPException, Request
from pydantic import Field, model_validator, model_serializer
from .base_schemas import StrictModel
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row


class StreamSettings(StrictModel):
    channel: int = Field(default=0, ge=0, le=3, strict=True)
    at_us: int = Field(default=5000, ge=1, le=900000, strict=True)
    duration_us: int = Field(default=30, ge=1, le=10000, strict=True)


class RawGap(StreamSettings):
    boundary: Literal['camera_raw_csi']


class RepeatedStall(StrictModel):
    at_us: int = Field(ge=1, le=9950000, strict=True)
    duration_us: int = Field(default=100, ge=1, le=500000, strict=True)


class FpgaStall(StreamSettings):
    boundary: Literal['fpga']
    at_us: int = Field(default=5000,ge=1,le=9950000,strict=True)
    frames: int = Field(default=30,ge=30,le=300,strict=True)
    duration_us: int = Field(default=100, ge=1, le=500000, strict=True)
    repeats: list[RepeatedStall] = Field(default_factory=list, max_length=4)
    @model_serializer(mode='wrap')
    def unchanged_single_stall(self, handler):
        value=handler(self)
        if not self.repeats:value.pop('repeats',None)
        if self.frames==30:value.pop('frames',None)
        return value
    @model_validator(mode='after')
    def recovery_window(self):
        end = self.at_us + self.duration_us
        for stall in self.repeats:
            if stall.at_us < end:
                raise ValueError('Repeated FPGA stalls must be ordered and must not overlap')
            end = stall.at_us + stall.duration_us
        if (end+50000)*30 > self.frames*1000000:
            raise ValueError('FPGA stall must leave at least 50ms of native recovery; increase frames if the fault is later')
        return self


class VoltageSettings(StrictModel):
    start_ms: float = Field(default=3, ge=.1, le=999, allow_inf_nan=False)
    end_ms: float = Field(default=7, gt=.1, le=999.99, allow_inf_nan=False)
    stop_ms: float = Field(default=10,ge=1,le=1000,allow_inf_nan=False)
    @model_validator(mode='after')
    def window(self):
        if self.end_ms-self.start_ms < .01:
            raise ValueError('Voltage fault needs at least 0.01ms and must end after it starts')
        if self.stop_ms-self.end_ms<.01:raise ValueError('Voltage model must leave at least 0.01ms after fault end')
        return self


class RepeatedPowerWindow(StrictModel):
    start_ms: float = Field(ge=.1, le=3999, allow_inf_nan=False)
    end_ms: float = Field(gt=.1, le=3999.99, allow_inf_nan=False)
    @model_validator(mode='after')
    def window(self):
        if self.end_ms-self.start_ms < .01:
            raise ValueError('Repeated power sag needs at least 0.01ms')
        return self


class PowerSag(VoltageSettings):
    boundary: Literal['power']
    voltage_v: float = Field(default=6, ge=0, lt=12, allow_inf_nan=False)
    stop_ms: float = Field(default=10,ge=1,le=4000,allow_inf_nan=False)
    step_us: float = Field(default=10,ge=1,le=100,allow_inf_nan=False)
    repeats: list[RepeatedPowerWindow] = Field(default_factory=list,max_length=4)
    @model_serializer(mode='wrap')
    def unchanged_single_sag(self,handler):
        value=handler(self)
        if not self.repeats:value.pop('repeats',None)
        if self.step_us==10:value.pop('step_us',None)
        return value
    @model_validator(mode='after')
    def repeat_windows(self):
        end=self.end_ms
        for fault in self.repeats:
            if fault.start_ms < end+.001:
                raise ValueError('Power sag repeats must be ordered, separate and leave the 1us source recovery edge')
            end=fault.end_ms
        if self.stop_ms-end<.01:
            raise ValueError('Power model must leave at least 0.01ms after last sag')
        if self.stop_ms*1000/self.step_us>250000:
            raise ValueError('Power transient exceeds 250000 requested steps; increase step_us or shorten stop_ms')
        return self


class AnalogOffset(VoltageSettings):
    boundary: Literal['analog']
    offset_v: float = Field(default=1, ge=-2.5, le=2.5, allow_inf_nan=False)
    @model_validator(mode='after')
    def nonzero(self):
        if self.offset_v == 0:raise ValueError('Offset injection must be nonzero')
        return self


class ModelInjectionSave(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    name: str = Field(default='경계 주입 모델 A/B', min_length=1, max_length=160)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
    settings: Annotated[Union[RawGap, FpgaStall, PowerSag, AnalogOffset], Field(discriminator='boundary')]


def catalog():
    return [dict(boundary=cls.model_fields['boundary'].annotation.__args__[0], title=title,
        settings=cls(boundary=cls.model_fields['boundary'].annotation.__args__[0]).model_dump(),
        scope=scope, engine=engine, carla_connected=False, physical_hardware_connected=False)
        for cls,title,scope,engine in [
            (RawGap,'RAW 스트림 입력 단절','CSI 수신 이후 RAW12 valid 단절 · CSI 패킷/PHY는 제외 · 4×1080p30', 'adas_module_rtl'),
            (FpgaStall,'FPGA 출력 정체','4채널 RTL의 선택 채널 ready 정체 · FIFO/abort/다음 프레임 회복', 'adas_module_rtl'),
            (PowerSag,'전원 전압 강하','12V 전원·2Ω 배선·100µF·120Ω 부하의 예시 회로 · ECU 전원 모델 미연결', 'spice'),
            (AnalogOffset,'아날로그 센서 오프셋','2.5V 센서·1kΩ/1µF 입력 필터의 예시 회로 · ECU ADC 미연결', 'spice')]]


def compile_plan(body):
    from .adas_module_schemas import AdasModuleConfig
    from .engineering_lab_schemas import SpiceEngineeringConfig
    p=body.settings.model_dump();boundary=p['boundary']
    item=next(x for x in catalog() if x['boundary']==boundary)
    if boundary in ('camera_raw_csi','fpga'):
        schedule=[dict(at_us=p['at_us'], duration_us=p['duration_us'])]+p.get('repeats',[])
        edges=[v*100 for fault in schedule for v in (fault['at_us'],fault['at_us']+fault['duration_us'])]
        windows=[]
        for start,stop in sorted([(0,1024)]+[(max(0,edge-128),edge+896) for edge in edges]):
            if windows and start<=windows[-1]['end_cycle']:
                windows[-1]['end_cycle']=max(stop,windows[-1]['end_cycle'])
            else:windows.append(dict(start_cycle=start,end_cycle=stop))
        frames=p.get('frames',30)
        extended={}
        if frames!=30:
            preview={0,frames-1}
            for fault in schedule:
                index=fault['at_us']*30//1000000
                preview.update(f for f in (index-1,index,index+1) if 0<=f<frames)
            extended=dict(frames=frames,max_cycles=(frames*100000000+29)//30+10000000,preview_frames=sorted(preview))
        baseline=AdasModuleConfig(engine='adas_module_rtl',trace_windows=windows,**extended).model_dump()
        faulted=AdasModuleConfig(engine='adas_module_rtl',trace_windows=windows,**extended, faults=[dict(channel=p['channel'],
            kind='input_gap' if boundary=='camera_raw_csi' else 'output_stall',
            at_cycle=fault['at_us']*100, duration_cycles=fault['duration_us']*100) for fault in schedule]).model_dump()
    else:
        nominal=12 if boundary=='power' else 2.5
        injected=p['voltage_v'] if boundary=='power' else nominal+p['offset_v']
        start=p['start_ms']/1000;end=p['end_ms']/1000;stop=p['stop_ms']/1000;edge=1e-6
        tail='.01' if stop==.01 else f'{stop:.9g}'
        load='Rwire source out 2\nCbulk out 0 100u\nRload out 0 120' if boundary=='power' else 'Rfilter source out 1k\nCfilter out 0 1u'
        def circuit(fault):
            # 1us ramps are an explicit source model, not measured supply edges.
            windows=[(start,end)]+[(f['start_ms']/1000,f['end_ms']/1000) for f in p.get('repeats',[])]
            points=' '.join(f'{a:.9g} {nominal} {a+edge:.9g} {injected} {b:.9g} {injected} {b+edge:.9g} {nominal}' for a,b in windows)
            source=f'PWL(0 {nominal} {points} {tail} {nominal})' if fault else f'DC {nominal}'
            return SpiceEngineeringConfig(engine='spice',netlist=f'* {boundary} injection reference; no ECU connection\nVsource source 0 {source}\n{load}\n.end\n',
                step_s=1e-5 if p.get('step_us',10)==10 else p['step_us']/1000000,stop_s=stop,probes=['v(source)','v(out)','i(Vsource)']).model_dump()
        baseline,faulted=circuit(False),circuit(True)
    result=dict(schema_version='hil-boundary-injection-plan/1',settings=p,seed=body.seed,
        scope=item['scope'],engine=item['engine'],carla_connected=False,physical_hardware_connected=False,
        sides={'baseline':baseline,'faulted':faulted})
    return result|{'sha256':digest(result)}


def register_model_injection_routes(api,db):
    from . import jobs,experiment_jobs
    from .engineering_lab import source_identity,read_test
    from .engineering_lab_schemas import EngineeringTestSave
    from .team_access import project_authorize
    base='/hil-bridge/model-injections'

    @api.get(base,tags=['hil-bridge'])
    def listing(request:Request,project_id:str='default'):
        from sqlalchemy import select
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            items=[]
            for row in session.scalars(select(Row).where(Row.project_id==project_id,Row.kind=='hil-model-injection').order_by(Row.id)):
                value=json.loads(row.manifest)
                if digest(value)!=row.manifest_hash:raise HTTPException(409,'Model injection integrity failed')
                items.append(value|{'sha256':row.manifest_hash})
            return {'items':items}

    @api.get(base+'/catalog',tags=['hil-bridge'])
    def templates(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        return dict(schema_version='hil-boundary-injection-catalog/1',items=catalog())

    @api.post(base,status_code=201,tags=['hil-bridge'])
    def save(body:ModelInjectionSave,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer')
        experiment_jobs._key(body.project_id,idempotency_key,'hil-model-injection')
        plan=compile_plan(body);identity='hilmodel_'+digest([body.project_id,idempotency_key])[:32]
        request_hash=digest(body.model_dump())
        with jobs._write(db) as session:
            old=session.get(Row,(body.project_id,'hil-model-injection',identity,1))
            if old:
                value=json.loads(old.manifest)
                if digest(value)!=old.manifest_hash or value['request_hash']!=request_hash:
                    raise HTTPException(409,'Model injection idempotency key reused with different settings')
                return value|{'sha256':old.manifest_hash}
            tests={}
            for side,config in plan['sides'].items():
                content=EngineeringTestSave(project_id=body.project_id,name=body.name+(' · A 정상' if side=='baseline' else ' · B 고장'),config=config,seed=body.seed).model_dump(exclude={'id','expected_revision'})
                from .engineering_lab_schemas import FIDELITY
                value=content|dict(schema_version='engineering-test/1',id=uid('engt'),revision=1,created_at=utcnow(),fidelity=FIDELITY,
                    injection_origin=dict(pair_id=identity,plan_sha256=plan['sha256'],side=side,boundary=body.settings.boundary))
                value.update(source_identity(value['config']));sha=digest(value)
                session.add(Row(project_id=body.project_id,kind='engineering-test',id=value['id'],revision=1,manifest=canonical_bytes(value),manifest_hash=sha))
                tests[side]={k:(sha if k=='sha256' else value[k]) for k in ('id','revision','sha256')}
            value=dict(schema_version='hil-model-injection/1',id=identity,revision=1,project_id=body.project_id,name=body.name,
                request_hash=request_hash,plan=plan,tests=tests,created_at=utcnow())
            sha=digest(value);session.add(Row(project_id=body.project_id,kind='hil-model-injection',id=identity,revision=1,manifest=canonical_bytes(value),manifest_hash=sha))
            return value|{'sha256':sha}

    @api.get(base+'/{identity}',tags=['hil-bridge'])
    def get(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            row=session.get(Row,(project_id,'hil-model-injection',identity,1))
            if row is None:raise HTTPException(404,'Model injection pair not found')
            value=json.loads(row.manifest)
            if digest(value)!=row.manifest_hash:raise HTTPException(409,'Model injection integrity failed')
            for ref in value['tests'].values():
                test=read_test(session.get(Row,(project_id,'engineering-test',ref['id'],ref['revision'])))
                if test['sha256']!=ref['sha256']:raise HTTPException(409,'Model injection test identity changed')
            return value|{'sha256':row.manifest_hash}
