"""Ownership guard fixtures; no CARLA actor is created by these checks."""
from types import SimpleNamespace
import json

import pytest

from axiom_carla.native_journal import SETTINGS, WEATHER, fields
from axiom_carla.reconcile import inspect_world
from axiom_carla.spool import StoreConflict


def world_fixture():
    settings = SimpleNamespace(**{key: 0.0 for key in SETTINGS})
    settings.synchronous_mode = False
    weather = SimpleNamespace(**{key: 0.0 for key in WEATHER})
    actor = SimpleNamespace(id=10, type_id='vehicle.tesla.model3', attributes={'role_name': 'unique-owned-ego'})
    world = SimpleNamespace(id=13687974690369489725, get_settings=lambda: settings, get_weather=lambda: weather,
                           get_actors=lambda: [actor])
    entries = [{'kind': 'world_original', 'data': {'world_id': str(world.id),
        'original_settings': fields(settings, SETTINGS), 'original_weather': fields(weather, WEATHER), 'original_actors': []}},
        {'kind': 'spawn_intent', 'data': {'role_name': 'unique-owned-ego', 'blueprint_id': actor.type_id}},
        {'kind': 'spawn_returned', 'data': {'role_name': 'unique-owned-ego', 'actor_id': 10, 'spawned': True}}]
    attempt = SimpleNamespace(store=SimpleNamespace(id='store'), meta=lambda: {'execution_id': 'run'},
                              journal=lambda: {'entries': entries, 'journal_sha256': 'a'*64})
    return attempt, world, actor, entries


def test_plan_keeps_lossless_episode_and_exact_owned_actor():
    attempt, world, actor, entries = world_fixture()
    plan = inspect_world(attempt, world)
    assert plan['world_id'] == '13687974690369489725'
    assert plan['owned_actors'] == [{'actor_id': 10, 'type_id': actor.type_id, 'role_name': 'unique-owned-ego'}]


@pytest.mark.parametrize('change', ['episode', 'foreign_role', 'foreign_blueprint', 'reused_id', 'weather', 'settings'])
def test_changed_ownership_or_world_state_refuses_before_any_mutation(change):
    attempt, world, actor, entries = world_fixture()
    if change == 'episode': world.id += 1
    elif change == 'foreign_role': actor.attributes['role_name'] = 'someone-else'
    elif change == 'foreign_blueprint': actor.type_id = 'vehicle.other'
    elif change == 'reused_id': actor.id = 11
    elif change == 'weather': world.get_weather().fog_density = 12
    else: world.get_settings().synchronous_mode = True
    with pytest.raises(StoreConflict):
        inspect_world(attempt, world)


def test_spawn_response_loss_uses_unique_role_and_blueprint_not_guessed_actor_id():
    attempt, world, actor, entries = world_fixture()
    entries.pop()
    assert inspect_world(attempt, world)['owned_actors'][0]['actor_id'] == 10


def test_multiple_actors_sharing_a_role_are_ambiguous():
    attempt, world, actor, entries = world_fixture()
    entries.pop()
    world.get_actors = lambda: [actor, SimpleNamespace(id=11, type_id=actor.type_id, attributes=actor.attributes)]
    with pytest.raises(StoreConflict, match='Duplicated'):
        inspect_world(attempt, world)


def cleanup_fixture(tmp_path, monkeypatch, carla_scenario):
    """Real durable ownership/report files with an authored engine surface."""
    from ade2e.execution_attempt import ExecutionAttemptRequest
    from ade2e.simulation_context import simulation_input_hash
    from axiom_carla.reconcile import CarlaRunner
    from axiom_carla.spool import Store
    from axiom_carla.version import ADAPTER_VERSION

    _, world, owned, entries = world_fixture()
    baseline = SimpleNamespace(id=1, type_id='spectator', attributes={})
    actors = [baseline, owned]
    operations = []
    entries[0]['data']['original_actors'] = [
        {'actor_id': baseline.id, 'type_id': baseline.type_id, 'role_name': None}
    ]
    world.get_actors = lambda: list(actors)
    world.get_actor = lambda actor_id: next((actor for actor in actors if actor.id == actor_id), None)

    def destroy():
        operations.append('destroy')
        actors.remove(owned)
        return True

    owned.destroy = destroy
    world.set_weather = lambda value: operations.append('set_weather')
    world.apply_settings = lambda value: operations.append('apply_settings')
    world.wait_for_tick = lambda timeout: operations.append('wait_for_tick')
    monkeypatch.setattr(CarlaRunner, '_connect', lambda self: (None, None, world, 'authored-test'))
    store = Store(tmp_path / 'store')
    request = ExecutionAttemptRequest(execution_id='run_baseline_cleanup', adapter_id='carla',
        adapter_version=ADAPTER_VERSION, scenario=carla_scenario,
        input_hash=simulation_input_hash(carla_scenario, 'carla', ADAPTER_VERSION))
    with store.begin(request) as attempt:
        for entry in entries:
            attempt.event(entry['kind'], entry['data'])
        attempt.event('world_mutation_intent', {'world_id': str(world.id)})
    assert store.inspect(request).state == 'interrupted'
    return store, request, attempt, world, baseline, actors, operations


def test_missing_original_actor_refuses_cleanup_before_any_destructive_call(tmp_path, monkeypatch, carla_scenario):
    from axiom_carla.reconcile import reconcile
    store, request, attempt, _, baseline, actors, operations = cleanup_fixture(tmp_path, monkeypatch, carla_scenario)
    actors.remove(baseline)
    with pytest.raises(StoreConflict, match='Original baseline actor disappeared'):
        reconcile(store.root, request.execution_id, expected_store_id=store.id,
            expected_journal_sha256=attempt.journal()['journal_sha256'], cleanup=True)
    assert operations == []
    assert (store.root / 'quarantine.json').exists()
    assert not list(attempt.path.glob('cleanup-*.json'))


def test_unchanged_original_actor_is_preserved_by_verified_cleanup(tmp_path, monkeypatch, carla_scenario):
    from axiom_carla.reconcile import reconcile
    store, request, attempt, _, baseline, actors, operations = cleanup_fixture(tmp_path, monkeypatch, carla_scenario)
    result = reconcile(store.root, request.execution_id, expected_store_id=store.id,
        expected_journal_sha256=attempt.journal()['journal_sha256'], cleanup=True)
    assert result['status'] == 'verified' and result['final']['owned_actors'] == []
    assert actors == [baseline]
    assert operations == ['destroy', 'set_weather', 'apply_settings', 'wait_for_tick']
    assert not (store.root / 'quarantine.json').exists()


def test_original_actor_disappearing_during_cleanup_retains_incomplete_outcome(tmp_path, monkeypatch, carla_scenario):
    from axiom_carla.reconcile import reconcile
    store, request, attempt, world, baseline, actors, operations = cleanup_fixture(tmp_path, monkeypatch, carla_scenario)

    def external_change_during_wait(timeout):
        operations.append('wait_for_tick')
        actors.remove(baseline)

    world.wait_for_tick = external_change_during_wait
    with pytest.raises(StoreConflict, match='Original baseline actor disappeared'):
        reconcile(store.root, request.execution_id, expected_store_id=store.id,
            expected_journal_sha256=attempt.journal()['journal_sha256'], cleanup=True)
    outcomes = list(attempt.path.glob('cleanup-*-outcome.json'))
    assert len(outcomes) == 1
    outcome = json.loads(outcomes[0].read_bytes())
    assert outcome['status'] == 'incomplete' and 'Original baseline actor disappeared' in outcome['error']
    assert outcome['operations'][0]['acknowledged'] is True
    assert 'final' not in outcome
    assert (store.root / 'quarantine.json').exists()
