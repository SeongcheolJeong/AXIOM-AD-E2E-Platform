import pytest
from axiom_hil_gateway.hooks import ReferenceHooks,validate_runtime_faults
from axiom_hil_gateway.evidence import derive


def test_overlapping_reset_and_event_loss_keep_independent_state_and_repeated_epochs():
    hooks=ReferenceHooks([dict(kind='ecu_reset',start_ms=3000,end_ms=3200),dict(kind='ecu_reset',start_ms=6900,end_ms=7100),dict(kind='os_event_drop',start_ms=3100,end_ms=7000)])
    assert hooks.advance(2999)==[]
    assert hooks.advance(3000)==[('reset_asserted',{'epoch':1})]
    hooks.advance(3100);assert hooks.reset and hooks.os_blocked
    assert hooks.advance(3200)==[('reset_released',{'epoch':1})]
    assert hooks.os_blocked and not hooks.reset
    hooks.advance(6900);assert hooks.epoch==2 and hooks.reset
    hooks.advance(7000);assert hooks.reset and not hooks.os_blocked
    hooks.advance(7100);assert not hooks.reset


@pytest.mark.parametrize('kind',['ecu_reset','os_event_drop'])
def test_internal_fault_rejected_at_runtime_for_hardware(kind):
    f=dict(kind=kind,start_ms=3000,end_ms=3200)
    validate_runtime_faults(dict(device={'target':'vcan'},faults=[f]))
    with pytest.raises(ValueError,match='physical driver'):
        validate_runtime_faults(dict(device={'target':'hardware'},faults=[f]))


@pytest.mark.parametrize('kind',['ecu_reset','os_event_drop'])
def test_internal_injection_has_brake_watchdog_causality_without_fabricated_adas_command(kind):
    events=[dict(kind='fault_start',fault_index=0,time_ms=3000),
        dict(kind='reset_asserted' if kind=='ecu_reset' else 'task_suppressed',reason=kind,time_ms=3001,ecu='adas'),
        dict(kind='detection',reason='command_watchdog',time_ms=3055,ecu='brake',evidence_source='reference_process'),
        dict(kind='brake_output_observed',control={'brake':1},time_ms=3058)]
    result=derive({'faulted':[{'step':61,'reply':{'bridge':{'events':events}}}]},{'binding':{'injections':[{'kind':kind}],'source_ref':{'id':'source'}}})['cases'][0]
    assert result['stages']['can_command'] is None
    assert result['stages']['detection']['ecu']=='brake'
    assert result['reference_hook_application']['ecu']=='adas'


def test_hook_before_gateway_start_is_matched_to_its_schedule_without_changing_time():
    events=[dict(kind='reset_asserted',time_ms=6899,ecu='adas'),
        dict(kind='reset_asserted',time_ms=6900.02,ecu='adas'),
        dict(kind='fault_start',fault_index=0,time_ms=6900.12),
        dict(kind='reset_asserted',time_ms=7200,ecu='adas')]
    result=derive({'faulted':[{'step':138,'reply':{'bridge':{'events':events}}}]},
        {'binding':{'injections':[dict(kind='ecu_reset',start_ms=6900,end_ms=7100)],'source_ref':{'id':'source'}}})['cases'][0]
    assert result['reference_hook_application']['time_ms']==6900.02
    assert result['stages']['injection']['time_ms']==6900.12
    assert result['stages']['detection'] is None
