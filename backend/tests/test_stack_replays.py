"""Actual MCAP geometry through the common queue; authored fixtures, no simulator claims."""
import hashlib
import io
import json
import struct
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi import HTTPException
from mcap.writer import Writer, CompressionType
from sqlalchemy import event, select

from ade2e import jobs, stack_replays
from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost
from ade2e.job_models import JobRow
from ade2e.recording_models import RecordingMessageRow
from ade2e.service import canonical_bytes, digest
from ade2e.stack_models import StackRevisionRow
from ade2e.stack_replay_models import StackReplayRow, StackReplayTickRow
from ade2e.stack_replay_sources import EGO_SCHEMA, decode_ego
from ade2e.stack_runtime import StackRegistry, StackSession
from ade2e.worker import run_once
from test_sensors import sensor_schema, lidar

BASE = 2**63 + 123


def definition():
    return {'name': 'Authored corridor stack', 'note': 'Experimental source geometry only', 'definition': {
        'reference_frame': 'ego', 'perception': {'id': 'lidar-corridor', 'version': '1', 'parameters': {'ground_reference_z_m': 0.0, 'ground_reference_source': 'Authored fixture ground plane'}},
        'policy': {'id': 'stopping-headway', 'version': '1', 'parameters': {'allow_no_obstacle_observed_motion': True}},
        'controller': {'id': 'proportional-control', 'version': '1', 'parameters': {}}}}


def ground():
    return [(x, y, 0) for x in (4, 8, 14, 19, 24, 29) for y in (-.4, .4)]


def context(client, *, points=None, times=None, ego_values=None, rig_values=None, binding_values=None, schema=None):
    stack_response = client.post('/api/v1/stacks', json=definition())
    assert stack_response.status_code == 201, stack_response.text
    stack = stack_response.json()
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.ZSTD, enable_crcs=True, enable_data_crcs=True)
    writer.start()
    ls = writer.register_schema('foxglove.PointCloud', 'jsonschema', sensor_schema('foxglove.PointCloud'))
    es = writer.register_schema('axiom.EgoState.v1', 'jsonschema', json.dumps(schema or EGO_SCHEMA).encode())
    lc = writer.register_channel('/points', 'json', ls)
    ec = writer.register_channel('/ego', 'json', es)
    points = points if points is not None else [ground() + [(5, 0, .7)], ground() + [(25, 0, .7)], ground()]
    times = times or [BASE, BASE + 100_000_000, BASE + 200_000_000]
    ordinals = []
    for index, (at, cloud) in enumerate(zip(times, points)):
        writer.add_message(lc, at, json.dumps(lidar(points=cloud)).encode(), at-1)
        ego = {'timestamp_ns': str(50+index), 'frame_id': 'ego', 'speed_mps': 3.,
            'route_heading_error_rad': .1, 'pitch_rad': 0., 'roll_rad': 0., 'source': 'Authored source state'}
        ego.update(ego_values or {})
        writer.add_message(ec, at, json.dumps(ego).encode(), at-2)
        ordinals.append(index*2+1)
    writer.finish()
    response = client.post('/api/v1/recordings?name=StackFixture&source_kind=synthetic', content=output.getvalue(), headers={'Content-Type': 'application/octet-stream'})
    assert response.status_code == 201, response.text
    recording = response.json()
    rig = {'name': 'Declared stack rig', 'reference_frame': 'ego', 'coordinate_convention': 'right_handed_z_up',
        'source_kind': 'synthetic', 'source': 'Authored test rig', 'note': 'Not measured calibration',
        'sensors': [{'key': 'roof', 'name': 'Roof', 'kind': 'lidar', 'frame_id': 'lidar_roof',
            'transform': {'translation_m': [0, 0, 0], 'rotation_xyzw': [0, 0, 0, 1]}}]}
    rig.update(rig_values or {})
    response = client.post('/api/v1/sensor-rigs', json=rig)
    assert response.status_code == 201, response.text
    rig = response.json()
    mapping = {'channel_id': lc, 'sensor_key': 'roof', 'decoder_id': 'foxglove-json', 'decoder_version': '1',
        'max_age_ns': '100000000', 'log_time_offset_ns': '0'}
    mapping.update(binding_values or {})
    response = client.post(f'/api/v1/recordings/{recording["id"]}/sensor-bindings', json={'expected_revision': 0,
        'rig_id': rig['id'], 'rig_revision': 1, 'note': 'Declared log alignment', 'channels': [mapping]})
    assert response.status_code == 201, response.text
    request = {'label': 'Exact recorded stack', 'stack_id': stack['id'], 'stack_revision': 1,
        'recording_id': recording['id'], 'binding_revision': 1, 'lidar_channel_id': lc, 'ego_channel_id': ec,
        'ego_ordinals': ordinals}
    return client, client.app, request, stack, recording, rig


def submit(ctx, key='stack', body=None):
    response = ctx[0].post('/api/v1/stack-replays', json=body or ctx[2], headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json()


def execute(ctx, job):
    app = ctx[1]
    return run_once(app.state.db, app.state.registry, 'stack-test-worker', job_id=job['id'],
        runtime_registry=app.state.runtime_registry, sensor_registry=app.state.sensor_registry,
        evaluator_registry=app.state.evaluator_registry, stack_registry=app.state.stack_registry)


def claim(ctx, job):
    result = jobs.claim_job(ctx[1].state.db, 'manual-stack-worker', job_id=job['id'], capabilities={('stack_replay', 'recorded-stack', '1')})
    assert result is not None
    assert jobs.mark_dispatched(ctx[1].state.db, result)
    return result


def test_actual_recording_kernel_full_buffers_and_terminal_export(client):
    ctx = context(client)
    source = client.get('/api/v1/stack-replay-sources/' + ctx[4]['id']).json()
    assert [item['role'] for item in source['channels']] == ['lidar', 'ego']
    job = submit(ctx)
    assert job['counts'] == {'total': 3, 'running': 0, 'completed': 0, 'failed': 0, 'not_attempted': 3}
    assert client.get('/api/v1/jobs/' + job['id']).status_code == 404
    assert jobs.claim_job(ctx[1].state.db, 'simulation-only') is None
    result = execute(ctx, job)
    assert result.status == 'completed', result
    assert [tick.output['decision']['mode'] for tick in result.ticks] == ['obstacle_stop', 'approach', 'pilot_cruise']
    assert result.counts.completed == result.counts.total == 3
    for index, tick in enumerate(result.ticks):
        assert tick.control_application == 'not_applied_open_loop'
        assert tick.packet['ego']['timestamp_ns'] == str(BASE + index*100_000_000)
        assert tick.packet['ego']['capture_time_ns'] == str(50+index)
        assert tick.packet['lidar']['capture_time_ns'] == '11000000008'
        payload = client.get(tick.points.download_url)
        assert payload.status_code == 200, payload.text
        assert hashlib.sha256(payload.content).hexdigest() == tick.points.sha256 == tick.output['input_points_sha256']
        assert len(payload.content) == tick.points.point_count * 12
    exported = client.get(result.download_url)
    assert hashlib.sha256(exported.content).hexdigest() == result.manifest_hash
    assert execute(ctx, job) is None
    assert client.post(f'/api/v1/stack-replays/{result.id}/cancel').json()['status'] == 'completed'
    assert client.get(result.download_url).content == exported.content
    assert submit(ctx)['id'] == job['id']


def test_stack_revision_pinning_idempotence_and_conflicts(client):
    ctx = context(client)
    original = client.get(ctx[3]['download_url']).content
    job = submit(ctx)
    revised = definition() | {'expected_revision': 1, 'name': 'Changed head'}
    revised['definition']['policy']['parameters']['cruise_speed_mps'] = 1.
    response = client.post('/api/v1/stacks/' + ctx[3]['id'] + '/revisions', json=revised)
    assert response.status_code == 201, response.text
    assert client.post('/api/v1/stacks/' + ctx[3]['id'] + '/revisions', json=revised).status_code == 409
    assert client.get(ctx[3]['download_url']).content == original
    assert len(client.get('/api/v1/stacks/' + ctx[3]['id'] + '/revisions').json()) == 2
    result = execute(ctx, job)
    assert result.stack_snapshot['revision'] == 1
    assert result.ticks[-1].output['decision']['target_speed_mps'] == 5.
    assert client.post('/api/v1/stack-replays', json=ctx[2] | {'label': 'different'}, headers={'Idempotency-Key': 'stack'}).status_code == 409
    assert client.post('/api/v1/stack-replays', json=ctx[2]).status_code == 422


@pytest.mark.parametrize('body', [{'ego_ordinals': [1, 1]}, {'ego_ordinals': [3, 1]}, {'ego_ordinals': [0]}, {'ego_ordinals': [99999]}, {'stack_revision': 99}, {'binding_revision': 99}])
def test_invalid_selection_does_not_publish_jobs(client, body):
    ctx = context(client)
    response = client.post('/api/v1/stack-replays', json=ctx[2] | body, headers={'Idempotency-Key': 'invalid'})
    assert response.status_code in {422, 404}, response.text
    assert client.get('/api/v1/stack-replays').json()['items'] == []


@pytest.mark.parametrize('values, reason', [({'ego_values': {'frame_id': 'unknown'}}, 'ego_unavailable'),
    ({'ego_values': {'pitch_rad': .2}}, 'flat_road'), ({'rig_values': {'coordinate_convention': 'unknown'}}, 'lidar_unavailable'),
    ({'binding_values': {'log_time_offset_ns': '1'}}, 'future'),
    ({'binding_values': {'log_time_offset_ns': '-1', 'max_age_ns': '0'}}, 'maximum age')])
def test_unavailable_or_stale_is_unknown_stop(client, values, reason):
    ctx = context(client, **values)
    result = execute(ctx, submit(ctx))
    assert result.status == 'completed'
    tick = result.ticks[0]
    assert tick.output['decision']['mode'] == 'unknown_stop'
    assert tick.output['control']['throttle'] == 0 and tick.output['control']['brake'] == 1
    assert reason in str(tick.output['warnings'])


@pytest.mark.parametrize('cloud', [[], [(1, 0, 0)] * 20001, [(float('nan'), 0, 0)]])
def test_empty_full_and_invalid_or_decimated_input_distinct(client, cloud):
    ctx = context(client, points=[cloud])
    result = execute(ctx, submit(ctx))
    tick = result.ticks[0]
    assert result.status == 'completed' and tick.output['decision']['mode'] == 'unknown_stop'
    if not cloud:
        assert tick.points.point_count == tick.points.size_bytes == 0
        assert client.get(tick.points.download_url).content == b''
        assert tick.points.sha256 == hashlib.sha256(b'').hexdigest()
    else:
        assert tick.points is None and tick.packet['lidar'] is None


@pytest.mark.parametrize('payload', [b'[]', b'{"speed_mps":NaN}', b'\xff', b'{"source":"a","source":"b"}', b'null'])
def test_strict_ego_decoder_rejects_bad_payload(payload):
    with pytest.raises((ValueError, UnicodeError)):
        decode_ego(payload)


def test_concurrent_duplicate_single_job_and_page(client):
    ctx = context(client)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: submit(ctx), range(3)))
    assert len({item['id'] for item in results}) == 1
    next_job = submit(ctx, 'next')
    page = client.get('/api/v1/stack-replays?limit=1').json()
    assert page['items'][0]['id'] == next_job['id']
    assert client.get('/api/v1/stack-replays', params={'limit': 1, 'before_seq': page['next_before_seq']}).json()['items'][0]['id'] == results[0]['id']


def test_cancelled_queued_full_denominator_and_immutable_export(client):
    ctx = context(client)
    job = submit(ctx)
    response = client.post('/api/v1/stack-replays/' + job['id'] + '/cancel')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['status'] == 'cancelled' and value['counts']['not_attempted'] == 3
    content = client.get(value['download_url']).content
    assert hashlib.sha256(content).hexdigest() == value['manifest_hash']
    assert execute(ctx, job) is None
    assert client.get(value['download_url']).content == content


def test_partial_tick_loss_preserves_points_and_denominator(client, monkeypatch):
    ctx = context(client)
    job = submit(ctx)
    claimed = claim(ctx, job)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise AdapterOwnershipLost('Injected stopped owner')
        return False
    with pytest.raises(AdapterOwnershipLost):
        stack_replays.execute_claim(ctx[1].state.db, claimed, ctx[1].state.stack_registry, ctx[1].state.sensor_registry, cancel)
    with ctx[1].state.db.session() as session:
        row = session.get(JobRow, claimed.job.submission_seq)
        row.lease_expires_at = '2000-01-01T00:00:00Z'
        session.commit()
    jobs.expire_jobs(ctx[1].state.db)
    result = jobs.job_get(ctx[1].state.db, job['id'], kind='stack_replay')
    assert result.status == 'interrupted'
    assert result.counts.completed == 1 and result.counts.not_attempted == 2
    assert client.get(result.ticks[0].points.download_url).status_code == 200
    with pytest.raises(jobs.LostLease):
        stack_replays.start_tick(ctx[1].state.db, claimed, 1)


def test_running_tick_interrupt_sealed_as_failed(client):
    ctx = context(client)
    job = submit(ctx)
    claimed = claim(ctx, job)
    stack_replays.start_tick(ctx[1].state.db, claimed, 0)
    result = jobs.fail_job(ctx[1].state.db, claimed, 'Confirmed worker shutdown', status='interrupted')
    assert result.counts.failed == 1 and result.counts.not_attempted == 2
    assert result.ticks[0].status == 'failed' and result.ticks[0].started_at
    assert result.ticks[1].started_at is None


def test_original_message_corruption_never_becomes_free_geometry(client):
    ctx = context(client)
    job = submit(ctx)
    with ctx[1].state.db.session() as session:
        row = session.get(RecordingMessageRow, (ctx[4]['id'], 0))
        row.sha256 = '0'*64
        session.commit()
    result = execute(ctx, job)
    assert result.ticks[0].output['decision']['mode'] == 'unknown_stop'
    assert result.ticks[0].points is None


def test_points_and_manifest_tampering_fail_closed(client):
    ctx = context(client)
    result = execute(ctx, submit(ctx))
    db = ctx[1].state.db
    with db.session() as session:
        row = session.get(StackReplayTickRow, (result.id, 0))
        key = row.points_storage_key
    (db.stack_replay_dir / key).write_bytes(b'x')
    assert client.get(result.ticks[0].points.download_url).status_code == 409
    with db.session() as session:
        row = session.get(StackReplayTickRow, (result.id, 0))
        altered = deepcopy(row.payload)
        altered['output']['control']['throttle'] = .5
        row.payload, row.payload_hash = altered, digest(altered)
        session.commit()
    assert client.get('/api/v1/stack-replays/' + result.id).status_code == 409
    assert client.get(result.download_url).status_code == 409


def test_stack_manifest_tamper_and_unknown_component(client):
    ctx = context(client)
    invalid = definition()
    invalid['definition']['policy']['id'] = 'uninstalled'
    assert client.post('/api/v1/stacks', json=invalid).status_code == 422
    with ctx[1].state.db.session() as session:
        row = session.get(StackRevisionRow, (ctx[3]['id'], 1))
        row.manifest = b'{}'
        session.commit()
    assert client.get('/api/v1/stacks/' + ctx[3]['id']).status_code == 409


def test_component_changed_after_submit_no_dispatch_and_replay_first(client):
    ctx = context(client)
    job = submit(ctx)
    registry = ctx[1].state.stack_registry
    component = registry.get('lidar-corridor', '1')
    old = component.metadata
    component.metadata = lambda: old().model_copy(update={'version': 'changed'})
    try:
        assert submit(ctx)['id'] == job['id']
        result = execute(ctx, job)
        assert result.status == 'failed' and result.dispatched_at is None
        assert result.counts.not_attempted == 3
    finally:
        component.metadata = old


def test_duplicate_lidar_time_chooses_latest_ordinal_and_ego_order_strict(client):
    ctx = context(client, times=[BASE, BASE, BASE+1])
    response = client.post('/api/v1/stack-replays', json=ctx[2], headers={'Idempotency-Key': 'duplicate-ego'})
    assert response.status_code == 422
    value = submit(ctx, body=ctx[2] | {'ego_ordinals': [1, 5]})
    assert value['inputs'][0]['lidar']['ordinal'] == 2
    result = execute(ctx, value)
    assert result.ticks[0].output['observation']['nearest_point_xyz_m'][0] == 25.


def test_declared_transform_changes_actual_consumed_geometry(client):
    sensors = [{'key': 'roof', 'name': 'Roof', 'kind': 'lidar', 'frame_id': 'lidar_roof',
        'transform': {'translation_m': [2, 0, 0], 'rotation_xyzw': [0, 0, 0, 1]}}]
    ctx = context(client, points=[ground()+[(5, 0, .7)]], rig_values={'sensors': sensors})
    result = execute(ctx, submit(ctx))
    raw = client.get(result.ticks[0].points.download_url).content
    assert list(struct.iter_unpack('<fff', raw))[-1][0] == 7.
    assert result.ticks[0].output['observation']['nearest_point_xyz_m'][0] == 7.


@pytest.mark.parametrize('sensors', [[{'key': 'roof', 'name': 'Roof', 'kind': 'lidar', 'frame_id': 'wrong',
    'transform': {'translation_m': [0, 0, 0], 'rotation_xyzw': [0, 0, 0, 1]}}],
    [{'key': 'roof', 'name': 'Roof', 'kind': 'lidar', 'frame_id': 'lidar_roof', 'transform': None}]])
def test_unknown_transform_or_frame_mismatch_withholds_consumed_points(client, sensors):
    ctx = context(client, rig_values={'sensors': sensors})
    result = execute(ctx, submit(ctx))
    assert all(tick.points is None and tick.output['decision']['mode'] == 'unknown_stop' for tick in result.ticks)


def test_submission_atomic_rollback_leaves_no_queue_or_ticks(client):
    ctx = context(client)
    db = ctx[1].state.db
    def fail(conn, cursor, statement, *_):
        if statement.startswith('INSERT INTO stack_replay_ticks'):
            raise RuntimeError('Injected membership failure')
    event.listen(db.engine, 'after_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='Injected membership'):
            submit(ctx)
    finally:
        event.remove(db.engine, 'after_cursor_execute', fail)
    assert client.get('/api/v1/stack-replays').json()['items'] == []
    assert submit(ctx)['counts']['total'] == 3


def test_point_commit_failure_cleans_own_files_and_retains_other_replay(client):
    ctx = context(client)
    other = execute(ctx, submit(ctx))
    db = ctx[1].state.db
    original_files = {path: path.read_bytes() for path in db.stack_replay_dir.iterdir()}
    job = submit(ctx, 'faulted')
    injected = False
    def fail(conn):
        nonlocal injected
        if not injected and conn.exec_driver_sql("SELECT count(*) FROM stack_replay_ticks WHERE job_id=? AND json_extract(payload,'$.status')='completed'", (job['id'],)).scalar():
            injected = True
            raise RuntimeError('Injected publication commit failure')
    event.listen(db.engine, 'commit', fail)
    try:
        result = execute(ctx, job)
    finally:
        event.remove(db.engine, 'commit', fail)
    assert injected and result.status == 'failed'
    assert result.counts.failed == 1 and result.counts.not_attempted == 2
    assert {path: path.read_bytes() for path in db.stack_replay_dir.iterdir()} == original_files
    assert list(db.stack_replay_staging_dir.iterdir()) == []
    assert client.get(other.ticks[0].points.download_url).status_code == 200


def test_short_point_write_removes_partial_staging_file(client, monkeypatch):
    import os
    ctx = context(client)
    job = submit(ctx)
    original = os.fdopen
    class ShortWriter:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def write(self, data): return self.stream.write(data[:3])
    monkeypatch.setattr(stack_replays.os, 'fdopen', lambda fd, mode: ShortWriter(original(fd, mode)) if mode == 'wb' else original(fd, mode))
    result = execute(ctx, job)
    assert result.status == 'failed' and result.counts.failed == 1
    assert list(ctx[1].state.db.stack_replay_dir.iterdir()) == []
    assert list(ctx[1].state.db.stack_replay_staging_dir.iterdir()) == []


def test_actual_cancel_after_first_tick_preserves_partial_evidence(client, monkeypatch):
    ctx = context(client)
    job = submit(ctx)
    claimed = claim(ctx, job)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls >= 3:
            client.post('/api/v1/stack-replays/' + job['id'] + '/cancel')
            return True
        return False
    with pytest.raises(AdapterCancelled):
        stack_replays.execute_claim(ctx[1].state.db, claimed, ctx[1].state.stack_registry, ctx[1].state.sensor_registry, cancel)
    result = jobs.fail_job(ctx[1].state.db, claimed, 'Stack acknowledged between-tick cancellation', status='cancelled')
    assert result.status == 'cancelled' and result.counts.completed == 1 and result.counts.not_attempted == 2
    assert client.get(result.ticks[0].points.download_url).status_code == 200


def test_bad_component_output_identity_not_published(client, monkeypatch):
    ctx = context(client)
    original = StackSession.step
    def bad(self, *args, **kwargs):
        return original(self, *args, **kwargs).model_copy(update={'sequence': 999})
    monkeypatch.setattr(StackSession, 'step', bad)
    result = execute(ctx, submit(ctx))
    assert result.status == 'failed' and result.counts.failed == 1 and result.counts.not_attempted == 2
    assert result.ticks[0].output is None
    assert list(ctx[1].state.db.stack_replay_dir.iterdir()) == []


def test_all_stack_routes_require_configured_token(settings):
    from fastapi.testclient import TestClient
    from ade2e.main import create_app
    settings.api_token = 'stack-test-secret'
    with TestClient(create_app(settings)) as client:
        for method, path in [('GET', '/stack-components'), ('GET', '/stacks'), ('POST', '/stacks'),
            ('GET', '/stacks/id/revisions'), ('GET', '/stacks/id/export'), ('GET', '/stack-replay-sources/id'),
            ('GET', '/stack-replays'), ('POST', '/stack-replays'), ('GET', '/stack-replays/id'),
            ('POST', '/stack-replays/id/cancel'), ('GET', '/stack-replays/id/export'), ('GET', '/stack-replays/id/ticks/0/points')]:
            assert client.request(method, '/api/v1' + path).status_code == 401, path
        assert client.get('/api/v1/stack-components', headers={'Authorization': 'Bearer stack-test-secret'}).status_code == 200


def test_schema_hash_tamper_drops_ego_capability(client):
    from ade2e.recording_models import RecordingSchemaRow
    ctx = context(client)
    with ctx[1].state.db.session() as session:
        row = session.get(RecordingSchemaRow, (ctx[4]['id'], 2))
        row.payload = row.payload | {'sha256': '0'*64}
        session.commit()
    source = client.get('/api/v1/stack-replay-sources/' + ctx[4]['id']).json()
    assert all(item['role'] != 'ego' for item in source['channels'])
    assert client.post('/api/v1/stack-replays', json=ctx[2], headers={'Idempotency-Key': 'bad-schema'}).status_code == 422


def _killable_stack_worker(directory, job_id, pipe):
    from pathlib import Path
    from ade2e.database import Database, Settings
    from ade2e.adapters import Registry
    from ade2e.sensor_decoders import SensorDecoderRegistry
    db = Database(Settings(data_dir=Path(directory), seed_examples=False))
    db.initialize()
    original = StackSession.step
    def pause(self, packet, points_bytes=None):
        if packet.sequence == 1:
            pipe.send('second-tick-running')
            pipe.recv()
        return original(self, packet, points_bytes)
    StackSession.step = pause
    run_once(db, Registry(load_plugins=False), 'killable-stack-worker', job_id=job_id,
        stack_registry=StackRegistry(load_plugins=False), sensor_registry=SensorDecoderRegistry())


def test_actual_process_kill_preserves_completed_tick_and_marks_started_failure(client, monkeypatch):
    import multiprocessing
    from datetime import datetime, timedelta, timezone
    ctx = context(client)
    job = submit(ctx)
    mp = multiprocessing.get_context('spawn')
    parent, child = mp.Pipe()
    process = mp.Process(target=_killable_stack_worker, args=(str(ctx[1].state.settings.data_dir), job['id'], child))
    process.start()
    try:
        assert parent.poll(15) and parent.recv() == 'second-tick-running'
        live = jobs.job_get(ctx[1].state.db, job['id'], kind='stack_replay')
        assert live.counts.model_dump() == {'total': 3, 'running': 1, 'completed': 1, 'failed': 0, 'not_attempted': 1}
        first_bytes = client.get(live.ticks[0].points.download_url).content
        process.kill()
        process.join(timeout=5)
        assert not process.is_alive()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec='microseconds')
        monkeypatch.setattr(jobs, 'utcnow', lambda: future)
        jobs.expire_jobs(ctx[1].state.db)
        result = jobs.job_get(ctx[1].state.db, job['id'], kind='stack_replay')
        assert result.status == 'interrupted'
        assert result.counts.model_dump() == {'total': 3, 'running': 0, 'completed': 1, 'failed': 1, 'not_attempted': 1}
        assert result.ticks[0].output_hash == live.ticks[0].output_hash
        assert client.get(result.ticks[0].points.download_url).content == first_bytes
        assert result.ticks[1].started_at and result.ticks[2].started_at is None
        assert result.manifest_hash and client.get(result.download_url).status_code == 200
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        parent.close()
        child.close()


def test_one_component_tick_failure_retains_other_outcomes_and_full_denominator(client, monkeypatch):
    ctx = context(client)
    original = StackSession.step
    def fail_second(self, packet, points_bytes=None):
        if packet.sequence == 1:
            raise RuntimeError('Injected second-tick component failure')
        return original(self, packet, points_bytes)
    monkeypatch.setattr(StackSession, 'step', fail_second)
    result = execute(ctx, submit(ctx))
    assert result.status == 'failed'
    assert result.counts.model_dump() == {'total': 3, 'running': 0, 'completed': 2, 'failed': 1, 'not_attempted': 0}
    assert result.ticks[1].output is None and 'second-tick component failure' in result.ticks[1].error
    assert all(tick.points is not None and client.get(tick.points.download_url).status_code == 200 for tick in result.ticks)
    assert result.ticks[0].output['decision']['mode'] == 'obstacle_stop'
    assert result.ticks[2].output['decision']['mode'] == 'pilot_cruise'
    assert client.get(result.download_url).status_code == 200


def test_absent_ground_declaration_never_assumes_free_driving(client):
    ctx = context(client)
    unspecified = definition()
    unspecified['definition']['perception']['parameters'] = {}
    response = client.post('/api/v1/stacks', json=unspecified)
    assert response.status_code == 201, response.text
    job = submit(ctx, body=ctx[2] | {'stack_id': response.json()['id']})
    result = execute(ctx, job)
    assert all(tick.output['decision']['mode'] == 'unknown_stop' for tick in result.ticks)
    assert all(tick.output['control']['brake'] == 1 for tick in result.ticks)


def test_declared_age_valid_but_sensor_skew_exceeds_kernel_limit(client):
    ctx = context(client, binding_values={'log_time_offset_ns': '-60000000'})
    job = submit(ctx)
    assert job['inputs'][0]['lidar_age_ns'] == '60000000'
    result = execute(ctx, job)
    assert result.ticks[0].output['decision']['mode'] == 'unknown_stop'
    assert 'skew' in str(result.ticks[0].output['warnings'])
