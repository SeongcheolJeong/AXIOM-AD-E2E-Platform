"""Real sensor payloads, immutable declarations and honest cursor selection."""

import base64
import hashlib
import io
import json
import math
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from mcap.writer import CompressionType, Writer
from PIL import Image
from sqlalchemy import event, inspect, select

from ade2e import sensors
from ade2e.adapters import Registry
from ade2e.database import Base, Database, SchemaVersionRow, Settings, SCHEMA_VERSION
from ade2e.main import create_app
from ade2e.recording_models import RecordingRow
from ade2e.sensor_decoders import StandardSensorDecoder
from ade2e.sensor_models import (RecordingSensorBindingChannelRow, RecordingSensorBindingRevisionRow,
                                RecordingSensorBindingRow, SensorRigRevisionRow, SensorRigRow)

BASE = 2**63 + 123
MAX = 2**64 - 1


def sensor_schema(name):
    kinds = {"timestamp": "object", "frame_id": "string", "data": "string"}
    kinds.update({"pose": "object", "point_stride": "integer", "fields": "array"} if name.endswith("PointCloud") else
                 {"width": "integer", "height": "integer", "encoding": "string", "step": "integer"})
    properties = {key: {"type": value} for key, value in kinds.items()}
    properties["data"]["contentEncoding"] = "base64"
    return json.dumps({"title": name, "type": "object", "properties": properties}).encode()


def camera(color=(255, 0, 0), frame="camera_front_optical"):
    return {"timestamp": {"sec": 10, "nsec": 7}, "frame_id": frame, "width": 2, "height": 1,
            "encoding": "rgb8", "step": 6, "data": base64.b64encode(bytes(color) * 2).decode()}


def lidar(frame="lidar_roof", points=None):
    return {"timestamp": {"sec": 11, "nsec": 8}, "frame_id": frame, "point_stride": 12,
            "pose": {"position": {"x": 0, "y": 0, "z": 0}, "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
            "fields": [{"name": axis, "offset": index * 4, "type": 7} for index, axis in enumerate("xyz")],
            "data": base64.b64encode(b"".join(struct.pack("<fff", *point) for point in (points if points is not None else [(1, 2, 3), (4, 5, 6)]))).decode()}


def make_recording(client, *, camera_frame="camera_front_optical", points=None):
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.NONE, use_chunking=False, enable_crcs=True, enable_data_crcs=True)
    writer.start()
    cs = writer.register_schema("foxglove.RawImage", "jsonschema", sensor_schema("foxglove.RawImage"))
    ls = writer.register_schema("foxglove.PointCloud", "jsonschema", sensor_schema("foxglove.PointCloud"))
    channels = [writer.register_channel("/camera", "json", cs), writer.register_channel("/lidar", "json", ls),
                writer.register_channel("/opaque", "opaque-test", 0), writer.register_channel("/empty", "json", cs)]
    records = [(channels[0], BASE + 20, camera((0, 0, 255), camera_frame)),
               (channels[0], BASE, camera((255, 0, 0), camera_frame)),
               (channels[0], BASE, camera((0, 255, 0), camera_frame)),
               (channels[1], BASE + 5, lidar(points=points)), (channels[2], BASE, {"unknown": True})]
    payloads = []
    for ordinal, (channel, timestamp, message) in enumerate(records):
        payload = json.dumps(message).encode()
        payloads.append(payload)
        writer.add_message(channel, timestamp, payload, timestamp - 2, ordinal)
    writer.finish()
    response = client.post("/api/v1/recordings?name=Sensor%20fixture&source_kind=synthetic", content=output.getvalue(), headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 201, response.text
    return response.json(), channels, payloads


def rig_body():
    return {"name": "Declared test rig", "reference_frame": "base_link", "coordinate_convention": "right_handed_z_up",
            "source_kind": "synthetic", "source": "Authored test declaration", "note": "Math-valid fixture, not calibration evidence",
            "sensors": [{"key": key, "name": key, "kind": kind, "frame_id": frame,
                         "transform": {"translation_m": [1, 0, 2], "rotation_xyzw": [0, 0, 0, 1]}}
                        for key, kind, frame in (("front", "camera", "camera_front_optical"), ("roof", "lidar", "lidar_roof"))]}


def make_rig(client, body=None):
    response = client.post("/api/v1/sensor-rigs", json=body or rig_body())
    assert response.status_code == 201, response.text
    return response.json()


def binding_body(rig, channels):
    return {"expected_revision": 0, "rig_id": rig["id"], "rig_revision": rig["revision"], "note": "Declared alignment only",
            "channels": [{"channel_id": channel, "sensor_key": key, "decoder_id": "foxglove-json", "decoder_version": "1",
                          "max_age_ns": "10", "log_time_offset_ns": "0"} for channel, key in zip(channels[:2], ["front", "roof"])]}


def bind(client, recording, body):
    response = client.post(f'/api/v1/recordings/{recording["id"]}/sensor-bindings', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def bundle(client, recording, time=BASE, **params):
    response = client.get(f'/api/v1/recordings/{recording["id"]}/sensor-bundle', params={"at_ns": str(time), **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_exact_sensor_messages_preserve_pixels_points_hashes_and_clocks(client):
    recording, channels, payloads = make_recording(client)
    support = client.get(f'/api/v1/recordings/{recording["id"]}/sensor-channels').json()
    assert [item["status"] for item in support["channels"]] == ["supported", "supported", "unsupported", "supported"]
    assert [item["kind"] for item in support["channels"]] == ["camera", "lidar", None, "camera"]
    for ordinal, color in [(1, (255, 0, 0)), (2, (0, 255, 0))]:
        result = client.get(f'/api/v1/recordings/{recording["id"]}/sensor-frame', params={"channel_id": channels[0], "ordinal": ordinal}).json()
        assert result["status"] == "decoded", result
        assert result["message"]["log_time_ns"] == str(BASE)
        assert result["decoded"]["capture_time_ns"] == "10000000007"
        assert result["message"]["sha256"] == result["decoded"]["source_sha256"] == hashlib.sha256(payloads[ordinal]).hexdigest()
        preview = result["decoded"]["camera"]
        png = base64.b64decode(preview["data_base64"])
        assert hashlib.sha256(png).hexdigest() == preview["sha256"] and len(png) == preview["size_bytes"]
        assert Image.open(io.BytesIO(png)).getpixel((0, 0)) == color
        assert client.get(f'/api/v1/recordings/{recording["id"]}/messages/{ordinal}/payload').content == payloads[ordinal]
    point = client.get(f'/api/v1/recordings/{recording["id"]}/sensor-frame', params={"channel_id": channels[1], "ordinal": 3, "max_points": 1}).json()
    assert point["status"] == "decoded", point
    preview = point["decoded"]["point_cloud"]
    assert preview["point_count"] == 1 and preview["original_point_count"] == 2 and preview["skipped_points"] == 1
    assert struct.unpack("<fff", base64.b64decode(preview["data_base64"])) == (1, 2, 3)
    assert client.get(f'/api/v1/recordings/{recording["id"]}/sensor-frame', params={"channel_id": channels[1], "ordinal": 2}).status_code == 404


def test_bundle_never_substitutes_future_and_keeps_duplicate_and_unknown_age(client):
    recording, channels, _ = make_recording(client)
    future = bundle(client, recording, BASE - 1)
    assert all(item["selection_status"] == "missing" and item["frame"] is None for item in future["entries"])
    assert future["entries"][0]["next_log_time_ns"] == str(BASE)
    current = bundle(client, recording)
    assert current["binding_revision"] is None
    camera_entry, lidar_entry, empty_entry = current["entries"]
    assert camera_entry["message"]["ordinal"] == 2 and camera_entry["time_relation"] == "at_cursor"
    assert camera_entry["freshness"] == "unknown" and camera_entry["frame"]["status"] == "decoded"
    assert camera_entry["transform_status"] == "unbound"
    assert lidar_entry["selection_status"] == empty_entry["selection_status"] == "missing"
    assert lidar_entry["next_log_time_ns"] == str(BASE + 5) and empty_entry["next_log_time_ns"] is None
    stale = bundle(client, recording, BASE + 15, channel_id=channels[0], max_age_ns="10")["entries"][0]
    assert stale["freshness"] == "stale" and stale["age_ns"] == "15" and stale["frame"] is None
    assert stale["message"]["ordinal"] == 2 and stale["message"]["preview_hex"] is None
    boundary = bundle(client, recording, BASE + 10, channel_id=channels[0], max_age_ns="10")["entries"][0]
    assert boundary["freshness"] == "fresh" and boundary["frame"]["status"] == "decoded"


def test_rig_and_binding_history_are_pinned_atomic_and_export_exact(client):
    recording, channels, _ = make_recording(client)
    assert client.get(f'/api/v1/recordings/{recording["id"]}/sensor-bindings/revisions').json() == []
    first = make_rig(client)
    body = binding_body(first, channels)
    original = bind(client, recording, body)
    rig_bytes = client.get(first["download_url"]).content
    binding_bytes = client.get(original["download_url"]).content
    assert hashlib.sha256(rig_bytes).hexdigest() == first["manifest_hash"]
    assert hashlib.sha256(binding_bytes).hexdigest() == original["manifest_hash"]
    changed = rig_body()
    changed["sensors"][0]["transform"]["translation_m"] = [9, 8, 7]
    second = client.post(f'/api/v1/sensor-rigs/{first["id"]}/revisions', json={**changed, "expected_revision": 1})
    assert second.status_code == 201, second.text
    assert client.post(f'/api/v1/sensor-rigs/{first["id"]}/revisions', json={**changed, "expected_revision": 1}).status_code == 409
    assert client.get(f'/api/v1/sensor-rigs/{first["id"]}/revisions').json() == [second.json(), first]
    assert bundle(client, recording)["binding_revision"] is None  # no silent latest binding
    pinned = bundle(client, recording, binding_revision=1)["entries"][0]
    assert pinned["transform_status"] == "available" and pinned["transform"]["translation_m"] == [1, 0, 2]
    updated_body = {**body, "expected_revision": 1, "rig_revision": 2}
    updated = bind(client, recording, updated_body)
    assert updated["rig_snapshot"]["sensors"][0]["transform"]["translation_m"] == [9, 8, 7]
    assert client.get(first["download_url"]).content == rig_bytes
    assert client.get(original["download_url"]).content == binding_bytes
    assert client.post(f'/api/v1/recordings/{recording["id"]}/sensor-bindings', json=updated_body).status_code == 409
    assert [item["revision"] for item in client.get(f'/api/v1/recordings/{recording["id"]}/sensor-bindings/revisions').json()] == [2, 1]


@pytest.mark.parametrize("offset,cursor,expected_ordinal,age", [("5", BASE + 4, None, None), ("5", BASE + 5, 2, "0"),
    (str(-MAX), MAX, 0, str(MAX + MAX - BASE - 20)), (str(MAX), 0, None, None)])
def test_offset_arithmetic_preserves_exact_large_signed_alignment(client, offset, cursor, expected_ordinal, age):
    recording, channels, _ = make_recording(client)
    body = binding_body(make_rig(client), channels)
    body["channels"] = body["channels"][:1]
    body["channels"][0].update(log_time_offset_ns=offset, max_age_ns=None)
    bind(client, recording, body)
    entry = bundle(client, recording, cursor, binding_revision=1)["entries"][0]
    assert (entry["message"]["ordinal"] if entry["message"] else None) == expected_ordinal
    assert entry["age_ns"] == age
    if expected_ordinal is None:
        assert entry["frame"] is None and entry["selection_status"] == "missing"


@pytest.mark.parametrize("fault,expected", [("frame", "frame_mismatch"), ("unknown_convention", "unknown_convention"), ("no_transform", "unknown_transform")])
def test_unknown_or_mismatching_declarations_disable_rig_transform(client, fault, expected):
    recording, channels, _ = make_recording(client, camera_frame="different_frame" if fault == "frame" else "camera_front_optical")
    rig = rig_body()
    if fault == "unknown_convention": rig["coordinate_convention"] = "unknown"
    if fault == "no_transform": rig["sensors"][0]["transform"] = None
    bind(client, recording, binding_body(make_rig(client, rig), channels))
    result = bundle(client, recording, binding_revision=1)["entries"][0]
    assert result["frame"]["status"] == "decoded"
    assert result["transform_status"] == expected and result["transform"] is None


@pytest.mark.parametrize("fault", ["duplicate", "quaternion", "nonfinite", "translation_too_large", "intrinsics", "lidar_intrinsics", "empty_note", "unknown_field"])
def test_invalid_rig_never_publishes(client, fault):
    body = rig_body()
    if fault == "duplicate": body["sensors"][1]["key"] = body["sensors"][0]["key"]
    elif fault == "quaternion": body["sensors"][0]["transform"]["rotation_xyzw"] = [0, 0, 0, 0]
    elif fault == "nonfinite": body["sensors"][0]["transform"]["translation_m"][0] = "NaN"
    elif fault == "translation_too_large": body["sensors"][0]["transform"]["translation_m"][0] = 1e40
    elif fault in {"intrinsics", "lidar_intrinsics"}:
        body["sensors"][0 if fault == "intrinsics" else 1]["intrinsics"] = {"width": 2, "height": 1, "fx": 1, "fy": 1, "cx": 1, "cy": 1,
                            "distortion_model": "plumb_bob" if fault == "intrinsics" else "none", "distortion_coefficients": []}
    elif fault == "empty_note": body["note"] = " "
    else: body["validated"] = True
    response = client.post("/api/v1/sensor-rigs", json=body)
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/sensor-rigs").json() == []


@pytest.mark.parametrize("fault", ["missing_channel", "duplicate_channel", "wrong_sensor", "wrong_kind", "unsupported_channel", "version", "offset", "age"])
def test_invalid_binding_does_not_create_head_or_partial_mappings(client, fault):
    recording, channels, _ = make_recording(client)
    body = binding_body(make_rig(client), channels)
    if fault == "missing_channel": body["channels"][1]["channel_id"] = 999
    elif fault == "duplicate_channel": body["channels"][1]["channel_id"] = channels[0]
    elif fault == "wrong_sensor": body["channels"][1]["sensor_key"] = "absent"
    elif fault == "wrong_kind": body["channels"][0]["sensor_key"], body["channels"][1]["sensor_key"] = "roof", "front"
    elif fault == "unsupported_channel": body["channels"][1]["channel_id"] = channels[2]
    elif fault == "version": body["channels"][1]["decoder_version"] = "absent"
    elif fault == "offset": body["channels"][1]["log_time_offset_ns"] = str(MAX + 1)
    else: body["channels"][1]["max_age_ns"] = "-1"
    response = client.post(f'/api/v1/recordings/{recording["id"]}/sensor-bindings', json=body)
    assert response.status_code in {404, 422}, response.text
    assert client.get(f'/api/v1/recordings/{recording["id"]}/sensor-bindings/revisions').json() == []
    with client.app.state.db.session() as session:
        for model in (RecordingSensorBindingRow, RecordingSensorBindingRevisionRow, RecordingSensorBindingChannelRow):
            assert list(session.scalars(select(model))) == []


def test_concurrent_revisions_serialize_one_winner_and_commit_failure_rolls_back(client, monkeypatch):
    recording, channels, _ = make_recording(client)
    rig = make_rig(client)
    body = binding_body(rig, channels)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: client.post(f'/api/v1/recordings/{recording["id"]}/sensor-bindings', json=body), range(3)))
    assert sorted(response.status_code for response in responses) == [201, 409, 409]
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: client.post(f'/api/v1/sensor-rigs/{rig["id"]}/revisions', json={**rig_body(), "expected_revision": 1}), range(3)))
    assert sorted(response.status_code for response in responses) == [201, 409, 409]
    def fail(*_): raise OSError("Injected sensor revision commit failure")
    event.listen(client.app.state.db.session.class_, "before_commit", fail)
    try:
        with pytest.raises(OSError):
            client.post(f'/api/v1/recordings/{recording["id"]}/sensor-bindings', json={**body, "expected_revision": 1})
    finally:
        event.remove(client.app.state.db.session.class_, "before_commit", fail)
    assert len(client.get(f'/api/v1/recordings/{recording["id"]}/sensor-bindings/revisions').json()) == 1


def test_decoder_replacement_unavailable_version_and_invalid_output_fail_closed(client):
    recording, channels, _ = make_recording(client)
    bind(client, recording, binding_body(make_rig(client), channels))
    class Changed(StandardSensorDecoder):
        def metadata(self):
            return super().metadata().model_copy(update={"version": "2"})
    client.app.state.sensor_registry.register(Changed("foxglove-json"), replace=True)
    entry = bundle(client, recording, binding_revision=1)["entries"][0]
    assert entry["frame"]["status"] == "decoder_unavailable" and entry["transform"] is None
    class BadOutput(StandardSensorDecoder):
        def decode(self, *args, **kwargs):
            result = super().decode(*args, **kwargs)
            result.camera.sha256 = "0" * 64
            return result
    client.app.state.sensor_registry.register(BadOutput("foxglove-json"), replace=True)
    entry = bundle(client, recording, binding_revision=1)["entries"][0]
    assert entry["frame"]["status"] == "decoder_error" and entry["frame"]["decoded"] is None


@pytest.mark.parametrize("fault", ["png_header_only", "png_truncated", "png_truncated_idat", "point_accounting", "point_bounds", "intensity_omitted", "source_hash", "version_during_decode"])
def test_core_rejects_rendering_and_evidence_contract_violations_from_plugins(client, fault):
    recording, channels, _ = make_recording(client)
    bind(client, recording, binding_body(make_rig(client), channels))
    class Invalid(StandardSensorDecoder):
        changed = False
        def metadata(self):
            metadata = super().metadata()
            return metadata.model_copy(update={"version": "2"}) if self.changed else metadata
        def decode(self, *args, **kwargs):
            result = super().decode(*args, **kwargs)
            if fault.startswith("png"):
                real = base64.b64decode(result.camera.data_base64)
                broken = real[:24] if fault == "png_header_only" else real[:-8]
                if fault == "png_truncated_idat":
                    # Keep complete framing and correct CRCs; only actual pixel
                    # decompression can detect this truncated IDAT stream.
                    parts, offset = [real[:8]], 8
                    while offset < len(real):
                        size = struct.unpack_from(">I", real, offset)[0]
                        kind, data = real[offset + 4:offset + 8], real[offset + 8:offset + 8 + size]
                        if kind == b"IDAT": data = data[:len(data) // 2]
                        parts.append(struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data)))
                        offset += size + 12
                    broken = b"".join(parts)
                result.camera.data_base64 = base64.b64encode(broken).decode()
                result.camera.size_bytes = len(broken)
                result.camera.sha256 = hashlib.sha256(broken).hexdigest()
            elif fault == "point_accounting": result.point_cloud.invalid_points = 999
            elif fault == "point_bounds": result.point_cloud.bounds_min = (100, 100, 100)
            elif fault == "intensity_omitted": result.point_cloud.intensity_encoding = "float32_le"
            elif fault == "source_hash": result.source_sha256 = "0" * 64
            else: self.changed = True
            return result
    client.app.state.sensor_registry.register(Invalid("foxglove-json"), replace=True)
    channel = channels[0] if fault.startswith("png") or fault in {"source_hash", "version_during_decode"} else channels[1]
    result = bundle(client, recording, BASE + 5, binding_revision=1, channel_id=channel)["entries"][0]
    assert result["frame"]["status"] == "decoder_error", result
    assert result["frame"]["decoded"] is None and result["transform"] is None


def test_rig_transform_overflow_is_withheld_while_native_finite_points_remain_inspectable(client):
    recording, channels, _ = make_recording(client, points=[(3e38, 3e38, 0)])
    body = rig_body()
    body["sensors"][1]["transform"]["rotation_xyzw"] = [0, 0, math.sin(math.pi / 8), math.cos(math.pi / 8)]
    definition = binding_body(make_rig(client, body), channels)
    definition["channels"] = definition["channels"][1:]
    bind(client, recording, definition)
    entry = bundle(client, recording, BASE + 5, binding_revision=1)["entries"][0]
    assert entry["frame"]["status"] == "decoded", entry
    assert entry["transform_status"] == "out_of_range" and entry["transform"] is None


def test_preview_bundle_limit_reports_omission_without_affecting_exact_inspection(client, monkeypatch):
    recording, channels, _ = make_recording(client)
    before = bundle(client, recording, BASE + 5, channel_id=channels[:2])
    amounts = [len(entry["frame"]["decoded"].get("camera", {}).get("data_base64", "")) if entry["frame"]["decoded"]["camera"] else
               len(entry["frame"]["decoded"]["point_cloud"]["data_base64"]) for entry in before["entries"]]
    monkeypatch.setattr(sensors, "MAX_BUNDLE_DATA_BYTES", max(amounts) + 1)
    result = bundle(client, recording, BASE + 5, channel_id=channels[:2])
    assert result["entries"][0]["frame"]["status"] == "decoded"
    assert result["entries"][1]["frame"]["status"] == "limit_exceeded" and result["entries"][1]["frame"]["decoded"] is None
    assert result["encoded_data_size_bytes"] == amounts[0]
    assert client.get(f'/api/v1/recordings/{recording["id"]}/sensor-frame', params={"channel_id": channels[1], "ordinal": 3}).json()["status"] == "decoded"


def test_corrupted_message_isolated_in_bundle_and_rejected_by_exact_raw_read(client):
    recording, channels, _ = make_recording(client)
    with client.app.state.db.session() as session:
        row = session.get(RecordingRow, recording["id"])
        path = client.app.state.db.recording_dir / row.index_storage_key
    data = path.read_bytes()
    path.write_bytes(data[:10] + bytes([data[10] ^ 1]) + data[11:])
    assert client.get(f'/api/v1/recordings/{recording["id"]}/sensor-frame', params={"channel_id": channels[0], "ordinal": 0}).status_code == 409
    assert client.get(f'/api/v1/recordings/{recording["id"]}/messages/0/payload').status_code == 409
    entries = bundle(client, recording, BASE + 20)["entries"]
    assert entries[0]["frame"]["status"] == "integrity_error" and entries[0]["frame"]["decoded"] is None
    assert entries[1]["frame"]["status"] == "decoded"


@pytest.mark.parametrize("target", ["rig_bytes", "rig_identity", "binding_bytes", "binding_mapping"])
def test_manifest_and_relational_metadata_tamper_fails_closed(client, target):
    recording, channels, _ = make_recording(client)
    rig = make_rig(client)
    binding = bind(client, recording, binding_body(rig, channels))
    with client.app.state.db.session.begin() as session:
        if target.startswith("rig"):
            row = session.get(SensorRigRevisionRow, (rig["id"], 1))
            if target == "rig_bytes": row.manifest = b"{}"
            else: row.created_at = "different"
        elif target == "binding_bytes": session.get(RecordingSensorBindingRevisionRow, (recording["id"], 1)).manifest = b"{}"
        else: session.get(RecordingSensorBindingChannelRow, (recording["id"], 1, channels[0])).sensor_key = "different"
    if target.startswith("rig"):
        assert client.get(f'/api/v1/sensor-rigs/{rig["id"]}').status_code == 409
        assert client.get(rig["download_url"]).status_code == 409
        # The already copied binding snapshot retains its own immutable bytes.
        assert client.get(binding["download_url"]).status_code == 200
    else:
        assert client.get(f'/api/v1/recordings/{recording["id"]}/sensor-bindings').status_code == 409
        assert client.get(binding["download_url"]).status_code == 409


def test_auth_and_invalid_selector_boundaries(tmp_path):
    settings = Settings(data_dir=tmp_path, seed_examples=False, api_token="sensor-secret")
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        for path in ("/sensor-rigs", "/sensor-decoders", "/recordings/x/sensor-channels", "/recordings/x/sensor-bindings/revisions",
                     "/recordings/x/sensor-frame?channel_id=1&ordinal=1", "/recordings/x/sensor-bundle?at_ns=0", "/recordings/x/messages/1/payload"):
            assert client.get("/api/v1" + path).status_code == 401
        assert client.post("/api/v1/sensor-rigs", json=rig_body()).status_code == 401
        client.headers["Authorization"] = "Bearer sensor-secret"
        recording, channels, _ = make_recording(client)
        binding = bind(client, recording, binding_body(make_rig(client), channels))
        for params in ({"at_ns": str(MAX + 1)}, {"at_ns": "-1"}, {"at_ns": "0", "channel_id": [1, 1]},
                       {"at_ns": "0", "channel_id": [1, 2, 3, 4, 5]}, {"at_ns": "0", "binding_revision": 1, "max_age_ns": "1"},
                       {"at_ns": "0", "binding_revision": 1, "channel_id": channels[2]}):
            assert client.get(f'/api/v1/recordings/{recording["id"]}/sensor-bundle', params=params).status_code == 422


def test_schema4_to5_preserves_all_existing_tables_and_payload_files(client):
    from test_suites import baseline, campaign, finish, make_suite
    from ade2e import sensor_models, run_recording_models

    recording, channels, _ = make_recording(client)
    suite, *_ = make_suite(client, count=1)
    baseline(client, finish(client, campaign(client, suite)))
    captured = client.post("/api/v1/comparisons", json={"campaign_id": client.get("/api/v1/campaigns").json()["items"][0]["id"],
                  "baseline_id": client.get("/api/v1/baselines").json()[0]["id"], "name": "pre-migration", "note": "real evidence"},
                  headers={"Idempotency-Key": "pre-migration"})
    assert captured.status_code == 201, captured.text
    db = client.app.state.db
    new_tables = {"sensor_rigs", "sensor_rig_revisions", "recording_sensor_bindings", "recording_sensor_binding_revisions", "recording_sensor_binding_channels", "recording_origins"}
    old_tables = [table for table in Base.metadata.sorted_tables if table.name not in new_tables | {"schema_versions"}]
    with db.engine.begin() as connection:
        before = {table.name: list(connection.execute(select(table)).mappings()) for table in old_tables}
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in new_tables: table.drop(connection)
        connection.execute(SchemaVersionRow.__table__.update().values(version=4))
    files = {str(path): path.read_bytes() for directory in (db.recording_dir, db.artifact_dir) for path in directory.rglob("*") if path.is_file()}
    db.initialize()
    with db.engine.connect() as connection:
        assert connection.scalar(select(SchemaVersionRow.version)) == SCHEMA_VERSION
        assert new_tables <= set(inspect(connection).get_table_names())
        assert before == {table.name: list(connection.execute(select(table)).mappings()) for table in old_tables}
    assert all(__import__("pathlib").Path(path).read_bytes() == content for path, content in files.items())
    assert bundle(client, recording)["entries"][0]["frame"]["status"] == "decoded"
