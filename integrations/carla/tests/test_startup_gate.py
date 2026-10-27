"""Scripted coherent snapshots; policy regressions, not CARLA physics trials."""
import json
from types import SimpleNamespace as NS

import pytest

from axiom_carla.startup import StartupFailure, StartupPolicy, wait_for_stability


def vector(x=0., y=0., z=0.):
    return NS(x=x, y=y, z=z)


def state(*, position=None, velocity=None, angular=None, acceleration=None, yaw=0.):
    return NS(get_transform=lambda: NS(location=position or vector(), rotation=NS(pitch=0., yaw=yaw, roll=0.)),
        get_velocity=lambda: velocity or vector(), get_acceleration=lambda: acceleration or vector(),
        get_angular_velocity=lambda: angular or vector())


class Clock:
    now = 0.

    def __call__(self):
        return self.now


class World:
    def __init__(self, sample=lambda role, tick: state(), clock=None, cost=0.):
        self.index = 0
        self.sample = sample
        self.timeouts = []
        self.clock, self.cost = clock, cost
        self.snapshot_edit = lambda snapshot: snapshot

    def tick(self, seconds):
        self.timeouts.append(seconds)
        self.index += 1
        if self.clock:
            self.clock.now += self.cost
        return self.index

    def get_snapshot(self):
        # Freeze both states at the same index. Handles deliberately have no
        # getter methods: using separately cached live actor reads would fail.
        samples = {key: self.sample(role, self.index) for key, role in ((1, 'ego'), (2, 'lead'))}
        return self.snapshot_edit(NS(frame=self.index,
            timestamp=NS(elapsed_seconds=self.index*.05, delta_seconds=.05), find=samples.get))


ACTORS = {'ego': NS(id=1), 'lead': NS(id=2)}


def run(world, **kwargs):
    return wait_for_stability(world, ACTORS, **kwargs)


def failed(world, code, **kwargs):
    with pytest.raises(StartupFailure) as info:
        run(world, **kwargs)
    assert info.value.report['status'] == 'failed'
    assert info.value.report['reason_code'] == code
    json.dumps(info.value.report, allow_nan=False)
    return info.value.report


def test_rest_requires_minimum_eight_ticks_and_preserves_all_raw_snapshots():
    world = World()
    report = run(world)
    assert report['status'] == 'passed' and world.index == 8
    assert report['completed_ticks'] == len(report['trace']) == 8
    assert report['stable_streak'] == 7
    assert report['trace'][0]['actors']['ego']['criteria']['position_step'] is False
    assert report['trace'][-1]['actors']['lead']['velocity_mps'] == {'x': 0., 'y': 0., 'z': 0.}
    assert report['elapsed_simulated_seconds'] == pytest.approx(.4)
    assert report['angular_velocity_basis']['unit'] == 'degrees_per_second'
    assert 'WorldObserver.cpp' in report['angular_velocity_basis']['server']
    assert all(0 < timeout <= 10 for timeout in world.timeouts)


def test_waits_for_both_actors_and_consecutive_streak_resets():
    world = World(lambda role, tick: state(velocity=vector(z=.06)) if role == 'lead' and tick in (*range(1, 12), 15) else state())
    report = run(world)
    assert world.index == 20
    assert report['trace'][13]['stable_streak'] == 3
    assert report['trace'][14]['stable_streak'] == 0
    assert report['stable_streak'] == 5


@pytest.mark.parametrize(('criterion', 'sample'), [
    ('linear_speed', lambda tick: state(velocity=vector(x=.11))),
    ('vertical_speed', lambda tick: state(velocity=vector(z=.06))),
    ('angular_speed', lambda tick: state(angular=vector(z=1.01))),
    ('acceleration', lambda tick: state(acceleration=vector(y=.51))),
    ('position_step', lambda tick: state(position=vector(z=.01*(tick%2)))),
    ('rotation_step', lambda tick: state(yaw=.051*(tick%2))),
])
def test_each_declared_rest_bound_is_enforced_for_either_vehicle(criterion, sample):
    world = World(lambda role, tick: sample(tick) if role == 'ego' else state())
    report = failed(world, 'stability_timeout')
    assert world.index == report['completed_ticks'] == 100
    assert report['elapsed_simulated_seconds'] == pytest.approx(5.)
    assert report['trace'][-1]['actors']['ego']['criteria'][criterion] is False
    assert report['trace'][-1]['actors']['lead']['stable'] is True


def test_stability_on_last_allowed_tick_succeeds_without_extra_tick():
    world = World(lambda role, tick: state(velocity=vector(x=.2 if tick < 96 else 0)))
    report = run(world)
    assert report['status'] == 'passed' and report['stable_streak'] == 5
    assert world.index == 100


def test_wrapped_yaw_change_does_not_create_a_360_degree_step():
    report = run(World(lambda role, tick: state(yaw=179.99 if tick%2 else -179.99)))
    assert report['trace'][-1]['actors']['ego']['derived']['rotation_step_deg'] == pytest.approx(.02)


@pytest.mark.parametrize('field', ['position', 'velocity', 'angular', 'acceleration', 'yaw'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf')])
def test_invalid_actor_values_fail_closed_with_serializable_raw_evidence(field, bad):
    kwargs = {field: bad if field == 'yaw' else vector(x=bad)}
    world = World(lambda role, tick: state(**kwargs) if role == 'lead' and tick == 3 else state())
    report = failed(world, 'invalid_actor_sample')
    assert world.index == 3 and len(report['trace']) == 3
    invalid = report['trace'][-1]['actors']['lead']['invalid_fields']
    assert invalid and invalid[0]['observed'] in ('nan', 'inf')


def test_missing_actor_does_not_count_as_still():
    report = failed(World(lambda role, tick: None if role == 'lead' and tick == 2 else state()), 'invalid_actor_sample')
    assert report['trace'][-1]['actors']['lead']['invalid_fields'][0]['observed'] == 'actor absent from world snapshot'


@pytest.mark.parametrize('fault', ['repeated_frame', 'skipped_frame', 'returned_frame', 'delta', 'elapsed', 'nonfinite_time'])
def test_rejects_nonprogressing_or_inconsistent_synchronous_clock(fault):
    world = World()
    def change(snapshot):
        if world.index == 3:
            if fault == 'repeated_frame': snapshot.frame -= 1
            elif fault == 'skipped_frame': snapshot.frame += 1
            elif fault == 'delta': snapshot.timestamp.delta_seconds = .04
            elif fault == 'elapsed': snapshot.timestamp.elapsed_seconds += .001
            elif fault == 'nonfinite_time': snapshot.timestamp.elapsed_seconds = float('nan')
        return snapshot
    world.snapshot_edit = change
    if fault == 'returned_frame':
        tick = world.tick
        def wrong(seconds):
            value = tick(seconds)
            return value+1 if world.index == 3 else value
        world.tick = wrong
    report = failed(world, 'invalid_clock')
    assert world.index == 3 and len(report['trace']) == 3
    assert report['trace'][-1]['stable'] is False


def test_tick_failure_keeps_previously_observed_trace():
    world = World()
    tick = world.tick
    def error(seconds):
        if world.index == 4:
            raise RuntimeError('scripted RPC failure')
        return tick(seconds)
    world.tick = error
    report = failed(world, 'snapshot_error')
    assert len(report['trace']) == 4 and 'scripted RPC failure' in report['reason']


def test_wall_deadline_caps_each_tick_and_stops_before_simulation_limit():
    clock = Clock()
    world = World(lambda role, tick: state(velocity=vector(x=1)), clock=clock, cost=9.)
    report = failed(world, 'wall_timeout', clock=clock)
    assert world.index == 7
    assert world.timeouts == [10., 10., 10., 10., 10., 10., 6.]
    # A non-cooperative native RPC may return after its timeout. Its return is
    # not success: the gate immediately fails, preserving prior completed reads.
    assert report['elapsed_wall_seconds'] == 63.
    assert len(report['trace']) == 6


def test_snapshot_read_cost_is_also_checked_against_wall_deadline():
    clock = Clock()
    world = World(clock=clock)
    original = world.get_snapshot
    def slow():
        result = original()
        clock.now += 61
        return result
    world.get_snapshot = slow
    report = failed(world, 'wall_timeout', clock=clock)
    assert world.index == 0 and report['trace'] == []


@pytest.mark.parametrize('kwargs', [{'max_ticks': 101}, {'max_wall_time_s': 61}, {'max_tick_rpc_time_s': 11},
    {'max_acceleration_mps2': float('nan')}, {'consecutive_stable_samples': 1}, {'min_ticks': 0}])
def test_policy_cannot_remove_or_expand_bounded_execution(kwargs):
    with pytest.raises(ValueError):
        StartupPolicy(**kwargs)
