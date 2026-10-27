"""Bounded, immutable authoring contract for the native vECU network lab."""
import ipaddress
from typing import Literal
from pydantic import Field, model_validator, field_validator
from .base_schemas import StrictModel
from .hil_beta_schemas import HilTestRef, safe_text
from .vecu_schemas import SaveBase, Action

PROFILE_ID = 'actual_silkit_autoas_trampoline'
ROLES = ['perception', 'adas', 'brake', 'restbus']
FAULT_KINDS = ['udp_delay', 'udp_drop', 'udp_repeat', 'can_command_drop', 'restbus_drop', 'ecu_reset', 'os_event_drop']

class CanConfig(StrictModel):
    format: Literal['classic_can_11bit_8byte'] = 'classic_can_11bit_8byte'
    command_id: Literal[256] = 256
    status_id: Literal[257] = 257
    vehicle_state_id: Literal[512] = 512
    heartbeat_id: Literal[513] = 513
    command_period_ms: Literal[50] = 50
    restbus_period_ms: Literal[10] = 10
    watchdog_ms: Literal[100] = 100
    recovery_packets: Literal[3] = 3
    @model_validator(mode='after')
    def unique_ids(self):
        ids=[self.command_id,self.status_id,self.vehicle_state_id,self.heartbeat_id]
        if len(set(ids)) != len(ids):raise ValueError('CAN message IDs must be unique')
        return self

class EthernetConfig(StrictModel):
    protocol: Literal['ethernet_ipv4_udp'] = 'ethernet_ipv4_udp'
    source_ip: str = '192.0.2.10'
    destination_ip: str = '192.0.2.20'
    source_port: int = Field(default=39001, ge=1, le=65535, strict=True)
    destination_port: int = Field(default=39002, ge=1, le=65535, strict=True)
    period_ms: Literal[50] = 50
    @field_validator('source_ip','destination_ip')
    @classmethod
    def ipv4(cls,value):
        address=ipaddress.IPv4Address(value)
        if address.is_multicast or address.is_unspecified or address.is_loopback:
            raise ValueError('A unicast virtual IPv4 address is required')
        return str(address)
    @model_validator(mode='after')
    def unique_endpoints(self):
        if self.source_ip==self.destination_ip:raise ValueError('ECU IPv4 addresses must differ')
        return self

class PolicyConfig(StrictModel):
    max_object_age_ms: Literal[250] = 250
    recovery_fresh_packets: Literal[3] = 3

class NetworkSave(SaveBase):
    profile_id: Literal['actual_silkit_autoas_trampoline'] = PROFILE_ID
    roles: list[Literal['perception','adas','brake','restbus']] = Field(default_factory=lambda:list(ROLES),min_length=4,max_length=4)
    tick_ms: Literal[1] = 1
    bsw_period_ms: Literal[10] = 10
    control_period_ms: Literal[50] = 50
    can: CanConfig = Field(default_factory=CanConfig)
    ethernet: EthernetConfig = Field(default_factory=EthernetConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    @model_validator(mode='after')
    def fixed_roles(self):
        if self.roles!=ROLES:raise ValueError('The registered profile requires perception, adas, brake, restbus in that order')
        return self
    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        value=safe_text(value)
        if not value:raise ValueError('A network name is required')
        return value

class Fault(StrictModel):
    kind: Literal['udp_delay','udp_drop','udp_repeat','can_command_drop','restbus_drop','ecu_reset','os_event_drop']
    start_ms: int = Field(default=3000, ge=1, le=59999, strict=True)
    end_ms: int = Field(default=7000, ge=2, le=60000, strict=True)
    delay_ms: Literal[400] = 400
    reset_restart_ms: Literal[200] = 200
    target_ecu: Literal['adas'] = 'adas'
    @model_validator(mode='after')
    def interval(self):
        if self.start_ms>=self.end_ms:raise ValueError('Fault requires start_ms < end_ms')
        if self.kind=='ecu_reset' and self.start_ms+self.reset_restart_ms>self.end_ms:
            raise ValueError('Reset recovery must fit inside the fault interval')
        return self

class NetworkTestSave(SaveBase):
    network_ref: HilTestRef
    mode: Literal['fixed_replay','carla_closed_loop'] = 'fixed_replay'
    input_ref: HilTestRef | None = None
    hil_test_ref: HilTestRef | None = None
    faults: list[Fault] = Field(default_factory=list,max_length=16)
    seed: int = Field(default=42,ge=0,le=2147483647,strict=True)
    @model_validator(mode='after')
    def inputs(self):
        if self.mode=='fixed_replay':
            if self.input_ref is None or self.hil_test_ref is not None:raise ValueError('Fixed replay requires only input_ref')
        elif self.hil_test_ref is None or self.input_ref is not None:raise ValueError('CARLA closed loop requires only hil_test_ref')
        ordered=sorted(self.faults,key=lambda fault:fault.start_ms)
        if self.faults!=ordered:
            raise ValueError('Fault intervals must be ordered by start_ms')
        for index,first in enumerate(ordered):
            for second in ordered[index+1:]:
                if second.start_ms>=first.end_ms:break
                if {first.kind,second.kind}=={'restbus_drop','ecu_reset'}:
                    restbus=first if first.kind=='restbus_drop' else second
                    reset=first if first.kind=='ecu_reset' else second
                    if (restbus.start_ms<=reset.start_ms<restbus.end_ms and
                        (reset.end_ms<=restbus.end_ms or reset.end_ms==reset.start_ms+reset.reset_restart_ms)):
                        continue
                raise ValueError('Overlapping faults support only an ADAS reset starting during a Restbus interruption; a reset crossing recovery must end at its 200ms restart boundary')
        # The v1 CAN command carries an 8-bit sequence. Bound cumulative loss
        # across adjacent fault types, not just each individual fault duration.
        missing=0
        for tick in range(0,max((fault.end_ms for fault in ordered),default=0)+50,50):
            blocked=any((fault.kind in ('can_command_drop','os_event_drop') and fault.start_ms<=tick<fault.end_ms)
                or (fault.kind=='ecu_reset' and fault.start_ms<=tick<fault.start_ms+fault.reset_restart_ms) for fault in ordered)
            missing=missing+1 if blocked else 0
            if missing>=127:
                raise ValueError('CAN counter recovery supports fewer than 127 consecutive missing 50ms commands (6.35s); split with successful delivery')
        return self
    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        value=safe_text(value)
        if not value:raise ValueError('A network test name is required')
        return value
