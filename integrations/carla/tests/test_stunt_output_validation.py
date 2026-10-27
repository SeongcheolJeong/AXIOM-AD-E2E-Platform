"""Authored trace tampering and actual video decoding; no CARLA execution."""
from copy import deepcopy
import math
from pathlib import Path
import shutil
import struct

import pytest

from ade2e.service import digest
from ade2e.stunt_lab_schemas import StuntConfig, model_definition
from axiom_carla import stunt_math as mechanics
from axiom_carla.stunt import FIDELITY, VERSION, file_hash, validate_native_output
import axiom_carla.stunt as handler_module


@pytest.fixture(scope='module')
def valid_bundle(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp('stunt-video-contract'))


def make_bundle(work, *, dt=.01, thrust_scale=0., native_matrix=None):
    cv2 = pytest.importorskip('cv2')
    np = pytest.importorskip('numpy')
    config = StuntConfig(duration_s=3., dt_s=dt, thrust_scale=thrust_scale).model_dump()
    steps = round(config['duration_s']/dt)
    actual_dt = struct.unpack('<f', struct.pack('<f', dt))[0]
    design = dict(schema_version='stunt-lab-design/1', project_id='default', id='authored-contract', revision=1,
        name='AUTHORED contract only', seed=42, config=config, model_definition=model_definition(),
        fidelity=FIDELITY, physical_validation='not_established', created_at='2026-10-23T00:00:00Z')
    design['sha256'] = digest(design)
    identity = dict(stunt_native_sha256=file_hash(Path(handler_module.__file__).with_name('stunt_native.py')),
        stunt_math_sha256=file_hash(mechanics.__file__), server_version='test/1', client_version='test/1',
        server_pid=123, map='Carla/Maps/Town10HD_Opt', opendrive_sha256='a'*64)
    probe = dict(**identity, available=True, physics_verified=True, commissioning_sha256='b'*64)
    snapshot = dict(schema_version='stunt-execution/1', design=design, probe=probe, transport_sha256='c'*64,
        execution_fidelity=FIDELITY, physical_validation='not_established')
    controller = mechanics.KickflipController(config)
    native_matrix = native_matrix or [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
    frames = []
    command = dict(force_n=[0., 0., 0.], torque_nm=[0., 0., 0.], thruster_forces_n=[0.]*4,
        remaining_impulse_ns=config['impulse_budget_ns'], phase='initial', accumulated_roll_rad=0.)
    for index in range(steps+1):
        state = dict(world_frame=100+index, time_s=index*actual_dt, delta_seconds=actual_dt,
            position_m=[0., 0., 1.], rotation_deg=[0., 0., 0.], quaternion_xyzw=mechanics.quaternion(native_matrix),
            matrix=native_matrix, velocity_mps=[0., 0., 0.],
            angular_velocity_rad_s=[0., 0., 0.], height_m=0., contact_confirmed=False)
        next_command = controller.step(state) if index < steps else command
        if index == steps: controller.roll = controller.accumulator.observe(state['quaternion_xyzw'])
        row = {key: value for key, value in state.items() if key != 'matrix'}
        row['rotation_matrix3x3'] = [list(matrix_row) for matrix_row in state['matrix']]
        row.update({key: value for key, value in command.items() if key not in ('brake', 'rotation_complete')})
        row['observed_accumulated_roll_rad'] = controller.roll
        frames.append(row)
        command = next_command
    summary = mechanics.landing_summary(frames, contact=None, rotation_complete=False)
    summary.update(max_height_m=0., accumulated_roll_deg=0., impulse_used_ns=config['impulse_budget_ns']-controller.remaining)
    trace = dict(schema_version='carla-stunt-trace/1', adapter_version=VERSION, controller_version=mechanics.VERSION,
        provenance={**snapshot, 'external_execution_id': 'stunt_test'},
        design_ref={key: design[key] for key in ('id', 'revision', 'sha256')}, seed=42, config=config,
        native_identity=identity, commissioning_sha256=probe['commissioning_sha256'], execution_fidelity=FIDELITY,
        physical_validation='not_established', status='completed', error=None, cleanup_status='confirmed', cleanup_errors=[],
        coordinate_basis='carla_world_m_with_proper_transform_rotation_matrix', torque_api_per_nm=100*180/math.pi,
        clock_contract=dict(nominal_dt_s=dt, engine_dt_s=actual_dt,
            normalization='exact_float32_promoted_to_double_for_1ae5356_sync_window',
            impulse_accounting='nominal_dt_conservative_budget'),
        settings=dict(synchronous_mode=True, fixed_delta_seconds=actual_dt, substepping=True, max_substep_delta_time=actual_dt, max_substeps=1),
        vehicle_physics=dict(mass_kg=1500., blueprint='vehicle.audi.tt', bounding_box_extent_m=[2., 1., .5]),
        spawn_transform=dict(position_m=[0., 0., 1.], rotation_deg=[0., 0., 0.]),
        ground_support_baseline={'available': False}, frames=frames, events=[], summary=summary)
    camera_frames = []
    image = np.zeros((540, 960, 3), dtype=np.uint8)
    for name in ('external', 'onboard'):
        writer = cv2.VideoWriter(str(work/(name+'.mp4')), cv2.VideoWriter_fourcc(*'mp4v'), 20., (960, 540))
        assert writer.isOpened()
        for index in range(60):
            image[:] = index*4
            writer.write(image)
            camera_frames.append(dict(camera=name, index=index, world_frame=100+round(.05/dt)*(index+1),
                                      time_s=(index+1)*round(.05/dt)*actual_dt, sha256='d'*64))
        writer.release()
    cameras = dict(schema_version='stunt-camera-manifest/1', fps=20, frames=camera_frames,
                   counts=dict(external=60, onboard=60), physical_validation='not_established')
    receipt = dict(schema_version='carla-stunt-native-receipt/1', external_execution_id='stunt_test',
                   status='completed', cleanup_status='confirmed', error=None, summary=summary)
    return snapshot, trace, cameras, receipt, work


def validate(bundle):
    return validate_native_output(*bundle[:4], bundle[4], 'stunt_test')


def test_authored_zero_thrust_failure_verdict_and_decodable_videos(valid_bundle):
    result = validate(valid_bundle)
    assert result['demo_landing_passed'] is False
    assert result['reason'] == 'contact_unconfirmed'


def test_half_timestep_and_nonzero_applied_impulse_are_recomputed(tmp_path):
    result = validate(make_bundle(tmp_path, dt=.005, thrust_scale=.3))
    assert result['impulse_used_ns'] == pytest.approx(36000.)
    assert result['demo_landing_passed'] is False


def test_replay_retains_float32_matrix_instead_of_quaternion_projection(tmp_path):
    raw = [[1., 0., 2**-23], [0., 1., 0.], [0., 0., 1.]]
    bundle = make_bundle(tmp_path, thrust_scale=.3, native_matrix=raw)
    assert validate(bundle)['impulse_used_ns'] == pytest.approx(36000.)
    # The raw tiny off-axis force differs from the quaternion's orthonormal
    # projection, but force checks themselves must not gain any wider envelope.
    assert bundle[1]['frames'][1]['force_n'][0] == pytest.approx(12000*2**-23)
    bundle[1]['frames'][1]['force_n'][0] += .0002
    with pytest.raises(ValueError, match='applied force_n'):
        validate(bundle)


@pytest.mark.parametrize('mutation,match', [
    (lambda s, t, c, r: t.update(seed=43), 'seed'),
    (lambda s, t, c, r: t['design_ref'].update(sha256='0'*64), 'reference'),
    (lambda s, t, c, r: t['config'].update(mass_kg=2000), 'design content hash|configuration'),
    (lambda s, t, c, r: t['native_identity'].update(server_pid=999), 'identity changed'),
    (lambda s, t, c, r: t.update(commissioning_sha256='0'*64), 'commissioning'),
    (lambda s, t, c, r: t.update(torque_api_per_nm=10000*180/math.pi), 'torque conversion'),
    (lambda s, t, c, r: t['frames'][20].update(world_frame=900), 'world frame'),
    (lambda s, t, c, r: t['frames'][20].update(time_s=.201), 'cadence'),
    (lambda s, t, c, r: t['frames'][20].update(delta_seconds=.02), 'native delta seconds'),
    (lambda s, t, c, r: t['frames'][20].update(delta_seconds=.01), 'native delta seconds'),
    (lambda s, t, c, r: t['settings'].update(fixed_delta_seconds=.01), 'exact world clock'),
    (lambda s, t, c, r: t['clock_contract'].update(engine_dt_s=.01), 'exact engine clock'),
    (lambda s, t, c, r: t['frames'][20].update(velocity_mps=[0, 0, float('nan')]), 'nonfinite'),
    (lambda s, t, c, r: t['frames'][20].update(quaternion_xyzw=[0, 0, 0, 2]), 'norm'),
    (lambda s, t, c, r: t['frames'][20].update(rotation_matrix3x3=[[1, 0], [0, 1]]), 'dimension'),
    (lambda s, t, c, r: t['frames'][20].update(rotation_matrix3x3=[[.999, 0, 0], [0, 1, 0], [0, 0, 1]]), 'exact float32'),
    (lambda s, t, c, r: t['frames'][20].update(rotation_matrix3x3=[[.5, 0, 0], [0, 1, 0], [0, 0, 1]]), 'orientation'),
    (lambda s, t, c, r: t['frames'][20].update(rotation_deg=[0, 0, 10]), 'orientation'),
    (lambda s, t, c, r: t['frames'][20].update(force_n=[0, 0, 1]), 'applied force'),
    (lambda s, t, c, r: t['frames'][20].update(thruster_forces_n=[0, 0, 0, 1]), 'applied thruster'),
    (lambda s, t, c, r: t['frames'][20].update(remaining_impulse_ns=0), 'applied remaining'),
    (lambda s, t, c, r: t['frames'][20].update(observed_accumulated_roll_rad=6.28), 'accumulated roll'),
    (lambda s, t, c, r: t['summary'].update(demo_landing_passed=True), 'summary'),
    (lambda s, t, c, r: t['summary'].update(impulse_used_ns=100), 'summary'),
    (lambda s, t, c, r: t['frames'][20].update(contact_confirmed=True), 'contact confirmation|applied|contact flag'),
    (lambda s, t, c, r: c['counts'].update(external=61), 'denominator'),
    (lambda s, t, c, r: c['frames'][10].update(world_frame=999), 'camera frame outside'),
    (lambda s, t, c, r: c['frames'][10].update(time_s=.6), 'timestamp'),
])
def test_tampered_native_evidence_fails_closed(valid_bundle, mutation, match):
    snapshot, trace, cameras, receipt = deepcopy(valid_bundle[:4])
    mutation(snapshot, trace, cameras, receipt)
    with pytest.raises(ValueError, match=match):
        validate((snapshot, trace, cameras, receipt, valid_bundle[4]))


def test_truncated_playable_video_is_not_a_complete_capture(valid_bundle, tmp_path):
    cv2 = pytest.importorskip('cv2')
    np = pytest.importorskip('numpy')
    shutil.copy(valid_bundle[4]/'onboard.mp4', tmp_path/'onboard.mp4')
    writer = cv2.VideoWriter(str(tmp_path/'external.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 20., (960, 540))
    for _ in range(30): writer.write(np.zeros((540, 960, 3), dtype=np.uint8))
    writer.release()
    with pytest.raises(ValueError, match='decoded count'):
        validate((*valid_bundle[:4], tmp_path))


def test_delayed_collision_callback_uses_source_frame_impact_history(valid_bundle):
    """An authored 200ms callback delay must not substitute already-settled speed."""
    snapshot, trace, cameras, receipt = deepcopy(valid_bundle[:4])
    contact = dict(source='native_collision_callback', world_frame=250, time_s=1.5,
                   pre_contact_vertical_speed_mps=4.)
    trace['events'] = [dict(type='native_collision', world_frame=250, time_s=1.5,
                           normal_impulse=[0., 0., 10.], other_actor_id=0, other_actor_type='static.road')]
    controller = mechanics.KickflipController(trace['config'])
    command = dict(force_n=[0., 0., 0.], torque_nm=[0., 0., 0.], thruster_forces_n=[0.]*4,
        remaining_impulse_ns=trace['config']['impulse_budget_ns'], phase='initial', accumulated_roll_rad=0.)
    for index, row in enumerate(trace['frames']):
        row.update(height_m=1. if 10 <= index < 150 else 0.,
                   position_m=[0., 0., 2. if 10 <= index < 150 else 1.],
                   velocity_mps=[0., 0., -4. if 135 <= index < 150 else 0.], contact_confirmed=index >= 170)
        row.update({key: value for key, value in command.items() if key not in ('brake', 'rotation_complete')})
        if index < 300:
            command = controller.step({**row, 'matrix': [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]})
        else:
            controller.roll = controller.accumulator.observe(row['quaternion_xyzw'])
        row['observed_accumulated_roll_rad'] = controller.roll
    trace['summary'] = mechanics.landing_summary(trace['frames'], contact=contact, rotation_complete=False)
    trace['summary'].update(max_height_m=1., accumulated_roll_deg=0., impulse_used_ns=0.)
    receipt['summary'] = deepcopy(trace['summary'])
    result = validate((snapshot, trace, cameras, receipt, valid_bundle[4]))
    assert result['impact_vertical_speed_mps'] == 4.
    # A falsely gentle speed calculated from callback-delivery time is rejected.
    trace['summary']['contact']['pre_contact_vertical_speed_mps'] = 0.
    with pytest.raises(ValueError, match='conservative pre-contact speed'):
        validate((snapshot, trace, cameras, receipt, valid_bundle[4]))
