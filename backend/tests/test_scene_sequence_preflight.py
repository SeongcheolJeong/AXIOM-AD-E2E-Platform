"""Exact native clocks, nonmutating inspection and preview-bound source freezing."""
import copy

import pytest
from sqlalchemy import select, func

from ade2e.scene_sequence_models import SceneSequenceRow
from ade2e.scene_sequence_schemas import SceneSequenceCreate
from ade2e.scene_sequences import prepare_sequence, validate_retained_sequence, get_scene_sequence
from ade2e.service import digest
from test_planning_replay import BASE, authored, log_client, recorded, replay, sequence


def preview(client, body):
    response = client.post('/api/v1/scene-sequences/preview', json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('role', ['ego', 'objects'])
@pytest.mark.parametrize('delta', [-1, 1, 100_000_001])
def test_recorded_capture_clock_mismatch_blocks_freeze(log_client, role, delta):
    values = {'timestamp_ns': str(BASE + delta)}
    body, _ = recorded(log_client, **({'ego_values': values} if role == 'ego' else {'object_values': values}))
    checked = preview(log_client, body)
    assert not checked['executable']
    assert checked['snapshot_sha256'] is None
    assert checked['checks'][0]['id'] == 'capture_clock_mismatch'
    assert role in checked['blocking_reasons'][0]
    response = log_client.post('/api/v1/scene-sequences', json=body)
    assert response.status_code == 422 and 'capture timestamp' in response.text


def test_preview_is_nonmutating_and_freeze_matches_exact_sources(log_client):
    body, original = recorded(log_client)
    db = log_client.app.state.db
    with db.read_session() as session:
        before = session.scalar(select(func.count()).select_from(SceneSequenceRow))
    first = preview(log_client, body)
    second = preview(log_client, body)
    assert first == second and first['executable']
    assert first['input_summary']['recording_sha256'] == original['sha256']
    assert first['supported_modes'] == ['original_replay', 'open_loop', 'planning_closed_loop']
    assert all(frame['ego_source']['recording_sha256'] == original['sha256'] for frame in first['frames'])
    with db.read_session() as session:
        assert session.scalar(select(func.count()).select_from(SceneSequenceRow)) == before
    response = log_client.post('/api/v1/scene-sequences/freeze', json={'definition':body, 'preview_sha256':first['snapshot_sha256']})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['sha256'] == first['snapshot_sha256']
    assert saved['snapshot']['schema_version'] == 'scene-sequence/2'


def test_definition_drift_cannot_publish_after_preview(log_client):
    body, _ = recorded(log_client)
    checked = preview(log_client, body)
    changed = copy.deepcopy(body)
    changed['ego_ordinals'] = changed['ego_ordinals'][:-1]
    response = log_client.post('/api/v1/scene-sequences/freeze', json={'definition':changed, 'preview_sha256':checked['snapshot_sha256']})
    assert response.status_code == 409 and 'changed after preview' in response.text
    with log_client.app.state.db.read_session() as session:
        assert session.scalar(select(func.count()).select_from(SceneSequenceRow)) == 0


def test_explicit_offsets_preserve_native_capture_identity(log_client):
    body, _ = recorded(log_client, relative=True)
    body.update(ego_log_time_offset_ns='500000', object_log_time_offset_ns='500000')
    checked = preview(log_client, body)
    assert checked['executable']
    saved = log_client.post('/api/v1/scene-sequences/freeze', json={'definition':body, 'preview_sha256':checked['snapshot_sha256']}).json()
    for i, frame in enumerate(saved['snapshot']['frames']):
        original = BASE + i * 100_000_000
        assert frame['at_ns'] == str(original + 500000)
        assert frame['ego']['timestamp_ns'] == frame['objects']['timestamp_ns'] == str(original)
        assert frame['ego_source']['log_time_ns'] == frame['object_source']['log_time_ns'] == str(original)
        assert frame['object_reference_ego_source']['ordinal'] == frame['ego_source']['ordinal']


def test_missing_objects_preserve_steps_without_claiming_closed_loop(log_client):
    body, _ = recorded(log_client, skip_first=True)
    checked = preview(log_client, body)
    assert checked['executable']
    assert checked['frame_count'] == 4
    assert checked['input_summary']['object_steps_unevaluated'] == 1
    assert checked['frames'][0]['object_drop'] == 'no_causal_object_message'
    assert checked['supported_modes'] == ['original_replay', 'open_loop']
    assert checked['next_actions']


def test_legacy_v1_snapshot_is_revalidated_without_hash_rewrite(log_client):
    body, _ = recorded(log_client)
    saved = sequence(log_client, body)
    db = log_client.app.state.db
    with db.session.begin() as session:
        old_snapshot = prepare_sequence(session, db, SceneSequenceCreate.model_validate(body), snapshot_version='scene-sequence/1')
        row = session.get(SceneSequenceRow, saved['id'])
        old_payload = row.payload | {'snapshot':old_snapshot, 'sha256':digest(old_snapshot)}
        row.payload, row.sha256 = old_payload, old_payload['sha256']
    with db.read_session() as session:
        old_detail = get_scene_sequence(session, saved['id'])
        assert validate_retained_sequence(session, db, old_detail) == old_snapshot
        assert old_detail.sha256 == digest(old_snapshot)
    received = log_client.get('/api/v1/scene-sequences/' + saved['id']).json()
    assert received['sha256'] == old_payload['sha256']
    assert 'metadata' not in received['snapshot']['recording_snapshot']


def test_authored_preview_declares_reference_and_does_not_assume_source_rights(log_client):
    checked = preview(log_client, authored())
    assert checked['executable'] and checked['source_kind'] == 'synthetic'
    assert checked['checks'][0]['status'] == 'authored'
    assert checked['source_use_scope']['commercial_use'] is None
    assert checked['input_summary']['recording_sha256'] is None
