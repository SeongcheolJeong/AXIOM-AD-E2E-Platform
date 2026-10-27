"""Published engine bytes -> ingested recording -> immutable relational origin."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from mcap.writer import Writer
from sqlalchemy import event, select

from ade2e.adapters import Registry
from ade2e.database import ArtifactRow, RunRow
from ade2e.main import create_app
from ade2e.recording_models import RecordingRow
from ade2e.reference import ReferenceAdapter
from ade2e.run_recording_models import RecordingOriginRow
from ade2e.schemas import ArtifactPayload


def recording_bytes():
    stream = io.BytesIO()
    writer = Writer(stream)
    writer.start()
    channel = writer.register_channel('/fixture', 'unknown', 0)
    writer.add_message(channel, 123, b'actual message bytes', 100, 7)
    writer.finish()
    return stream.getvalue()


@pytest.fixture
def recording_run(settings):
    payload = recording_bytes()

    class RecordingAdapter(ReferenceAdapter):
        def metadata(self):
            return super().metadata().model_copy(update={'id': 'recording-fixture', 'version': 'fixture/1'})

        def execute(self, scenario):
            result = super().execute(scenario)
            result.artifact_payloads.append(ArtifactPayload(name='fixture.mcap', kind='sensor_recording', media_type='application/mcap', content=payload))
            return result

    registry = Registry(load_plugins=False)
    registry.register(RecordingAdapter())
    with TestClient(create_app(settings, registry)) as client:
        scenario = client.post('/api/v1/scenarios', json={'name': 'Original fixture', 'category': 'lane_follow', 'duration_s': 2, 'parameters': {'trigger_time_s': 1}}).json()
        response = client.post('/api/v1/runs', json={'scenario_id': scenario['id'], 'adapter_id': 'recording-fixture'})
        assert response.status_code == 201, response.text
        run = response.json()
        assert run['status'] == 'completed', run
        artifact = next(item for item in run['artifacts'] if item['kind'] == 'sensor_recording')
        yield client, run, artifact, payload


def test_import_links_exact_source_artifact_and_reuses_original_id(recording_run):
    client, run, artifact, payload = recording_run
    path = f'/api/v1/runs/{run["id"]}/recordings'
    first = client.post(path, json={'artifact_id': artifact['id']})
    assert first.status_code == 201, first.text
    repeat = client.post(path, json={'artifact_id': artifact['id']})
    assert repeat.status_code == 200 and repeat.json() == first.json()
    recording = first.json()
    assert client.get(recording['download_url']).content == payload
    origin = client.get(f'/api/v1/recordings/{recording["id"]}/origin').json()
    assert origin['run_id'] == run['id'] and origin['artifact_id'] == artifact['id']
    assert origin['snapshot']['input_hash'] == run['provenance']['input_hash']
    assert origin['snapshot']['model_fidelity'] == run['provenance']['model_fidelity']
    client.patch(f'/api/v1/scenarios/{run["scenario_id"]}', json={'name': 'Library changed'}).raise_for_status()
    assert client.get(f'/api/v1/recordings/{recording["id"]}/origin').json() == origin
    assert len(client.get('/api/v1/runs').json()) == 1


def test_foreign_artifact_and_non_mcap_are_rejected(recording_run):
    client, run, artifact, _ = recording_run
    assert client.post('/api/v1/runs/missing/recordings', json={'artifact_id': artifact['id']}).status_code == 404
    assert client.post(f'/api/v1/runs/{run["id"]}/recordings', json={'artifact_id': 'missing'}).status_code == 404
    manifest = next(item for item in run['artifacts'] if item['kind'] == 'evidence_manifest')
    assert client.post(f'/api/v1/runs/{run["id"]}/recordings', json={'artifact_id': manifest['id']}).status_code == 422
    assert client.get('/api/v1/recordings').json() == []


def test_altered_artifact_bytes_never_publish_a_recording(recording_run):
    client, run, artifact, payload = recording_run
    db = client.app.state.db
    with db.session() as session:
        row = session.get(ArtifactRow, artifact['id'])
        path = db.artifact_dir / row.storage_key
    path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    result = client.post(f'/api/v1/runs/{run["id"]}/recordings', json={'artifact_id': artifact['id']})
    assert result.status_code == 409
    assert client.get('/api/v1/recordings').json() == []


def test_tampered_run_output_cannot_become_sensor_origin(recording_run):
    client, run, artifact, _ = recording_run
    with client.app.state.db.session.begin() as session:
        row = session.get(RunRow, run['id'])
        payload = json.loads(json.dumps(row.payload))
        payload['metrics'][0]['value'] = 12345
        row.payload = payload
    assert client.post(f'/api/v1/runs/{run["id"]}/recordings', json={'artifact_id': artifact['id']}).status_code == 409
    assert client.get('/api/v1/recordings').json() == []


def test_origin_commit_failure_is_repaired_without_duplicate_recording(recording_run):
    client, run, artifact, _ = recording_run
    db = client.app.state.db

    def fail_origin(connection, cursor, statement, *_):
        if statement.startswith('INSERT INTO recording_origins'):
            raise RuntimeError('origin publication interrupted')

    event.listen(db.engine, 'before_cursor_execute', fail_origin)
    try:
        with pytest.raises(RuntimeError, match='origin publication'):
            client.post(f'/api/v1/runs/{run["id"]}/recordings', json={'artifact_id': artifact['id']})
    finally:
        event.remove(db.engine, 'before_cursor_execute', fail_origin)
    with db.session() as session:
        original_id = session.scalar(select(RecordingRow)).id
        assert list(session.scalars(select(RecordingOriginRow))) == []
    assert client.get(f'/api/v1/recordings/{original_id}/origin').json() is None
    repaired = client.post(f'/api/v1/runs/{run["id"]}/recordings', json={'artifact_id': artifact['id']})
    assert repaired.status_code == 200 and repaired.json()['id'] == original_id
    assert client.get(f'/api/v1/recordings/{original_id}/origin').json()['artifact_id'] == artifact['id']
    assert len(client.get('/api/v1/recordings').json()) == 1


@pytest.mark.parametrize('mutation', ['hash', 'snapshot_type', 'artifact_type', 'index'])
def test_origin_metadata_integrity_fails_closed(recording_run, mutation):
    client, run, artifact, _ = recording_run
    recording = client.post(f'/api/v1/runs/{run["id"]}/recordings', json={'artifact_id': artifact['id']}).json()
    with client.app.state.db.session.begin() as session:
        row = session.get(RecordingOriginRow, recording['id'])
        if mutation == 'hash': row.definition_hash = '0' * 64
        elif mutation == 'snapshot_type': row.payload = []
        elif mutation == 'artifact_type': row.payload = {**row.payload, 'artifact': 'bad'}
        else: row.created_at = 'wrong'
    assert client.get(f'/api/v1/recordings/{recording["id"]}/origin').status_code == 409
