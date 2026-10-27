"""Authored mechanics/contract checks; these are not native CARLA evidence."""
import math
import json
import struct
from types import SimpleNamespace

import pytest

from axiom_carla import stunt_math as m
from axiom_carla import stunt_native as native

IDENTITY = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]


def rotation_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[1., 0., 0.], [0., c, -s], [0., s, c]]


def allocate(force=0., roll=0., pitch=0., remaining=10000., limit=40000., matrix=IDENTITY):
    return m.allocate(force, roll, pitch, matrix, limit_n=limit, remaining_ns=remaining, dt_s=.01)


def test_offset_nozzles_generate_force_and_roll_pitch_not_yaw():
    result = allocate(1000., 300., -500.)
    assert result['force_n'] == pytest.approx([0., 0., 1000.])
    assert result['torque_nm'] == pytest.approx([300., -500., 0.])
    # Four configured positions, not a synthetic unconstrained torque actuator.
    recovered = [sum(m.cross(p, [0., 0., f])[i] for p, f in zip(m.THRUSTERS, result['thruster_forces_n'])) for i in range(3)]
    assert recovered == pytest.approx(result['torque_nm'])


def test_rotation_transforms_both_force_and_moment():
    result = allocate(1000., 300., -500., matrix=rotation_x(math.pi/2))
    assert result['force_n'] == pytest.approx([0., -1000., 0.], abs=1e-10)
    assert result['torque_nm'] == pytest.approx([300., 0., -500.], abs=1e-10)


def test_canceling_nozzles_consume_impulse_and_obey_per_nozzle_limit():
    result = allocate(30000., 30000., 20000.)
    assert max(abs(v) for v in result['thruster_forces_n']) <= 10000.+1e-8
    assert sum(abs(v) for v in result['thruster_forces_n']) <= 40000.+1e-8
    assert result['consumed_impulse_ns'] == pytest.approx(.01*sum(abs(v) for v in result['thruster_forces_n']))
    pure_couple = allocate(roll=3000.)
    assert pure_couple['force_n'] == [0., 0., 0.]
    assert pure_couple['consumed_impulse_ns'] > 0


def test_last_partial_budget_is_applied_once_then_zero():
    result = allocate(40000., remaining=15.)
    assert result['consumed_impulse_ns'] == pytest.approx(15.)
    assert result['remaining_impulse_ns'] == 0
    exhausted = allocate(40000., roll=2000., remaining=result['remaining_impulse_ns'])
    assert exhausted['force_n'] == [0., 0., 0.]
    assert exhausted['torque_nm'] == [0., 0., 0.]


def test_quaternion_accumulation_crosses_euler_wrap_and_quaternion_sign():
    accumulator = m.RollAccumulator()
    for i in range(361):
        q = m.quaternion(rotation_x(math.radians(i)))
        if i % 3 == 0:
            q = [-v for v in q]
        observed = accumulator.observe(q)
    assert observed == pytest.approx(2*math.pi, abs=1e-9)
    assert m.norm(m.quaternion(rotation_x(math.pi))) == pytest.approx(1.)


def stable_frames():
    return [{'time_s': i*.01, 'rotation_deg': [0., 0., 0.], 'velocity_mps': [0., 0., 0.]} for i in range(151)]


def test_upright_stationary_vehicle_without_contact_cannot_pass_landing():
    assert m.landing_summary(stable_frames(), contact=None, rotation_complete=True)['reason'] == 'contact_unconfirmed'


def test_contact_with_wrapped_upright_pose_without_full_roll_cannot_pass():
    contact = {'time_s': .2, 'pre_contact_vertical_speed_mps': 1.}
    result = m.landing_summary(stable_frames(), contact=contact, rotation_complete=False)
    assert result['demo_landing_passed'] is False
    assert result['reason'] == 'rotation_incomplete'


def test_hard_contact_remains_failed_even_after_settling():
    contact = {'time_s': .2, 'pre_contact_vertical_speed_mps': 3.}
    result = m.landing_summary(stable_frames(), contact=contact, rotation_complete=True)
    assert result['reason'] == 'impact_speed_exceeded'
    assert result['demo_landing_passed'] is False


def test_settled_window_must_be_contiguous_and_one_second():
    contact = {'time_s': .2, 'pre_contact_vertical_speed_mps': 1.}
    frames = stable_frames()
    assert m.landing_summary(frames, contact=contact, rotation_complete=True)['demo_landing_passed']
    frames[80]['rotation_deg'][0] = 12.
    assert not m.landing_summary(frames, contact=contact, rotation_complete=True)['demo_landing_passed']


def test_ramp_fails_closed_before_native_execution():
    with pytest.raises(ValueError, match='collision asset'):
        native.validate_config({'schema_version': 'stunt-spec/1', 'preset': 'ramp'})


def test_ground_projection_rejects_vehicle_hits_instead_of_claiming_contact():
    carla = SimpleNamespace(Location=lambda **kw: SimpleNamespace(**kw))
    world = SimpleNamespace(ground_projection=lambda *_: SimpleNamespace(location=SimpleNamespace(x=0., y=0., z=0.), label='Vehicles'))
    result = native.ground_support(carla, world, {'position_m': [0., 0., .3], 'matrix': IDENTITY})
    assert result['available'] is False
    assert result['reason'] == 'non_ground_geometry'


def test_read_only_probe_does_not_tick_load_or_write(tmp_path):
    world = SimpleNamespace(get_map=lambda: SimpleNamespace(name='Town10HD_Opt', to_opendrive=lambda: 'native-xodr'),
        get_actors=lambda: [], get_settings=lambda: SimpleNamespace(synchronous_mode=False, fixed_delta_seconds=None,
            substepping=True, max_substep_delta_time=.01, max_substeps=10))
    client = SimpleNamespace(get_world=lambda: world, get_client_version=lambda: '1ae5356', get_server_version=lambda: '1ae5356')
    actor = SimpleNamespace(**{name: (lambda: None) for name in ['add_force', 'add_torque', 'add_impulse_at_location', 'get_angular_velocity', 'set_enable_gravity']})
    result = native.probe(SimpleNamespace(Actor=actor), client, {'pid': 123}, SimpleNamespace(spool=str(tmp_path)))
    assert result['available'] is True
    assert result['physics_verified'] is False
    assert list(tmp_path.iterdir()) == []


def test_cleanup_destroys_even_when_new_actor_cached_alive_is_false(monkeypatch):
    monkeypatch.setattr(native, 'tick_snapshot', lambda world: world.tick())
    original = SimpleNamespace(synchronous_mode=False, fixed_delta_seconds=None,
        substepping=True, max_substep_delta_time=.01, max_substeps=10)
    current = SimpleNamespace(synchronous_mode=True, fixed_delta_seconds=.01,
        substepping=True, max_substep_delta_time=.01, max_substeps=1)
    calls, pending = [], {'destroy': False}
    actor = SimpleNamespace(id=24, type_id='vehicle.audi.tt', is_alive=False)
    def destroy():
        calls.append('destroy')
        pending['destroy'] = True
        return True
    actor.destroy = destroy
    actors = [actor]
    def tick():
        calls.append('tick')
        if pending['destroy']:
            actors.clear()
    def restore(value):
        calls.append('restore')
        current.__dict__.update(value.__dict__)
    world = SimpleNamespace(get_settings=lambda: current, tick=tick,
        get_actors=lambda: actors, apply_settings=restore)
    state = {'clean': False}
    result = native.clean_world(world, original, [actor], state)
    assert calls == ['destroy', 'tick', 'restore']
    assert state['clean'] and result['cleanup_status'] == 'confirmed'


def test_failed_destroy_never_reports_confirmed_cleanup(monkeypatch):
    monkeypatch.setattr(native, 'tick_snapshot', lambda world: world.tick())
    settings = SimpleNamespace(synchronous_mode=True, fixed_delta_seconds=.01,
        substepping=True, max_substep_delta_time=.01, max_substeps=1)
    actor = SimpleNamespace(id=24, type_id='vehicle.audi.tt', is_alive=False, destroy=lambda: False)
    world = SimpleNamespace(get_settings=lambda: settings, tick=lambda: None,
        get_actors=lambda: [actor], apply_settings=lambda _: None)
    state = {'clean': False}
    result = native.clean_world(world, settings, [actor], state)
    assert not state['clean']
    assert result['cleanup_status'] == 'unconfirmed'


def test_terminal_overturn_cannot_pass_an_earlier_settled_window():
    frames = stable_frames()
    frames[-1]['rotation_deg'][0] = 180.
    result = m.landing_summary(frames, contact={'time_s': .1, 'pre_contact_vertical_speed_mps': 1.}, rotation_complete=True)
    assert not result['demo_landing_passed']
    assert result['settled_window_s'] == 0


def test_tick_snapshot_waits_for_cache_without_issuing_another_tick():
    calls = []
    frames = iter([7, 7, 8])
    world = SimpleNamespace(tick=lambda: calls.append('tick') or 8,
        get_snapshot=lambda: SimpleNamespace(frame=next(frames)))
    assert native.tick_snapshot(world).frame == 8
    assert calls == ['tick']


def test_tick_snapshot_rejects_uncommanded_extra_frame():
    world = SimpleNamespace(tick=lambda: 8, get_snapshot=lambda: SimpleNamespace(frame=9))
    with pytest.raises(RuntimeError, match='expected frame 8, observed 9'):
        native.tick_snapshot(world)


def test_engine_clock_avoids_double_vs_float_extra_sync_tick():
    delta = native.engine_dt(.01)
    assert delta == .009999999776482582
    # The engine's float increment exactly meets its double sync target.
    elapsed = 1184.7937662322074
    assert not elapsed+delta > elapsed+native.engine_dt(delta)
    assert elapsed+.01 > elapsed+delta


def test_saved_raw_engine_matrix_replays_controller_without_quaternion_projection():
    raw = [[struct.unpack('<f', struct.pack('<f', v))[0] for v in row]
           for row in rotation_x(.6)]
    config = dict(mass_kg=1500., max_thrust_n=40000., thrust_scale=1.,
                  impulse_budget_ns=120000., dt_s=.01, target_height_m=5.)
    state = dict(matrix=[row+[0.] for row in raw]+[[0., 0., 0., 1.]],
                 quaternion_xyzw=m.quaternion(raw), height_m=0.,
                 velocity_mps=[0., 0., 0.], angular_velocity_rad_s=[0., 0., 0.])
    native_command = m.KickflipController(config).step(state)
    saved = json.loads(json.dumps(native.record_observation(state, {}, 0.)))
    assert saved['rotation_matrix3x3'] == raw
    assert 'matrix' not in saved
    replayed = m.KickflipController(config).step({**saved, 'matrix': saved['rotation_matrix3x3']})
    assert replayed == native_command
    # The normalized quaternion is intentionally not the exact engine matrix.
    x, _, _, w = state['quaternion_xyzw']
    projected = [[1., 0., 0.], [0., 1-2*x*x, -2*x*w], [0., 2*x*w, 1-2*x*x]]
    assert m.KickflipController(config).step({**state, 'matrix': projected})['force_n'] != native_command['force_n']


@pytest.mark.parametrize('failure', ['encode', 'gap'])
def test_camera_artifact_failure_retains_confirmed_cleanup_and_receipt(tmp_path, monkeypatch, failure):
    def encode(*_):
        raise RuntimeError('Camera encoding failed: preserved ffmpeg stderr')
    monkeypatch.setattr(native, 'encode_camera', encode)
    record = {'status': 'completed', 'cleanup_status': 'confirmed', 'error': None,
              'summary': {'demo_landing_passed': True, 'rotation_complete': True}}
    native.write_json(tmp_path/'stunt-trace.json', record)
    native.write_json(tmp_path/'camera-manifest.json', {})
    rows = [{'camera': 'external', 'world_frame': i*5} for i in range(60)]
    if failure == 'gap':
        rows[-1]['world_frame'] += 1
    receipt = native.finish_capture_output(tmp_path,
        {'camera_fps': 20, 'duration_s': 3., 'dt_s': .01}, {'external': 60}, rows,
        record, {'external_execution_id': 'failed-media-test'})
    assert receipt['status'] == 'failed'
    assert receipt['cleanup_status'] == 'confirmed'
    assert receipt['summary']['demo_landing_passed'] is False
    assert receipt['summary']['rotation_complete'] is True
    assert ('preserved ffmpeg stderr' if failure == 'encode' else 'source frame gap') in receipt['error']
    assert json.loads((tmp_path/'receipt.json').read_bytes()) == receipt
    assert json.loads((tmp_path/'stunt-trace.json').read_bytes())['status'] == 'failed'
    assert len(receipt['artifacts']) == 2
