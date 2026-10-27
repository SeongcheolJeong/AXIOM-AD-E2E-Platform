"""Synthetic math/contact-contract tests; no native Ramp execution claim."""
from copy import deepcopy
import math
from pathlib import Path

import pytest

from axiom_carla.stunt_math import cross, norm
from axiom_carla.stunt_ramp_math import RampGeometry, RampPhaseGate, allocate_suction, fit_query_plane, ideal_exit_estimate

COLLISION = 'a'*64


def hits():
    return [dict(pad_id=name, evidence_kind='native_collision_query', collision_sha256=COLLISION,
        world_frame=42, surface_component_id='test-fixture-ramp',
        pad_position_m=[x, y, .1], hit_position_m=[x, y, 0.], normal_into_free_space=[0., 0., 1.])
        for name, x, y in [('front-left', 1.5, -.75), ('front-right', 1.5, .75),
                            ('rear-left', -1.5, -.75), ('rear-right', -1.5, .75)]]


def suction(observations=None, **overrides):
    arguments = dict(expected_collision_sha256=COLLISION, current_frame=42,
        centre_of_mass_m=[0., 0., .5], max_force_n=25000., remaining_impulse_ns=50000., dt_s=.01, enabled=True)
    arguments.update(overrides)
    return allocate_suction(hits() if observations is None else observations, **arguments)


def test_piecewise_ramp_geometry_matches_saved_source_vertices():
    source = Path(__file__).resolve().parents[3]/'assets/stunt-ramp/source-v1/visual.obj'
    vertices = []
    in_ramp = False
    for line in source.read_text().splitlines():
        if line.startswith('o '):
            in_ramp = line == 'o StuntRamp'
        elif in_ramp and line.startswith('v '):
            vertices.append([float(v) for v in line.split()[1:]])
    geometry = RampGeometry()
    for index in range(56):
        point = geometry.point(math.radians(index*2))
        assert [point[0], -4., point[2]] == pytest.approx(vertices[index], abs=6e-10)
    for index in range(55):
        face = geometry.facet(index)
        assert cross(face['tangent'], [0., 1., 0.]) == pytest.approx(face['normal_into_free_space'])


def test_projection_uses_actual_planar_facet_and_is_never_contact_evidence():
    geometry = RampGeometry()
    face = geometry.facet(40)
    centre = [(a+b)/2 for a, b in zip(face['start_m'], face['end_m'])]
    sample = [p+.15*n for p, n in zip(centre, face['normal_into_free_space'])]
    projected = geometry.project(sample)
    assert projected['index'] == 40
    assert projected['signed_normal_gap_m'] == pytest.approx(.15)
    assert projected['point_m'] == pytest.approx(centre)
    assert projected['evidence_kind'] == 'authored_geometry_prediction'
    assert geometry.project([sample[0], 4.1, sample[2]])['within_lateral_width'] is False


def test_tip_points_backwards_and_low_entry_speed_cannot_reach_it():
    geometry = RampGeometry()
    low = ideal_exit_estimate(geometry, 3., 110.)
    assert not low['sufficient_entry_energy']
    assert low['minimum_energy_entry_mps'] > 12.
    high = ideal_exit_estimate(geometry, 15., 110.)
    assert high['release_velocity_mps'][0] < 0
    assert high['unobstructed_landing_x_m'] < 0
    assert high['requires_collision_clearance_check']
    assert high['native_validation'] == 'not_established'


def test_suction_points_into_surface_and_respects_pad_and_total_bounds():
    result = suction()
    assert result['force_n'] == [0., 0., -25000.]
    assert result['torque_nm'] == [0., 0., 0.]
    assert result['consumed_impulse_ns'] == 250.
    assert all(norm(p['force_n']) <= 6250. for p in result['pads'])
    assert norm(suction(hits()[:1])['force_n']) == 6250.


def test_suction_world_normal_and_com_offset_produce_r_cross_f():
    row = hits()[0]
    row.update(pad_position_m=[1., 2., 3.], hit_position_m=[1.1, 2., 3.],
               normal_into_free_space=[-1., 0., 0.])
    result = suction([row], centre_of_mass_m=[0., 0., 0.])
    assert result['force_n'] == [6250., 0., 0.]
    assert result['torque_nm'] == [0., 18750., -12500.]


def test_last_partial_suction_budget_and_disabled_phase_cannot_add_free_force():
    result = suction(remaining_impulse_ns=5.)
    assert norm(result['force_n']) == pytest.approx(500.)
    assert result['consumed_impulse_ns'] == 5.
    assert result['remaining_impulse_ns'] == 0.
    assert suction(remaining_impulse_ns=0.)['force_n'] == [0., 0., 0.]
    assert suction(enabled=False)['consumed_impulse_ns'] == 0.


@pytest.mark.parametrize('change,reason', [
    ({'evidence_kind': 'authored_geometry_prediction'}, 'native_query_required'),
    ({'collision_sha256': 'b'*64}, 'collision_identity_changed'),
    ({'world_frame': 41}, 'stale_or_future_frame'),
    ({'surface_component_id': None}, 'surface_identity_unavailable'),
    ({'pad_position_m': [1.5, -.75, .5]}, 'pad_outside_contact_range'),
    ({'normal_into_free_space': [0., 0., 2.]}, 'nonunit_native_normal'),
])
def test_unqualified_contact_cannot_create_suction(change, reason):
    row = hits()[0]
    row.update(change)
    result = suction([row])
    assert result['force_n'] == [0., 0., 0.]
    assert result['consumed_impulse_ns'] == 0.
    assert result['rejected'] == [{'pad_id': 'front-left', 'reason': reason}]


def test_duplicate_pad_cannot_double_its_force_capacity():
    row = hits()[0]
    with pytest.raises(ValueError, match='Duplicate'):
        suction([row, deepcopy(row)])


def test_release_switch_does_not_pretend_that_suction_off_means_airborne():
    gate = RampPhaseGate(release_angle_deg=85.)
    observation = dict(native_query_available=True, qualified_pad_ids=['front-left', 'rear-right'], ramp_angle_deg=10.)
    assert gate.observe(**observation)['phase'] == 'wallride'
    observation['ramp_angle_deg'] = 85.
    release = gate.observe(**observation)
    assert release['phase'] == 'release' and not release['suction_enabled']
    assert release['inward_release_requested']
    assert gate.observe(**observation)['phase'] == 'release'
    assert gate.observe(**observation, native_clearance_confirmed=True)['phase'] == 'airborne'
    assert gate.observe(**observation, terminal_settled_confirmed=True)['phase'] == 'airborne'
    assert gate.observe(**observation, native_landing_contact_confirmed=True)['phase'] == 'settle'
    assert gate.observe(**observation, terminal_settled_confirmed=True)['phase'] == 'complete'


def test_missing_native_queries_and_lost_wall_support_fail_closed():
    gate = RampPhaseGate()
    result = gate.observe(native_query_available=False, qualified_pad_ids=[], ramp_angle_deg=30.)
    assert result['phase'] == 'aborted' and not result['suction_enabled']
    gate = RampPhaseGate()
    gate.observe(native_query_available=True, qualified_pad_ids=['front-left', 'rear-right'], ramp_angle_deg=30.)
    lost = gate.observe(native_query_available=True, qualified_pad_ids=['front-left', 'front-right'], ramp_angle_deg=40.)
    assert lost['failure_reason'] == 'wallride_support_lost'
    assert not lost['flight_control_enabled']


def test_native_point_plane_fit_checks_degeneracy_and_seams():
    plane = fit_query_plane([[0., 0., 0.], [1., 0., 1.], [0., 1., 0.]], [-1., 0., 1.])
    assert plane['normal_into_free_space'] == pytest.approx([-2**-.5, 0., 2**-.5])
    with pytest.raises(ValueError, match='collinear'):
        fit_query_plane([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]], [0., 0., 1.])
    with pytest.raises(ValueError, match='nonplanar'):
        fit_query_plane([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [1., 1., .5]], [0., 0., 1.])


def test_isolated_native_region_requires_recomputable_fresh_query_plane():
    row = hits()[0]
    row.pop('surface_component_id')
    row.update(surface_region_id='commissioned-ramp-region', normal_method='native_query_point_plane_fit/1',
        native_query_frames=[42, 42, 42], native_query_points_m=[[1.5, -.75, 0.], [1.6, -.75, 0.], [1.5, -.65, 0.]])
    region = dict(id='commissioned-ramp-region', bounds_min_m=[0., -2., -1.],
        bounds_max_m=[3., 2., 2.], max_plane_residual_m=.005)
    assert suction([row], verified_region=region)['force_n'] == [0., 0., -6250.]
    assert suction([row])['force_n'] == [0., 0., 0.]
    stale = deepcopy(row)
    stale['native_query_frames'][1] = 41
    assert suction([stale], verified_region=region)['force_n'] == [0., 0., 0.]
    foreign = deepcopy(row)
    foreign['native_query_points_m'][1][0] = 10.
    assert suction([foreign], verified_region=region)['force_n'] == [0., 0., 0.]
    extrapolated = deepcopy(row)
    extrapolated.update(pad_position_m=[10., 0., .1], hit_position_m=[10., 0., 0.])
    assert suction([extrapolated], verified_region=region)['force_n'] == [0., 0., 0.]


def test_85_degree_finite_tip_release_waits_for_native_clearance_without_inward_pulse():
    gate = RampPhaseGate(release_mode='surface_end')
    gate.observe(native_query_available=True, qualified_pad_ids=['front-left', 'rear-right'], ramp_angle_deg=60.)
    exiting = dict(native_query_available=True, qualified_pad_ids=['rear-left', 'rear-right'], ramp_angle_deg=75.,
                   native_front_edge_exit_confirmed=True)
    result = gate.observe(**exiting)
    assert result['phase'] == 'release'
    assert not result['inward_release_requested'] and not result['flight_control_enabled']
    assert not result['suction_enabled']
    assert gate.observe(**exiting, native_clearance_confirmed=True)['phase'] == 'airborne'
