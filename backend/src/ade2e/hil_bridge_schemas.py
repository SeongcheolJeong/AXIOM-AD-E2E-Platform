"""Saved SIL-to-HIL bindings. Device paths and executable commands are operator-owned."""
from typing import Literal
from pydantic import Field, field_validator, model_validator
from .base_schemas import StrictModel
from .vecu_schemas import SaveBase
from .hil_beta_schemas import HilTestRef, safe_text
from .service import digest

BOUNDARIES = {'udp_delay':'perception.udp.tx', 'udp_drop':'perception.udp.tx',
    'udp_repeat':'perception.udp.tx', 'restbus_drop':'restbus.can.tx',
    'can_command_drop':'adas_brake.can.bridge', 'brake_feedback_drop':'brake.plant.feedback',
    'ecu_reset':'adas.reset', 'os_event_drop':'adas.os.event',
    **{'model_'+k:'model.'+k+'.measurement' for k in ('camera_raw_csi','fpga','power','analog')}}
SUPPORTED = set(BOUNDARIES)-{'ecu_reset','os_event_drop'}-{k for k in BOUNDARIES if k.startswith('model_')}

def supports(profile, kind):
    return profile['fault_capabilities'].get(kind) == 'supported'

def profiles():
    result=[]
    for target in ('vcan','hardware'):
        item=dict(schema_version='hil-bench-profile/1',id='axiom-two-ecu-'+target,version=4,target=target,
            name='Linux vCAN + UDP · 독립 ADAS/Brake' if target=='vcan' else '실물 ADAS + Brake · 장비 등록 필요',
            codec_id='axiom-example/1',roles={'adas':'reference_process' if target=='vcan' else 'physical_ecu',
                'brake':'reference_process' if target=='vcan' else 'physical_ecu','perception':'software','restbus':'software'},
            can=dict(format='classic_can_11bit_8byte',command_id=256,status_id=257,vehicle_state_id=512,heartbeat_id=513),
            periods_ms=dict(plant=50,objects=50,restbus=10),max_lateness_ms=5,
            inputs=['AXO1 objects','versioned route/physics initialization','versioned ego pose'],
            fault_capabilities={kind:('supported' if kind in SUPPORTED or target=='vcan' else 'driver_required') for kind in BOUNDARIES},
            other_boundaries={key:'unsupported' for key in ('camera_raw_csi','fpga','power','analog')},
            boundary_model_tests=['camera_raw_csi','fpga','power','analog'],
            model_loop=target=='vcan',model_loop_scope='Native output replay to reference ECU measurement monitor; CARLA closed loop. No pixel perception, physical ADC or bidirectional RTL/SPICE coupling.',
            model_combination='any_protect' if target=='vcan' else 'driver_required',
            fault_support_details={
                'udp_delay':'게이트웨이 송신 큐에서 지정 시간 대기 후 UDP 전달',
                'udp_drop':'게이트웨이의 객체 UDP 송신 생략',
                'udp_repeat':'직전 객체 패킷의 bytes와 sequence를 그대로 재전송',
                'restbus_drop':'차량 상태와 heartbeat 생성 주체에서 CAN 송신 중단',
                'can_command_drop':'분리된 CAN A/B 사이의 지정 명령 중계 차단 · 두 구간 필수',
                'brake_feedback_drop':'Brake 출력의 차량 모델 전달 차단 · 피드백 상실 시 시험 중단',
                'ecu_reset':'참조 ADAS 상태 초기화·명령 송신 억제·epoch 증가·새 입력 복구' if target=='vcan' else '장치 리셋 드라이버·재초기화·재시작 확인 연동 미구현',
                'os_event_drop':'참조 ADAS의 제어 tick 실행과 CAN 명령 생성 억제 · 실제 OS 커널 검증 제외' if target=='vcan' else 'ECU 펌웨어 내부 이벤트 시험 훅·누락/복원·관측 연동 미구현'},
            hard_realtime=False,physical_validation=False)
        result.append(item|{'sha256':digest(item)})
    return result

def get_profile(identity):
    return next((p for p in profiles() if p['id']==identity),None)

class FeedbackFault(StrictModel):
    kind: Literal['brake_feedback_drop'] = 'brake_feedback_drop'
    start_ms: int = Field(ge=1,strict=True)
    end_ms: int = Field(le=60000,strict=True)
    @model_validator(mode='after')
    def interval(self):
        if self.start_ms>=self.end_ms:raise ValueError('Fault interval must be nonempty')
        return self

class ReferenceInternalFault(StrictModel):
    kind: Literal['ecu_reset', 'os_event_drop']
    start_ms: int = Field(ge=50, le=59000, strict=True)
    end_ms: int = Field(ge=100, le=60000, strict=True)
    @model_validator(mode='after')
    def interval(self):
        if self.start_ms >= self.end_ms or self.start_ms % 50 or self.end_ms % 50:
            raise ValueError('Reference internal faults require a nonempty interval on the 50ms task grid')
        return self

class ModelCoupling(StrictModel):
    pair_ref: HilTestRef
    baseline_execution: str = Field(pattern=r'^job_[a-zA-Z0-9_]+$',max_length=80)
    faulted_execution: str = Field(pattern=r'^job_[a-zA-Z0-9_]+$',max_length=80)
    start_ms: int = Field(default=3000,ge=500,le=15000,strict=True)
    min_hold_ms: int = Field(default=500,ge=50,le=2000,strict=True)
    recovery_samples: int = Field(default=3,ge=1,le=20,strict=True)
    @model_validator(mode='after')
    def grid(self):
        if self.start_ms%50 or self.min_hold_ms%50:raise ValueError('Model loop requires 50ms scheduling grid')
        if self.baseline_execution==self.faulted_execution:raise ValueError('Distinct normal/fault model executions required')
        return self

class BindingSave(SaveBase):
    source_kind: Literal['vecu-network-test','vecu-test','hil-test'] = 'vecu-network-test'
    source_ref: HilTestRef
    profile_id: Literal['axiom-two-ecu-vcan','axiom-two-ecu-hardware'] = 'axiom-two-ecu-vcan'
    max_lateness_ms: float = Field(default=5,gt=0,le=50,allow_inf_nan=False)
    additional_faults: list[FeedbackFault | ReferenceInternalFault] = Field(default_factory=list,max_length=16)
    model_coupling: ModelCoupling | None = None
    model_couplings: list[ModelCoupling] = Field(default_factory=list,max_length=4)
    @model_validator(mode='after')
    def model_selection(self):
        if self.model_coupling and self.model_couplings:raise ValueError('Select single or multiple model coupling, not both')
        if self.model_couplings and len(self.model_couplings)<2:raise ValueError('Multiple model coupling requires at least two members')
        if len({v.pair_ref.id for v in self.model_couplings})!=len(self.model_couplings):raise ValueError('Duplicate model pair')
        return self
    @field_validator('name')
    @classmethod
    def name_clean(cls,v):
        v=safe_text(v)
        if not v:raise ValueError('Binding name is required')
        return v
