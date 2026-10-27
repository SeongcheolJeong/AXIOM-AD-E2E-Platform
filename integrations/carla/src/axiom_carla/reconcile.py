"""Explicit owned-world reconciliation on the simulator host.

Inspect is read-only. Cleanup requires the inspected store and journal digest,
OS owner locks, the original episode, exact actor roles/blueprints, and current
settings compatible with this attempt's original or requested state. It never
reloads a map, resumes controls, or deletes a foreign actor.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import uuid
import os

from .native_journal import fields, SETTINGS, WEATHER
from .runner import CarlaRunner
from .spool import Store, StoreConflict, StoreUnavailable, canonical, sha, file_lock, _fsync_dir, now


def inspect_world(attempt, world):
    journal = attempt.journal()
    entries = journal['entries']
    originals = [entry['data'] for entry in entries if entry['kind'] == 'world_original']
    if len(originals) != 1:
        raise StoreUnavailable('Exactly one durable original world observation is required for cleanup')
    original = originals[0]
    if str(world.id) != original['world_id']:
        raise StoreConflict('Engine episode changed; old actor identifiers cannot authorize cleanup')
    settings = fields(world.get_settings(), SETTINGS)
    weather = fields(world.get_weather(), WEATHER)
    expected = {'settings': [original['original_settings']], 'weather': [original['original_weather']]}
    for entry in entries:
        if entry['kind'] == 'mutation_intent':
            data = entry['data']
            if data['operation'] == 'world.apply_settings' and 'requested' in data:
                expected['settings'].append(data['requested'])
            if data['operation'] == 'world.set_weather' and 'requested' in data:
                expected['weather'].append(data['requested'])
    for kind, observed in [('settings', settings), ('weather', weather)]:
        if observed['invalid_fields'] or any(item['invalid_fields'] for item in expected[kind]):
            raise StoreConflict('World properties are unavailable; automatic restoration is unsupported')
        if observed not in expected[kind]:
            raise StoreConflict('Current world ' + kind + ' differs from this execution; another owner may have changed it')
    spawn_intents = [entry['data'] for entry in entries if entry['kind'] == 'spawn_intent']
    allowed = {(item['role_name'], item['blueprint_id']) for item in spawn_intents}
    baseline = {(item['actor_id'], item['type_id'], item['role_name']) for item in original['original_actors']}
    actors, owned = list(world.get_actors()), []
    current = {(actor.id, actor.type_id, dict(actor.attributes).get('role_name')) for actor in actors}
    if not baseline.issubset(current):
        raise StoreConflict('Original baseline actor disappeared or changed; another owner may have changed the world')
    for actor in actors:
        role = dict(actor.attributes).get('role_name')
        if (actor.id, actor.type_id, role) in baseline:
            continue
        if (role, actor.type_id) not in allowed:
            raise StoreConflict('Foreign actor detected; no actor was destroyed')
        returned = [entry['data'] for entry in entries if entry['kind'] == 'spawn_returned'
                    and entry['data']['role_name'] == role and entry['data'].get('spawned')]
        if returned and any(item['actor_id'] != actor.id for item in returned):
            raise StoreConflict('Actor identity differs from its durable spawn acknowledgement')
        owned.append({'actor_id': actor.id, 'type_id': actor.type_id, 'role_name': role})
    if len({item['role_name'] for item in owned}) != len(owned):
        raise StoreConflict('Duplicated ownership role; cleanup is ambiguous')
    return {'schema_version': 'carla-owned-cleanup-plan/1', 'store_id': attempt.store.id,
        'execution_id': attempt.meta()['execution_id'], 'journal_sha256': journal['journal_sha256'],
        'world_id': original['world_id'], 'observed_at': now(), 'owned_actors': owned,
        'original_settings': original['original_settings'], 'original_weather': original['original_weather'],
        'current_settings': settings, 'current_weather': weather}


def reconcile(root, execution_id, *, expected_store_id, expected_journal_sha256=None, cleanup=False):
    store = Store(root)
    if store.id != expected_store_id:
        raise StoreConflict('Durable store differs from the inspected owner')
    attempt = store.find(execution_id)
    if attempt is None:
        raise StoreUnavailable('Execution is absent from this store')
    with ExitStack() as stack:
        stack.enter_context(file_lock(store.root / 'engine.lock'))
        stack.enter_context(file_lock(attempt.path / 'owner.lock'))
        meta = attempt.meta()
        if meta['state'] == 'running':
            # Inspector normally seals an abandoned attempt first. Do not
            # silently alter status from this world cleanup command.
            raise StoreConflict('Inspect the abandoned remote execution before reconciling its world')
        quarantine_path = store.root / 'quarantine.json'
        if not quarantine_path.exists():
            raise StoreConflict('This store has no pending quarantine to reconcile')
        quarantine = json.loads(quarantine_path.read_bytes())
        if quarantine != {'execution_id': execution_id, 'request_hash': meta['request_hash'], 'store_id': store.id}:
            raise StoreConflict('Quarantine belongs to another immutable execution')
        journal = attempt.journal()
        if not meta['mutations_started']:
            external_intents = {'world_mutation_intent', 'mutation_intent', 'spawn_intent', 'control_intent', 'cleanup_intent'}
            if any(entry['kind'] in external_intents for entry in journal['entries']):
                raise StoreConflict('No-mutation status contradicts the durable native journal')
            plan = {'schema_version': 'carla-owned-cleanup-plan/1', 'store_id': store.id,
                'execution_id': execution_id, 'journal_sha256': journal['journal_sha256'],
                'observed_at': now(), 'scope': 'Stopped owner before the first committed external mutation intent',
                'mutations_started': False, 'owned_actors': []}
            if not cleanup:
                return plan
            if not expected_journal_sha256 or expected_journal_sha256 != journal['journal_sha256']:
                raise StoreConflict('Explicit stopped-owner journal binding is required')
            result = {'schema_version': 'carla-owned-cleanup-result/1', 'plan': plan,
                'operations': [], 'status': 'verified_no_mutation', 'completed_at': now()}
            report_path = attempt.path / ('cleanup-' + uuid.uuid4().hex + '-outcome.json')
            with report_path.open('xb') as stream:
                stream.write(canonical(result)); stream.flush(); os.fsync(stream.fileno())
            _fsync_dir(attempt.path)
            quarantine_path.unlink()
            _fsync_dir(store.root)
            return {**result, 'report_path': str(report_path), 'report_sha256': sha(canonical(result))}
        carla, client, world, version = CarlaRunner()._connect()
        plan = inspect_world(attempt, world)
        if not cleanup:
            return plan
        if not expected_journal_sha256 or plan['journal_sha256'] != expected_journal_sha256:
            raise StoreConflict('Journal changed after inspection; explicit cleanup binding is required')
        result = {'schema_version': 'carla-owned-cleanup-result/1', 'plan': plan, 'operations': [], 'status': 'started'}
        report_path = attempt.path / ('cleanup-' + uuid.uuid4().hex + '.json')
        # Persist the exact reviewed ownership before the first destructive call.
        with report_path.open('xb') as stream:
            stream.write(canonical(result)); stream.flush(); os.fsync(stream.fileno())
        _fsync_dir(attempt.path)
        try:
            for item in sorted(plan['owned_actors'], key=lambda item: not item['type_id'].startswith('sensor.')):
                actor = world.get_actor(item['actor_id'])
                if actor is None or actor.type_id != item['type_id'] or dict(actor.attributes).get('role_name') != item['role_name']:
                    raise StoreConflict('Owned actor changed after inspection')
                if item['type_id'].startswith('sensor.') and actor.is_listening:
                    actor.stop()
                acknowledged = actor.destroy()
                result['operations'].append({'operation': 'destroy', **item, 'acknowledged': acknowledged is True})
                if acknowledged is not True:
                    raise StoreConflict('Owned actor destruction was not acknowledged')
            weather = world.get_weather()
            for key, value in plan['original_weather']['values'].items():
                setattr(weather, key, value)
            world.set_weather(weather)
            settings = world.get_settings()
            for key, value in plan['original_settings']['values'].items():
                setattr(settings, key, value)
            world.apply_settings(settings)
            # Only wait for the engine's own asynchronous tick to refresh its
            # actor cache. Never issue a control or a world.tick during recovery.
            if not settings.synchronous_mode:
                world.wait_for_tick(5)
            final = inspect_world(attempt, world)
            if (final['owned_actors'] or final['current_settings'] != plan['original_settings']
                    or final['current_weather'] != plan['original_weather']):
                raise StoreConflict('Final actor absence/settings/weather readback did not match restoration')
            result.update(status='verified', final=final, completed_at=now())
        except BaseException as error:
            result.update(status='incomplete', error=type(error).__name__ + ': ' + str(error)[:1000], completed_at=now())
            raise
        finally:
            # Retain the intent file and append a separate immutable outcome.
            outcome_path = report_path.with_name(report_path.stem + '-outcome.json')
            with outcome_path.open('xb') as stream:
                stream.write(canonical(result)); stream.flush(); os.fsync(stream.fileno())
            _fsync_dir(attempt.path)
        quarantine_path.unlink()
        _fsync_dir(store.root)
        return {**result, 'report_path': str(outcome_path), 'report_sha256': sha(canonical(result))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'cleanup'])
    parser.add_argument('--store', required=True)
    parser.add_argument('--execution-id', required=True)
    parser.add_argument('--expected-store-id', required=True)
    parser.add_argument('--journal-sha256')
    args = parser.parse_args()
    print(json.dumps(reconcile(args.store, args.execution_id, expected_store_id=args.expected_store_id,
        expected_journal_sha256=args.journal_sha256, cleanup=args.action == 'cleanup'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
