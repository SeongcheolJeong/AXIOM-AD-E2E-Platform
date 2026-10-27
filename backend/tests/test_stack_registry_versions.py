"""Version coexistence, exact pins and immutable saved-stack compatibility."""
from types import SimpleNamespace

import pytest

from ade2e import stack_runtime
from ade2e.stack_components import ProportionalControl
from ade2e.stack_runtime import StackRegistry, StackSession
from test_stack_authoring_normalization import request
from test_stack_runtime import definition, packet


class AlternateControl(ProportionalControl):
    """Authored plugin with a visible output delta; not a production controller."""
    def metadata(self):
        return super().metadata().model_copy(update={
            'version': '99', 'name': 'Authored controller v2 for registry contract',
        })

    def control(self, packet, decision, parameters):
        result = super().control(packet, decision, parameters)
        return result.model_copy(update={'throttle': .123}) if not decision.force_brake else result


def output(definition, registry):
    sample, points = packet(speed=2.)
    return StackSession(definition, registry).step(sample, points).model_dump(mode='json', exclude={'elapsed_ms'})


def test_same_id_versions_coexist_and_versionless_ambiguity_is_explicit():
    registry = StackRegistry(load_plugins=False)
    before = registry.list()
    original = registry.get('proportional-control', '1')
    legacy_output = output(definition(), registry)
    second = AlternateControl()
    registry.register(second)

    assert registry.get('proportional-control', '1') is original
    assert registry.get('proportional-control', '99') is second
    assert registry.list() == [*before, second.metadata()]
    assert output(definition(), registry) == legacy_output
    v2 = definition()
    v2.controller.version = '99'
    changed = output(v2, registry)
    assert changed['control']['throttle'] == .123
    assert changed['control'] != legacy_output['control']
    assert changed['observation'] == legacy_output['observation']
    assert changed['decision'] == legacy_output['decision']
    with pytest.raises(KeyError, match='specify an exact version'):
        registry.get('proportional-control')
    for identifier, version in [('proportional-control', '3'), ('missing', '1'), ('missing', None)]:
        with pytest.raises(KeyError, match='unavailable'):
            registry.get(identifier, version)
    class SingleRelease(ProportionalControl):
        component_id = 'single-release-control'
    registry.register(SingleRelease())
    assert registry.get('single-release-control') is registry.get('single-release-control', '1')


def test_duplicate_and_replacement_apply_only_to_the_exact_tuple():
    registry = StackRegistry(load_plugins=False)
    v1 = registry.get('proportional-control', '1')
    second = AlternateControl()
    registry.register(second, replace=True)  # New tuple still adds, never removes v1.
    replacement = AlternateControl()
    before = registry.list()
    with pytest.raises(ValueError, match='ID/version is already registered'):
        registry.register(replacement)
    assert registry.get('proportional-control', '99') is second
    assert registry.list() == before
    registry.register(replacement, replace=True)
    assert registry.get('proportional-control', '99') is replacement
    assert registry.get('proportional-control', '1') is v1
    assert [(item.id, item.version) for item in registry.list()] == [(item.id, item.version) for item in before]
    v1replacement = ProportionalControl()
    registry.register(v1replacement, replace=True)
    assert registry.get('proportional-control', '1') is v1replacement
    assert registry.get('proportional-control', '99') is replacement


@pytest.mark.parametrize('factory', [AlternateControl, AlternateControl()])
def test_plugin_factory_and_instance_loading_preserve_builtin_version(monkeypatch, factory):
    groups = []
    def entry_points(*, group):
        groups.append(group)
        return [SimpleNamespace(load=lambda: factory)]
    monkeypatch.setattr(stack_runtime, 'entry_points', entry_points)
    registry = StackRegistry()
    assert groups == ['ade2e.stack_components']
    assert type(registry.get('proportional-control', '1')) is ProportionalControl
    assert isinstance(registry.get('proportional-control', '99'), AlternateControl)
    assert [(item.id, item.version) for item in registry.list()][-1] == ('proportional-control', '99')
    groups.clear()
    assert len(StackRegistry(load_plugins=False).list()) == 6
    assert groups == []


def test_plugin_exact_version_replacement_retains_existing_loader_semantics(monkeypatch):
    class Replacement(ProportionalControl):
        pass
    plugin = Replacement()
    monkeypatch.setattr(stack_runtime, 'entry_points', lambda **_: [SimpleNamespace(load=lambda: plugin)])
    registry = StackRegistry()
    assert len(registry.list()) == 6
    assert registry.get('proportional-control', '1') is plugin
    with pytest.raises(KeyError, match='specify an exact version'):
        registry.get('proportional-control')


@pytest.mark.parametrize('changed', [{'version': '99'}, {'id': 'different-controller'}])
def test_identity_drift_does_not_silently_rekey_or_return_another_release(changed):
    component = ProportionalControl()
    original_metadata = component.metadata
    registry = StackRegistry(load_plugins=False)
    registry.register(component, replace=True)
    component.metadata = lambda: original_metadata().model_copy(update=changed)
    with pytest.raises(KeyError, match='identity changed'):
        registry.get('proportional-control', '1')
    with pytest.raises(KeyError, match='specify an exact version'):
        registry.get('proportional-control')
    with pytest.raises(KeyError, match='identity changed'):
        registry.list()
    with pytest.raises(KeyError, match='unavailable'):
        registry.get(changed.get('id', 'proportional-control'), changed.get('version', '1'))


def test_api_catalog_and_explicit_revision_keep_old_manifest_and_execution(client):
    registry = client.app.state.stack_registry
    body = request()
    response = client.post('/api/v1/stacks', json=body)
    assert response.status_code == 201, response.text
    first = response.json()
    before = client.get(first['download_url']).content
    result_v1 = output(first['definition'], registry)

    registry.register(AlternateControl())
    catalog = client.get('/api/v1/stack-components')
    assert catalog.status_code == 200
    assert [(item['id'], item['version']) for item in catalog.json() if item['id'] == 'proportional-control'] == [
        ('proportional-control', '1'), ('proportional-control', '2'), ('proportional-control', '99')]
    body['definition']['controller']['version'] = '99'
    revised = client.post(f"/api/v1/stacks/{first['id']}/revisions", json={**body, 'expected_revision': 1})
    assert revised.status_code == 201, revised.text
    second = revised.json()
    assert second['definition']['controller']['version'] == '99'
    assert second['component_metadata'][2]['version'] == '99'
    assert output(second['definition'], registry)['control']['throttle'] == .123
    assert output(first['definition'], registry) == result_v1
    assert first['kernel_version'] == second['kernel_version']
    assert client.get(first['download_url']).content == before
    assert client.get(f"/api/v1/stacks/{first['id']}?revision=1").json() == first
    assert first['manifest_hash'] != second['manifest_hash']


def test_queued_replay_keeps_pinned_v1_after_v2_is_installed(client):
    from test_stack_replays import context, submit, execute
    ctx = context(client)
    job = submit(ctx)
    ctx[1].state.stack_registry.register(AlternateControl())
    completed = execute(ctx, job)
    assert completed.status == 'completed'
    assert len(completed.ticks) == 3
    for tick in completed.ticks:
        assert tick.output['components'][2]['id'] == 'proportional-control'
        assert tick.output['components'][2]['version'] == '1'
        assert tick.output['control']['throttle'] != .123
