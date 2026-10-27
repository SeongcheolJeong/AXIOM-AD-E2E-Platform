"""Real MCAP bytes → relational catalog → bounded original/derived-file access."""

import hashlib
import io
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcap.writer import CompressionType, IndexType, Writer
from sqlalchemy import event, inspect, select

from ade2e import mcap_ingest
from ade2e.adapters import Registry
from ade2e.database import Base, Database, RunRow, ScenarioRow, SchemaVersionRow, Settings, SCHEMA_VERSION
from ade2e.main import create_app
from ade2e.recording_models import RecordingChannelRow, RecordingMessageRow, RecordingRow, RecordingSchemaRow
from ade2e.recordings import MAX_DECODE_BYTES

BASE_NS = 2**63 + 123
MAX_NS = 2**64 - 1


def make_mcap(messages=None, *, compression=CompressionType.NONE, indexed=True, chunks=True, duplicate_topics=False):
    output = io.BytesIO()
    writer = Writer(output, compression=compression, chunk_size=1024, use_chunking=chunks,
                    index_types=IndexType.ALL if indexed else IndexType.NONE,
                    use_statistics=indexed, use_summary_offsets=indexed,
                    repeat_schemas=True, repeat_channels=True,
                    enable_crcs=True, enable_data_crcs=True)
    writer.start(profile="original.test.v1", library="independent-backend-tests")
    schema = writer.register_schema("TestTelemetry", "jsonschema", b'{"type":"object"}')
    json_channel = writer.register_channel("/same-topic" if duplicate_topics else "/telemetry", "json", schema, {"clock": "log_time_ns"})
    opaque_channel = writer.register_channel("/same-topic" if duplicate_topics else "/opaque", "unknown-test-encoding", 0)
    empty_channel = writer.register_channel("/empty", "json", 0)
    writer.add_metadata("test-source", {"source_kind": "synthetic", "calibration": "absent"})
    for channel_id, timestamp, payload, publish_time, sequence in messages or []:
        writer.add_message(channel_id, timestamp, payload, publish_time, sequence)
    writer.finish()
    return output.getvalue(), json_channel, opaque_channel, empty_channel


def upload(client, data, **metadata):
    return client.post("/api/v1/recordings", params={"name": "Original fixture", "source_kind": "synthetic", "source": "authored test data", **metadata},
                       content=data, headers={"Content-Type": "application/octet-stream"})


def samples(client, recording_id, channel, at=None, limit=1):
    params = {"channel_id": channel, "limit": limit}
    if at is not None:
        params["at_ns"] = str(at)
    response = client.get(f"/api/v1/recordings/{recording_id}/samples", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("compression", [CompressionType.NONE, CompressionType.ZSTD, CompressionType.LZ4])
@pytest.mark.parametrize("indexed", [True, False])
def test_import_counts_exact_original_and_real_relational_index(client, compression, indexed):
    messages = [(1, BASE_NS, b'{"speed":3}', BASE_NS - 12, 7), (2, MAX_NS, b"\x00\xffunknown", MAX_NS - 9, 99)]
    data, json_id, opaque_id, empty_id = make_mcap(messages, compression=compression, indexed=indexed, duplicate_topics=True)
    imported = upload(client, data)
    assert imported.status_code == 201, imported.text
    detail = imported.json()
    assert detail["message_count"] == 2 and detail["channel_count"] == 3
    assert detail["start_time_ns"] == str(BASE_NS) and detail["end_time_ns"] == str(MAX_NS)
    assert detail["sha256"] == hashlib.sha256(data).hexdigest()
    assert detail["index"]["source_sha256"] == detail["sha256"]
    assert detail["metadata"] == [{"name": "test-source", "metadata": {"source_kind": "synthetic", "calibration": "absent"}}]
    assert detail["channels"][0]["topic"] == detail["channels"][1]["topic"]
    assert detail["channels"][0]["decoder"] == "json" and detail["channels"][1]["decoder"] == "opaque"
    assert client.get(detail["download_url"]).content == data
    assert client.get(f'/api/v1/recordings/{detail["id"]}').json() == detail
    assert client.get("/api/v1/recordings", params={"q": "authored test"}).json()[0]["id"] == detail["id"]
    assert client.get("/api/v1/recordings", params={"q": "not-present"}).json() == []
    empty = samples(client, detail["id"], empty_id)
    assert empty["samples"] == [] and empty["warnings"]
    with client.app.state.db.session() as session:
        assert len(list(session.scalars(select(RecordingRow)))) == 1
        assert len(list(session.scalars(select(RecordingSchemaRow)))) == 1
        assert len(list(session.scalars(select(RecordingChannelRow)))) == 3
        indexes = list(session.scalars(select(RecordingMessageRow).order_by(RecordingMessageRow.ordinal)))
        assert [entry.log_time_key for entry in indexes] == [f"{BASE_NS:020d}", f"{MAX_NS:020d}"]
        assert [entry.payload_offset for entry in indexes] == [0, len(messages[0][2])]
        row = session.get(RecordingRow, detail["id"])
        sidecar = (client.app.state.db.recording_dir / row.index_storage_key).read_bytes()
        assert sidecar == b"".join(item[2] for item in messages)
        assert hashlib.sha256(sidecar).hexdigest() == detail["index"]["sha256"]


def test_seek_handles_file_order_duplicate_timestamps_and_uint64_without_reparse(client, monkeypatch):
    timestamps = [MAX_NS, BASE_NS + 20, BASE_NS + 10, BASE_NS + 20, BASE_NS + 30]
    messages = [(1, timestamp, json.dumps({"ordinal": ordinal}).encode(), MAX_NS - ordinal, ordinal + 1) for ordinal, timestamp in enumerate(timestamps)]
    data, channel, _, _ = make_mcap(messages, indexed=False, chunks=False)
    response = upload(client, data)
    assert response.status_code == 201, response.text
    recording_id = response.json()["id"]
    # Prove sample access uses the derived seek index rather than parsing again.
    monkeypatch.setattr(mcap_ingest, "parse_mcap", lambda *_: pytest.fail("Seek reparsed the MCAP"))
    middle = samples(client, recording_id, channel, BASE_NS + 25, limit=3)
    assert [item["ordinal"] for item in middle["samples"]] == [3, 4, 0]
    assert [item["log_time_ns"] for item in middle["samples"]] == [str(BASE_NS + 20), str(BASE_NS + 30), str(MAX_NS)]
    assert middle["samples"][0]["publish_time_ns"] == str(MAX_NS - 3)
    assert middle["samples"][0]["sequence"] == 4
    assert samples(client, recording_id, channel, BASE_NS + 20)["samples"][0]["ordinal"] == 3
    before = samples(client, recording_id, channel, 0)
    assert before["samples"][0]["ordinal"] == 2 and before["warnings"]
    assert before["requested_time_ns"] == "0"
    assert samples(client, recording_id, channel, MAX_NS)["samples"][0]["ordinal"] == 0


@pytest.mark.parametrize("payload,expected_status", [
    (b"null", "decoded"), (b'{"ok":true}', "decoded"), (b"NaN", "invalid_json"), (b"Infinity", "invalid_json"),
    (b"-Infinity", "invalid_json"), (b"1e999", "invalid_json"), (b"\xff", "invalid_json"),
    (b'{"broken":', "invalid_json"), (b'"\\ud800"', "invalid_json"),
    (b"x" * (MAX_DECODE_BYTES + 1), "too_large"),
])
def test_decoder_is_explicit_strict_and_bounded(client, payload, expected_status):
    data, channel, _, _ = make_mcap([(1, 10, payload, 9, 8)])
    detail = upload(client, data).json()
    sample = samples(client, detail["id"], channel)["samples"][0]
    assert sample["decode_status"] == expected_status
    assert len(sample["preview_hex"]) <= 128
    assert sample["sha256"] == hashlib.sha256(payload).hexdigest()
    if payload == b"null":
        assert sample["decoded"] is None and sample["decoded_text"] == "null"


def test_large_json_integer_is_preserved_as_exact_text_without_unsafe_browser_number(client):
    payload = b'{"timestamp":18446744073709551615,"nested":[9007199254740993]}'
    data, channel, _, _ = make_mcap([(1, MAX_NS, payload, MAX_NS, 0)])
    detail = upload(client, data).json()
    response = samples(client, detail["id"], channel)
    sample = response["samples"][0]
    assert sample["decode_status"] == "decoded" and sample["decoded"] is None
    assert sample["decoded_text"] == payload.decode()
    assert "JavaScript" in response["warnings"][0]


def test_unknown_encoding_stays_opaque_even_if_payload_looks_like_json(client):
    payload = b'{"not_a_known_decoder":true}'
    data, _, channel, _ = make_mcap([(2, 1, payload, 0, 1)])
    detail = upload(client, data).json()
    sample = samples(client, detail["id"], channel)["samples"][0]
    assert sample["decode_status"] == "opaque" and sample["decoded"] is None
    assert sample["preview_hex"] == payload.hex()


def test_empty_recording_retains_channels_with_null_time_bounds(client):
    data, channel, _, empty = make_mcap([])
    response = upload(client, data)
    assert response.status_code == 201, response.text
    detail = response.json()
    assert detail["message_count"] == 0 and detail["channel_count"] == 3
    assert detail["start_time_ns"] is None and detail["end_time_ns"] is None and detail["duration_s"] == 0
    assert all(item["message_count"] == 0 and item["start_time_ns"] is None for item in detail["channels"])
    assert samples(client, detail["id"], empty)["requested_time_ns"] is None


def test_import_idempotence_is_metadata_sensitive_and_concurrent(client):
    data, *_ = make_mcap([(1, 1, b"null", 0, 0)])
    with ThreadPoolExecutor(max_workers=3) as workers:
        responses = list(workers.map(lambda _: upload(client, data), range(3)))
    assert sorted(response.status_code for response in responses) == [200, 200, 201]
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1
    different = upload(client, data, source="different attribution")
    assert different.status_code == 201 and different.json()["id"] not in ids
    assert len(client.get("/api/v1/recordings").json()) == 2
    assert len(list(client.app.state.db.recording_dir.iterdir())) == 4
    assert list(client.app.state.db.recording_staging_dir.iterdir()) == []


def test_upload_bounds_invalid_magic_and_resource_validation(client, monkeypatch):
    assert upload(client, b"not-an-mcap").status_code == 422
    assert client.post("/api/v1/recordings?name=X", content=b"bad", headers={"Content-Type": "application/json"}).status_code == 415
    monkeypatch.setattr(mcap_ingest, "MAX_UPLOAD_BYTES", 16)
    assert upload(client, b"x" * 17).status_code == 413
    assert client.get("/api/v1/recordings").json() == []
    assert not list(client.app.state.db.recording_dir.iterdir())
    assert not list(client.app.state.db.recording_staging_dir.iterdir())
    assert client.get("/api/v1/recordings/missing").status_code == 404


def test_invalid_footer_crc_removes_already_written_payload_sidecar(client):
    data, *_ = make_mcap([(1, 1, b'{"written_before_footer":true}', 0, 0)])
    corrupt = bytearray(data)
    corrupt[-12] ^= 1  # Footer CRC immediately precedes closing magic.
    response = upload(client, bytes(corrupt))
    assert response.status_code == 422 and "CRC" in response.json()["detail"]
    assert client.get("/api/v1/recordings").json() == []
    assert not list(client.app.state.db.recording_dir.iterdir())
    assert not list(client.app.state.db.recording_staging_dir.iterdir())


def test_streamed_upload_size_is_enforced_without_content_length(client, monkeypatch):
    monkeypatch.setattr(mcap_ingest, "MAX_UPLOAD_BYTES", 16)
    response = client.post("/api/v1/recordings?name=streamed", content=iter([b"12345678", b"x" * 9]), headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 413
    assert client.get("/api/v1/recordings").json() == []
    assert not list(client.app.state.db.recording_staging_dir.iterdir())


def test_extreme_declared_size_is_rejected_without_integer_conversion_failure(client):
    response = client.post("/api/v1/recordings?name=oversized", content=b"small", headers={"Content-Type": "application/octet-stream", "Content-Length": "9" * 5000})
    assert response.status_code == 413
    assert not list(client.app.state.db.recording_staging_dir.iterdir())


@pytest.mark.parametrize("bad_time", [str(2**64), "-1", "1.5", "NaN", "", "9" * 21])
def test_seek_rejects_invalid_uint64(client, bad_time):
    data, channel, _, _ = make_mcap([(1, 1, b"null", 0, 0)])
    detail = upload(client, data).json()
    response = client.get(f'/api/v1/recordings/{detail["id"]}/samples', params={"channel_id": channel, "at_ns": bad_time})
    assert response.status_code == 422


def test_unknown_channel_and_sample_limit_validation(client):
    data, *_ = make_mcap([])
    detail = upload(client, data).json()
    path = f'/api/v1/recordings/{detail["id"]}/samples'
    assert client.get(path, params={"channel_id": 999}).status_code == 404
    for limit in (0, 101):
        assert client.get(path, params={"channel_id": 1, "limit": limit}).status_code == 422


def test_original_and_sidecar_integrity_fail_closed(client):
    data, channel, _, _ = make_mcap([(1, 1, b"null", 0, 0)])
    detail = upload(client, data).json()
    with client.app.state.db.session() as session:
        row = session.get(RecordingRow, detail["id"])
        original = client.app.state.db.recording_dir / row.storage_key
        sidecar = client.app.state.db.recording_dir / row.index_storage_key
    original.write_bytes(b"x" + data[1:])
    sidecar.write_bytes(b"NULL")
    assert client.get(detail["download_url"]).status_code == 409
    assert client.get(f'/api/v1/recordings/{detail["id"]}/samples', params={"channel_id": channel}).status_code == 409


def test_recording_paths_cannot_escape_storage_root(client, tmp_path):
    data, *_ = make_mcap([])
    detail = upload(client, data).json()
    outside = tmp_path / "outside.mcap"
    outside.write_bytes(data)
    with client.app.state.db.session.begin() as session:
        row = session.get(RecordingRow, detail["id"])
        row.storage_key = "../outside.mcap"
    assert client.get(detail["download_url"]).status_code == 404


@pytest.mark.parametrize("failure", ["commit", "payload_write", "original_copy"])
def test_failed_recording_publication_cleans_own_files_preserves_existing(settings, monkeypatch, failure):
    app = create_app(settings, Registry(load_plugins=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        data, *_ = make_mcap([(1, 1, b"null", 0, 0)])
        committed = upload(client, data).json()
        original_files = {path.name: path.read_bytes() for path in app.state.db.recording_dir.iterdir()}
        real_open = Path.open

        class PartialWrite:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def write(self, content):
                self.stream.write(content[:2]); self.stream.flush()
                raise OSError("Injected recording storage failure")
            def __exit__(self, *_): self.stream.close()

        def fault_open(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            suffix = ".payloads" if failure == "payload_write" else ".mcap"
            return PartialWrite(stream) if path.suffix == suffix and args and args[0] == "xb" else stream

        def fail_commit(*_): raise OSError("Injected recording commit failure")

        if failure == "commit":
            event.listen(app.state.db.session.class_, "before_commit", fail_commit)
        else:
            monkeypatch.setattr(Path, "open", fault_open)
        try:
            response = upload(client, data, name="Uncommitted second attribution")
        finally:
            if failure == "commit": event.remove(app.state.db.session.class_, "before_commit", fail_commit)
        assert response.status_code == 500
        assert [item["id"] for item in client.get("/api/v1/recordings").json()] == [committed["id"]]
        assert {path.name: path.read_bytes() for path in app.state.db.recording_dir.iterdir()} == original_files
        assert list(app.state.db.recording_staging_dir.iterdir()) == []


def test_duplicate_commit_failure_does_not_delete_existing_files(settings):
    app = create_app(settings, Registry(load_plugins=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        data, *_ = make_mcap([])
        committed = upload(client, data).json()
        original_files = {path.name: path.read_bytes() for path in app.state.db.recording_dir.iterdir()}
        def fail_commit(*_): raise OSError("Injected duplicate commit failure")
        event.listen(app.state.db.session.class_, "before_commit", fail_commit)
        try: response = upload(client, data)
        finally: event.remove(app.state.db.session.class_, "before_commit", fail_commit)
        assert response.status_code == 500
        assert {path.name: path.read_bytes() for path in app.state.db.recording_dir.iterdir()} == original_files
        assert client.get(committed["download_url"]).content == data


def test_recording_auth_applies_before_body_is_written(tmp_path):
    settings = Settings(data_dir=tmp_path, seed_examples=False, api_token="recording-secret")
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        data, *_ = make_mcap([])
        assert upload(client, data).status_code == 401
        assert client.get("/api/v1/recordings").status_code == 401
        assert not list(client.app.state.db.recording_dir.iterdir())
        assert not list(client.app.state.db.recording_staging_dir.iterdir())
        response = client.post("/api/v1/recordings?name=Authorized", content=data, headers={"Authorization": "Bearer recording-secret", "Content-Type": "application/octet-stream"})
        assert response.status_code == 201
        rid = response.json()["id"]
        for path in (f"/api/v1/recordings/{rid}", f"/api/v1/recordings/{rid}/download", f"/api/v1/recordings/{rid}/samples?channel_id=1"):
            assert client.get(path).status_code == 401


def test_parse_runs_off_eventloop_and_persists_across_restart(settings, monkeypatch):
    original_parse = mcap_ingest.parse_mcap
    called = []

    def verify_worker_thread(*args):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        called.append(True)
        return original_parse(*args)

    monkeypatch.setattr(mcap_ingest, "parse_mcap", verify_worker_thread)
    data, channel, _, _ = make_mcap([(1, MAX_NS, b"null", MAX_NS, 2**32 - 1)])
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        detail = upload(client, data).json()
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        assert client.get(f'/api/v1/recordings/{detail["id"]}').json() == detail
        assert client.get(detail["download_url"]).content == data
        selected = samples(client, detail["id"], channel)["samples"][0]
        assert selected["sequence"] == 2**32 - 1
        assert selected["log_time_ns"] == str(MAX_NS)
    assert called == [True]


def test_schema_one_migrates_additively_and_preserves_actual_runs(tmp_path):
    settings = Settings(data_dir=tmp_path, seed_examples=True)
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        originals = {item["id"]: client.get(f'/api/v1/runs/{item["id"]}').json() for item in client.get("/api/v1/runs").json()}
    legacy = Database(settings)
    recording_tables = [RecordingMessageRow.__table__, RecordingChannelRow.__table__, RecordingSchemaRow.__table__, RecordingRow.__table__]
    with legacy.engine.begin() as connection:
        for table in recording_tables: table.drop(connection)
        connection.execute(SchemaVersionRow.__table__.update().values(version=1))
    legacy.engine.dispose()
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        with client.app.state.db.session() as session:
            assert list(session.scalars(select(SchemaVersionRow.version))) == [SCHEMA_VERSION]
        for run_id, payload in originals.items():
            assert client.get(f"/api/v1/runs/{run_id}").json() == payload
        assert client.get("/api/v1/recordings").json() == []
        assert upload(client, make_mcap([])[0]).status_code == 201


def test_unknown_database_version_is_rejected_before_schema_writes(tmp_path):
    db = Database(Settings(data_dir=tmp_path, seed_examples=False))
    with db.engine.begin() as connection:
        SchemaVersionRow.__table__.create(connection)
        connection.execute(SchemaVersionRow.__table__.insert().values(version=99))
    before = set(inspect(db.engine).get_table_names())
    with pytest.raises(RuntimeError, match="Unsupported database schema"):
        db.initialize()
    assert set(inspect(db.engine).get_table_names()) == before == {"schema_versions"}
    with db.engine.connect() as connection:
        assert connection.scalar(select(SchemaVersionRow.version)) == 99
    db.engine.dispose()
