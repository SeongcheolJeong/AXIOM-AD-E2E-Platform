"""Versioned declarations and bounded replay of actual recorded sensor payloads."""

import base64
import hashlib
import math
import struct

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from . import jobs
from .database import Database
from .recording_models import RecordingChannelRow, RecordingMessageRow, RecordingSchemaRow
from .recording_schemas import RecordingChannel, RecordingSchema
from .recordings import _get_recording, read_message_bytes, select_message_at
from .sensor_models import (RecordingSensorBindingChannelRow, RecordingSensorBindingRevisionRow,
                            RecordingSensorBindingRow, SensorRigRevisionRow, SensorRigRow)
from .sensor_schemas import (BindingCreate, BindingDetail, BindingSnapshot, RigCreate, RigDetail,
                             RigRevisionCreate, RigSnapshot, SensorBundle, SensorBundleEntry, SensorChannels,
                             SensorChannelSupport, SensorFrame, SensorMessage, SignedNs, UnsignedNs)
from .sensor_decoder_schemas import SensorDecodeResult
from .service import canonical_bytes, digest, uid, utcnow

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_BUNDLE_DATA_BYTES = 24 * 1024 * 1024
MAX_PREVIEW_DATA_BYTES = 24 * 1024 * 1024


def _manifest(row, model):
    try:
        if (len(row.manifest) != row.manifest_size_bytes or len(row.manifest) > MAX_MANIFEST_BYTES
                or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash):
            raise ValueError("Manifest bytes differ from their digest")
        value = model.model_validate_json(row.manifest)
        if canonical_bytes(value.model_dump(mode="json")) != row.manifest:
            raise ValueError("Manifest is not canonical")
        return value
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Sensor declaration manifest failed its integrity check") from exc


def _manifest_values(snapshot) -> dict:
    content = canonical_bytes(snapshot.model_dump(mode="json"))
    if len(content) > MAX_MANIFEST_BYTES:
        raise HTTPException(413, "Sensor declaration manifest exceeds 1 MiB")
    return {"manifest": content, "manifest_hash": hashlib.sha256(content).hexdigest(), "manifest_size_bytes": len(content)}


def _rig_row(session, rig_id: str, revision: int | None):
    head = session.get(SensorRigRow, rig_id)
    if head is None:
        raise HTTPException(404, "Sensor rig not found")
    row = session.get(SensorRigRevisionRow, (rig_id, head.revision if revision is None else revision))
    if row is None:
        raise HTTPException(404, "Sensor rig revision not found")
    return row


def rig_get(session, rig_id: str, revision: int | None = None) -> RigDetail:
    row = _rig_row(session, rig_id, revision)
    snapshot = _manifest(row, RigSnapshot)
    definition = {name: getattr(snapshot, name) for name in RigCreate.model_fields}
    expected_hash = digest(RigCreate(**definition).model_dump(mode="json"))
    if (snapshot.id != row.rig_id or snapshot.revision != row.revision or snapshot.created_at != row.created_at
            or snapshot.definition_hash != expected_hash):
        raise HTTPException(409, "Sensor rig metadata differs from its sealed definition")
    return RigDetail(**snapshot.model_dump(), manifest_hash=row.manifest_hash, manifest_size_bytes=row.manifest_size_bytes,
                     download_url=f"/api/v1/sensor-rigs/{rig_id}/export?revision={row.revision}")


def rig_create(db: Database, body: RigCreate) -> RigDetail:
    with jobs._write(db) as session:
        rig_id, now = uid("rig"), utcnow()
        snapshot = RigSnapshot(**body.model_dump(), id=rig_id, revision=1, created_at=now,
                               definition_hash=digest(body.model_dump(mode="json")))
        session.add(SensorRigRow(id=rig_id, revision=1, created_at=now))
        session.flush()
        session.add(SensorRigRevisionRow(rig_id=rig_id, revision=1, created_at=now, **_manifest_values(snapshot)))
        session.flush()
        return rig_get(session, rig_id)


def rig_revise(db: Database, rig_id: str, body: RigRevisionCreate) -> RigDetail:
    with jobs._write(db) as session:
        current = rig_get(session, rig_id)
        if body.expected_revision != current.revision:
            raise HTTPException(409, "Sensor rig revision changed; reload before revising")
        definition = RigCreate.model_validate(body.model_dump(mode="json", exclude={"expected_revision"}))
        snapshot = RigSnapshot(**definition.model_dump(), id=rig_id, revision=current.revision + 1, created_at=utcnow(),
                               definition_hash=digest(definition.model_dump(mode="json")))
        session.get(SensorRigRow, rig_id).revision = snapshot.revision
        session.add(SensorRigRevisionRow(rig_id=rig_id, revision=snapshot.revision, created_at=snapshot.created_at, **_manifest_values(snapshot)))
        session.flush()
        return rig_get(session, rig_id)


def _channel(session, recording_id: str, channel_id: int) -> tuple[RecordingChannel, RecordingSchema | None]:
    row = session.get(RecordingChannelRow, (recording_id, channel_id))
    if row is None:
        raise HTTPException(404, "Channel not found in this recording")
    try:
        channel = RecordingChannel.model_validate(row.payload)
        schema_row = session.get(RecordingSchemaRow, (recording_id, row.schema_ref)) if row.schema_ref is not None else None
        schema = RecordingSchema.model_validate(schema_row.payload) if schema_row else None
        if channel.id != channel_id or (channel.schema_id or None) != row.schema_ref or (schema is not None and schema.id != row.schema_ref):
            raise ValueError("Channel/schema identity mismatch")
        if row.schema_ref is not None and schema is None:
            raise ValueError("Referenced schema is unavailable")
        return channel, schema
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Recording channel metadata failed its integrity check") from exc


def _binding_definition(snapshot: BindingSnapshot) -> dict:
    return snapshot.model_dump(mode="json", exclude={"schema_version", "revision", "created_at", "definition_hash"})


def _binding_row(session, recording_id: str, revision: int | None):
    _get_recording(session, recording_id)
    head = session.get(RecordingSensorBindingRow, recording_id)
    if head is None:
        raise HTTPException(404, "Recording has no sensor binding")
    row = session.get(RecordingSensorBindingRevisionRow, (recording_id, head.revision if revision is None else revision))
    if row is None:
        raise HTTPException(404, "Recording sensor binding revision not found")
    return row


def binding_get(session, recording_id: str, revision: int | None = None) -> BindingDetail:
    row = _binding_row(session, recording_id, revision)
    snapshot = _manifest(row, BindingSnapshot)
    recording = _get_recording(session, recording_id)
    channels = list(session.scalars(select(RecordingSensorBindingChannelRow).where(
        RecordingSensorBindingChannelRow.recording_id == recording_id, RecordingSensorBindingChannelRow.revision == row.revision)))
    expected_channels = {(item.channel_id, item.sensor_key) for item in snapshot.channels}
    actual_channels = {(item.channel_id, item.sensor_key) for item in channels}
    rig = snapshot.rig_snapshot
    if (snapshot.recording_id != row.recording_id or snapshot.revision != row.revision or snapshot.created_at != row.created_at
            or snapshot.rig_id != row.rig_id or snapshot.rig_revision != row.rig_revision
            or rig.id != snapshot.rig_id or rig.revision != snapshot.rig_revision
            or hashlib.sha256(canonical_bytes(rig.model_dump(mode="json"))).hexdigest() != snapshot.rig_manifest_hash
            or snapshot.recording_sha256 != recording.sha256 or expected_channels != actual_channels
            or len(channels) != len(snapshot.channels) or snapshot.definition_hash != digest(_binding_definition(snapshot))):
        raise HTTPException(409, "Recording binding metadata differs from its sealed definition")
    return BindingDetail(**snapshot.model_dump(), manifest_hash=row.manifest_hash, manifest_size_bytes=row.manifest_size_bytes,
                         download_url=f"/api/v1/recordings/{recording_id}/sensor-bindings/export?revision={row.revision}")


def binding_create(db: Database, registry, recording_id: str, body: BindingCreate) -> BindingDetail:
    with jobs._write(db) as session:
        recording = _get_recording(session, recording_id)
        head = session.get(RecordingSensorBindingRow, recording_id)
        current_revision = head.revision if head else 0
        if body.expected_revision != current_revision:
            raise HTTPException(409, "Recording binding revision changed; reload before revising")
        if head is not None:
            binding_get(session, recording_id)
        rig = rig_get(session, body.rig_id, body.rig_revision)
        sensors = {sensor.key: sensor for sensor in rig.sensors}
        for item in body.channels:
            if item.sensor_key not in sensors:
                raise HTTPException(422, f"Sensor key {item.sensor_key} is absent from the selected rig revision")
            channel, schema = _channel(session, recording_id, item.channel_id)
            decoder = _resolve_decoder(registry, channel, schema, item.decoder_id, item.decoder_version)
            if decoder.kind_for(channel, schema) != sensors[item.sensor_key].kind:
                raise HTTPException(422, "Bound decoder kind differs from the selected rig sensor kind")
        now = utcnow()
        values = {"recording_id": recording_id, "recording_sha256": recording.sha256, "revision": current_revision + 1,
                  "created_at": now, "rig_id": rig.id, "rig_revision": rig.revision, "rig_manifest_hash": rig.manifest_hash,
                  "rig_snapshot": RigSnapshot.model_validate(rig.model_dump(exclude={"manifest_hash", "manifest_size_bytes", "download_url"})),
                  "note": body.note, "channels": body.channels}
        snapshot = BindingSnapshot(**values, definition_hash="")
        snapshot.definition_hash = digest(_binding_definition(snapshot))
        if head is None:
            head = RecordingSensorBindingRow(recording_id=recording_id, revision=snapshot.revision, created_at=now)
            session.add(head)
            session.flush()
        else:
            head.revision = snapshot.revision
        session.add(RecordingSensorBindingRevisionRow(recording_id=recording_id, revision=snapshot.revision, rig_id=rig.id,
                                                       rig_revision=rig.revision, created_at=now, **_manifest_values(snapshot)))
        session.flush()
        for item in body.channels:
            session.add(RecordingSensorBindingChannelRow(recording_id=recording_id, revision=snapshot.revision,
                                                         channel_id=item.channel_id, sensor_key=item.sensor_key))
        session.flush()
        return binding_get(session, recording_id, snapshot.revision)


def _resolve_decoder(registry, channel, schema, decoder_id: str | None = None, version: str | None = None):
    if decoder_id is not None:
        try:
            adapter = registry.get(decoder_id, version)
        except KeyError as exc:
            raise HTTPException(422, "Pinned sensor decoder/version is not installed") from exc
        metadata = adapter.metadata()
        if metadata.id != decoder_id or metadata.version != version:
            raise HTTPException(422, "Pinned sensor decoder version changed")
        if not adapter.accepts(channel, schema):
            raise HTTPException(422, "Pinned decoder does not support this exact channel/schema")
        return adapter
    matches = []
    for metadata in registry.list():
        adapter = registry.get(metadata.id, metadata.version)
        if adapter.accepts(channel, schema):
            matches.append(adapter)
    if len(matches) != 1:
        raise HTTPException(422, "No unique installed decoder supports this exact channel/schema; use a pinned binding if ambiguous")
    return matches[0]


def sensor_channels(session, registry, recording_id: str) -> SensorChannels:
    _get_recording(session, recording_id)
    entries = []
    for row in session.scalars(select(RecordingChannelRow).where(RecordingChannelRow.recording_id == recording_id).order_by(RecordingChannelRow.channel_id)):
        channel, schema = _channel(session, recording_id, row.channel_id)
        matches = [metadata for metadata in registry.list() if registry.get(metadata.id, metadata.version).accepts(channel, schema)]
        kinds = {registry.get(metadata.id, metadata.version).kind_for(channel, schema) for metadata in matches}
        entries.append(SensorChannelSupport(channel_id=channel.id, topic=channel.topic, message_encoding=channel.message_encoding,
                                             schema_id=channel.schema_id, schema_name=schema.name if schema else None,
                                             schema_sha256=schema.sha256 if schema else None, status="supported" if matches else "unsupported",
                                             kind=next(iter(kinds)) if len(kinds) == 1 else None, decoders=matches,
                                             reason=None if matches else "No installed decoder accepts the exact schema and encoding"))
    return SensorChannels(recording_id=recording_id, channels=entries,
                          warnings=["Decoder support describes payload syntax, not sensor accuracy, calibration or clock synchronization."])


def _message(entry, content: bytes | None = None) -> SensorMessage:
    return SensorMessage(ordinal=entry.ordinal, log_time_ns=str(int(entry.log_time_key)), publish_time_ns=str(int(entry.publish_time_key)),
                         sequence=entry.sequence, size_bytes=entry.payload_length, sha256=entry.sha256,
                         preview_hex=content[:64].hex() if content is not None else None)


def _preview_bytes(preview) -> bytes:
    if len(preview.data_base64) > MAX_PREVIEW_DATA_BYTES:
        raise ValueError("Decoder preview exceeds the encoded output limit")
    data = base64.b64decode(preview.data_base64, validate=True)
    if len(data) != preview.size_bytes or hashlib.sha256(data).hexdigest() != preview.sha256:
        raise ValueError("Decoder preview size or digest differs from its bytes")
    return data


def _validate_decoded(result, adapter, channel, schema, message: SensorMessage, max_points: int, selected_metadata) -> SensorDecodeResult:
    result = SensorDecodeResult.model_validate(result)
    metadata = adapter.metadata()
    if (metadata != selected_metadata or result.decoder_id != selected_metadata.id or result.decoder_version != selected_metadata.version
            or result.source_sha256 != message.sha256 or result.source_size_bytes != message.size_bytes
            or result.kind != adapter.kind_for(channel, schema)):
        raise ValueError("Decoder output does not identify its actual source, kind or decoder version")
    if result.frame_id is not None and len(result.frame_id) > 180:
        raise ValueError("Decoder frame ID is too long")
    if result.capture_time_ns is not None:
        TypeAdapter(SignedNs).validate_python(result.capture_time_ns)
    if result.kind == "camera":
        if result.camera is None or result.point_cloud is not None or result.units != "pixels":
            raise ValueError("Camera result requires only a PNG preview in pixels")
        preview = result.camera
        if preview.width <= 0 or preview.height <= 0 or preview.width * preview.height > min(metadata.max_pixels, 2_000_000):
            raise ValueError("Decoded image dimensions exceed their declared bound")
        data = _preview_bytes(preview)
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR" or struct.unpack(">II", data[16:24]) != (preview.width, preview.height):
            raise ValueError("Image preview is not the declared PNG/dimensions")
        from .sensor_decoders import validate_png_preview
        try:
            validate_png_preview(data, preview.width, preview.height)
        except ValueError as exc:
            raise ValueError(f"Decoder PNG output is invalid: {exc}") from exc
    else:
        if result.point_cloud is None or result.camera is not None or result.units != "m":
            raise ValueError("LiDAR result requires only an XYZ preview in metres")
        preview = result.point_cloud
        if not 0 <= preview.point_count <= min(max_points, metadata.max_source_points, 1_000_000) or not preview.point_count <= preview.original_point_count <= min(metadata.max_source_points, 1_000_000):
            raise ValueError("Point preview counts exceed their bounds")
        if min(preview.invalid_points, preview.skipped_points) < 0 or preview.sampling_stride < 1:
            raise ValueError("Point preview accounting is invalid")
        if preview.point_count + preview.invalid_points + preview.skipped_points != preview.original_point_count:
            raise ValueError("Point preview accounting does not preserve the original denominator")
        data = _preview_bytes(preview)
        if len(data) != preview.point_count * 12 or any(not math.isfinite(value) for (value,) in struct.iter_unpack("<f", data)):
            raise ValueError("Point preview requires finite XYZ float32 triples")
        points = list(struct.iter_unpack("<fff", data))
        actual_min = tuple(min(point[axis] for point in points) for axis in range(3)) if points else None
        actual_max = tuple(max(point[axis] for point in points) for axis in range(3)) if points else None
        if preview.bounds_min != actual_min or preview.bounds_max != actual_max:
            raise ValueError("Point preview bounds differ from the actual XYZ bytes")
        if not 0 <= preview.intensity_invalid_points <= preview.point_count:
            raise ValueError("Invalid intensity count exceeds selected points")
        if preview.intensity is not None:
            intensity = _preview_bytes(preview.intensity)
            if (preview.intensity_invalid_points != 0 or preview.intensity_encoding != "float32_le" or len(intensity) != preview.point_count * 4
                    or any(not math.isfinite(value) for (value,) in struct.iter_unpack("<f", intensity))):
                raise ValueError("Intensity preview must match the finite selected point count")
        elif preview.intensity_encoding is not None:
            raise ValueError("An omitted intensity buffer cannot declare a decoded encoding")
    return result


def sensor_frame(session, db: Database, registry, recording_id: str, channel_id: int, ordinal: int, max_points: int,
                 *, decoder_id: str | None = None, decoder_version: str | None = None) -> SensorFrame:
    from .sensor_decoders import SensorDecodeError

    recording = _get_recording(session, recording_id)
    channel, schema = _channel(session, recording_id, channel_id)
    entry = session.get(RecordingMessageRow, (recording_id, ordinal))
    if entry is None or entry.channel_id != channel_id:
        raise HTTPException(404, "Message ordinal does not belong to the selected recording/channel")
    content = read_message_bytes(db, recording, entry)
    message = _message(entry, content)
    try:
        adapter = _resolve_decoder(registry, channel, schema, decoder_id, decoder_version)
    except HTTPException as exc:
        return SensorFrame(recording_id=recording_id, channel_id=channel_id, message=message,
                           status="decoder_unavailable" if decoder_id else "unsupported", reason=str(exc.detail))
    selected_metadata = adapter.metadata().model_copy(deep=True)
    if len(content) > selected_metadata.max_input_bytes:
        return SensorFrame(recording_id=recording_id, channel_id=channel_id, message=message, status="limit_exceeded",
                           reason="Message exceeds this decoder's input byte limit")
    try:
        result = adapter.decode(content, channel, schema, max_points=max_points)
        result = _validate_decoded(result, adapter, channel, schema, message, max_points, selected_metadata)
        return SensorFrame(recording_id=recording_id, channel_id=channel_id, message=message, status="decoded", decoded=result)
    except SensorDecodeError as exc:
        status = {"unsupported_schema": "unsupported", "invalid_payload": "invalid_payload", "limit_exceeded": "limit_exceeded"}.get(exc.code, "decoder_error")
        return SensorFrame(recording_id=recording_id, channel_id=channel_id, message=message, status=status, reason=str(exc)[:1000])
    except Exception as exc:
        return SensorFrame(recording_id=recording_id, channel_id=channel_id, message=message, status="decoder_error",
                           reason=f"Decoder failed its execution or output contract: {type(exc).__name__}: {str(exc)[:500]}")


def _encoded_size(frame: SensorFrame | None) -> int:
    if frame is None or frame.decoded is None:
        return 0
    result = frame.decoded
    if result.camera is not None:
        return len(result.camera.data_base64)
    points = result.point_cloud
    return len(points.data_base64) + (len(points.intensity.data_base64) if points.intensity is not None else 0)


def _transform_fits_preview(frame: SensorFrame, transform) -> bool:
    if frame.decoded.point_cloud is None:
        return True
    qx, qy, qz, qw = transform.rotation_xyzw
    dx, dy, dz = transform.translation_m
    for x, y, z in struct.iter_unpack("<fff", base64.b64decode(frame.decoded.point_cloud.data_base64, validate=True)):
        tx, ty, tz = 2 * (qy * z - qz * y), 2 * (qz * x - qx * z), 2 * (qx * y - qy * x)
        point = (x + qw * tx + qy * tz - qz * ty + dx,
                 y + qw * ty + qz * tx - qx * tz + dy,
                 z + qw * tz + qx * ty - qy * tx + dz)
        if any(not math.isfinite(value) or abs(value) > 3.4028234663852886e38 for value in point):
            return False
    return True


def sensor_bundle(session, db: Database, registry, recording_id: str, at_ns: str, binding_revision: int | None,
                  channel_ids: list[int] | None, max_age_ns: str | None, max_points: int) -> SensorBundle:
    recording = _get_recording(session, recording_id)
    if binding_revision is not None and max_age_ns is not None:
        raise HTTPException(422, "A pinned binding supplies max_age_ns; query overrides are not allowed")
    binding = binding_get(session, recording_id, binding_revision) if binding_revision is not None else None
    mappings = {item.channel_id: item for item in binding.channels} if binding else {}
    if channel_ids is None:
        channel_ids = list(mappings) if binding else [item.channel_id for item in sensor_channels(session, registry, recording_id).channels if item.status == "supported"][:4]
    if not 0 <= len(channel_ids) <= 4 or len(set(channel_ids)) != len(channel_ids):
        raise HTTPException(422, "Request at most four unique channel IDs; select a subset when a binding has more channels")
    if binding and any(channel_id not in mappings for channel_id in channel_ids):
        raise HTTPException(422, "Requested channel is absent from the selected binding revision")
    sensors = {item.key: item for item in binding.rig_snapshot.sensors} if binding else {}
    results, encoded_size = [], 0
    for channel_id in channel_ids:
        _channel(session, recording_id, channel_id)
        mapping = mappings.get(channel_id)
        declared = sensors[mapping.sensor_key] if mapping else None
        age_limit = mapping.max_age_ns if mapping else max_age_ns
        offset = int(mapping.log_time_offset_ns) if mapping else 0
        previous, following = select_message_at(session, recording_id, channel_id, int(at_ns) - offset)
        policy_source = "binding" if mapping else "query" if age_limit is not None else "unknown"
        common = {"channel_id": channel_id, "sensor_key": mapping.sensor_key if mapping else None, "max_age_ns": age_limit,
                  "age_policy_source": policy_source, "log_time_offset_ns": str(offset), "declared_sensor": declared,
                  "next_log_time_ns": str(int(following.log_time_key)) if following else None,
                  "next_aligned_time_ns": str(int(following.log_time_key) + offset) if following else None}
        if previous is None:
            results.append(SensorBundleEntry(**common, selection_status="missing", time_relation="missing", freshness="missing",
                                             transform_status="not_decoded" if binding else "unbound",
                                             reason="No observation exists at or before the cursor; future observations are not rendered"))
            continue
        aligned = int(previous.log_time_key) + offset
        age = int(at_ns) - aligned
        freshness = "unknown" if age_limit is None else "stale" if age > int(age_limit) else "fresh"
        frame, reason = None, None
        if freshness == "stale":
            reason = "Observation exceeds its declared maximum age; preview is withheld"
        else:
            try:
                frame = sensor_frame(session, db, registry, recording_id, channel_id, previous.ordinal, max_points,
                                     decoder_id=mapping.decoder_id if mapping else None, decoder_version=mapping.decoder_version if mapping else None)
            except HTTPException as exc:
                if exc.status_code not in {404, 409}:
                    raise
                frame = SensorFrame(recording_id=recording_id, channel_id=channel_id, message=_message(previous),
                                    status="integrity_error", reason=str(exc.detail))
            amount = _encoded_size(frame)
            if encoded_size + amount > MAX_BUNDLE_DATA_BYTES:
                frame = frame.model_copy(update={"status": "limit_exceeded", "decoded": None,
                                                 "reason": "Bundle exceeds the 24 MiB encoded preview budget"})
            else:
                encoded_size += amount
        transform, transform_status = None, "unbound"
        if binding:
            if frame is None or frame.decoded is None:
                transform_status = "not_decoded"
            elif frame.decoded.kind != declared.kind:
                transform_status = "kind_mismatch"
            elif frame.decoded.frame_id != declared.frame_id:
                transform_status = "frame_mismatch"
            elif binding.rig_snapshot.coordinate_convention == "unknown":
                transform_status = "unknown_convention"
            elif declared.transform is None:
                transform_status = "unknown_transform"
            elif not _transform_fits_preview(frame, declared.transform):
                transform_status, reason = "out_of_range", "Declared transform would exceed the finite float32 preview range"
            else:
                transform, transform_status = declared.transform, "available"
        results.append(SensorBundleEntry(**common, selection_status="selected", time_relation="at_cursor" if age == 0 else "past",
                                         freshness=freshness, aligned_time_ns=str(aligned), age_ns=str(age), message=_message(previous),
                                         frame=frame, transform_status=transform_status, transform=transform, reason=reason))
    return SensorBundle(recording_id=recording_id, recording_sha256=recording.sha256, at_ns=at_ns,
                        binding_revision=binding.revision if binding else None, binding_manifest_hash=binding.manifest_hash if binding else None,
                        rig_snapshot=binding.rig_snapshot if binding else None, entries=results, encoded_data_size_bytes=encoded_size,
                        warnings=["Log-time offsets are source declarations; this bundle does not establish acquisition synchronization.",
                                  "Rig transforms and intrinsics are source-declared and are not physically validated calibration.",
                                  "Payload hashes verify bytes; the original MCAP and SQL timestamp/index metadata have distinct integrity boundaries."])


def register_sensor_routes(api: APIRouter, db: Database, registry):
    @api.get("/sensor-decoders", tags=["sensors"])
    def list_sensor_decoders():
        return registry.list()

    @api.post("/sensor-rigs", response_model=RigDetail, status_code=201, tags=["sensors"])
    def post_rig(body: RigCreate):
        return rig_create(db, body)

    @api.get("/sensor-rigs", response_model=list[RigDetail], tags=["sensors"])
    def list_rigs():
        with db.session() as session:
            return [rig_get(session, row.id) for row in session.scalars(select(SensorRigRow).order_by(SensorRigRow.created_at.desc(), SensorRigRow.id.desc()))]

    @api.get("/sensor-rigs/{rig_id}", response_model=RigDetail, tags=["sensors"])
    def get_rig(rig_id: str, revision: int | None = Query(default=None, ge=1)):
        with db.session() as session:
            return rig_get(session, rig_id, revision)

    @api.post("/sensor-rigs/{rig_id}/revisions", response_model=RigDetail, status_code=201, tags=["sensors"])
    def post_rig_revision(rig_id: str, body: RigRevisionCreate):
        return rig_revise(db, rig_id, body)

    @api.get("/sensor-rigs/{rig_id}/revisions", response_model=list[RigDetail], tags=["sensors"])
    def list_rig_revisions(rig_id: str):
        with db.session() as session:
            _rig_row(session, rig_id, None)
            return [rig_get(session, rig_id, revision) for revision in session.scalars(select(SensorRigRevisionRow.revision)
                    .where(SensorRigRevisionRow.rig_id == rig_id).order_by(SensorRigRevisionRow.revision.desc()))]

    @api.get("/sensor-rigs/{rig_id}/export", tags=["sensors"])
    def export_rig(rig_id: str, revision: int = Query(ge=1)):
        with db.session() as session:
            detail = rig_get(session, rig_id, revision)
            row = _rig_row(session, rig_id, revision)
            return Response(row.manifest, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{rig_id}.r{revision}.json"',
                                                                                 "ETag": f'"{detail.manifest_hash}"', "X-Content-Type-Options": "nosniff"})

    @api.post("/recordings/{recording_id}/sensor-bindings", response_model=BindingDetail, status_code=201, tags=["sensors"])
    def post_binding(recording_id: str, body: BindingCreate):
        return binding_create(db, registry, recording_id, body)

    @api.get("/recordings/{recording_id}/sensor-bindings", response_model=BindingDetail, tags=["sensors"])
    def get_binding(recording_id: str, revision: int | None = Query(default=None, ge=1)):
        with db.session() as session:
            return binding_get(session, recording_id, revision)

    @api.get("/recordings/{recording_id}/sensor-bindings/revisions", response_model=list[BindingDetail], tags=["sensors"])
    def list_binding_revisions(recording_id: str):
        with db.session() as session:
            _get_recording(session, recording_id)
            return [binding_get(session, recording_id, revision) for revision in session.scalars(select(RecordingSensorBindingRevisionRow.revision)
                    .where(RecordingSensorBindingRevisionRow.recording_id == recording_id).order_by(RecordingSensorBindingRevisionRow.revision.desc()))]

    @api.get("/recordings/{recording_id}/sensor-bindings/export", tags=["sensors"])
    def export_binding(recording_id: str, revision: int = Query(ge=1)):
        with db.session() as session:
            detail = binding_get(session, recording_id, revision)
            row = _binding_row(session, recording_id, revision)
            return Response(row.manifest, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{recording_id}.binding-r{revision}.json"',
                                                                                 "ETag": f'"{detail.manifest_hash}"', "X-Content-Type-Options": "nosniff"})

    @api.get("/recordings/{recording_id}/sensor-channels", response_model=SensorChannels, tags=["sensors"])
    def get_sensor_channels(recording_id: str):
        with db.session() as session:
            return sensor_channels(session, registry, recording_id)

    @api.get("/recordings/{recording_id}/sensor-frame", response_model=SensorFrame, tags=["sensors"])
    def get_sensor_frame(recording_id: str, channel_id: int = Query(ge=0, le=65535), ordinal: int = Query(ge=0, le=99999),
                         max_points: int = Query(default=20000, ge=1, le=100000)):
        with db.session() as session:
            return sensor_frame(session, db, registry, recording_id, channel_id, ordinal, max_points)

    @api.get("/recordings/{recording_id}/sensor-bundle", response_model=SensorBundle, tags=["sensors"])
    def get_sensor_bundle(recording_id: str, at_ns: UnsignedNs, binding_revision: int | None = Query(default=None, ge=1),
                          channel_id: list[int] | None = Query(default=None, max_length=4), max_age_ns: UnsignedNs | None = None,
                          max_points: int = Query(default=20000, ge=1, le=100000)):
        if channel_id is not None and any(value < 0 or value > 65535 for value in channel_id):
            raise HTTPException(422, "Channel ID must be in 0..65535")
        with db.session() as session:
            return sensor_bundle(session, db, registry, recording_id, at_ns, binding_revision, channel_id, max_age_ns, max_points)

    @api.get("/recordings/{recording_id}/messages/{ordinal}/payload", tags=["sensors"])
    def raw_message(recording_id: str, ordinal: int):
        with db.session() as session:
            recording = _get_recording(session, recording_id)
            entry = session.get(RecordingMessageRow, (recording_id, ordinal))
            if entry is None:
                raise HTTPException(404, "Message ordinal not found in this recording")
            content = read_message_bytes(db, recording, entry)
            return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{recording_id}.message-{ordinal}.bin"',
                                                                                     "ETag": f'"{entry.sha256}"', "X-Content-Type-Options": "nosniff"})
