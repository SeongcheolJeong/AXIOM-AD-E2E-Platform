"""Exact recorded source identities for the stack replay kernel.

No source-selection fallback can turn unknown input into observed free space.
"""
import json
import hashlib
import math
from typing import Annotated

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from .recording_models import RecordingChannelRow, RecordingMessageRow
from .recordings import _get_recording, select_message_at, read_message_bytes
from .schemas import Name, StrictModel
from .sensor_schemas import UnsignedNs
from .sensors import _channel, _resolve_decoder, binding_get
from .stack_replay_schemas import StackReplayChannel, StackReplayInput, StackReplayMessage, StackReplaySources

EGO_DECODER_ID = 'axiom-ego-json'
EGO_DECODER_VERSION = '1'
EGO_SCHEMA_NAME = 'axiom.EgoState.v1'
EGO_SCHEMA = {'type': 'object', 'properties': {
    'timestamp_ns': {'type': 'string'}, 'frame_id': {'type': 'string'},
    'speed_mps': {'type': 'number'}, 'route_heading_error_rad': {'type': 'number'},
    'pitch_rad': {'type': 'number'}, 'roll_rad': {'type': 'number'}, 'source': {'type': 'string'}},
    'required': ['timestamp_ns', 'frame_id', 'speed_mps', 'route_heading_error_rad', 'pitch_rad', 'roll_rad', 'source'],
    'additionalProperties': False}
MAX_POINTS = 20_000


class EgoState(StrictModel):
    timestamp_ns: UnsignedNs
    frame_id: Name
    speed_mps: float = Field(ge=0, le=100)
    route_heading_error_rad: float = Field(ge=-math.pi, le=math.pi)
    pitch_rad: float = Field(ge=-math.pi, le=math.pi)
    roll_rad: float = Field(ge=-math.pi, le=math.pi)
    source: Name


def supports_ego(channel, schema):
    if (channel.message_encoding != 'json' or schema is None or schema.name != EGO_SCHEMA_NAME
            or schema.encoding != 'jsonschema' or schema.definition is None or schema.definition_truncated):
        return False
    try:
        raw = schema.definition.encode('utf-8')
        if len(raw) != schema.size_bytes or hashlib.sha256(raw).hexdigest() != schema.sha256:
            return False
        definition = json.loads(schema.definition)
        if not isinstance(definition, dict):
            return False
        # Documentation-only JSON Schema keys do not change payload syntax.
        return {key: value for key, value in definition.items() if key not in {'$schema', 'title', 'description'}} == EGO_SCHEMA
    except (ValueError, TypeError, RecursionError):
        return False


def decode_ego(content):
    if len(content) > 64 * 1024:
        raise ValueError('Ego telemetry payload exceeds 64 KiB')
    def invalid_constant(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate ego telemetry key')
            result[key] = value
        return result
    value = json.loads(content.decode('utf-8'), parse_constant=invalid_constant, object_pairs_hook=unique_pairs)
    if not isinstance(value, dict):
        raise ValueError('Ego telemetry must be a JSON object')
    for key in ('speed_mps', 'route_heading_error_rad', 'pitch_rad', 'roll_rad'):
        if isinstance(value.get(key), bool) or not isinstance(value.get(key), (int, float)):
            raise ValueError('Ego telemetry numerical values must use JSON numbers')
    return EgoState.model_validate(value)


def discover_sources(session, decoders, recording_id):
    recording = _get_recording(session, recording_id)
    channels = []
    for row in session.scalars(select(RecordingChannelRow).where(RecordingChannelRow.recording_id == recording_id).order_by(RecordingChannelRow.channel_id)):
        channel, schema = _channel(session, recording_id, row.channel_id)
        options = []
        if supports_ego(channel, schema):
            options.append(('ego', EGO_DECODER_ID, EGO_DECODER_VERSION))
        for metadata in decoders.list():
            decoder = decoders.get(metadata.id, metadata.version)
            if decoder and decoder.kind_for(channel, schema) == 'lidar':
                options.append(('lidar', metadata.id, metadata.version))
        for role, decoder_id, decoder_version in options:
            channels.append(StackReplayChannel(channel_id=channel.id, topic=channel.topic,
                schema_name=schema.name if schema else None, schema_sha256=schema.sha256 if schema else None,
                message_count=channel.message_count, start_time_ns=channel.start_time_ns, end_time_ns=channel.end_time_ns,
                role=role, decoder_id=decoder_id, decoder_version=decoder_version))
    return StackReplaySources(recording_id=recording_id, recording_sha256=recording.sha256, channels=channels,
        warnings=['Channel roles require exact supported schema and encoding; topic names are not evidence.',
                  'Ego state, route heading and rig transforms remain source declarations.',
                  'Recorded open-loop commands do not modify subsequent source observations.'])


def recording_snapshot(session, recording_id, channel_ids):
    recording = _get_recording(session, recording_id)
    values = [_channel(session, recording_id, identifier) for identifier in channel_ids]
    return {key: recording.payload[key] for key in ('id', 'name', 'sha256', 'size_bytes', 'source_kind', 'source', 'index')} | {
        'channels': [channel.model_dump(mode='json') for channel, _ in values],
        'schemas': [schema.model_dump(mode='json') if schema else None for _, schema in values]}


def message_input(recording, entry, decoder_id, decoder_version):
    return StackReplayMessage(recording_id=recording.id, recording_sha256=recording.sha256,
        channel_id=entry.channel_id, ordinal=entry.ordinal, log_time_ns=str(int(entry.log_time_key)),
        publish_time_ns=str(int(entry.publish_time_key)), message_sha256=entry.sha256,
        message_size_bytes=entry.payload_length, decoder_id=decoder_id, decoder_version=decoder_version)


def prepare_sources(session, decoders, body, stack):
    recording = _get_recording(session, body.recording_id)
    binding = binding_get(session, body.recording_id, body.binding_revision)
    if binding.rig_snapshot.reference_frame != stack['definition']['reference_frame']:
        raise HTTPException(422, 'Pinned rig reference frame differs from the selected stack reference frame')
    mapping = next((item for item in binding.channels if item.channel_id == body.lidar_channel_id), None)
    if mapping is None:
        raise HTTPException(422, 'LiDAR channel is absent from the selected binding revision')
    declared = next(item for item in binding.rig_snapshot.sensors if item.key == mapping.sensor_key)
    channel, schema = _channel(session, body.recording_id, body.lidar_channel_id)
    decoder = _resolve_decoder(decoders, channel, schema, mapping.decoder_id, mapping.decoder_version)
    if declared.kind != 'lidar' or decoder.kind_for(channel, schema) != 'lidar':
        raise HTTPException(422, 'The selected bound channel does not support a LiDAR role')
    ego_channel, ego_schema = _channel(session, body.recording_id, body.ego_channel_id)
    if not supports_ego(ego_channel, ego_schema):
        raise HTTPException(422, 'Ego channel must use exact axiom.EgoState.v1 JSON/jsonschema')
    inputs, last_time = [], None
    for index, ordinal in enumerate(body.ego_ordinals):
        ego = session.get(RecordingMessageRow, (body.recording_id, ordinal))
        if ego is None or ego.channel_id != body.ego_channel_id:
            raise HTTPException(422, 'An ego ordinal does not belong to the selected recording channel')
        at = int(ego.log_time_key) + int(body.ego_log_time_offset_ns)
        if not 0 <= at <= 2**64 - 1 or (last_time is not None and at <= last_time):
            raise HTTPException(422, 'Selected ego aligned timestamps must be uint64 and strictly increasing in request order')
        last_time = at
        lidar, following = select_message_at(session, body.recording_id, body.lidar_channel_id, at - int(mapping.log_time_offset_ns))
        aligned = int(lidar.log_time_key) + int(mapping.log_time_offset_ns) if lidar else None
        inputs.append(StackReplayInput(index=index, at_ns=str(at), ego=message_input(recording, ego, body.ego_decoder_id, body.ego_decoder_version),
            lidar=message_input(recording, lidar, mapping.decoder_id, mapping.decoder_version) if lidar else None,
            lidar_aligned_time_ns=str(aligned) if aligned is not None else None,
            lidar_age_ns=str(at-aligned) if aligned is not None else None,
            next_lidar_aligned_time_ns=str(int(following.log_time_key) + int(mapping.log_time_offset_ns)) if following else None))
    return {'recording_snapshot': recording_snapshot(session, body.recording_id, [body.lidar_channel_id, body.ego_channel_id]),
            'binding_snapshot': binding.model_dump(mode='json'), 'inputs': [item.model_dump(mode='json') for item in inputs]}


def load_packet(db, decoders, prepared, item):
    """Read/hash exact source bytes, then form one bounded aligned kernel packet."""
    import base64
    import hashlib
    import struct
    from .sensors import sensor_frame
    from .stack_schemas import StackPacket, StackEgo, StackLidar

    body = prepared['request']
    reference = prepared['stack_snapshot']['definition']['reference_frame']
    packet = StackPacket(sequence=item.index, at_ns=item.at_ns)
    points_bytes = None
    with db.read_session() as session:
        try:
            current = recording_snapshot(session, body['recording_id'], [body['lidar_channel_id'], body['ego_channel_id']])
            if current != prepared['recording_snapshot']:
                raise ValueError('Pinned recording/channel/schema definition changed')
            recording = _get_recording(session, body['recording_id'])
            binding = binding_get(session, body['recording_id'], body['binding_revision'])
            if binding.model_dump(mode='json') != prepared['binding_snapshot']:
                raise ValueError('Pinned binding definition changed')
        except Exception as error:
            packet.unavailable_reasons.append('source_definition_unavailable: ' + str(error)[:400])
            return packet, None

        def exact_entry(message):
            entry = session.get(RecordingMessageRow, (message.recording_id, message.ordinal))
            if entry is None or message_input(recording, entry, message.decoder_id, message.decoder_version) != message:
                raise ValueError('Original source message index/identity changed')
            return entry

        try:
            ego = decode_ego(read_message_bytes(db, recording, exact_entry(item.ego)))
            if ego.frame_id != reference:
                raise ValueError('Declared ego frame differs from stack reference frame')
            packet.ego = StackEgo(**(ego.model_dump(mode='json') | {'timestamp_ns': item.at_ns, 'capture_time_ns': ego.timestamp_ns}))
        except Exception as error:
            packet.unavailable_reasons.append('ego_unavailable: ' + str(error)[:400])
        try:
            if item.lidar is None:
                raise ValueError('No LiDAR message exists at or before this aligned cursor; future data is excluded')
            if not 0 <= int(item.lidar_aligned_time_ns) <= 2**64-1:
                raise ValueError('Aligned LiDAR clock is outside uint64')
            mapping = next(value for value in binding.channels if value.channel_id == body['lidar_channel_id'])
            if mapping.max_age_ns is not None and int(item.lidar_age_ns) > int(mapping.max_age_ns):
                raise ValueError('LiDAR exceeds the pinned binding maximum age')
            declared = next(value for value in binding.rig_snapshot.sensors if value.key == mapping.sensor_key)
            if binding.rig_snapshot.coordinate_convention != 'right_handed_z_up' or declared.transform is None:
                raise ValueError('An explicit right-handed Z-up rig and sensor-to-rig transform are required')
            exact_entry(item.lidar)
            frame = sensor_frame(session, db, decoders, item.lidar.recording_id, item.lidar.channel_id, item.lidar.ordinal,
                MAX_POINTS, decoder_id=item.lidar.decoder_id, decoder_version=item.lidar.decoder_version)
            if frame.status != 'decoded' or frame.decoded is None or frame.decoded.point_cloud is None:
                raise ValueError('LiDAR decode unavailable: ' + str(frame.reason or frame.status))
            decoded, cloud = frame.decoded, frame.decoded.point_cloud
            if decoded.frame_id != declared.frame_id:
                raise ValueError('Decoded LiDAR frame differs from the declared sensor transform frame')
            if (cloud.original_point_count > MAX_POINTS or cloud.invalid_points or cloud.skipped_points
                    or cloud.sampling_stride != 1 or cloud.point_count != cloud.original_point_count):
                raise ValueError('Full valid LiDAR input required; invalid, decimated or oversized frames are unavailable')
            raw = base64.b64decode(cloud.data_base64, validate=True)
            qx, qy, qz, qw = declared.transform.rotation_xyzw
            dx, dy, dz = declared.transform.translation_m
            result = bytearray()
            for x, y, z in struct.iter_unpack('<fff', raw):
                tx, ty, tz = 2*(qy*z-qz*y), 2*(qz*x-qx*z), 2*(qx*y-qy*x)
                point = (x+qw*tx+qy*tz-qz*ty+dx, y+qw*ty+qz*tx-qx*tz+dy, z+qw*tz+qx*ty-qy*tx+dz)
                if any(not math.isfinite(value) or abs(value) > 1_000_000 for value in point):
                    raise ValueError('Transformed point exceeds bounded finite kernel geometry')
                result.extend(struct.pack('<fff', *point))
            points_bytes = bytes(result)
            packet.lidar = StackLidar(timestamp_ns=item.lidar_aligned_time_ns, capture_time_ns=decoded.capture_time_ns,
                frame_id=reference, point_count=cloud.point_count, points_sha256=hashlib.sha256(points_bytes).hexdigest(),
                size_bytes=len(points_bytes), source_message_sha256=item.lidar.message_sha256, complete_frame=True)
        except Exception as error:
            points_bytes = None
            packet.lidar = None
            packet.unavailable_reasons.append('lidar_unavailable: ' + str(error)[:400])
    return packet, points_bytes
