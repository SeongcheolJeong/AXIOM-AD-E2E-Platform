"""Authored observations for input-isolation contracts, never native evidence."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import carla_brake_protocol as brake
import carla_throttle_isolation_protocol as p


def row(index=0, *, gear=1, speed=1.9, throttle=.12):
    frame=100+index
    field=lambda v: {'status':'available','value':v,'reason':None}
    control=brake.control(throttle=throttle);control['gear']=gear
    return {'world_frame':frame,'source_time_s':20.+index*.05,'delta_seconds':.05,
        'ego':{'position_m':{'x':12.+index*.1,'y':0.,'z':0.},
            'rotation_deg':{'pitch':0.,'yaw':0.,'roll':0.},
            'velocity_mps':{'x':speed,'y':0.,'z':0.},
            'acceleration_mps2':{'x':.2,'y':0.,'z':0.},
            'angular_velocity_deg_s':{'x':0.,'y':0.,'z':0.}},
        'reported_control':{'status':'available','before_world_frame':frame,'after_world_frame':frame,'data':control},
        'telemetry':{'status':'available','before_world_frame':frame,'after_world_frame':frame,
            'scalars':{'engine_rpm':field(450.)},
            'wheels':[{'index':i,'fields':{'omega':field(5.)}} for i in range(4)]}}


def protocol(branch='continue_pi', count=225):
    return p.make_protocol(brake.make_protocol('throttle',version=brake.SETTLED_VERSION),branch,count)


def test_absolute_source_clock_is_not_copied_into_fresh_pi_state():
    observation=row(0,gear=0,speed=0.,throttle=0.)
    reference={'controls':[brake.control(throttle=.005)],'source_cached_gears':[0,0]}
    first=p.prefix_step(protocol(count=1),reference,0,observation)
    assert first['matched']
    assert first['state']['previous_world_frame']==100
    assert first['state']['previous_source_time_s']==20.
    continuation=p.extension_step(protocol(count=1),0,row(1,gear=0,speed=0.,throttle=.005),
                                  first['state'],reference['controls'][-1])
    assert continuation['state']['step']==2
    assert continuation['control']['throttle']==pytest.approx(.01)
    assert first['state']['step']==1  # immutable until caller commits the completed actuation


def test_replay_keeps_original_requested_float_and_reports_float32_mismatch():
    observation=row(0,gear=0,speed=0.,throttle=0.)
    original=brake.control(throttle=.005+1e-12)
    reference={'controls':[original],'source_cached_gears':[0,0]}
    proposal=p.prefix_step(protocol(count=1),reference,0,observation)
    assert proposal['matched'] and proposal['control']==original
    assert proposal['control']['throttle'] != proposal['trace']['proposed_control']['throttle']
    assert proposal['state']['previous_throttle']==original['throttle']
    reference['controls'][0]['throttle']=.006
    mismatch=p.prefix_step(protocol(count=1),reference,0,observation)
    assert mismatch['matched'] is False
    assert mismatch['trace']['requested_control']['throttle']==.006


def test_hold_uses_requested_throttle_and_freezes_pi_without_float32_substitution():
    state={'step':225,'integral':.12,'previous_throttle':.12510625523063976,
           'previous_source_time_s':19.95,'previous_world_frame':99}
    requested=brake.control(throttle=state['previous_throttle'])
    assert requested['throttle'] != p.f32(requested['throttle'])
    for i in range(40):
        output=p.extension_step(protocol('hold_throttle'),i,row(i),state,requested)
        assert output['control']==requested and output['state']==state
        assert output['trace']['method']=='hold_throttle'


def test_complete_plan_does_not_invent_future_feedback_controls():
    reference={'controls':[brake.control(throttle=.123)]*225}
    pi=p.sequence_descriptor(protocol(),reference)
    fixed=p.sequence_descriptor(protocol('hold_throttle'),reference)
    assert len(pi['prefix_controls'])==225
    assert len(pi['continuation'])==40 and pi['continuation'][-1]['time_s']==pytest.approx(1.95)
    assert all(r['control'] is None for r in pi['continuation'])
    assert all(r['control']==reference['controls'][-1] for r in fixed['continuation'])
    assert p.sha(p.canonical(pi)) != p.sha(p.canonical(fixed))


@pytest.mark.parametrize('n',[0,-1,261,True,225.])
def test_prefix_cannot_exceed_controller_horizon(n):
    with pytest.raises(ValueError): protocol(count=n)


def test_each_source_anchor_pass_does_not_imply_direct_pair_agreement():
    source=row();left=deepcopy(source);right=deepcopy(source)
    left['ego']['position_m']['x']-=.014
    right['ego']['position_m']['x']+=.014
    assert p.compare_entry(protocol(),left,source)['admitted']
    assert p.compare_entry(protocol(),right,source)['admitted']
    pair=p.compare_entry(protocol(),left,right)
    assert not pair['admitted'] and not pair['criteria']['position_distance_m']


@pytest.mark.parametrize('change',[
    lambda r:r['telemetry'].update(status='unavailable'),
    lambda r:r['telemetry'].update(after_world_frame=101),
    lambda r:r['telemetry']['wheels'][2]['fields']['omega'].update(value=0.),
    lambda r:r['telemetry']['wheels'][1]['fields']['omega'].update(value=-5.),
    lambda r:r['telemetry']['scalars']['engine_rpm'].update(value=float('nan')),
    lambda r:r['reported_control']['data'].update(gear=0),
    lambda r:r['reported_control']['data'].update(gear=True),
    lambda r:r['reported_control']['data'].update(brake=.1),
    lambda r:r['ego']['velocity_mps'].update(x=2.),
    lambda r:r['ego']['acceleration_mps2'].update(x=.3),
])
def test_bad_or_mismatched_raw_entry_fields_do_not_become_admission(change):
    source=row();candidate=deepcopy(source);change(candidate)
    assert not p.compare_entry(protocol(),candidate,source)['admitted']


def test_initial_rest_comparison_allows_zero_rpm_while_rotating_handoff_does_not():
    source=row(gear=0,speed=0.,throttle=0.)
    source['telemetry']['scalars']['engine_rpm']['value']=0.
    assert p.compare_entry(protocol(),source,source,rotating=False)['admitted']
    assert not p.compare_entry(protocol(),source,source)['admitted']


def test_handoff_validates_full_gear_history_and_clock_not_just_last_row():
    rows=[row(i) for i in range(4)]
    reference={'rest_ticks':26,'inventory_refresh_ticks':1,'source_cached_gears':[1]*4,
               'initial_observation':rows[0],'entry_observation':rows[-1]}
    assert p.handoff_gate(protocol(count=3),reference,rows,26,1)['admitted']
    changed=deepcopy(rows);changed[1]['reported_control']['data']['gear']=0
    assert not p.handoff_gate(protocol(count=3),reference,changed,26,1)['admitted']
    changed=deepcopy(rows);changed[1]['source_time_s']+=.01
    assert not p.handoff_gate(protocol(count=3),reference,changed,26,1)['admitted']
    assert not p.handoff_gate(protocol(count=3),reference,rows,25,1)['admitted']


def test_unavailable_and_duplicate_json_values_are_rejected():
    for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}'):
        with pytest.raises(ValueError):p.parse_json(raw)


def test_spatial_limits_are_retained_without_requiring_the_failed_settled_gate():
    spawn={'position_m':{'x':0.,'y':0.,'z':0.},'rotation_deg':{'pitch':0.,'yaw':0.,'roll':0.}}
    position={'x':14.,'y':0.,'z':0.}
    velocity={'x':1.94,'y':0.,'z':0.}
    check=p.spatial_check(position,spawn['rotation_deg'],velocity,spawn,protocol=protocol())
    assert check['status']=='within_bounds'
    velocity['x']=3.01
    assert p.spatial_check(position,spawn['rotation_deg'],velocity,spawn,protocol=protocol())['status']=='failed'


def test_substep_override_is_bounded_to_fixed_throttle_and_five_milliseconds():
    controller = brake.make_protocol('throttle', version=brake.SETTLED_VERSION)
    selected = p.make_protocol(controller, 'hold_throttle', 225, continuation_substep_s=.005)
    assert selected['continuation_physics'] == {
        'mode': 'apply_settings_first_interval', 'max_substep_delta_time_s': .005,
        'max_substeps': 10, 'expected_advanced_frames': 1}
    with pytest.raises(ValueError, match='fixed-throttle'):
        p.make_protocol(controller, 'continue_pi', 225, continuation_substep_s=.005)
    bad=deepcopy(selected);bad['continuation_physics']['max_substep_delta_time_s']=.0025
    with pytest.raises(ValueError, match='0.005s'):
        p.validate_protocol(bad)
