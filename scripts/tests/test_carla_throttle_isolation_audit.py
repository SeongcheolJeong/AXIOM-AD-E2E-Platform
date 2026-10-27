"""Independent source-boundary counterexamples; all evidence is authored.

No CARLA imports, native execution, real captures or production databases are
used. Hashes here check internal consistency, not evidence authenticity.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import carla_throttle_isolation_protocol as protocol


@pytest.fixture(scope='module')
def authored_source(tmp_path_factory):
    path = ROOT / 'scripts/tests/test_prepare_carla_throttle_isolation_intent.py'
    spec = importlib.util.spec_from_file_location('isolation_independent_authored_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prescribed_reference.__wrapped__(tmp_path_factory)


@pytest.fixture
def capture(tmp_path, authored_source):
    root, directory, sources, original, helpers = authored_source
    target = tmp_path / 'authored-capture'
    shutil.copytree(directory, target)
    return target, helpers


def blobs(directory):
    return {name: (directory / name).read_bytes() for name in protocol.CAPTURE_FILES}


def parsed(directory):
    raw = blobs(directory)
    return json.loads(raw['report.json']), json.loads(raw['source-intent.json']), {
        name: protocol.sha(data) for name, data in raw.items()}


def test_valid_authored_source_has_verified_capture_before_compact_projection(capture):
    directory, _ = capture
    result = protocol.verify_reference_capture(blobs(directory))
    assert result is not None


def test_updated_file_hash_does_not_hide_broken_terminal_report_projection(capture):
    directory, _ = capture
    raw = blobs(directory)
    report = json.loads(raw['report.json'])
    report['preparation']['commands'][100]['rpc_response']['actor_id'] = 102
    raw['report.json'] = protocol.canonical(report)
    # Every caller can compute the new file hash; the journal still pins the
    # original report projection and must prevent this compact reference.
    with pytest.raises(ValueError):
        protocol.verify_reference_capture(raw)


@pytest.mark.parametrize('damage', ['not_json', 'broken_link', 'truncated_terminal'])
def test_journal_file_digest_is_not_a_substitute_for_the_event_chain(capture, damage):
    directory, _ = capture
    raw = blobs(directory)
    if damage == 'not_json':
        raw['events.jsonl'] = b'not an event journal\n'
    else:
        events = [json.loads(line) for line in raw['events.jsonl'].splitlines()]
        if damage == 'broken_link':
            events[-1]['previous_sha256'] = 'e' * 64
        else:
            events = events[:-1]
        raw['events.jsonl'] = b''.join(protocol.canonical(event) + b'\n' for event in events)
    with pytest.raises(ValueError):
        protocol.verify_reference_capture(raw)


@pytest.mark.parametrize('field,value', [
    ('report_sha256', 'e' * 64), ('experiment_id', 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'),
    ('store_id', 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'), ('status', 'retained'),
])
def test_release_receipt_requires_exact_source_identity_and_report(capture, field, value):
    directory, _ = capture
    raw = blobs(directory)
    receipt = json.loads(raw['reservation-release.json'])
    receipt[field] = value
    raw['reservation-release.json'] = protocol.canonical(receipt)
    with pytest.raises(ValueError):
        protocol.verify_reference_capture(raw)


def test_direct_compact_reference_rejects_wrong_ack_actor(capture):
    directory, _ = capture
    report, intent, hashes = parsed(directory)
    report['preparation']['commands'][100]['rpc_response']['actor_id'] = 102
    hashes['report.json'] = protocol.sha(protocol.canonical(report))
    with pytest.raises(ValueError):
        protocol.reference_from_report(report, intent, hashes)


@pytest.mark.parametrize('mutation', [
    lambda r: r['physics_configuration']['scalars']['mass'].update(value=1846.),
    lambda r: r['cleanup'].update(observed={}),
    lambda r: r['cleanup']['errors'].append('destroy unconfirmed'),
    lambda r: r['callback_shutdown'].update(active=1, drained=False),
    lambda r: r['original'].update(world_id='123'),
])
def test_resealed_failed_source_still_requires_physics_world_and_cleanup(capture, mutation):
    directory, helpers = capture
    report, intent, _ = parsed(directory)
    mutation(report)
    helpers.seal(directory, intent, report)
    # This is an internally resealed authored counterexample; signature or
    # engine authenticity is deliberately not being claimed.
    with pytest.raises(ValueError):
        protocol.verify_reference_capture(blobs(directory))


def test_source_ack_readback_booleans_are_not_numeric_truthiness(capture):
    directory, _ = capture
    report, intent, hashes = parsed(directory)
    report['preparation']['observations'][101]['reported_control']['data']['hand_brake'] = 0
    with pytest.raises(ValueError):
        protocol.reference_from_report(report, intent, hashes)


@pytest.mark.parametrize('field', ['source-intent.json', 'report.json', 'events.jsonl', 'reservation-release.json'])
def test_no_reference_artifact_is_optional(capture, field):
    directory, _ = capture
    raw = blobs(directory)
    del raw[field]
    with pytest.raises((ValueError, KeyError)):
        protocol.verify_reference_capture(raw)
